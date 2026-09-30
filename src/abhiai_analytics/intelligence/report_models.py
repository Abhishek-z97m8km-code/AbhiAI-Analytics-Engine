"""Typed, portable Phase 5D business-report contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .models import Evidence, Limitation
from .response_models import FaithfulnessStatus, GenerationMethod, ResponseLanguage


class ReportType(str, Enum):
    BUSINESS_OVERVIEW = "business_overview"
    SALES_PERFORMANCE = "sales_performance"
    FINANCIAL_PERFORMANCE = "financial_performance"


class ReportStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    INSUFFICIENT_DATA = "insufficient_data"


class ReportDetailLevel(str, Enum):
    EXECUTIVE = "executive"
    STANDARD = "standard"
    DETAILED = "detailed"


class SectionType(str, Enum):
    EXECUTIVE_SUMMARY = "executive_summary"
    KPI_OVERVIEW = "kpi_overview"
    REVENUE_ANALYSIS = "revenue_analysis"
    COST_ANALYSIS = "cost_analysis"
    PROFITABILITY = "profitability"
    TREND_ANALYSIS = "trend_analysis"
    DIMENSION_PERFORMANCE = "dimension_performance"
    CONTRIBUTION_ANALYSIS = "contribution_analysis"
    ANOMALIES = "anomalies"
    KEY_FINDINGS = "key_findings"
    LIMITATIONS = "limitations"


class ReportChartType(str, Enum):
    KPI = "kpi"
    LINE = "line"
    BAR = "bar"
    DONUT = "donut"
    TABLE = "table"


@dataclass(frozen=True)
class DatasetIdentity:
    dataset_id: str
    filename: str
    row_count: int
    backend: str
    schema_fingerprint: str


@dataclass(frozen=True)
class ReportKPI:
    metric: str
    raw_value: float
    formatted_value: str
    unit: str | None
    currency: str | None
    source_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReportFinding:
    finding_id: str
    title: str
    text: str
    category: str
    importance: str
    source_insight_ids: tuple[str, ...]
    source_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReportSection:
    section_type: SectionType
    title: str
    narrative: str
    finding_ids: tuple[str, ...] = ()
    source_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReportChartSpec:
    chart_id: str
    chart_type: ReportChartType
    title: str
    metric: str
    dimension: str | None
    data: tuple[dict[str, Any], ...]
    formatting: str
    source_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class BusinessReport:
    report_id: str
    title: str
    report_type: ReportType
    status: ReportStatus
    generated_at: str
    dataset: DatasetIdentity
    currency: str | None
    language: ResponseLanguage
    detail_level: ReportDetailLevel
    executive_summary: str
    kpis: tuple[ReportKPI, ...]
    sections: tuple[ReportSection, ...]
    key_findings: tuple[ReportFinding, ...]
    chart_specs: tuple[ReportChartSpec, ...]
    evidence: tuple[Evidence, ...]
    limitations: tuple[Limitation, ...]
    source_insight_ids: tuple[str, ...]
    source_evidence_ids: tuple[str, ...]
    generation_method: GenerationMethod
    faithfulness_status: FaithfulnessStatus
    generation_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        def convert(value):
            if isinstance(value, Enum): return value.value
            if isinstance(value, dict): return {key: convert(item) for key, item in value.items()}
            if isinstance(value, (tuple, list)): return [convert(item) for item in value]
            return value
        return convert(asdict(self))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BusinessReport":
        return cls(
            payload["report_id"], payload["title"], ReportType(payload["report_type"]), ReportStatus(payload["status"]),
            payload["generated_at"], DatasetIdentity(**payload["dataset"]), payload.get("currency"),
            ResponseLanguage(payload["language"]), ReportDetailLevel(payload["detail_level"]), payload["executive_summary"],
            tuple(ReportKPI(**{**item, "source_evidence_ids": tuple(item["source_evidence_ids"])}) for item in payload["kpis"]),
            tuple(ReportSection(SectionType(item["section_type"]), item["title"], item["narrative"], tuple(item["finding_ids"]), tuple(item["source_evidence_ids"])) for item in payload["sections"]),
            tuple(ReportFinding(**{**item, "source_insight_ids": tuple(item["source_insight_ids"]), "source_evidence_ids": tuple(item["source_evidence_ids"])}) for item in payload["key_findings"]),
            tuple(ReportChartSpec(item["chart_id"], ReportChartType(item["chart_type"]), item["title"], item["metric"], item.get("dimension"), tuple(item["data"]), item["formatting"], tuple(item["source_evidence_ids"])) for item in payload["chart_specs"]),
            tuple(Evidence(**item) for item in payload["evidence"]), tuple(Limitation(item["code"], item["message"], tuple(item["affected_capabilities"])) for item in payload["limitations"]),
            tuple(payload["source_insight_ids"]), tuple(payload["source_evidence_ids"]), GenerationMethod(payload["generation_method"]),
            FaithfulnessStatus(payload["faithfulness_status"]), payload.get("generation_metadata", {}),
        )


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
