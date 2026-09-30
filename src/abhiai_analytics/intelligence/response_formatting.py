"""Deterministic, currency-disciplined presentation formatting."""

from __future__ import annotations

from .comparisons import finite_number


def format_number(value, *, percentage: bool = False, currency: str | None = None, compact: bool = True) -> str:
    number = finite_number(value)
    if number is None:
        return "unavailable"
    if percentage:
        return f"{number:,.2f}%"
    prefix = f"{currency} " if currency else ""
    absolute = abs(number)
    if compact:
        for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
            if absolute >= threshold:
                return f"{prefix}{number / threshold:,.2f}{suffix}"
    if number.is_integer():
        return f"{prefix}{number:,.0f}"
    return f"{prefix}{number:,.2f}"


def fact_value(metric: str | None, value, percentage: float | None = None, currency: str | None = None) -> str:
    if percentage is not None:
        return format_number(percentage, percentage=True)
    if isinstance(value, str):
        return value
    return format_number(value, percentage=metric == "margin", currency=currency if metric in {"revenue", "cost", "profit"} else None)
