"""Детектор подписок.

Находит повторяющиеся списания детерминированным алгоритмом, без LLM:
нормализация мерчанта -> словарь -> группировка по сумме ->
медианный интервал между списаниями -> пересечения по категориям.

Результат воспроизводим: один и тот же файл всегда даёт один ответ.
LLM подключается отдельно и только для того, чего здесь нет —
категоризации неизвестных сервисов и текстовых объяснений.
"""

from __future__ import annotations

import csv
import re
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
MERCHANTS_CSV = ROOT / "data" / "merchants.csv"

# Допуск на сумму внутри одной подписки известного сервиса
AMOUNT_TOLERANCE = 0.07
# Для неизвестного мерчанта — почти точное совпадение: настоящая подписка
# списывает одну и ту же сумму, а покупки в магазине всегда разные
AMOUNT_TOLERANCE_UNKNOWN = 0.0005
# Максимальный разброс интервалов (дней). У подписки он около 1-2,
# у регулярных бытовых трат — десятки
MAX_JITTER_DAYS = 4.0
# Сколько списаний нужно, чтобы поверить неизвестному мерчанту
MIN_OCCURRENCES_UNKNOWN = 3
# Ниже этого порога подписку не показываем
MIN_CONFIDENCE = 0.6

# Категории, где одна подписка — это норма, а не повод оптимизировать
ESSENTIAL = {"bank_premium", "telecom", "cloud"}

# Бандлы: включают несколько сервисов сразу, поэтому при пересечении
# выгоднее оставить именно их, даже если стоят дороже
BUNDLES = {
    "Яндекс Плюс", "СберПрайм", "МТС Premium", "T-Premium",
    "Тинькофф Pro", "Alfa Only", "Ozon Premium",
}

# Мусор, который встречается в описаниях и мешает сопоставлению
NOISE_TOKENS = {
    "MOSCOW", "MSK", "EKB", "EKATERINBURG", "SPB", "PETERSBURG", "KAZAN",
    "NOVOSIBIRSK", "RUS", "RU", "NLD", "USA", "IRL", "LUX", "CYP",
    "OOO", "ZAO", "AO", "IP", "LLC", "INC", "LTD", "PAO",
    "PODPISKA", "SUBSCR", "SUBSCRIPTION", "PAYMENT", "PAY", "BILL",
}

_PERIODS = [
    ("weekly", 5, 9),
    ("monthly", 25, 36),
    ("quarterly", 84, 100),
    ("yearly", 350, 380),
]


def normalize_merchant(raw: str) -> str:
    """'YANDEX*KINOPOISK MOSCOW RUS 123456' -> 'YANDEX KINOPOISK'."""
    s = raw.upper()
    s = re.sub(r"[*/\\|,.\-_#№]+", " ", s)
    s = re.sub(r"\b\d{4,}\b", " ", s)          # коды терминалов
    tokens = [t for t in s.split() if t and t not in NOISE_TOKENS]
    return " ".join(tokens).strip()


def _pattern_regex(pattern: str) -> re.Pattern:
    """Паттерн -> регулярка с границами слов.

    Границы обязательны: без них 'VK' совпадает внутри 'VKUSVILL',
    а 'IVI' внутри 'DIVIDEND'. Пунктуация в паттерне и в описании
    может отличаться, поэтому между словами допускаем любой разделитель.
    """
    tokens = [t for t in re.split(r"[^A-Z0-9А-Я]+", pattern.upper()) if t]
    if not tokens:
        return re.compile(r"(?!x)x")
    body = r"[^A-Z0-9А-Я]*".join(re.escape(t) for t in tokens)
    return re.compile(r"(?<![A-Z0-9А-Я])" + body + r"(?![A-Z0-9А-Я])")


def load_merchants(path: Path = MERCHANTS_CSV) -> list[tuple[str, dict]]:
    """Словарь мерчантов, отсортированный по длине паттерна.

    Длинные первыми — иначе 'SBER' перехватит 'SBERPRIME',
    а 'MTS' перехватит 'MTS PREMIUM'.
    """
    if not path.exists():
        return []
    entries: dict[str, dict] = {}
    with path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            pattern = (row.get("pattern") or "").strip().upper()
            if not pattern:
                continue
            entries[pattern] = {
                "name": (row.get("canonical_name") or "").strip(),
                "category": (row.get("category") or "other").strip(),
                "period": (row.get("period") or "unknown").strip(),
                "regex": _pattern_regex(pattern),
            }
    return sorted(entries.items(), key=lambda kv: -len(kv[0]))


