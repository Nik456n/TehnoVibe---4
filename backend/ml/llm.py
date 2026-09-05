"""LLM-слой поверх детектора.

Разделение ответственности жёсткое: детектор находит подписки и считает
деньги, модель отвечает за то, что нельзя вычислить — распознавание
незнакомых сервисов, приоритеты оптимизации, объяснения и тексты писем.

Все суммы после ответа модели пересчитываются нами заново: модель может
ошибиться в арифметике, и её числам мы не доверяем.

Слой никогда не роняет пайплайн. Если модель недоступна, ответила мусором
или ключа нет — возвращается результат детектора без изменений,
а в ответе стоит llm_used: false.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parent.parent.parent
PROMPTS_DIR = ROOT / "ai_core" / "prompts"
CACHE_FILE = ROOT / "data" / "llm_cache.json"

BATCH_SIZE = 20
TEMPERATURE = 0.2

VALID_CATEGORIES = {
    "video", "music", "cloud", "fitness", "education", "books", "games",
    "delivery", "transport", "ai_tools", "bank_premium", "telecom", "other",
}


# ─────────────────────────── провайдеры ───────────────────────────


class LLMProvider(Protocol):
    """Любая модель, умеющая ответить текстом на пару system + user.

    Смена провайдера — это замена одного класса: локальная модель
    подключается сюда же, остальной код не меняется.
    """

    def complete(self, system: str, user: str) -> str | None: ...


class GigaChatProvider:
    def __init__(self, credentials: str, model: str | None = None) -> None:
        self.credentials = credentials
        self.model = model

    def complete(self, system: str, user: str) -> str | None:
        try:
            from gigachat import GigaChat
            from gigachat.models import Chat, Messages, MessagesRole
        except ImportError:
            return None

        try:
            with GigaChat(credentials=self.credentials,
                          verify_ssl_certs=False, timeout=30) as giga:
                payload = Chat(
                    messages=[
                        Messages(role=MessagesRole.SYSTEM, content=system),
                        Messages(role=MessagesRole.USER, content=user),
                    ],
                    temperature=TEMPERATURE,
                )
                if self.model:
                    payload.model = self.model
                response = giga.chat(payload)
            return response.choices[0].message.content
        except Exception:
            return None


def get_provider() -> LLMProvider | None:
    key = os.getenv("LLM_API_KEY", "").strip()
    if not key:
        return None
    return GigaChatProvider(key, os.getenv("LLM_MODEL") or None)


# ─────────────────────────── утилиты ───────────────────────────


def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / name
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    # Тестовые прогоны в конце файла в промпт не отправляем
    return text.split("### Тестовые прогоны")[0].strip()


def parse_json(text: str | None) -> Any:
    """Достаёт JSON из ответа модели.

    Модель иногда оборачивает ответ в markdown или добавляет пояснения,
    поэтому при неудаче ищем первый JSON-объект или массив регуляркой.
    """
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(),
                     flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = re.search(r"[\[{].*[\]}]", cleaned, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


class Cache:
    """Кэш ответов модели на диске.

    Нужен, чтобы на защите ничего не считалось вживую: кэш прогревается
    заранее, и демо работает с нулевой задержкой даже без сети.
    """

    def __init__(self, path: Path = CACHE_FILE) -> None:
        self.path = path
        self.data: dict[str, Any] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.data = {}

    def get(self, key: str) -> Any:
        return self.data.get(key)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError:
            pass


# ─────────────────────── 1. категоризация ───────────────────────


def categorize(raw_names: list[str], provider: LLMProvider,
               cache: Cache) -> dict[str, dict]:
    """Распознаёт сервисы, которых нет в словаре мерчантов.

    Возвращает {исходная строка: {name, category, is_subscription}}.
    Строки, про которые модель сказала «это разовая покупка»,
    в результат не попадают.
    """
    result: dict[str, dict] = {}
    todo = []
    for raw in raw_names:
        cached = cache.get(f"cat:{raw}")
        if cached is not None:
            if cached:
                result[raw] = cached
        else:
            todo.append(raw)

    if not todo:
        return result

    system = load_prompt("normalize.md") or (
        "Ты определяешь, какому сервису принадлежит строка из банковской "
        "выписки. Верни только JSON."
    )

    for i in range(0, len(todo), BATCH_SIZE):
        batch = todo[i:i + BATCH_SIZE]
        user = json.dumps(batch, ensure_ascii=False)
        parsed = parse_json(provider.complete(system, user))
        items = parsed.get("items") if isinstance(parsed, dict) else parsed
        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            raw = (item.get("raw_description") or item.get("raw")
                   or item.get("original_name") or "")
            if raw not in batch:
                continue
            if item.get("is_subscription") is False:
                cache.set(f"cat:{raw}", {})
                continue
            category = str(item.get("category", "other")).lower()
            entry = {
                "name": (item.get("name")
                         or item.get("canonical_name") or raw).strip(),
                "category": category if category in VALID_CATEGORIES else "other",
            }
            result[raw] = entry
            cache.set(f"cat:{raw}", entry)

    cache.save()
    return result


# ──────────────────── 2. план и выбор что оставить ────────────────────


def build_plan(result: dict, provider: LLMProvider, cache: Cache) -> bool:
    """Просит модель расставить приоритеты и выбрать, что оставить.

    Модель видит контекст, которого нет у правила «оставить самый дешёвый»:
    сколько раз списывалось, когда подключено, входит ли в бандл.
    Суммы экономии пересчитываем сами — числам модели не доверяем.
    """
    subs = result["subscriptions"]
    if len(subs) < 2:
        return False

    brief = [{
        "id": s["id"], "name": s["name"], "category": s["category"],
        "amount": s["amount"], "period": s["period"],
        "occurrences": s["occurrences"], "yearly_cost": s["yearly_cost"],
        "first_seen": str(s["first_seen"]),
    } for s in subs]

    key = "plan:" + json.dumps(brief, ensure_ascii=False, sort_keys=True)
    parsed = cache.get(key)
    if parsed is None:
        system = load_prompt("plan.md") or (
            "Ты финансовый советник. По списку подписок предложи, что "
            "отключить в первую очередь и что оставить. Только JSON."
        )
        parsed = parse_json(provider.complete(
            system, json.dumps(brief, ensure_ascii=False)))
        if parsed is None:
            return False
        cache.set(key, parsed)
        cache.save()

    items = parsed.get("plan") if isinstance(parsed, dict) else parsed
    if not isinstance(items, list):
        return False

    by_id = {s["id"]: s for s in subs}
    plan = []
    for i, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        sub_id = item.get("subscription_id") or item.get("id")
        if sub_id not in by_id:
            continue
        action = item.get("action", "review")
        if action not in {"cancel", "downgrade", "keep", "review"}:
            action = "review"
        plan.append({
            "subscription_id": sub_id,
            "action": action,
            "priority": i,
            "reason": str(item.get("reason", ""))[:200],
            # экономию берём свою, а не из ответа модели
            "savings_yearly": (by_id[sub_id]["yearly_cost"]
                               if action == "cancel" else 0.0),
        })
    if plan:
        result["plan"] = plan
        return True
    return False


# ──────────────────── 3. живые объяснения ────────────────────


def explain_overlaps(result: dict, provider: LLMProvider,
                     cache: Cache) -> bool:
    """Заменяет шаблонные тексты кластеров на человеческие."""
    overlaps = result.get("overlaps") or []
    if not overlaps:
        return False
    changed = False

    names = {s["id"]: s["name"] for s in result["subscriptions"]}
    for group in overlaps:
        members = [{"id": i, "name": names.get(i, i)}
                   for i in group["subscription_ids"]]
        key = ("expl:" + group["category"] + ":"
               + ",".join(sorted(names.get(i, i) for i in group["subscription_ids"])))
        text = cache.get(key)
        if text is None:
            system = load_prompt("overlaps.md") or (
                "Объясни в одном-двух предложениях, почему эти подписки "
                "дублируют друг друга. Только текст, без JSON."
            )
            payload = {
                "category": group["category"],
                "subscriptions": members,
                "keep_suggestion": group["keep_suggestion"],
                "savings_yearly": group["savings_yearly"],
            }
            raw = provider.complete(
                system, json.dumps(payload, ensure_ascii=False))
            parsed = parse_json(raw)
            if isinstance(parsed, dict):
                text = (parsed.get("explanation")
                        or (parsed.get("overlaps") or [{}])[0]
                        .get("explanation", ""))
            elif isinstance(parsed, list) and parsed:
                text = (parsed[0] or {}).get("explanation", "")
            else:
                # Свободный текст вместо JSON не принимаем: модель могла
                # ответить «Конечно, вот ваш ответ» — такое в интерфейс
                # попадать не должно. Остаётся шаблон детектора.
                text = ""
            if not text:
                continue
            cache.set(key, text)
        group["explanation"] = str(text)[:400]
        changed = True
    cache.save()
    return changed


# ──────────────── 4. инструкция отмены и письмо ────────────────


def cancel_instruction(service_name: str, provider: LLMProvider,
                       cache: Cache) -> dict | None:
    """Инструкция для сервиса, которого нет в базе Elizabeth.

    Помечается source: llm_generated, чтобы в интерфейсе было видно,
    что шаги не проверены человеком.
    """
    key = f"cancel:{service_name}"
    parsed = cache.get(key)
    if parsed is None:
        system = load_prompt("cancel.md") or (
            "Опиши, как отменить подписку на сервис. Верни только JSON "
            'вида {"difficulty": "easy|medium|hard", "steps": ["..."], '
            '"url": "https://..."}. Если не уверен — steps оставь общими, '
            "url поставь null."
        )
        parsed = parse_json(provider.complete(system, service_name))
        if not isinstance(parsed, dict):
            return None
        cache.set(key, parsed)
        cache.save()

    steps = parsed.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    difficulty = parsed.get("difficulty", "medium")
    return {
        "difficulty": difficulty if difficulty in
        {"easy", "medium", "hard"} else "medium",
        "steps": [str(s)[:200] for s in steps[:6]],
        "url": parsed.get("url") or None,
    }


def write_letter(service_name: str, provider: LLMProvider,
                 cache: Cache) -> str | None:
    """Готовый текст письма в поддержку для отмены подписки."""
    key = f"letter:{service_name}"
    text = cache.get(key)
    if text is None:
        system = load_prompt("letter.md") or (
            "Напиши короткое деловое письмо в поддержку сервиса с просьбой "
            "отключить автопродление подписки. Русский язык, плейсхолдеры "
            "[имя] и [email]. Только текст письма."
        )
        raw = provider.complete(system, service_name)
        if not raw:
            return None
        text = raw.strip()
        cache.set(key, text)
        cache.save()
    return str(text)[:1200]


# ─────────────────────────── сборка ───────────────────────────


def enrich(result: dict, provider: LLMProvider | None = None,
           cache: Cache | None = None) -> dict:
    """Обогащает результат детектора. При любой ошибке возвращает как было."""
    provider = provider or get_provider()
    if provider is None:
        return result
    cache = cache or Cache()

    used = False
    try:
        # 1. Неопознанные сервисы: у них имя совпадает с описанием из выписки
        unknown = [s for s in result["subscriptions"]
                   if s["category"] == "other" and s["raw_names"]]
        if unknown:
            mapping = categorize([s["raw_names"][0] for s in unknown],
                                 provider, cache)
            for sub in unknown:
                info = mapping.get(sub["raw_names"][0])
                if info:
                    sub["name"] = info["name"]
                    sub["category"] = info["category"]
                    used = True

        used |= explain_overlaps(result, provider, cache)
        used |= build_plan(result, provider, cache)
    except Exception:
        # Слой не имеет права уронить ответ: детектор уже отработал
        used = False

    # Флаг честный: true только если модель реально что-то дала.
    # Фронт по нему решает, показывать ли блок советов.
    result["llm_used"] = used
    return result
