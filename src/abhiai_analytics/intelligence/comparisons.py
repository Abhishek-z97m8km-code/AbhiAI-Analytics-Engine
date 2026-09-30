"""Safe period comparisons and profitability relationships."""

from __future__ import annotations

from math import isfinite


def finite_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def compare(current, previous) -> dict[str, float | str | None]:
    current_number = finite_number(current)
    previous_number = finite_number(previous)
    if current_number is None or previous_number is None:
        return {"absolute_change": None, "percentage_change": None, "direction": "unknown"}
    change = current_number - previous_number
    percentage = None if previous_number == 0 else change / abs(previous_number) * 100.0
    direction = "increased" if change > 0 else "decreased" if change < 0 else "unchanged"
    return {"absolute_change": change, "percentage_change": percentage, "direction": direction}
