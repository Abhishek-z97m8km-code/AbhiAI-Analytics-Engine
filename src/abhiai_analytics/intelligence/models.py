"""Typed values shared by business-insight consumers and future reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class InterpretationType(str, Enum):
    OBSERVATION = "observation"
    DERIVED_RELATIONSHIP = "derived_relationship"
    POSSIBLE_EXPLANATION = "possible_explanation"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class EvidenceStrength(str, Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    NOT_APPLICABLE = "not_applicable"


class InsightCategory(str, Enum):
    KPI = "kpi"
    COMPARISON = "comparison"
    TREND = "trend"
    DIMENSION = "dimension"
    CONTRIBUTION = "contribution"
    PROFITABILITY = "profitability"
    ANOMALY = "anomaly"
    LIMITATION = "limitation"


@dataclass(frozen=True)
class Evidence:
    """A bounded, inspectable fact supporting an insight."""

    description: str
    values: dict[str, Any] = field(default_factory=dict)
    method: str = "deterministic aggregation"
    source: str = "structured analytics result"


@dataclass(frozen=True)
class Limitation:
    code: str
    message: str
    affected_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class BusinessInsight:
    id: str
    category: InsightCategory
    title: str
    summary: str
    interpretation_type: InterpretationType
    evidence_strength: EvidenceStrength
    evidence: tuple[Evidence, ...]
    metric: str | None = None
    current_value: float | None = None
    comparison_value: float | None = None
    absolute_change: float | None = None
    percentage_change: float | None = None
    dimensions: dict[str, str] = field(default_factory=dict)
    period: str | None = None
    importance: str = "informational"
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class InsightReport:
    dataset_id: str
    backend: str
    currency: str | None
    insights: tuple[BusinessInsight, ...]
    limitations: tuple[Limitation, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return an enum-safe structure for bridge/report consumers."""
        def convert(value: Any) -> Any:
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [convert(item) for item in value]
            return value

        return convert(asdict(self))
