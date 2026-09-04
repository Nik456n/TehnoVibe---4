"""
Генератор синтетических выписок для проекта "Сканер подписок".

Разворачивает contracts/mock_analyze_response.json обратно в транзакции
и добавляет бытовой шум. На выходе — три файла в data/:

    demo_statement_clean.csv   чистая выписка, показываем жюри
    demo_statement_dirty.csv   с ловушками, показываем что не рассыпаемся
    ground_truth.json          правильные ответы для замера precision/recall

Зависимости: только стандартная библиотека.

Запуск из корня репозитория:
    python scripts/generate_statements.py
"""

from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 42  # фиксируем, чтобы демо было одинаковым на всех машинах

ROOT = Path(__file__).resolve().parent.parent
MOCK = ROOT / "contracts" / "mock_analyze_response.json"
OUT_DIR = ROOT / "data"

# ─────────────────────── бытовой шум ───────────────────────
# (описание в выписке, категория, мин. сумма, макс. сумма, примерно раз в N дней)

NOISE = [
    ("MAGNIT MM ROSSIYANKA EKB", "Супермаркеты", 320, 2400, 3),
    ("PYATEROCHKA 12043 EKB", "Супермаркеты", 210, 1800, 4),
    ("LENTA-186 EKATERINBURG", "Супермаркеты", 890, 4200, 9),
    ("VKUSVILL 2281", "Супермаркеты", 260, 1500, 6),
    ("YANDEX.EDA MOSCOW RUS", "Рестораны", 480, 1900, 5),
    ("COFFEE LIKE EKB", "Кафе", 150, 420, 3),
    ("SURF COFFEE EKATERINBURG", "Кафе", 190, 560, 7),
    ("DODO PIZZA 1130", "Рестораны", 540, 1650, 11),
    ("YANDEX GO MOSCOW RUS", "Транспорт", 180, 890, 4),
    ("METRO EKB TURNIKET", "Транспорт", 33, 66, 5),
    ("GAZPROMNEFT AZS 152", "Автомобиль", 1400, 3600, 8),
    ("APTEKA APREL EKB", "Здоровье", 240, 1900, 16),
    ("OZON.RU MOSCOW", "Маркетплейсы", 390, 5400, 7),
    ("WILDBERRIES 4715", "Маркетплейсы", 450, 6200, 6),
    ("DNS SHOP EKATERINBURG", "Электроника", 1200, 14000, 34),
    ("SPORTMASTER EKB", "Одежда", 990, 7800, 29),
    ("ZOOMAGAZIN CHETYRE LAPY", "Питомцы", 380, 2100, 19),
    ("PEREVOD SBP", "Переводы", 500, 9000, 6),
]


def daterange_months(start: date, end: date, day_jitter: int = 0,
                     rnd: random.Random | None = None) -> list[date]:
    """Даты списаний раз в месяц от start до end включительно."""
    out: list[date] = []
    cur = start
    while cur <= end:
        d = cur
        if day_jitter and rnd:
            d = cur + timedelta(days=rnd.randint(-day_jitter, day_jitter))
        out.append(d)
        # плюс месяц с сохранением дня
        year, month = cur.year, cur.month + 1
        if month > 12:
            year, month = year + 1, 1
        day = min(cur.day, 28)
        cur = date(year, month, day)
    return out


