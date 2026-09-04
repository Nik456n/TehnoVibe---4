"""Замер точности детектора.

Сравнивает результат с эталоном из data/ground_truth.json и печатает
precision / recall / F1. Эту цифру несём на защиту.

Запуск из корня репозитория:
    python scripts/evaluate.py
    python scripts/evaluate.py data/demo_statement_dirty.csv
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.ml.detector import detect          # noqa: E402
from backend.ml.parser import parse_statement   # noqa: E402


def evaluate(statement: Path, truth_file: Path) -> dict:
    transactions = parse_statement(statement.read_bytes())
    result = detect(transactions)

    found = {s["name"]: s for s in result["subscriptions"]}
    truth = {t["name"]: t for t in json.loads(
        truth_file.read_text(encoding="utf-8"))}

    hits = set(found) & set(truth)
    false_positives = sorted(set(found) - set(truth))
    misses = sorted(set(truth) - set(found))

    precision = len(hits) / len(found) if found else 0.0
    recall = len(hits) / len(truth) if truth else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if precision + recall else 0.0)

    # Насколько верно определены период и сумма у найденных
    period_ok = sum(1 for n in hits if found[n]["period"] == truth[n]["period"])
    amount_ok = sum(1 for n in hits
                    if abs(found[n]["amount"] - truth[n]["amount"]) < 1)

    return {
        "statement": statement.name,
        "transactions": result["transactions_parsed"],
        "found": len(found),
        "expected": len(truth),
        "hits": len(hits),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "period_accuracy": period_ok / len(hits) if hits else 0.0,
        "amount_accuracy": amount_ok / len(hits) if hits else 0.0,
        "false_positives": false_positives,
        "misses": misses,
        "savings": result["summary"]["potential_savings_yearly"],
    }


def _truth_for(statement: Path) -> Path:
    """Каждому кейсу — свой эталон, демо-выпискам — общий."""
    if statement.name.endswith("_statement.csv"):
        return statement.with_name(
            statement.name.replace("_statement.csv", "_truth.json"))
    return ROOT / "data" / "ground_truth.json"


def main() -> None:
    args = sys.argv[1:]
    statements = ([Path(a) for a in args] if args else
                  [ROOT / "data" / "demo_statement_clean.csv",
                   ROOT / "data" / "demo_statement_dirty.csv"]
                  + sorted((ROOT / "data" / "cases").glob("*_statement.csv")))

    for path in statements:
        if not path.exists():
            print(f"нет файла: {path}")
            continue
        m = evaluate(path, _truth_for(path))
        print(f"\n=== {m['statement']} ===")
        print(f"транзакций разобрано : {m['transactions']}")
        print(f"найдено / в эталоне  : {m['found']} / {m['expected']}")
        print(f"precision            : {m['precision']:.3f}")
        print(f"recall               : {m['recall']:.3f}")
        print(f"F1                   : {m['f1']:.3f}")
        print(f"период определён     : {m['period_accuracy']:.1%}")
        print(f"сумма определена     : {m['amount_accuracy']:.1%}")
        print(f"найдено к экономии   : {m['savings']:,.0f} ₽/год"
              .replace(",", " "))
        if m["false_positives"]:
            print(f"ложные срабатывания  : {', '.join(m['false_positives'])}")
        if m["misses"]:
            print(f"пропущено            : {', '.join(m['misses'])}")


if __name__ == "__main__":
    main()
