import asyncio
import json
import logging
import pathlib
import sys
import tempfile

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    CallbackQuery,
    Document,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(
            pathlib.Path(__file__).resolve().parent.parent / "bot.log",
            encoding="utf-8",
        )
    ],
)
log = logging.getLogger("bot")

from bot.config import BOT_TOKEN, MINI_APP_URL
from contracts.schemas import (
    AnalyzeResponse,
    Category,
    Flag,
    OverlapGroup,
    PlanItem,
    Summary,
)
from backend.ml.parser import parse_statement as parse_statement_csv
from backend.ml.detector import detect as detect_subscriptions
from backend.ml.llm import enrich as enrich_with_llm

# ────────────── встроенные функции пересчёта после отписки ──────────────
# Нужны, чтобы бот работал независимо от backend/recurring_detector.py
# (его нет в main). Логика та же, что была там.

_ESSENTIAL = {Category.BANK_PREMIUM, Category.TELECOM, Category.CLOUD}


def _plural(n, one, few, many):
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and (n % 100 < 12 or n % 100 > 14):
        return few
    return many


def _build_groups(subscriptions):
    by_category = {}
    for s in subscriptions:
        by_category.setdefault(s.category, []).append(s)
    groups = []
    counter = 0
    for cat, subs in by_category.items():
        if len(subs) < 2:
            continue
        counter += 1
        keep = min(subs, key=lambda s: s.amount)
        others = [s for s in subs if s.id != keep.id]
        savings = sum(s.yearly_cost for s in others)
        groups.append(OverlapGroup(
            id=f"{cat.value}_overlap_{counter}",
            category=cat,
            subscription_ids=[s.id for s in subs],
            keep_suggestion=keep.id,
            savings_yearly=round(savings, 2),
            explanation=(f"Несколько {cat.value}-сервисов закрывают одну "
                         f"потребность. {keep.name} дешевле остальных — его "
                         f"можно оставить, остальные отключить."),
        ))
    kept_ids = {g.keep_suggestion for g in groups if g.keep_suggestion}
    for g in groups:
        for s in subscriptions:
            if s.id in g.subscription_ids and s.id not in kept_ids:
                s.flag = Flag.RED
                keep_name = next((x.name for x in subscriptions
                                  if x.id == g.keep_suggestion), "другую подписку")
                s.reason = "Дублирует " + keep_name
                s.overlap_group = g.id
            elif s.id == g.keep_suggestion:
                s.flag = Flag.YELLOW
                s.reason = "Оставьте эту — она закрывает всю категорию"
                s.overlap_group = g.id
    return groups


def _build_plan(subscriptions, groups):
    plan = []
    priority = 1
    for s in sorted([s for s in subscriptions if s.flag == Flag.RED],
                    key=lambda s: s.yearly_cost, reverse=True):
        plan.append(PlanItem(subscription_id=s.id, action="cancel",
                             priority=priority,
                             reason=f"Дублирует другую подписку в категории "
                                    f"{s.category.value}, экономия "
                                    f"{s.yearly_cost:,.0f} руб. в год",
                             savings_yearly=s.yearly_cost))
        priority += 1
    for s in sorted([s for s in subscriptions if s.flag == Flag.YELLOW],
                    key=lambda s: s.amount, reverse=True):
        plan.append(PlanItem(subscription_id=s.id, action="review",
                             priority=priority,
                             reason="Стоит проверить, пользуетесь ли вы сервисом",
                             savings_yearly=0.0))
        priority += 1
    for s in subscriptions:
        if s.flag == Flag.GREEN:
            plan.append(PlanItem(subscription_id=s.id, action="keep",
                                 priority=priority,
                                 reason="Регулярная подписка, которую выгодно "
                                        "сохранить",
                                 savings_yearly=0.0))
            priority += 1
    return plan