def subscription_rows(sub: dict, rnd: random.Random, dirty: bool) -> list[dict]:
    """Транзакции одной подписки."""
    first = date.fromisoformat(sub["first_seen"])
    last = date.fromisoformat(sub["last_seen"])
    name = sub["name"]
    raw_names = sub["raw_names"] or [name.upper()]
    amount = sub["amount"]

    if sub["period"] == "yearly":
        dates = [first]
    else:
        dates = daterange_months(first, last, day_jitter=1 if dirty else 0, rnd=rnd)

    rows = []
    for i, d in enumerate(dates):
        raw = raw_names[0]
        amt = amount

        if dirty:
            # Ловушка 1: сервис сменил написание в выписке с середины периода
            if len(raw_names) > 1 and i >= len(dates) // 2:
                raw = raw_names[1]
            # Ловушка 2: Okko поднял цену
            if name == "Okko" and i >= 3:
                amt = 599.0
            # Ловушка 3: Wink пропустил месяц
            if name == "Wink" and i == 2:
                continue
            # Ловушка 4: мусор в описании
            if rnd.random() < 0.3:
                raw = f"{raw} {rnd.randint(100000, 999999)}"
            if rnd.random() < 0.2:
                raw = raw.lower()

        rows.append({
            "date": d,
            "category": category_ru(sub["category"]),
            "desc": raw,
            "amount": amt,
        })

    # Ловушка 5: двойное списание в один день
    if dirty and name == "Литрес" and rows:
        rows.append(dict(rows[-1]))

    return rows


def category_ru(cat: str) -> str:
    """Категория как её пишет банк — намеренно не совпадает с нашей схемой."""
    return {
        "video": "Развлечения", "music": "Развлечения", "cloud": "Прочее",
        "fitness": "Спорт", "education": "Образование", "books": "Развлечения",
        "games": "Развлечения", "delivery": "Супермаркеты",
        "transport": "Транспорт", "ai_tools": "Прочее",
        "bank_premium": "Прочее", "telecom": "Связь",
    }.get(cat, "Прочее")


def noise_rows(start: date, end: date, rnd: random.Random) -> list[dict]:
    """Обычные бытовые покупки — фон, среди которого прячутся подписки."""
    rows = []
    for desc, cat, lo, hi, every in NOISE:
        cur = start + timedelta(days=rnd.randint(0, every))
        while cur <= end:
            rows.append({
                "date": cur,
                "category": cat,
                "desc": desc,
                "amount": round(rnd.uniform(lo, hi), 2),
            })
            cur += timedelta(days=max(1, int(rnd.gauss(every, every * 0.35))))
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    """Формат колонок — как в выгрузке СберБанк Онлайн."""
    rows = sorted(rows, key=lambda r: r["date"])
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig, иначе Excel на Windows покажет кириллицу кракозябрами
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Дата операции", "Категория", "Описание",
                    "Сумма в валюте счёта"])
        for r in rows:
            w.writerow([
                r["date"].strftime("%d.%m.%Y"),
                r["category"],
                r["desc"],
                f"-{r['amount']:.2f}".replace(".", ","),
            ])


def main() -> None:
    rnd = random.Random(SEED)
    mock = json.loads(MOCK.read_text(encoding="utf-8"))
    subs = mock["subscriptions"]
    start = date.fromisoformat(mock["period_from"])
    end = date.fromisoformat(mock["period_to"])

    for dirty in (False, True):
        rnd.seed(SEED)
        rows: list[dict] = []
        for sub in subs:
            rows += subscription_rows(sub, rnd, dirty)
        rows += noise_rows(start, end, rnd)

        suffix = "dirty" if dirty else "clean"
        path = OUT_DIR / f"demo_statement_{suffix}.csv"
        write_csv(path, rows)
        sub_count = sum(1 for r in rows if r["category"] in
                        ("Развлечения", "Спорт", "Образование", "Связь")
                        or r["desc"].upper().startswith(
                            ("SBERPRIME", "APPLE", "OPENAI", "DUOLINGO")))
        print(f"{path.relative_to(ROOT)}: {len(rows)} транзакций "
              f"(подписочных ~{sub_count})")

    # Ground truth — сравниваем с ним выход детектора
    truth = [{
        "name": s["name"],
        "amount": s["amount"],
        "period": s["period"],
        "category": s["category"],
        "flag": s["flag"],
    } for s in subs]
    gt = OUT_DIR / "ground_truth.json"
    gt.write_text(json.dumps(truth, ensure_ascii=False, indent=2),
                  encoding="utf-8")
    print(f"{gt.relative_to(ROOT)}: {len(truth)} подписок — эталон для метрик")


if __name__ == "__main__":
    main()
