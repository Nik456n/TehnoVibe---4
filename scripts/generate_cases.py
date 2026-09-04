"""Разворачивает сценарии из data/cases/*.json в выписки и эталоны.

Для каждого файла case_XX.json создаёт рядом:
    case_XX_statement.csv   выписка с бытовым шумом
    case_XX_truth.json      эталон для замера точности

Опциональные поля у подписки — ловушки, которые сценарий может объявить:
    "price_change": {"after": 3, "amount": 599}   с 4-го списания цена другая
    "skip": [2]                                    пропустить N-е списание
    "name_variant_after": 3                        сменить написание мерчанта

Запуск из корня репозитория:
    python scripts/generate_cases.py
"""

from __future__ import annotations

import json
import random
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.generate_statements import (  # noqa: E402
    SEED, category_ru, daterange_months, noise_rows, write_csv,
)

CASES_DIR = ROOT / "data" / "cases"


def subscription_rows(sub: dict, rnd: random.Random) -> list[dict]:
    first = date.fromisoformat(sub["start"])
    last = date.fromisoformat(sub.get("end", sub["start"]))
    raw_names = sub.get("raw_names") or [sub["name"].upper()]
    amount = float(sub["amount"])

    if sub["period"] == "yearly":
        dates = [first]
    else:
        dates = daterange_months(first, last, day_jitter=1, rnd=rnd)

    price_change = sub.get("price_change") or {}
    skip = set(sub.get("skip") or [])
    variant_after = sub.get("name_variant_after")

    rows = []
    for i, d in enumerate(dates):
        if i in skip:
            continue
        raw = raw_names[0]
        if variant_after is not None and i >= variant_after and len(raw_names) > 1:
            raw = raw_names[1]
        amt = amount
        if price_change and i >= price_change.get("after", 10 ** 6):
            amt = float(price_change["amount"])
        rows.append({
            "date": d,
            "category": category_ru(sub["category"]),
            "desc": raw,
            "amount": amt,
        })
    return rows


def build_case(path: Path) -> tuple[Path, Path, int]:
    case = json.loads(path.read_text(encoding="utf-8"))
    rnd = random.Random(SEED)
    start = date.fromisoformat(case["period_from"])
    end = date.fromisoformat(case["period_to"])

    rows: list[dict] = []
    for sub in case["subscriptions"]:
        rows += subscription_rows(sub, rnd)
    rows += noise_rows(start, end, rnd)

    stem = path.stem
    csv_path = path.with_name(f"{stem}_statement.csv")
    truth_path = path.with_name(f"{stem}_truth.json")

    write_csv(csv_path, rows)
    truth = [{
        "name": s["name"],
        "amount": (float(s["price_change"]["amount"])
                   if s.get("price_change") else float(s["amount"])),
        "period": s["period"],
        "category": s["category"],
        "flag": s.get("flag", "yellow"),
    } for s in case["subscriptions"]]
    truth_path.write_text(
        json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    return csv_path, truth_path, len(rows)


def main() -> None:
    files = sorted(p for p in CASES_DIR.glob("case_*.json")
                   if not p.stem.endswith("_truth"))
    if not files:
        print("в data/cases/ нет сценариев")
        return
    for path in files:
        csv_path, truth_path, n = build_case(path)
        case = json.loads(path.read_text(encoding="utf-8"))
        print(f"{path.stem}: {n} транзакций, "
              f"{len(case['subscriptions'])} подписок в эталоне")


if __name__ == "__main__":
    main()
