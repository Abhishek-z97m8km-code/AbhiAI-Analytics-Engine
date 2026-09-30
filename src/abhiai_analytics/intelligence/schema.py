"""Conservative business-column discovery without dataset-specific rules."""

from __future__ import annotations

import re
from dataclasses import dataclass


ALIASES = {
    "revenue": ("total revenue", "revenue", "sales", "sales revenue"),
    "cost": ("total cost", "cost", "cost of goods sold", "cogs"),
    "profit": ("total profit", "profit", "gross profit", "net profit"),
    "units": ("units sold", "units", "quantity sold", "quantity"),
    "orders": ("order count", "orders"),
}
DIMENSION_ALIASES = ("region", "country", "product", "item type", "sales channel", "channel")
DATE_ALIASES = ("order date", "date", "transaction date", "sale date", "ship date")


def _normalise(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


@dataclass(frozen=True)
class BusinessSchema:
    metrics: dict[str, str]
    dimensions: tuple[str, ...]
    dates: tuple[str, ...]


def discover_schema(columns: list[str] | tuple[str, ...]) -> BusinessSchema:
    """Map exact normalized aliases only; ambiguous matches remain unsupported."""
    normalized: dict[str, list[str]] = {}
    for column in columns:
        normalized.setdefault(_normalise(column), []).append(column)
    metrics: dict[str, str] = {}
    for concept, aliases in ALIASES.items():
        matches = [normalized[alias][0] for alias in aliases if len(normalized.get(alias, ())) == 1]
        if len(set(matches)) == 1:
            metrics[concept] = matches[0]
        elif matches:
            metrics[concept] = matches[0]
    dimensions = tuple(
        column for alias in DIMENSION_ALIASES for column in normalized.get(alias, ())
        if column not in metrics.values()
    )
    dates = tuple(column for alias in DATE_ALIASES for column in normalized.get(alias, ()))
    return BusinessSchema(metrics, tuple(dict.fromkeys(dimensions)), tuple(dict.fromkeys(dates)))
