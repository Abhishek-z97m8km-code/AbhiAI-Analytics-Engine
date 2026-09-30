"""Conservative descriptive trend classification (not forecasting)."""

from __future__ import annotations

from statistics import mean, pstdev

from .comparisons import finite_number

MIN_TREND_PERIODS = 4


def classify_trend(values) -> dict:
    numbers = [finite_number(value) for value in values]
    numbers = [value for value in numbers if value is not None]
    if len(numbers) < MIN_TREND_PERIODS:
        return {"classification": "insufficient_data", "strength": "weak", "periods": len(numbers)}
    scale = max(mean(abs(value) for value in numbers), 1e-12)
    x_mean = (len(numbers) - 1) / 2
    slope = sum((index - x_mean) * (value - mean(numbers)) for index, value in enumerate(numbers)) / sum(
        (index - x_mean) ** 2 for index in range(len(numbers))
    )
    normalized_slope = slope / scale
    volatility = pstdev(numbers) / scale
    if volatility >= 0.35 and abs(normalized_slope) < 0.08:
        label = "volatile"
    elif normalized_slope > 0.02:
        label = "increasing"
    elif normalized_slope < -0.02:
        label = "decreasing"
    else:
        label = "stable"
    return {
        "classification": label,
        "strength": "strong" if len(numbers) >= 6 else "moderate",
        "periods": len(numbers),
        "normalized_slope": normalized_slope,
        "coefficient_of_variation": volatility,
    }
