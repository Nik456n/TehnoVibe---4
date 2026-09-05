"""Собирает PDF-выписку в стиле Сбербанк Онлайн из демо-CSV.

Нужна, чтобы проверить путь «пользователь прислал PDF в бота».
Раскладка колонок повторяет выгрузку СБОЛ, поэтому подходит
для отладки PDF-парсера.

Запуск из корня репозитория:
    python scripts/make_pdf_statement.py
    python scripts/make_pdf_statement.py data/cases/case_04_heavy_statement.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

ROOT = Path(__file__).resolve().parent.parent

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]

GREEN = colors.HexColor("#21A038")


def _register_fonts() -> tuple[str, str]:
    """Кириллица во встроенных шрифтах reportlab не работает."""
    regular = next((p for p in FONT_CANDIDATES if Path(p).exists()), None)
    bold = next((p for p in FONT_BOLD_CANDIDATES if Path(p).exists()), None)
    if not regular:
        raise SystemExit("Не найден шрифт с кириллицей (DejaVuSans или Arial)")
    pdfmetrics.registerFont(TTFont("Body", regular))
    pdfmetrics.registerFont(TTFont("BodyBold", bold or regular))
    return "Body", "BodyBold"


def build(csv_path: Path, pdf_path: Path) -> int:
    font, font_bold = _register_fonts()
    styles = getSampleStyleSheet()

    cell = ParagraphStyle("cell", parent=styles["Normal"],
                          fontName=font, fontSize=7.5, leading=9.5)
    cell_right = ParagraphStyle("cell_r", parent=cell, alignment=TA_RIGHT)
    head = ParagraphStyle("head", parent=cell, fontName=font_bold,
                          fontSize=7.5, textColor=colors.white)
    title = ParagraphStyle("title", parent=styles["Title"],
                           fontName=font_bold, fontSize=15,
                           textColor=GREEN, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontName=font,
                         fontSize=9, textColor=colors.HexColor("#555555"))

    with csv_path.open(encoding="utf-8-sig") as f:
        rows = list(csv.reader(f, delimiter=";"))
    header, body = rows[0], rows[1:]

    period = f"{body[0][0]} — {body[-1][0]}" if body else ""
    total = sum(abs(float(r[3].replace(",", ".").replace(" ", "")))
                for r in body if len(r) > 3)

    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=A4,
        leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
        title="Выписка по счёту дебетовой карты",
    )

    story = [
        Paragraph("СберБанк Онлайн", title),
        Paragraph("Выписка по счёту дебетовой карты · MIR ••••4417", sub),
        Paragraph(f"Период: {period} · Операций: {len(body)} · "
                  f"Сумма расходов: {total:,.2f} ₽".replace(",", " "), sub),
        Spacer(1, 7 * mm),
    ]

    data = [[Paragraph(h, head) for h in header]]
    for r in body:
        data.append([
            Paragraph(r[0], cell),
            Paragraph(r[1], cell),
            Paragraph(r[2], cell),
            Paragraph(r[3], cell_right),
        ])

    table = Table(data, colWidths=[22 * mm, 30 * mm, 96 * mm, 30 * mm],
                  repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GREEN),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDDDDD")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#F5F7F6")]),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(table)

    doc.build(story)
    return len(body)


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        ROOT / "data" / "demo_statement_clean.csv"
    if not src.exists():
        raise SystemExit(f"нет файла: {src}")
    out = src.with_suffix(".pdf")
    n = build(src, out)
    size = out.stat().st_size / 1024
    print(f"{out}: {n} операций, {size:.0f} КБ")


if __name__ == "__main__":
    main()