def match_merchant(raw: str, merchants: list[tuple[str, dict]]) -> dict | None:
    """Ищет сервис по описанию транзакции. Длинные паттерны имеют приоритет."""
    haystack = raw.upper()
    for _pattern, info in merchants:
        if info["regex"].search(haystack):
            return info
    return None


def _cluster_by_amount(txs: list[dict], tolerance: float) -> list[list[dict]]:
    """Разбивает транзакции одного мерчанта на группы близких сумм."""
    clusters: list[list[dict]] = []
    for tx in sorted(txs, key=lambda t: t["amount"]):
        placed = False
        for cluster in clusters:
            base = statistics.median(t["amount"] for t in cluster)
            if abs(tx["amount"] - base) <= base * tolerance:
                cluster.append(tx)
                placed = True
                break
        if not placed:
            clusters.append([tx])
    return clusters


def _dedupe_same_day(txs: list[dict]) -> list[dict]:
    """Схлопывает двойные списания в один день на одну сумму."""
    seen: set[tuple] = set()
    out = []
    for tx in sorted(txs, key=lambda t: t["date"]):
        key = (tx["date"], round(tx["amount"], 2))
        if key in seen:
            continue
        seen.add(key)
        out.append(tx)
    return out


def _merge_price_change(clusters: list[list[dict]]) -> list[list[dict]]:
    """Объединяет группы, если сервис просто поднял цену.

    Признак: периоды не пересекаются и идут друг за другом.
    """
    if len(clusters) < 2:
        return clusters
    ordered = sorted(clusters, key=lambda c: min(t["date"] for t in c))
    merged = [ordered[0]]
    for cluster in ordered[1:]:
        prev_end = max(t["date"] for t in merged[-1])
        cur_start = min(t["date"] for t in cluster)
        gap = (cur_start - prev_end).days
        if 0 < gap <= 45 and len(merged[-1]) >= 2 and len(cluster) >= 2:
            merged[-1] = merged[-1] + cluster
        else:
            merged.append(cluster)
    return merged


def _detect_period(dates: list[date]) -> tuple[str, float]:
    """По датам списаний определяет период и разброс интервалов."""
    if len(dates) < 2:
        return "unknown", 0.0
    ds = sorted(dates)
    gaps = [(ds[i + 1] - ds[i]).days for i in range(len(ds) - 1)]
    med = statistics.median(gaps)
    # Медианное отклонение вместо стандартного: если сервис пропустил
    # один месяц, обычное СКО подскочит и мы потеряем настоящую подписку,
    # а медиана такой выброс игнорирует
    jitter = statistics.median([abs(g - med) for g in gaps]) if len(gaps) > 1 else 0.0
    for name, lo, hi in _PERIODS:
        if lo <= med <= hi:
            return name, jitter
    # пропущенный месяц даёт интервал около 60 дней — всё ещё monthly
    if 50 <= med <= 70:
        return "monthly", jitter
    return "unknown", jitter


def _confidence(occurrences: int, period: str, jitter: float,
                from_dict: bool) -> float:
    if period == "unknown" and not from_dict:
        return 0.0
    base = {1: 0.55, 2: 0.72, 3: 0.85}.get(occurrences, 0.93)
    if occurrences == 1:
        base = 0.65 if from_dict else 0.0
    base -= min(jitter / 60.0, 0.2)
    return round(max(0.0, min(base, 0.99)), 2)


def _yearly(amount: float, period: str) -> float:
    factor = {"weekly": 52, "monthly": 12, "quarterly": 4, "yearly": 1}
    return round(amount * factor.get(period, 12), 2)


def _initial_reason(occurrences: int, median_amount: float,
                    current: float) -> str:
    """Заметки, которые видны до анализа пересечений."""
    if occurrences == 1:
        return ("Одно списание за период — период взят из справочника, "
                "уточнится после следующего платежа")
    if abs(current - median_amount) > 1:
        return f"Цена выросла с {median_amount:.0f} до {current:.0f} ₽"
    return ""


