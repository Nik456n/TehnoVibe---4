"""Парсер банковских выписок.

Приводит CSV любого из поддерживаемых форматов к единой схеме Transaction.
Формат колонок определяется автоматически по заголовку, поэтому выписки
разных банков не требуют отдельного кода.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

# Как называются нужные колонки в выписках разных банков
COLUMN_ALIASES = {
    "date": ["дата операции", "дата", "date", "дата платежа"],
    "amount": ["сумма в валюте счёта", "сумма в валюте счета", "сумма",
               "amount", "сумма операции"],
    "description": ["описание", "получатель", "назначение платежа",
                    "description", "детали"],
    "category": ["категория", "category"],
}

DATE_FORMATS = ["%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y"]


def _find_column(header: list[str], key: str) -> int | None:
    """Ищет индекс колонки по списку возможных названий."""
    lowered = [h.strip().lower().lstrip("\ufeff") for h in header]
    for alias in COLUMN_ALIASES[key]:
        if alias in lowered:
            return lowered.index(alias)
    # частичное совпадение, если точного не нашлось
    for alias in COLUMN_ALIASES[key]:
        for i, h in enumerate(lowered):
            if alias in h:
                return i
    return None


def _parse_amount(raw: str) -> float | None:
    """'-399,00' -> 399.0. Возвращает None для поступлений и мусора."""
    s = raw.strip().replace("\xa0", "").replace(" ", "")
    if not s:
        return None
    negative = s.startswith("-")
    s = s.lstrip("+-").replace(",", ".")
    s = re.sub(r"[^\d.]", "", s)
    if not s:
        return None
    try:
        value = float(s)
    except ValueError:
        return None
    # Нас интересуют только списания. Если знака нет — считаем расходом.
    if not negative and value == 0:
        return None
    return abs(value)


def _parse_date(raw: str) -> date | None:
    s = raw.strip().split(" ")[0]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _sniff_delimiter(sample: str) -> str:
    """Определяет разделитель: ; или , или таб."""
    first_line = sample.split("\n")[0]
    counts = {d: first_line.count(d) for d in (";", ",", "\t")}
    return max(counts, key=counts.get) if max(counts.values()) else ";"


# Строка PDF-выписки: дата, потом текст, в конце сумма со знаком
PDF_LINE = re.compile(
    r"^(\d{2}[.\-/]\d{2}[.\-/]\d{2,4})\s+(.+?)\s+"
    r"([-\u2212+]?[\d\s\u00a0]{1,15}[.,]\d{2})\s*(?:₽|RUB|руб\.?)?$"
)


def parse_pdf(content: bytes) -> list[dict]:
    """Выписка в PDF. Сначала пробуем таблицу, потом разбор построчно.

    Таблица даёт чистые колонки, но в выписках некоторых банков рамок нет —
    тогда работает регулярка: дата в начале строки, сумма в конце.
    """
    try:
        import pdfplumber
    except ImportError:
        raise ValueError(
            "Для чтения PDF нужен пакет pdfplumber: pip install pdfplumber"
        )

    rows: list[list[str]] = []
    lines: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                for row in table:
                    cleaned = [(c or "").replace("\n", " ").strip()
                               for c in row]
                    if len(cleaned) >= 3:
                        rows.append(cleaned)
            lines += (page.extract_text() or "").split("\n")

    transactions: list[dict] = []

    # Вариант 1: в PDF есть размеченная таблица
    for i, row in enumerate(rows, start=1):
        d = _parse_date(row[0])
        amount = _parse_amount(row[-1])
        if d is None or amount is None:
            continue
        desc = " ".join(c for c in row[1:-1] if c).strip()
        if not desc:
            continue
        transactions.append({
            "id": f"tx_{len(transactions) + 1:05d}",
            "date": d, "amount": amount, "currency": "RUB",
            "raw_description": desc, "mcc": None,
        })
    if transactions:
        return transactions

    # Вариант 2: таблицы нет, разбираем текст построчно
    for line in lines:
        m = PDF_LINE.match(line.strip())
        if not m:
            continue
        d = _parse_date(m.group(1))
        amount = _parse_amount(m.group(3))
        if d is None or amount is None:
            continue
        transactions.append({
            "id": f"tx_{len(transactions) + 1:05d}",
            "date": d, "amount": amount, "currency": "RUB",
            "raw_description": m.group(2).strip(), "mcc": None,
        })
    return transactions


def parse_statement(content: bytes | str) -> list[dict]:
    """Разбирает выписку. Возвращает список словарей схемы Transaction."""
    if isinstance(content, bytes):
        if content[:5] == b"%PDF-":
            return parse_pdf(content)
        text = content.decode("utf-8-sig", errors="replace")
    else:
        text = content

    delimiter = _sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return []

    header = rows[0]
    idx_date = _find_column(header, "date")
    idx_amount = _find_column(header, "amount")
    idx_desc = _find_column(header, "description")
    idx_cat = _find_column(header, "category")

    missing = [
        name for name, idx in
        (("дата", idx_date), ("сумма", idx_amount), ("описание", idx_desc))
        if idx is None
    ]
    if missing:
        raise ValueError(
            "В выписке не найдены колонки: " + ", ".join(missing)
            + ". Ожидались: Дата операции, Описание, Сумма в валюте счёта."
        )

    transactions: list[dict] = []
    for i, row in enumerate(rows[1:], start=1):
        if max(idx_date, idx_amount, idx_desc) >= len(row):
            continue
        d = _parse_date(row[idx_date])
        amount = _parse_amount(row[idx_amount])
        desc = row[idx_desc].strip()
        if d is None or amount is None or not desc:
            continue
        transactions.append({
            "id": f"tx_{i:05d}",
            "date": d,
            "amount": amount,
            "currency": "RUB",
            "raw_description": desc,
            "mcc": row[idx_cat].strip() if idx_cat is not None
                   and idx_cat < len(row) else None,
        })

    return transactions
