"""Structural and traceability validation for BusinessReport."""

from __future__ import annotations

from dataclasses import dataclass

from .report_models import BusinessReport, SectionType

MAX_SECTIONS = 12
MAX_FINDINGS = 20
MAX_CHARTS = 10
MAX_EVIDENCE = 100
MAX_CHART_POINTS = 120


@dataclass(frozen=True)
class ReportValidation:
    valid: bool
    errors: tuple[str, ...]


def validate_report(report: BusinessReport) -> ReportValidation:
    errors = []
    evidence_ids = set(report.source_evidence_ids)
    finding_ids = {item.finding_id for item in report.key_findings}
    if not report.report_id or not report.dataset.dataset_id or not report.dataset.schema_fingerprint:
        errors.append("invalid_identity")
    if len(report.sections) > MAX_SECTIONS or len(report.key_findings) > MAX_FINDINGS or len(report.chart_specs) > MAX_CHARTS or len(report.evidence) > MAX_EVIDENCE:
        errors.append("report_bounds_exceeded")
    if any(not set(kpi.source_evidence_ids).issubset(evidence_ids) for kpi in report.kpis):
        errors.append("orphan_kpi")
    if any(not finding.source_evidence_ids or not set(finding.source_evidence_ids).issubset(evidence_ids) for finding in report.key_findings):
        errors.append("orphan_finding")
    if any(not set(section.finding_ids).issubset(finding_ids) or not set(section.source_evidence_ids).issubset(evidence_ids) for section in report.sections):
        errors.append("orphan_section")
    if any(len(chart.data) > MAX_CHART_POINTS or not chart.source_evidence_ids or not set(chart.source_evidence_ids).issubset(evidence_ids) for chart in report.chart_specs):
        errors.append("invalid_chart")
    if report.limitations and not any(section.section_type == SectionType.LIMITATIONS for section in report.sections):
        errors.append("limitations_not_preserved")
    if report.currency is None and any(symbol in report.executive_summary for symbol in "$₹€£"):
        errors.append("invented_currency")
    if "next year" in report.executive_summary.casefold() or "will grow" in report.executive_summary.casefold():
        errors.append("unsupported_forecast")
    if _contains_raw_rows(report.to_dict()):
        errors.append("raw_rows_embedded")
    return ReportValidation(not errors, tuple(dict.fromkeys(errors)))


def _contains_raw_rows(value):
    if isinstance(value, dict):
        if any(key in {"raw_rows", "source_rows", "dataset_rows"} and bool(item) for key, item in value.items()): return True
        return any(_contains_raw_rows(item) for item in value.values())
    if isinstance(value, list): return any(_contains_raw_rows(item) for item in value)
    return False
