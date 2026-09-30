"""Explicitly selected, read-only DuckDB storage for scalable CSV datasets.

The store is available through an explicit DataManager opt-in. The Phase 3
pandas path remains the default until later Phase 4 milestones prove parity.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Literal

from abhiai_analytics.analytics import DuckDBEngine, sanitize_table_name
from abhiai_analytics.analytics.types import DatasetMetadata


DATASET_STORE_ENV = "ABHIAI_DATA_STORE"
LEGACY_STORE = "legacy"
DUCKDB_STORE = "duckdb"
ALLOWED_STORE_MODES = frozenset({LEGACY_STORE, DUCKDB_STORE})
MAX_PREVIEW_ROWS = 100
MAX_AGGREGATE_COLUMNS = 20
MAX_GROUPED_ROWS = 1_000
MAX_TIME_SERIES_ROWS = 10_000


class DatasetStoreSelectionError(ValueError):
    """Raised when scalable storage was not explicitly or validly selected."""


def selected_dataset_store(explicit: str | None = None) -> str:
    """Return the controlled store mode; legacy is always the default."""
    value = explicit if explicit is not None else os.getenv(DATASET_STORE_ENV, LEGACY_STORE)
    mode = str(value).strip().casefold()
    if mode not in ALLOWED_STORE_MODES:
        allowed = ", ".join(sorted(ALLOWED_STORE_MODES))
        raise DatasetStoreSelectionError(
            f"Unsupported dataset store '{mode}'. Expected one of: {allowed}."
        )
    return mode


def create_scalable_dataset_store(mode: str | None = None) -> "DuckDBDatasetStore":
    """Create DuckDB storage only after explicit opt-in; never fall back."""
    selected = selected_dataset_store(mode)
    if selected != DUCKDB_STORE:
        raise DatasetStoreSelectionError(
            f"Scalable dataset storage is disabled. Set {DATASET_STORE_ENV}=duckdb "
            "or pass mode='duckdb' explicitly. The legacy pandas path remains active."
        )
    return DuckDBDatasetStore()


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


DATE_GRANULARITY = Literal["daily", "monthly", "quarterly", "yearly"]
SUPPORTED_DATE_FIELDS = frozenset({"Order Date", "Ship Date"})
SUPPORTED_GRANULARITIES = frozenset({"daily", "monthly", "quarterly", "yearly"})
METRIC_COLUMNS = ("Total Revenue", "Total Cost", "Total Profit", "Units Sold")


class DuckDBDatasetStore:
    """A bounded adapter over the existing DuckDB analytics engine."""

    def __init__(self, engine: DuckDBEngine | None = None):
        self._engine = engine or DuckDBEngine(database_path=":memory:")
        self._metadata: dict[str, DatasetMetadata] = {}
        self._source_paths: dict[str, Path] = {}

    def register_csv(self, dataset_id: str, source_path: str | Path) -> DatasetMetadata:
        """Register an immutable CSV view without creating a pandas DataFrame."""
        path = Path(source_path).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError("Dataset source must be a regular file.")
        if path.suffix.casefold() != ".csv":
            raise ValueError("The scalable store currently supports CSV files only.")
        before = (path.stat().st_size, path.stat().st_mtime_ns)
        table_name = sanitize_table_name(dataset_id)
        metadata = self._engine.register_csv(table_name, path)
        after = (path.stat().st_size, path.stat().st_mtime_ns)
        if after != before:
            self._engine.drop_table(table_name)
            raise RuntimeError("The source dataset changed during registration.")
        self._metadata[table_name] = metadata
        self._source_paths[table_name] = path
        return metadata

    def metadata(self, dataset_id: str) -> DatasetMetadata:
        table_name = self._require_dataset(dataset_id)
        return self._metadata[table_name]

    def preview(self, dataset_id: str, limit: int = MAX_PREVIEW_ROWS) -> list[dict[str, Any]]:
        """Return at most 100 source-order rows as JSON-friendly dictionaries."""
        if type(limit) is not int or not 1 <= limit <= MAX_PREVIEW_ROWS:
            raise ValueError(f"Preview limit must be between 1 and {MAX_PREVIEW_ROWS}.")
        table_name = self._require_dataset(dataset_id)
        cursor = self._engine.connect().execute(
            "SELECT * FROM read_csv_auto(?, all_varchar=true) LIMIT ?",
            [str(self._source_paths[table_name]), limit],
        )
        columns = [item[0] for item in cursor.description]
        types = {column.name: column.dtype for column in self._metadata[table_name].columns}
        return [
            {
                column: self._coerce_preview_value(value, types[column])
                for column, value in zip(columns, row)
            }
            for row in cursor.fetchall()
        ]

    def sums(self, dataset_id: str, columns: Iterable[str]) -> dict[str, float | None]:
        """Compute full-dataset sums while returning exactly one bounded row."""
        table_name = self._require_dataset(dataset_id)
        selected = self._validate_columns(table_name, columns)
        expressions = ", ".join(
            f"SUM({_quote_identifier(column)}) AS {_quote_identifier(column)}"
            for column in selected
        )
        row = self._engine.connect().execute(
            f"SELECT {expressions} FROM {table_name}"
        ).fetchone()
        return dict(zip(selected, row or ()))

    def grouped_sums(
        self,
        dataset_id: str,
        group_by: str,
        columns: Iterable[str],
        limit: int = MAX_GROUPED_ROWS,
    ) -> list[dict[str, Any]]:
        """Compute full-dataset grouped sums with an explicit response bound."""
        if type(limit) is not int or not 1 <= limit <= MAX_GROUPED_ROWS:
            raise ValueError(f"Grouped result limit must be between 1 and {MAX_GROUPED_ROWS}.")
        table_name = self._require_dataset(dataset_id)
        group = self._validate_columns(table_name, [group_by])[0]
        selected = self._validate_columns(table_name, columns)
        quoted_group = _quote_identifier(group)
        expressions = ", ".join(
            f"SUM({_quote_identifier(column)}) AS {_quote_identifier(column)}"
            for column in selected
        )
        cursor = self._engine.connect().execute(
            f"SELECT {quoted_group}, {expressions} FROM {table_name} "
            f"GROUP BY {quoted_group} ORDER BY {quoted_group} LIMIT ?",
            [limit],
        )
        names = [item[0] for item in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    def calculate_kpis(self, dataset_id: str) -> dict[str, float | None]:
        """Calculate five full-dataset KPIs: total revenue, cost, profit, units sold, profit margin."""
        table_name = self._require_dataset(dataset_id)
        available = {column.name for column in self._metadata[table_name].columns}
        required = ["Total Revenue", "Total Cost", "Total Profit", "Units Sold"]
        for col in required:
            if col not in available:
                raise ValueError(f"Required column '{col}' not found in dataset.")
        expressions = ", ".join(
            f"SUM({_quote_identifier(column)}) AS {_quote_identifier(column)}"
            for column in required
        )
        row = self._engine.connect().execute(
            f"SELECT {expressions} FROM {table_name}"
        ).fetchone()
        total_revenue, total_cost, total_profit, total_units = row or (None, None, None, None)
        profit_margin = None
        if total_revenue and total_revenue != 0 and total_profit is not None:
            profit_margin = (total_profit / total_revenue) * 100.0
        return {
            "total_revenue": total_revenue,
            "total_cost": total_cost,
            "total_profit": total_profit,
            "total_units_sold": total_units,
            "profit_margin_percent": profit_margin,
        }

    def grouped_analysis(
        self,
        dataset_id: str,
        group_by: str,
        filters: dict[str, list[str]] | None = None,
        limit: int = MAX_GROUPED_ROWS,
    ) -> list[dict[str, Any]]:
        """Compute full-dataset grouped analysis with optional filters.

        Returns aggregated revenue, cost, profit, units sold, and calculated profit margin
        for each group value. Filters are applied before aggregation.
        """
        if type(limit) is not int or not 1 <= limit <= MAX_GROUPED_ROWS:
            raise ValueError(f"Grouped result limit must be between 1 and {MAX_GROUPED_ROWS}.")
        table_name = self._require_dataset(dataset_id)
        available = {column.name for column in self._metadata[table_name].columns}

        if group_by not in available:
            raise ValueError(f"Group by column '{group_by}' not found in dataset.")

        if filters:
            for col, values in filters.items():
                if col not in available:
                    raise ValueError(f"Filter column '{col}' not found in dataset.")
                if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                    raise ValueError(f"Filter values for '{col}' must be a list of strings.")

        where_clauses = []
        params = []
        if filters:
            for col, values in filters.items():
                if values:
                    placeholders = ", ".join(["?"] * len(values))
                    where_clauses.append(f"{_quote_identifier(col)} IN ({placeholders})")
                    params.extend(values)

        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)

        for metric in METRIC_COLUMNS:
            if metric not in available:
                raise ValueError(f"Required metric column '{metric}' not found in dataset.")

        expressions = ", ".join(
            f"SUM({_quote_identifier(metric)}) AS {_quote_identifier(metric)}"
            for metric in METRIC_COLUMNS
        )

        quoted_group = _quote_identifier(group_by)
        query = f"""
            SELECT {quoted_group}, {expressions}
            FROM {table_name}
            {where_sql}
            GROUP BY {quoted_group}
            ORDER BY {quoted_group}
            LIMIT ?
        """
        params.append(limit)

        cursor = self._engine.connect().execute(query, params)
        names = [item[0] for item in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor.fetchall()]

        # Rename columns to consistent snake_case keys for downstream deserialization
        column_rename_map = {
            "Total Revenue": "total_revenue",
            "Total Cost": "total_cost",
            "Total Profit": "total_profit",
            "Units Sold": "total_units_sold",
            "Profit Margin": "profit_margin",
        }
        for row in rows:
            group_value = row.pop(group_by)
            row["group_value"] = group_value
            for old_key, new_key in column_rename_map.items():
                if old_key in row:
                    row[new_key] = row.pop(old_key)
            # Calculate profit_margin if not already present (for compatibility)
            if "profit_margin" not in row or row["profit_margin"] is None:
                total_revenue = row.get("total_revenue")
                total_profit = row.get("total_profit")
                if total_revenue and total_revenue != 0 and total_profit is not None:
                    row["profit_margin"] = (total_profit / total_revenue) * 100.0
                else:
                    row["profit_margin"] = None

        return rows

    def get_supported_date_fields(self, dataset_id: str) -> list[str]:
        """Return date fields actually present in the dataset that are supported for time-based analysis."""
        table_name = self._require_dataset(dataset_id)
        available = {column.name for column in self._metadata[table_name].columns}
        supported = [field for field in SUPPORTED_DATE_FIELDS if field in available]
        return supported

    def validate_date_field(self, dataset_id: str, date_field: str) -> None:
        """Validate that a date field exists and is supported."""
        supported = self.get_supported_date_fields(dataset_id)
        if date_field not in supported:
            raise ValueError(
                f"Date field '{date_field}' is not available or not supported. "
                f"Supported fields in this dataset: {supported}"
            )

    def validate_granularity(self, granularity: str) -> None:
        """Validate that a granularity is supported."""
        if granularity not in SUPPORTED_GRANULARITIES:
            raise ValueError(
                f"Granularity '{granularity}' is not supported. "
                f"Supported granularities: {sorted(SUPPORTED_GRANULARITIES)}"
            )

    def get_date_field_stats(self, dataset_id: str, date_field: str) -> dict[str, Any]:
        """Get statistics about a date field: valid count, invalid count, min, max."""
        self.validate_date_field(dataset_id, date_field)
        table_name = self._require_dataset(dataset_id)

        total = self._engine.connect().execute(
            f"SELECT COUNT(*) FROM {table_name}"
        ).fetchone()[0]

        valid = self._engine.connect().execute(
            f"SELECT COUNT(*) FROM {table_name} WHERE TRY_CAST({_quote_identifier(date_field)} AS DATE) IS NOT NULL"
        ).fetchone()[0]

        min_date = self._engine.connect().execute(
            f"SELECT MIN(TRY_CAST({_quote_identifier(date_field)} AS DATE)) FROM {table_name}"
        ).fetchone()[0]

        max_date = self._engine.connect().execute(
            f"SELECT MAX(TRY_CAST({_quote_identifier(date_field)} AS DATE)) FROM {table_name}"
        ).fetchone()[0]

        return {
            "date_field": date_field,
            "total_records": total,
            "valid_dates": valid,
            "invalid_dates": total - valid,
            "min_date": str(min_date) if min_date else None,
            "max_date": str(max_date) if max_date else None,
        }

    def _build_period_expression(self, date_field: str, granularity: DATE_GRANULARITY) -> str:
        """Build a DuckDB expression for the period based on granularity."""
        quoted_field = _quote_identifier(date_field)
        cast_field = f"TRY_CAST({quoted_field} AS DATE)"

        if granularity == "daily":
            return f"DATE_TRUNC('day', {cast_field})"
        elif granularity == "monthly":
            return f"DATE_TRUNC('month', {cast_field})"
        elif granularity == "quarterly":
            return f"DATE_TRUNC('quarter', {cast_field})"
        elif granularity == "yearly":
            return f"DATE_TRUNC('year', {cast_field})"
        else:
            raise ValueError(f"Unsupported granularity: {granularity}")

    def _build_period_label(self, date_field: str, granularity: DATE_GRANULARITY) -> str:
        """Build a DuckDB expression for the period label based on granularity."""
        period_expr = self._build_period_expression(date_field, granularity)

        if granularity == "daily":
            return f"STRFTIME({period_expr}, '%Y-%m-%d')"
        elif granularity == "monthly":
            return f"STRFTIME({period_expr}, '%Y-%m')"
        elif granularity == "quarterly":
            return f"CONCAT(STRFTIME({period_expr}, '%Y'), '-Q', QUARTER({period_expr}))"
        elif granularity == "yearly":
            return f"STRFTIME({period_expr}, '%Y')"
        else:
            raise ValueError(f"Unsupported granularity: {granularity}")

    def _build_order_expression(self, date_field: str, granularity: DATE_GRANULARITY) -> str:
        """Build a DuckDB expression for chronological ordering."""
        return self._build_period_expression(date_field, granularity)

    def time_series_analysis(
        self,
        dataset_id: str,
        date_field: str,
        granularity: DATE_GRANULARITY,
        filters: dict[str, list[str]] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = MAX_TIME_SERIES_ROWS,
    ) -> list[dict[str, Any]]:
        """Compute full-dataset time-based aggregation.

        Returns chronological periods with aggregated revenue, cost, profit, units sold,
        and calculated profit margin. Optional inclusive date filters and categorical filters.
        """
        if type(limit) is not int or not 1 <= limit <= MAX_TIME_SERIES_ROWS:
            raise ValueError(f"Time series limit must be between 1 and {MAX_TIME_SERIES_ROWS}.")

        self.validate_date_field(dataset_id, date_field)
        self.validate_granularity(granularity)

        if start_date and end_date and start_date > end_date:
            raise ValueError("Start date must be before or equal to end date.")

        table_name = self._require_dataset(dataset_id)
        available = {column.name for column in self._metadata[table_name].columns}

        if filters:
            for col, values in filters.items():
                if col not in available:
                    raise ValueError(f"Filter column '{col}' not found in dataset.")
                if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                    raise ValueError(f"Filter values for '{col}' must be a list of strings.")

        for metric in METRIC_COLUMNS:
            if metric not in available:
                raise ValueError(f"Required metric column '{metric}' not found in dataset.")

        quoted_date = _quote_identifier(date_field)
        period_expr = self._build_period_expression(date_field, granularity)
        period_label = self._build_period_label(date_field, granularity)
        order_expr = self._build_order_expression(date_field, granularity)

        where_clauses = []
        params = []

        valid_date_expr = f"TRY_CAST({quoted_date} AS DATE) IS NOT NULL"
        where_clauses.append(valid_date_expr)

        if filters:
            for col, values in filters.items():
                if values:
                    placeholders = ", ".join(["?"] * len(values))
                    where_clauses.append(f"{_quote_identifier(col)} IN ({placeholders})")
                    params.extend(values)

        if start_date:
            where_clauses.append(f"TRY_CAST({quoted_date} AS DATE) >= ?")
            params.append(start_date)

        if end_date:
            where_clauses.append(f"TRY_CAST({quoted_date} AS DATE) <= ?")
            params.append(end_date)

        where_sql = "WHERE " + " AND ".join(where_clauses)

        expressions = ", ".join(
            f"SUM({_quote_identifier(metric)}) AS {_quote_identifier(metric)}"
            for metric in METRIC_COLUMNS
        )

        query = f"""
            SELECT {period_label} AS period, {expressions}
            FROM {table_name}
            {where_sql}
            GROUP BY {period_expr}
            ORDER BY {order_expr}
            LIMIT ?
        """
        params.append(limit)

        cursor = self._engine.connect().execute(query, params)
        names = [item[0] for item in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor.fetchall()]

        # Rename columns to consistent snake_case keys for downstream deserialization
        column_rename_map = {
            "Total Revenue": "total_revenue",
            "Total Cost": "total_cost",
            "Total Profit": "total_profit",
            "Units Sold": "total_units_sold",
            "Profit Margin": "profit_margin",
        }
        for row in rows:
            for old_key, new_key in column_rename_map.items():
                if old_key in row:
                    row[new_key] = row.pop(old_key)
            # Calculate profit_margin if not already present
            if "profit_margin" not in row or row["profit_margin"] is None:
                total_revenue = row.get("total_revenue")
                total_profit = row.get("total_profit")
                if total_revenue and total_revenue != 0 and total_profit is not None:
                    row["profit_margin"] = (total_profit / total_revenue) * 100.0
                else:
                    row["profit_margin"] = None

        return rows

    def close(self) -> None:
        self._engine.close()
        self._metadata.clear()
        self._source_paths.clear()

    def _require_dataset(self, dataset_id: str) -> str:
        table_name = sanitize_table_name(dataset_id)
        if table_name not in self._metadata:
            raise KeyError(f"Dataset '{dataset_id}' is not registered in the DuckDB store.")
        return table_name

    def _validate_columns(self, table_name: str, columns: Iterable[str]) -> list[str]:
        selected = list(columns)
        if not selected or len(selected) > MAX_AGGREGATE_COLUMNS:
            raise ValueError(
                f"Select between 1 and {MAX_AGGREGATE_COLUMNS} aggregate columns."
            )
        available = {column.name for column in self._metadata[table_name].columns}
        if any(type(column) is not str or column not in available for column in selected):
            raise ValueError("Every requested column must exist in the registered dataset.")
        return selected

    @staticmethod
    def _coerce_preview_value(value: str | None, dtype: str) -> Any:
        if value is None:
            return None
        normalized = dtype.upper()
        if any(token in normalized for token in ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT")):
            return int(value)
        if any(token in normalized for token in ("FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "REAL")):
            return float(value)
        if normalized == "BOOLEAN":
            return value.casefold() == "true"
        return value

    def __enter__(self) -> "DuckDBDatasetStore":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.close()
        return False