def _recompute_response(subscriptions, request_id="recomputed",
                        transactions_parsed=0, period_from=None,
                        period_to=None):
    subs = list(subscriptions)
    for s in subs:
        s.flag = Flag.YELLOW
        s.overlap_group = None
        s.reason = "Регулярное списание, проверьте нужность"
    groups = _build_groups(subs)
    for s in subs:
        if s.category in _ESSENTIAL:
            s.flag = Flag.GREEN
            s.reason = "Единственный сервис в категории"
            s.overlap_group = None
    plan = _build_plan(subs, groups)
    yearly_total = sum(s.yearly_cost for s in subs)
    monthly_total = yearly_total / 12 if yearly_total else 0
    savings = sum(p.savings_yearly for p in plan if p.action == "cancel")
    red = sum(1 for s in subs if s.flag == Flag.RED)
    yellow = sum(1 for s in subs if s.flag == Flag.YELLOW)
    green = sum(1 for s in subs if s.flag == Flag.GREEN)
    from datetime import date as _date
    return AnalyzeResponse(
        request_id=request_id,
        period_from=period_from or _date(2000, 1, 1),
        period_to=period_to or _date(2000, 1, 1),
        transactions_parsed=transactions_parsed,
        subscriptions=subs,
        overlaps=groups,
        summary=Summary(
            subscriptions_count=len(subs),
            monthly_total=round(monthly_total, 2),
            yearly_total=round(yearly_total, 2),
            red_count=red, yellow_count=yellow, green_count=green,
            potential_savings_yearly=round(savings, 2),
        ),
        plan=plan,
        headline=("По выписке найдено %d %s на %s ₽ в месяц. Отключив дубли, "
                  "вы вернёте %s ₽ за год." % (
                      len(subs), _plural(len(subs), "подписка", "подписки", "подписок"),
                      f"{monthly_total:,.0f}", f"{savings:,.0f}")),
        llm_used=False,
    )

CONTRACTS_DIR = pathlib.Path(__file__).resolve().parent.parent / "contracts"

MOCK: dict = json.loads(
    (CONTRACTS_DIR / "mock_analyze_response.json").read_text(encoding="utf-8")
)

DEMO = AnalyzeResponse.model_validate(MOCK)

# Последний результат анализа выписки. Для «Отписаться» показываем именно
# найденные ботом подписки; если анализа ещё не было — демо-данные.
LAST_RESULT: AnalyzeResponse = DEMO

# Сервисы, от которых пользователь нажал «Я отписался» — скрываем из списка.
UNSUBSCRIBED: set[str] = set()

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
CANCEL_KB: dict[str, dict] = {}
_cancel_kb_path = DATA_DIR / "cancel_kb.json"
if _cancel_kb_path.exists():
    CANCEL_KB = json.loads(_cancel_kb_path.read_text(encoding="utf-8"))

router = Router()

FLAG_EMOJI = {Flag.GREEN: "\U0001f7e2", Flag.YELLOW: "\U0001f7e1", Flag.RED: "\U0001f534"}
FLAG_LABEL = {Flag.GREEN: "Полезная", Flag.YELLOW: "Средняя", Flag.RED: "Лишняя"}

SBER_LINK = "https://online.sberbank.ru"


def _report_button():
    """Кнопка «Открыть отчёт» (Mini App). Только если URL HTTPS."""
    if MINI_APP_URL.startswith("https://"):
        return [
            InlineKeyboardButton(
                text="\U0001f4c8 Открыть отчёт",
                web_app=WebAppInfo(url=MINI_APP_URL),
            )
        ]
    return []


def build_main_kb() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text="\U0001f4e5\ufe0f Получить выписку",
                url=SBER_LINK,
            )
        ]
    ]
    report = _report_button()
    if report:
        buttons.append(report)
    buttons.append(
        [InlineKeyboardButton(text="\U0001f50d Демо-анализ", callback_data="analyze")]
    )
    buttons.append(
        [InlineKeyboardButton(text="\u274c Отписаться", callback_data="cancel_list")]
    )
    buttons.append(
        [InlineKeyboardButton(text="\u2139\ufe0f Помощь", callback_data="help")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_report_kb() -> InlineKeyboardMarkup:
    buttons = []
    report = _report_button()
    if report:
        buttons.append(report)
    buttons.append(
        [InlineKeyboardButton(text="\U0001f3e0 Главное меню", callback_data="menu")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


back_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="\U0001f3e0 Главное меню", callback_data="menu")]
    ]
)


def money(amount: float) -> str:
    return f"{amount:,.0f} \u20bd".replace(",", " ")


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русская плюрализация: 1 подписка / 2 подписки / 5 подписок."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if 2 <= n % 10 <= 4 and (n % 100 < 12 or n % 100 > 14):
        return f"{n} {few}"
    return f"{n} {many}"


