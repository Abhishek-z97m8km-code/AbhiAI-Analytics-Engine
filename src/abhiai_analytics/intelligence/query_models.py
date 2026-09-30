"""Typed Phase 5B analytics-question contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from .models import Evidence, Limitation


class AnalyticsOperation(str, Enum):
    KPI_LOOKUP = "kpi_lookup"
    RANKING = "ranking"
    COMPARISON = "comparison"
    TREND = "trend"
    CONTRIBUTION = "contribution"
    PROFITABILITY = "profitability"
    ANOMALY = "anomaly"
    DIAGNOSTIC = "diagnostic"
    SUMMARY = "summary"


class QueryStatus(str, Enum):
    ANSWERED = "answered"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNSUPPORTED = "unsupported"
    INSUFFICIENT_DATA = "insufficient_data"
    INTERPRETATION_UNAVAILABLE = "interpretation_unavailable"
    ERROR = "error"


@dataclass(frozen=True)
class AnalyticsIntent:
    operation: AnalyticsOperation
    metric: str | None = None
    secondary_metric: str | None = None
    dimension: str | None = None
    time_granularity: str = "monthly"
    sort_direction: str = "descending"
    limit: int = 1
    group_value: str | None = None
    confidence: float = 1.0
    interpretation_method: str = "deterministic"


@dataclass(frozen=True)
class QueryInterpretation:
    status: QueryStatus
    intent: AnalyticsIntent | None = None
    ambiguities: tuple[str, ...] = ()
    suggested_clarification: str | None = None
    limitation: Limitation | None = None


@dataclass(frozen=True)
class AnalyticsFact:
    label: str
    metric: str | None
    value: float | str | None
    dimension: str | None = None
    dimension_value: str | None = None
    percentage: float | None = None
    period: str | None = None


@dataclass(frozen=True)
class BusinessQuestionResult:
    original_question: str
    status: QueryStatus
    intent: AnalyticsIntent | None
    facts: tuple[AnalyticsFact, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    limitations: tuple[Limitation, ...] = ()
    ambiguities: tuple[str, ...] = ()
    suggested_clarification: str | None = None
    answer: str | None = None
    backend: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        def convert(value):
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [convert(item) for item in value]
            return value
        return convert(asdict(self))
