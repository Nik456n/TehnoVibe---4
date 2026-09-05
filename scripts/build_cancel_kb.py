"""Превращает базу отмены из таблицы в JSON для бэкенда.

Аналитик заполняет data/cancel_kb.csv в Excel или Google Таблицах —
это быстрее и нагляднее, чем писать JSON руками. Бэкенд читает
data/cancel_kb.json. Этот скрипт делает второе из первого.

Заодно проверяет, что названия сервисов совпадают со словарём
мерчантов: если не совпадают, инструкция не привяжется к найденной
подписке и пользователь увидит заглушку.

Запуск из корня репозитория:
    python scripts/build_cancel_kb.py
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_FILE = ROOT / "data" / "cancel_kb.csv"
JSON_FILE = ROOT / "data" / "cancel_kb.json"
MERCHANTS = ROOT / "data" / "merchants.csv"

DIFFICULTY_FIX = {
    "mediun": "medium", "medium": "medium", "средне": "medium",
    "easy": "easy", "просто": "easy", "легко": "easy",
    "hard": "hard", "сложно": "hard",
}


def clean(value: str | None) -> str:
    return (value or "").strip().strip('"').strip()


def build() -> tuple[dict, list[str]]:
    entries: dict[str, dict] = {}
    with CSV_FILE.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = clean(row.get("service_name"))
            if not name or name == "service_name":
                continue

            steps = []
            for i in range(1, 10):
                step = clean(row.get(f"step_{i}"))
                if step:
                    steps.append(step)
            if not steps:
                continue

            difficulty = DIFFICULTY_FIX.get(
                clean(row.get("difficulty")).lower(), "medium")

            url = clean(row.get("url"))
            if url in {"", "-", "—"}:
                url = None
            elif not url.startswith("http"):
                url = "https://" + url.lstrip("/")

            entries[name] = {
                "difficulty": difficulty,
                "steps": steps[:6],
                "url": url,
                "letter_template": clean(row.get("letter_template")) or None,
                "source_checked": clean(row.get("source_checked")) or None,
                "notes": clean(row.get("notes")) or None,
            }

    # Сверка со словарём: инструкция привязывается по точному имени
    known: set[str] = set()
    if MERCHANTS.exists():
        with MERCHANTS.open(encoding="utf-8-sig") as f:
            known = {clean(r.get("canonical_name")) for r in csv.DictReader(f)}
    orphans = sorted(n for n in entries if known and n not in known)
    return entries, orphans


def main() -> None:
    if not CSV_FILE.exists():
        sys.exit(f"нет файла: {CSV_FILE}")

    entries, orphans = build()
    JSON_FILE.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

    hard = [n for n, e in entries.items() if e["difficulty"] == "hard"]
    no_url = [n for n, e in entries.items() if not e["url"]]

    print(f"{JSON_FILE.relative_to(ROOT)}: {len(entries)} сервисов")
    if no_url:
        print(f"без прямой ссылки ({len(no_url)}): {', '.join(no_url[:6])}")
    if hard:
        print(f"отменяются только через поддержку: {', '.join(hard)}")
    if orphans:
        print(f"\nнет в словаре мерчантов ({len(orphans)}) — "
              f"инструкция не привяжется к подписке:")
        for n in orphans:
            print(f"   {n}")


if __name__ == "__main__":
    main()
