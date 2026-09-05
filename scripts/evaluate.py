"""Замер точности. Версия 2.

Считает две разные вещи отдельно:
  обнаружение — нашли ли подписку. Это работа детектора.
  именование  — точно ли назвали. Для сервисов вне словаря это работа LLM,
                и там возможна разница формы: «Obsidian» против
                «Obsidian Sync». Смешивать эти метрики нечестно.

Запуск из корня репозитория:
    python scripts/evaluate.py           только детектор, без модели
    python scripts/evaluate.py --llm     весь пайплайн вместе с LLM
    python scripts/evaluate.py data/demo_statement_dirty.csv
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from backend.ml.detector import detect          # noqa: E402
from backend.ml.parser import parse_statement   # noqa: E402


def same_service(a: str, b: str) -> bool:
    """Одна ли это подписка. Форма названия может отличаться."""
    x, y = a.strip().lower(), b.strip().lower()
    return x == y or x in y or y in x


def evaluate(statement: Path, truth_file: Path,
             use_llm: bool = False) -> dict:
    result = detect(parse_statement(statement.read_bytes()))

    llm_used = False
    if use_llm:
        from backend.ml import llm
        result = llm.enrich(result)
        llm_used = result.get("llm_used", False)

    found = {s["name"]: s for s in result["subscriptions"]}
    truth = {t["name"]: t for t in json.loads(
        truth_file.read_text(encoding="utf-8"))}

    pairs = {f: t for f in found for t in truth if same_service(f, t)}
    hits = set(pairs)
    false_positives = sorted(set(found) - hits)
    misses = sorted(set(truth) - set(pairs.values()))

    precision = len(hits) / len(found) if found else 0.0
    recall = len(hits) / len(truth) if truth else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if precision + recall else 0.0)

    exact = sum(1 for f, t in pairs.items() if f.strip() == t.strip())
    period_ok = sum(1 for f, t in pairs.items()
                    if found[f]["period"] == truth[t]["period"])
    amount_ok = sum(1 for f, t in pairs.items()
                    if abs(found[f]["amount"] - truth[t]["amount"]) < 1)

    return {
        "statement": statement.name,
        "transactions": result["transactions_parsed"],
        "found": len(found),
        "expected": len(truth),
        "hits": len(hits),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "name_accuracy": exact / len(hits) if hits else 0.0,
        "period_accuracy": period_ok / len(hits) if hits else 0.0,
        "amount_accuracy": amount_ok / len(hits) if hits else 0.0,
        "false_positives": false_positives,
        "misses": misses,
        "savings": result["summary"]["potential_savings_yearly"],
        "llm_used": llm_used,
    }


def _truth_for(statement: Path) -> Path:
    if statement.name.endswith("_statement.csv"):
        return statement.with_name(
            statement.name.replace("_statement.csv", "_truth.json"))
    return ROOT / "data" / "ground_truth.json"


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--llm"]
    use_llm = "--llm" in sys.argv[1:]

    statements = ([Path(a) for a in args] if args else
                  [ROOT / "data" / "demo_statement_clean.csv",
                   ROOT / "data" / "demo_statement_dirty.csv"]
                  + sorted((ROOT / "data" / "cases").glob("*_statement.csv")))

    print("режим: детектор + LLM" if use_llm else
          "режим: только детектор (для полного пайплайна — флаг --llm)")

    totals = {"tx": 0, "hits": 0, "found": 0, "expected": 0}
    for path in statements:
        if not path.exists():
            print(f"нет файла: {path}")
            continue
        m = evaluate(path, _truth_for(path), use_llm)
        totals["tx"] += m["transactions"]
        totals["hits"] += m["hits"]
        totals["found"] += m["found"]
        totals["expected"] += m["expected"]

        print(f"\n=== {m['statement']} ===")
        print(f"транзакций разобрано : {m['transactions']}")
        print(f"найдено / в эталоне  : {m['found']} / {m['expected']}")
        print(f"precision            : {m['precision']:.3f}")
        print(f"recall               : {m['recall']:.3f}")
        print(f"F1                   : {m['f1']:.3f}")
        print(f"название совпало     : {m['name_accuracy']:.1%}")
        print(f"период определён     : {m['period_accuracy']:.1%}")
        print(f"сумма определена     : {m['amount_accuracy']:.1%}")
        print(f"найдено к экономии   : {m['savings']:,.0f} \u20bd/год"
              .replace(",", "\u00a0"))
        if m["false_positives"]:
            print(f"ложные срабатывания  : {', '.join(m['false_positives'])}")
        if m["misses"]:
            print(f"пропущено            : {', '.join(m['misses'])}")

    p = totals["hits"] / totals["found"] if totals["found"] else 0.0
    r = totals["hits"] / totals["expected"] if totals["expected"] else 0.0
    print("\n=== ИТОГО ПО ВСЕМ ВЫПИСКАМ ===")
    print(f"транзакций           : {totals['tx']}")
    print(f"подписок в эталоне   : {totals['expected']}")
    print(f"precision            : {p:.3f}")
    print(f"recall               : {r:.3f}")


if __name__ == "__main__":
    main()
