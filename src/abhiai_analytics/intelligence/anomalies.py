"""Bounded, transparent anomaly signals over aggregate series."""

from __future__ import annotations

from statistics import median

from .comparisons import finite_number


def mad_anomalies(rows: list[dict], metric: str, label_key: str, threshold: float = 3.5) -> list[dict]:
    """Return robust-z candidates; zero-MAD series has no defensible signal."""
    points = [(row.get(label_key), finite_number(row.get(metric))) for row in rows]
    points = [(label, value) for label, value in points if value is not None]
    if len(points) < 5:
        return []
    center = median(value for _, value in points)
    mad = median(abs(value - center) for _, value in points)
    if mad == 0:
        return []
    candidates = []
    for label, value in points:
        robust_z = 0.6745 * (value - center) / mad
        if abs(robust_z) >= threshold:
            candidates.append({"label": str(label), "value": value, "median": center, "mad": mad, "robust_z": robust_z})
    return sorted(candidates, key=lambda row: abs(row["robust_z"]), reverse=True)[:10]
