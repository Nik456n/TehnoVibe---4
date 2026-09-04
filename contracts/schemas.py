"""
Контракты данных проекта "Сканер подписок".

ЕДИНСТВЕННЫЙ ИСТОЧНИК ПРАВДЫ. Меняется только через тимлида,
отдельным коммитом, с сообщением в общий чат.

Использование в FastAPI:
    from contracts.schemas import AnalyzeResponse

    @app.post("/analyze", response_model=AnalyzeResponse)
    def analyze(...): ...
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ─────────────────────────── справочники ───────────────────────────


class Category(str, Enum):
    """Функциональная категория сервиса. Используется для поиска пересечений."""

    VIDEO = "video"
    MUSIC = "music"
    CLOUD = "cloud"
    FITNESS = "fitness"
    EDUCATION = "education"
    BOOKS = "books"
    GAMES = "games"
    DELIVERY = "delivery"
    TRANSPORT = "transport"
    AI_TOOLS = "ai_tools"
    BANK_PREMIUM = "bank_premium"
    TELECOM = "telecom"
    OTHER = "other"


class Period(str, Enum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    UNKNOWN = "unknown"


class Flag(str, Enum):
    """Приоритет оптимизации.

    GREEN  — полезная подписка, трогать не предлагаем (СберПрайм, связь)
    YELLOW — средняя значимость, разовые развлекательные сервисы
    RED    — дублирует функции другой подписки, режем в первую очередь
    """

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


# ─────────────────────────── вход ───────────────────────────


class Transaction(BaseModel):
    """Одна операция из выписки после приведения к единому формату.

    Парсер CSV/XLSX/PDF обязан отдавать именно это, независимо от банка.
    """

    id: str = Field(description="Внутренний id, например 'tx_0042'")
    date: date
    amount: float = Field(gt=0, description="Сумма списания, всегда положительная")
    currency: str = "RUB"
    raw_description: str = Field(
        description="Описание как в выписке: 'YANDEX*KINOPOISK MOSCOW RUS'"
    )
    mcc: str | None = Field(default=None, description="MCC-код, если банк его отдал")


# ─────────────────────────── выход ───────────────────────────


class Subscription(BaseModel):
    """Найденная повторяющаяся подписка."""

    id: str = Field(description="'sub_001'")
    name: str = Field(description="Каноничное имя: 'Кинопоиск'")
    raw_names: list[str] = Field(
        default_factory=list,
        description="Все варианты написания, которые схлопнулись в эту подписку",
    )
    category: Category
    amount: float = Field(gt=0, description="Сумма одного списания")
    period: Period
    confidence: float = Field(
        ge=0, le=1, description="Уверенность детектора. Ниже 0.6 — не показываем"
    )
    first_seen: date
    last_seen: date
    occurrences: int = Field(ge=1, description="Сколько списаний нашли в выписке")
    total_paid: float = Field(ge=0, description="Сколько уже заплачено за период выписки")
    yearly_cost: float = Field(ge=0, description="Прогноз затрат за 12 месяцев")
    flag: Flag
    overlap_group: str | None = Field(
        default=None, description="id группы пересечения, если подписка в неё входит"
    )
    reason: str = Field(
        default="", description="Человеческое объяснение флага, одно предложение"
    )


class OverlapGroup(BaseModel):
    """Несколько подписок, закрывающих одну и ту же потребность."""

    id: str = Field(description="'video_streaming'")
    category: Category
    subscription_ids: list[str]
    keep_suggestion: str | None = Field(
        default=None, description="id подписки, которую советуем оставить"
    )
    savings_yearly: float = Field(
        ge=0, description="Сколько освободится, если оставить одну из группы"
    )
    explanation: str


class PlanItem(BaseModel):
    subscription_id: str
    action: Literal["cancel", "downgrade", "keep", "review"]
    priority: int = Field(ge=1, description="1 — делать первым")
    reason: str
    savings_yearly: float = Field(ge=0)


class Summary(BaseModel):
    subscriptions_count: int = Field(ge=0)
    monthly_total: float = Field(ge=0, description="Совокупный расход в месяц")
    yearly_total: float = Field(ge=0)
    red_count: int = Field(ge=0)
    yellow_count: int = Field(ge=0)
    green_count: int = Field(ge=0)
    potential_savings_yearly: float = Field(
        ge=0, description="ГЛАВНАЯ ЦИФРА ПИТЧА — крупно на первом экране"
    )


class AnalyzeResponse(BaseModel):
    """Ответ POST /analyze — то, что рисует Mini App."""

    request_id: str
    period_from: date
    period_to: date
    transactions_parsed: int = Field(ge=0)
    subscriptions: list[Subscription]
    overlaps: list[OverlapGroup] = Field(default_factory=list)
    summary: Summary
    plan: list[PlanItem] = Field(default_factory=list)
    headline: str = Field(
        default="",
        description="Одна фраза от LLM для верхнего блока экрана",
    )
    llm_used: bool = Field(
        default=True,
        description="False — LLM была недоступна, показан только детерминированный результат. "
        "Фронт в этом случае скрывает блок советов, но список подписок рисует.",
    )


# ─────────────────────── отмена подписки ───────────────────────


class CancelInstruction(BaseModel):
    """Ответ GET /cancel/{subscription_id}."""

    subscription_id: str
    service_name: str
    difficulty: Literal["easy", "medium", "hard"]
    steps: list[str] = Field(description="Пронумерованные шаги, 3-6 штук")
    url: str | None = Field(default=None, description="Прямая ссылка на страницу отмены")
    letter_template: str | None = Field(
        default=None, description="Готовое письмо, если отмена только через поддержку"
    )
    source: Literal["knowledge_base", "llm_generated"] = Field(
        description="llm_generated — показываем пометку 'проверьте на сайте сервиса'"
    )
    savings_yearly: float = Field(ge=0)