def build_subscription_card(sub) -> str:
    emoji = FLAG_EMOJI[sub.flag]
    label = FLAG_LABEL[sub.flag]
    period_map = {
        "monthly": "в месяц",
        "yearly": "в год",
        "weekly": "в неделю",
        "quarterly": "в квартал",
        "unknown": "/ период",
    }
    period_text = period_map.get(sub.period.value, sub.period.value)
    return (
        f"{emoji} <b>{sub.name}</b>  ({label})\n"
        f"   {money(sub.amount)} / {period_text}\n"
        f"   \u2248 {money(sub.yearly_cost)} / год\n"
        f"   {sub.reason}"
    )


def format_analysis(data: AnalyzeResponse) -> str:
    summary = data.summary
    header = (
        f"\U0001f4ca <b>Анализ подписок</b>\n\n"
        f"{data.headline}\n\n"
        f"\U0001f4b0 <b>{money(summary.monthly_total)}</b> / месяц\n"
        f"\U0001f4b5 Экономия: <b>{money(summary.potential_savings_yearly)}</b> / год\n\n"
        f"\U0001f534 {plural(summary.red_count, 'лишняя', 'лишние', 'лишних')}   "
        f"\U0001f7e1 {plural(summary.yellow_count, 'средняя', 'средние', 'средних')}   "
        f"\U0001f7e2 {plural(summary.green_count, 'полезная', 'полезные', 'полезных')}\n"
        f"{'—' * 28}"
    )
    # Сортировка: красные → жёлтые → зелёные
    flag_order = {Flag.RED: 0, Flag.YELLOW: 1, Flag.GREEN: 2}
    active = [s for s in data.subscriptions if s.id not in UNSUBSCRIBED]
    active.sort(key=lambda s: flag_order.get(s.flag, 3))
    cards = [build_subscription_card(sub) for sub in active]
    return "\n\n".join([header, *cards])