def detect(transactions: list[dict],
           merchants: list[tuple[str, dict]] | None = None) -> dict:
    """Главная функция. Транзакции на вход, AnalyzeResponse на выход."""
    if merchants is None:
        merchants = load_merchants()

    # 1. Сопоставление с словарём. Неопознанные группируем по
    #    нормализованному описанию — их потом доклассифицирует LLM.
    groups: dict[str, dict] = defaultdict(
        lambda: {"txs": [], "info": None, "from_dict": False}
    )
    for tx in transactions:
        info = match_merchant(tx["raw_description"], merchants)
        if info and info["name"]:
            key = info["name"]
            groups[key]["info"] = info
            groups[key]["from_dict"] = True
        else:
            key = normalize_merchant(tx["raw_description"])
            if not key:
                continue
        groups[key]["txs"].append(tx)

    # 2. Периодичность внутри каждой группы
    all_dates = [t["date"] for t in transactions]
    window_days = ((max(all_dates) - min(all_dates)).days if all_dates else 0)
    subscriptions: list[dict] = []
    counter = 0
    for name, data in groups.items():
        from_dict = data["from_dict"]
        tolerance = AMOUNT_TOLERANCE if from_dict else AMOUNT_TOLERANCE_UNKNOWN
        txs = _dedupe_same_day(data["txs"])
        clusters = _merge_price_change(_cluster_by_amount(txs, tolerance))

        for cluster in clusters:
            cluster = sorted(cluster, key=lambda t: t["date"])
            dates = [t["date"] for t in cluster]
            period, jitter = _detect_period(dates)

            # Неизвестному мерчанту верим только при устойчивом ритме:
            # три и более одинаковых списания с ровными интервалами.
            # Это и есть граница между подпиской и привычкой.
            if not from_dict:
                if len(cluster) < MIN_OCCURRENCES_UNKNOWN:
                    continue
                if jitter > MAX_JITTER_DAYS or period == "unknown":
                    continue

            if period == "unknown" and data["from_dict"]:
                # Одно списание — периодичность из дат не выводится.
                # Но если выписка покрывает больше двух месяцев, а платёж
                # был один, ежемесячной подписка быть не может: их было бы
                # шесть. Значит это годовая, и умножать сумму на 12 нельзя.
                if len(cluster) == 1 and window_days > 70:
                    period = "yearly"
                else:
                    period = data["info"].get("period", "unknown")
                    if period == "unknown":
                        period = "monthly"

            if from_dict and len(cluster) >= 2 and jitter > MAX_JITTER_DAYS * 2:
                continue

            conf = _confidence(len(cluster), period, jitter, from_dict)
            if conf < MIN_CONFIDENCE:
                continue

            counter += 1
            amount = round(statistics.median(t["amount"] for t in cluster), 2)
            current = round(cluster[-1]["amount"], 2)
            info = data["info"] or {}
            subscriptions.append({
                "id": f"sub_{counter:03d}",
                "name": info.get("name") or name.title(),
                "raw_names": sorted({t["raw_description"] for t in cluster})[:4],
                "category": info.get("category", "other"),
                "amount": current,
                "period": period,
                "confidence": conf,
                "first_seen": dates[0],
                "last_seen": dates[-1],
                "occurrences": len(cluster),
                "total_paid": round(sum(t["amount"] for t in cluster), 2),
                "yearly_cost": _yearly(current, period),
                "flag": "yellow",
                "overlap_group": None,
                "reason": _initial_reason(len(cluster), amount, current),
            })

    subscriptions.sort(key=lambda s: -s["yearly_cost"])

    # 3. Пересечения: больше одного сервиса в категории
    by_category: dict[str, list[dict]] = defaultdict(list)
    for s in subscriptions:
        by_category[s["category"]].append(s)

    overlaps = []
    for category, subs in by_category.items():
        if len(subs) < 2:
            continue
        group_id = f"{category}_overlap"
        bundle = next((s for s in subs if s["name"] in BUNDLES), None)
        if bundle:
            keeper = bundle
        else:
            # Сначала отбираем активные: у кого списаний не меньше,
            # чем у самого регулярного в группе. Дешёвый, но заброшенный
            # сервис рекомендовать к сохранению неправильно.
            top = max(s["occurrences"] for s in subs)
            active = [s for s in subs if s["occurrences"] >= top] or subs
            keeper = min(active, key=lambda s: s["yearly_cost"])
        savings = round(
            sum(s["yearly_cost"] for s in subs if s["id"] != keeper["id"]), 2
        )
        for s in subs:
            s["overlap_group"] = group_id
            if s["id"] == keeper["id"]:
                s["flag"] = "yellow"
                s["reason"] = (
                    f"Из {len(subs)} сервисов категории выгоднее оставить этот"
                )
            else:
                s["flag"] = "red"
                s["reason"] = f"Дублирует {keeper['name']}"
        overlaps.append({
            "id": group_id,
            "category": category,
            "subscription_ids": [s["id"] for s in subs],
            "keep_suggestion": keeper["id"],
            "savings_yearly": savings,
            "explanation": (
                f"{len(subs)} сервиса в одной категории закрывают одну "
                f"потребность. Оставив только «{keeper['name']}», "
                f"вы освободите {savings:,.0f} ₽ в год.".replace(",", " ")
            ),
        })

    # 4. Флаги для тех, кто вне пересечений
    for s in subscriptions:
        if s["overlap_group"]:
            continue
        if s["category"] in ESSENTIAL:
            s["flag"] = "green"
            s["reason"] = s["reason"] or "Единственный сервис в категории"
        else:
            s["flag"] = "yellow"
            s["reason"] = s["reason"] or "Проверьте, пользуетесь ли регулярно"

    # 5. Итоги
    per_month = {"weekly": 52 / 12, "monthly": 1, "quarterly": 1 / 3,
                 "yearly": 1 / 12}
    monthly = round(
        sum(s["amount"] * per_month.get(s["period"], 1) for s in subscriptions), 2
    )
    yearly = round(sum(s["yearly_cost"] for s in subscriptions), 2)
    savings_total = round(sum(o["savings_yearly"] for o in overlaps), 2)

    # 6. План оптимизации
    plan = []
    reds = sorted([s for s in subscriptions if s["flag"] == "red"],
                  key=lambda s: -s["yearly_cost"])
    for i, s in enumerate(reds, start=1):
        plan.append({
            "subscription_id": s["id"],
            "action": "cancel",
            "priority": i,
            "reason": s["reason"],
            "savings_yearly": s["yearly_cost"],
        })
    priority = len(plan) + 1
    for s in subscriptions:
        if s["flag"] == "yellow" and s["yearly_cost"] > 20000:
            plan.append({
                "subscription_id": s["id"],
                "action": "review",
                "priority": priority,
                "reason": "Самая дорогая подписка — сверьте с реальным использованием",
                "savings_yearly": 0.0,
            })
            priority += 1
            break

    dates_all = [t["date"] for t in transactions]
    headline = (
        f"Найдено {len(subscriptions)} подписок на {monthly:,.0f} ₽ в месяц."
        .replace(",", "\u00a0")
    )
    if savings_total:
        headline += (
            f" {len(reds)} из них дублируют друг друга — отключив лишнее, "
            + f"вы вернёте {savings_total:,.0f} ₽ за год.".replace(",", " ")
        )

    return {
        "request_id": "local",
        "period_from": min(dates_all) if dates_all else date.today(),
        "period_to": max(dates_all) if dates_all else date.today(),
        "transactions_parsed": len(transactions),
        "subscriptions": subscriptions,
        "overlaps": overlaps,
        "summary": {
            "subscriptions_count": len(subscriptions),
            "monthly_total": monthly,
            "yearly_total": yearly,
            "red_count": sum(1 for s in subscriptions if s["flag"] == "red"),
            "yellow_count": sum(1 for s in subscriptions if s["flag"] == "yellow"),
            "green_count": sum(1 for s in subscriptions if s["flag"] == "green"),
            "potential_savings_yearly": savings_total,
        },
        "plan": plan,
        "headline": headline,
        "llm_used": False,
    }
