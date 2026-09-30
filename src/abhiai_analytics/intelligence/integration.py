"""Backend-aware aggregate preparation for the business insight engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .business_insights import BusinessInsightEngine
from .comparisons import finite_number
from .models import Limitation
from .schema import discover_schema

if TYPE_CHECKING:
    from core.data.data_manager import DataManager


def analyze_dataset(manager: "DataManager", dataset_id: str | None = None, *, granularity: str = "monthly"):
    """Analyze one registered dataset without changing backend selection."""
    context = collect_analytics_context(manager, dataset_id, granularity=granularity)
    return BusinessInsightEngine().analyze(
        dataset_id=context["dataset_id"], backend=context["backend"],
        kpis=context["kpis"], groups=context["groups"],
        time_rows=context["time_rows"], currency=None,
        limitations=context["limitations"], metadata=context["metadata"],
    )


def collect_analytics_context(manager: "DataManager", dataset_id: str | None = None, *, granularity: str = "monthly") -> dict:
    """Prepare one bounded, backend-aware aggregate context for insight/query layers."""
    selected_id = dataset_id or manager.active_id
    record = manager.get_record(selected_id) if selected_id else None
    if record is None:
        raise ValueError("Dataset not found.")
    limits: list[Limitation] = []
    if record.backend == "duckdb":
        store = manager._ensure_duckdb_store()
        columns = [column.name for column in store.metadata(record.id).columns]
        schema = discover_schema(columns)
        kpis = _duckdb_kpis(store, record.id, schema.metrics, limits)
        groups = {}
        if all(key in schema.metrics for key in ("revenue", "cost", "profit", "units")):
            for dimension in schema.dimensions[:5]:
                groups[dimension] = [_normalize_row(row) for row in store.grouped_analysis(record.id, dimension)]
        date_fields = [field for field in schema.dates if field in store.get_supported_date_fields(record.id)]
        time_rows = []
        if date_fields and all(key in schema.metrics for key in ("revenue", "cost", "profit", "units")):
            time_rows = [_normalize_row(row) for row in store.time_series_analysis(record.id, date_fields[0], granularity)]
        elif schema.dates:
            limits.append(Limitation("incomplete_time_metrics", "Time analysis requires revenue, cost, profit, and units in the current DuckDB adapter.", ("comparison", "trend")))
        metadata = {"row_count": store.metadata(record.id).row_count, "aggregate_rows": sum(map(len, groups.values())) + len(time_rows), "date_field": date_fields[0] if date_fields else None, "granularity": granularity}
    else:
        frame = manager.get_frame(record.id)
        columns = list(frame.columns)
        schema = discover_schema(columns)
        kpis, groups, time_rows, metadata = _pandas_aggregates(frame, schema, granularity, limits)
    _schema_limits(schema, limits)
    return {
        "dataset_id": record.id,
        "backend": record.backend,
        "schema": schema,
        "kpis": kpis,
        "groups": groups,
        "time_rows": time_rows,
        "limitations": limits,
        "metadata": metadata,
    }


def _duckdb_kpis(store, dataset_id, metrics, limits):
    available = [metrics[key] for key in ("revenue", "cost", "profit", "units") if key in metrics]
    sums = store.sums(dataset_id, available) if available else {}
    result = {key: finite_number(sums.get(column)) for key, column in metrics.items() if key in ("revenue", "cost", "profit", "units")}
    if result.get("profit") is None and result.get("revenue") is not None and result.get("cost") is not None:
        result["profit"] = result["revenue"] - result["cost"]
    if result.get("revenue") not in (None, 0) and result.get("profit") is not None:
        result["margin"] = result["profit"] / result["revenue"] * 100.0
    return result


def _pandas_aggregates(frame, schema, granularity, limits):
    import pandas as pd
    numeric = {key: pd.to_numeric(frame[column], errors="coerce") for key, column in schema.metrics.items()}
    kpis = {key: finite_number(series.sum(min_count=1)) for key, series in numeric.items()}
    if kpis.get("profit") is None and kpis.get("revenue") is not None and kpis.get("cost") is not None:
        kpis["profit"] = kpis["revenue"] - kpis["cost"]
    if kpis.get("revenue") not in (None, 0) and kpis.get("profit") is not None:
        kpis["margin"] = kpis["profit"] / kpis["revenue"] * 100.0
    groups = {}
    for dimension in schema.dimensions[:5]:
        work = pd.DataFrame({dimension: frame[dimension]})
        for key, series in numeric.items():
            work[key] = series
        rows = work.groupby(dimension, dropna=False).sum(numeric_only=True).reset_index().head(1000).to_dict("records")
        normalized_rows = []
        for row in rows:
            group_value = row.pop(dimension)
            normalized = _normalize_row({**row, "group_value": group_value})
            if normalized.get("profit") is None and normalized.get("revenue") is not None and normalized.get("cost") is not None:
                normalized["profit"] = normalized["revenue"] - normalized["cost"]
            if normalized.get("revenue") not in (None, 0) and normalized.get("profit") is not None:
                normalized["margin"] = normalized["profit"] / normalized["revenue"] * 100.0
            normalized_rows.append(normalized)
        groups[dimension] = normalized_rows
    time_rows = []
    valid_date_count = 0
    if schema.dates:
        dates = pd.to_datetime(frame[schema.dates[0]], errors="coerce")
        valid_date_count = int(dates.notna().sum())
        freq = {"daily": "D", "monthly": "MS", "quarterly": "QS", "yearly": "YS"}.get(granularity)
        if freq is None:
            raise ValueError("Granularity must be daily, monthly, quarterly, or yearly.")
        work = pd.DataFrame({"date": dates, **numeric}).dropna(subset=["date"])
        if not work.empty:
            grouped = work.set_index("date").resample(freq).sum(numeric_only=True)
            grouped = grouped.loc[(grouped != 0).any(axis=1)].head(10000).reset_index()
            for row in grouped.to_dict("records"):
                row["period"] = row.pop("date").strftime("%Y-%m-%d")
                if row.get("revenue") not in (None, 0) and row.get("profit") is not None:
                    row["margin"] = row["profit"] / row["revenue"] * 100.0
                time_rows.append(_normalize_row(row))
        invalid = len(frame) - valid_date_count
        if invalid:
            limits.append(Limitation("invalid_dates", f"{invalid} rows have missing or invalid values in {schema.dates[0]} and were excluded from time analysis.", ("comparison", "trend")))
    return kpis, groups, time_rows, {"row_count": len(frame), "aggregate_rows": sum(map(len, groups.values())) + len(time_rows), "date_field": schema.dates[0] if schema.dates else None, "valid_date_rows": valid_date_count, "granularity": granularity}


def _normalize_row(row):
    result = dict(row)
    aliases = {"total_revenue": "revenue", "total_cost": "cost", "total_profit": "profit", "total_units_sold": "units", "profit_margin": "margin"}
    for source, target in aliases.items():
        if source in result:
            result[target] = result.pop(source)
    return result


def _schema_limits(schema, limits):
    for metric in ("revenue", "cost", "profit"):
        if metric not in schema.metrics:
            limits.append(Limitation(f"missing_{metric}", f"No unambiguous {metric} column is available.", (metric,)))
    if not schema.dates:
        limits.append(Limitation("missing_date", "No unambiguous date column is available.", ("comparison", "trend", "time_anomaly")))
    if not schema.dimensions:
        limits.append(Limitation("missing_dimension", "No supported categorical business dimension is available.", ("dimension", "contribution")))