def cancel_markup() -> InlineKeyboardMarkup:
    buttons = []
    for sub in LAST_RESULT.subscriptions:
        if sub.id in UNSUBSCRIBED:
            continue
        flag = FLAG_EMOJI[sub.flag]
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{flag} {sub.name}",
                    callback_data=f"cancel_{sub.id}",
                )
            ]
        )
    buttons.append(
        [InlineKeyboardButton(text="\u2190 Меню", callback_data="menu")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _reply_kb(event, text, kb):
    if isinstance(event, CallbackQuery):
        return event.message.answer(text, reply_markup=kb, parse_mode="HTML")
    return event.answer(text, reply_markup=kb, parse_mode="HTML")


async def _reply(event, text):
    return await _reply_kb(event, text, None)


@router.message(CommandStart())
async def cmd_start(message: Message):
    text = (
        "\U0001f50d <b>Сканер подписок</b>\n\n"
        "Пришлите PDF-выписку по счёту из Сбербанк Онлайн,\n"
        "и я покажу, за что вы платите каждый месяц.\n\n"
        "Как получить выписку:\n"
        "1. Нажмите «Получить выписку» — откроется Сбербанк Онлайн\n"
        "2. Сформируйте выписку за 6 месяцев и сохраните в PDF\n"
        "3. Пришлите файл сюда\n\n"
        "Или попробуйте демо-анализ на готовых данных."
    )
    await message.answer(text, reply_markup=build_main_kb(), parse_mode="HTML")


@router.message(Command("help"))
@router.callback_query(F.data == "help")
async def cmd_help(event: Message | CallbackQuery):
    text = (
        "\u2139\ufe0f <b>Как пользоваться</b>\n\n"
        "1. Нажмите «Получить выписку» — откроется Сбербанк Онлайн\n"
        "2. Сформируйте выписку по счёту за 6 месяцев (PDF)\n"
        "3. Отправьте PDF-файл мне\n"
        "4. Я найду все подписки и подсвечу их цветом\n\n"
        "\U0001f7e2 Зелёный — полезная, не трогать\n"
        "\U0001f7e1 Жёлтый — стоит проверить\n"
        "\U0001f534 Красный — дублирует другую, отключить\n\n"
        "Команды:\n"
        "/start — главное меню\n"
        "/analyze — демо-анализ\n"
        "/cancel — инструкция по отписке\n"
        "/help — эта справка"
    )
    await _reply(event, text)


@router.message(F.text & ~F.text.startswith("/"))
async def cmd_text(message: Message):
    text = (
        "\U0001f50d <b>Сканер подписок</b>\n\n"
        "Пришлите PDF-выписку по счёту из Сбербанк Онлайн,\n"
        "и я покажу, за что вы платите каждый месяц.\n\n"
        "Как получить выписку:\n"
        "1. Нажмите «Получить выписку» — откроется Сбербанк Онлайн\n"
        "2. Сформируйте выписку за 6 месяцев и сохраните в PDF\n"
        "3. Пришлите файл сюда\n\n"
        "Или попробуйте демо-анализ на готовых данных."
    )
    await message.answer(text, reply_markup=build_main_kb(), parse_mode="HTML")


@router.message(Command("analyze"))
@router.callback_query(F.data == "analyze")
async def cmd_analyze(event: Message | CallbackQuery):
    text = (
        "\U0001f50d <b>Демо-анализ</b> — данные из mock-файла.\n\n"
        + format_analysis(DEMO)
        + "\n\n\u26a0\ufe0f Это демо. Отправьте реальную PDF-выписку, "
        "чтобы получить настоящий анализ."
    )
    await _reply_kb(event, text, back_kb)


@router.callback_query(F.data == "menu")
async def cmd_menu(callback: CallbackQuery):
    await callback.message.answer(
        "\U0001f50d <b>Сканер подписок</b> — главное меню",
        reply_markup=build_main_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(Command("cancel"))
@router.callback_query(F.data == "cancel_list")
async def cmd_cancel(event: Message | CallbackQuery):
    text = (
        "\u274c <b>Отписка от сервиса</b>\n\n"
        "Выберите подписку, чтобы получить\n"
        "пошаговую инструкцию по отмене:"
    )
    await _reply_kb(event, text, cancel_markup())


@router.callback_query(F.data.startswith("cancel_sub_"))
async def cmd_cancel_detail(callback: CallbackQuery):
    sub_id = callback.data.replace("cancel_", "")
    sub = next((s for s in LAST_RESULT.subscriptions if s.id == sub_id), None)
    if not sub:
        await callback.answer("\u274c Подписка не найдена", show_alert=True)
        return

    # Ищем инструкцию в базе отмены (data/cancel_kb.json) по имени сервиса.
    # Нормализуем, чтобы «VK Музыка» нашла «VK музыка», «СберПрайм» — «Сбер Прайм».
    def _norm(s: str) -> str:
        import re as _re
        return _re.sub(r"[^а-яёa-z0-9]", "", s.lower())

    target = _norm(sub.name)
    instr = None
    for name, data in CANCEL_KB.items():
        if _norm(name) == target:
            instr = data
            break

    if not instr:
        await callback.answer(
            "\u26a0\ufe0f Для этого сервиса инструкция ещё не добавлена в базу. "
            "Попробуйте позже.",
            show_alert=True,
        )
        return

    difficulty_map = {
        "easy": "\U0001f7e2 Просто",
        "medium": "\U0001f7e1 Средне",
        "hard": "\U0001f534 Сложно",
    }
    text = (
        f"\u274c <b>Отписка: {sub.name}</b>\n\n"
        f"Сложность: {difficulty_map.get(instr['difficulty'], instr['difficulty'])}\n"
        f"Стоимость: {money(sub.amount)} / мес\n\n"
        f"<b>Шаги:</b>\n"
    )
    for i, step in enumerate(instr["steps"], 1):
        text += f"{i}. {step}\n"

    if instr.get("url"):
        text += f"\n\U0001f517 Прямая ссылка: {instr['url']}"

    if instr.get("letter_template"):
        text += f"\n\n<b>Шаблон письма:</b>\n<pre>{instr['letter_template']}</pre>"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="\u2705 Я отписался", callback_data=f"unsub_{sub.id}")],
            [InlineKeyboardButton(text="\u2190 К списку", callback_data="cancel_list")],
            [InlineKeyboardButton(text="\U0001f3e0 Меню", callback_data="menu")],
        ]
    )
    await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("unsub_"))
