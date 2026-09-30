"""Conservative practical validation for optional model-written prose."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .comparisons import finite_number
from .response_formatting import format_number


NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?\s*(?:%|[KMBT])?", re.I)
CURRENCY_RE = re.compile(r"[$₹€£]|\b(?:USD|INR|EUR|GBP)\b", re.I)
FORECAST_RE = re.compile(r"\b(?:will|forecast|predict|next year|next quarter|expected to|projected)\b", re.I)
EXTERNAL_CAUSE_RE = re.compile(r"\b(?:competitors?|customers?|preferred|customer preference|marketing campaign|macroeconomic|economy|supplier problem|employee performance)\b", re.I)
METRIC_TERMS = ("revenue", "cost", "profit", "margin", "units", "orders")


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reasons: tuple[str, ...] = ()


class FaithfulnessValidator:
    def validate(self, prose: str, source_result) -> ValidationResult:
        reasons: list[str] = []
        if not isinstance(prose, str) or not prose.strip() or len(prose) > 8_000:
            return ValidationResult(False, ("invalid_text",))
        currency = source_result.metadata.get("currency")
        if currency is None and CURRENCY_RE.search(prose):
            reasons.append("unsupported_currency")
        if FORECAST_RE.search(prose):
            reasons.append("unsupported_forecast")
        if EXTERNAL_CAUSE_RE.search(prose):
            reasons.append("unsupported_external_cause")
        allowed_metrics = {fact.metric for fact in source_result.facts if fact.metric}
        mentioned_metrics = {metric for metric in METRIC_TERMS if re.search(rf"\b{metric}\b", prose, re.I)}
        if mentioned_metrics - allowed_metrics:
            reasons.append("unsupported_metric")
        allowed_numbers = _allowed_numbers(source_result)
        for token in NUMBER_RE.findall(prose):
            parsed = _parse_display_number(token)
            if parsed is not None and not any(_close(parsed, allowed) for allowed in allowed_numbers):
                reasons.append(f"unsupported_number:{token.strip()}")
        if re.search(r"\b(?:highest|lowest|top|bottom|most|least)\b", prose, re.I):
            allowed_labels = {fact.dimension_value.casefold() for fact in source_result.facts if fact.dimension_value}
            if allowed_labels and not any(label in prose.casefold() for label in allowed_labels):
                reasons.append("unsupported_ranked_category")
        known_periods = {str(fact.period) for fact in source_result.facts if fact.period}
        mentioned_years = set(re.findall(r"\b(?:19|20)\d{2}\b", prose))
        if mentioned_years and not all(any(year in period for period in known_periods) for year in mentioned_years):
            reasons.append("unsupported_period")
        return ValidationResult(not reasons, tuple(dict.fromkeys(reasons)))


def _allowed_numbers(result) -> list[float]:
    values: list[float] = []
    for fact in result.facts:
        for candidate in (fact.value, fact.percentage):
            number = finite_number(candidate)
            if number is not None:
                values.extend((number, round(number, 2), abs(number), round(abs(number), 2)))
                for compact in (True, False):
                    displayed = _parse_display_number(format_number(number, percentage=candidate is fact.percentage, compact=compact))
                    if displayed is not None:
                        values.append(displayed)
    for evidence in result.evidence:
        for candidate in evidence.values.values():
            number = finite_number(candidate)
            if number is not None:
                values.extend((number, round(number, 2)))
    return values


def _parse_display_number(token: str) -> float | None:
    text = token.strip().replace(",", "").replace(" ", "")
    multiplier = 1.0
    if text.endswith("%"):
        text = text[:-1]
    elif text and text[-1].upper() in "KMBT":
        multiplier = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[text[-1].upper()]
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _close(left: float, right: float) -> bool:
    return abs(left - right) <= max(0.011, abs(right) * 0.0006)