async def cmd_unsubscribed(callback: CallbackQuery):
    global UNSUBSCRIBED, LAST_RESULT
    sub_id = callback.data.replace("unsub_", "")
    sub = next((s for s in LAST_RESULT.subscriptions if s.id == sub_id), None)
    if not sub:
        await callback.answer("\u274c Подписка не найдена", show_alert=True)
        return

    UNSUBSCRIBED.add(sub_id)
    log.info(f"пользователь отписался: {sub.name}")

    # Убираем подписку из результата и пересчитываем пересечения и флаги
    remaining = [s for s in LAST_RESULT.subscriptions if s.id != sub_id]
    LAST_RESULT = _recompute_response(
        remaining,
        request_id=LAST_RESULT.request_id,
        transactions_parsed=LAST_RESULT.transactions_parsed,
        period_from=LAST_RESULT.period_from,
        period_to=LAST_RESULT.period_to,
    )

    await callback.message.answer(
        f"\u2705 Готово. <b>{sub.name}</b> отмечен как отписанный.\n"
        "Пересечения и флаги пересчитаны — список обновлён.",
        parse_mode="HTML",
        reply_markup=back_kb,
    )
    await callback.answer()


@router.message(F.document)
async def handle_document(message: Message, bot: Bot):
    global LAST_RESULT
    doc: Document = message.document
    fname = (doc.file_name or "").lower()

    is_pdf = fname.endswith(".pdf")
    is_csv = fname.endswith(".csv")

    if not is_pdf and not is_csv:
        await message.answer(
            "\u26a0\ufe0f Я принимаю только PDF или CSV выписку. "
            "PDF — из Сбербанк Онлайн, CSV — выписка в формате с колонками "
            "«Дата операции; Описание; Сумма»."
        )
        return

    # Лимит размера: выписка за 6 месяцев обычно < 10 МБ
    if doc.file_size and doc.file_size > 10 * 1024 * 1024:
        await message.answer(
            "\u26a0\ufe0f Файл слишком большой (больше 10 МБ). "
            "Сформируйте выписку за меньший период (например, за 3 месяца)."
        )
        return

    wait = await message.answer("\U0001f4e5\ufe0f Скачиваю файл...")

    fd, path = tempfile.mkstemp(suffix=".pdf" if is_pdf else ".csv")
    try:
        await asyncio.wait_for(
            bot.download(file=doc.file_id, destination=path),
            timeout=60,
        )
        log.info(f"скачано: {doc.file_name} ({doc.file_size} байт)")
        await wait.edit_text("\U0001f4ca Разбираю выписку...")

        content = pathlib.Path(path).read_bytes()
        txs = parse_statement_csv(content)
        result_dict = detect_subscriptions(txs)
        # LLM-слой: без ключа безопасно вернёт результат как есть
        result_dict = enrich_with_llm(result_dict)
        response = AnalyzeResponse.model_validate(result_dict)
        log.info(f"разобрано: {len(txs)} операций, llm_used={response.llm_used}")

        # Запоминаем результат — меню «Отписаться» покажет эти подписки
        LAST_RESULT = response
    except asyncio.TimeoutError:
        await wait.delete()
        await message.answer(
            "\u274c Анализ занял слишком много времени. "
            "Файл слишком большой или сложный — сформируйте выписку "
            "за меньший период (например, за 3 месяца)."
        )
        return
    except Exception as exc:
        await wait.delete()
        await message.answer(f"\u274c Не удалось обработать файл: {exc}")
        return
    finally:
        try:
            pathlib.Path(path).unlink(missing_ok=True)
        except OSError:
            pass

    if not response.subscriptions:
        await wait.delete()
        await message.answer(
            "\u274c В выписке не найдено операций или подписок. "
            "Убедитесь, что это выписка за последние 6 месяцев.",
            reply_markup=back_kb,
        )
        return

    await wait.delete()
    header = (
        f"\u2705 Выписка разобрана: {response.transactions_parsed} операций.\n\n"
    )
    text = header + format_analysis(response)
    if response.overlaps:
        text += "\n\n<b>Что предлагаем:</b>\n"
        for g in response.overlaps:
            keep_name = next(
                (s.name for s in response.subscriptions if s.id == g.keep_suggestion),
                "другая подписка",
            )
            text += f"  • {g.category.value} — оставить {keep_name}, сэкономите {money(g.savings_yearly)}/год\n"

    await message.answer(text[:4096], reply_markup=build_report_kb(), parse_mode="HTML")


async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    print("Bot started!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())