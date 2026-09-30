"""Pandas fallback engine for the Large-Scale Analytics Engine."""

import time
from pathlib import Path
from typing import Any, Optional
import pandas as pd

from .types import (
    EngineType,
    SourceType,
    ProcessingMode,
    DatasetMetadata,
    ColumnInfo,
    AnalyticalRequest,
    AnalyticalResult,
    OperationType,
    AggregationFunction,
    ProgressState,
    SamplingMethod,
)
from .safety import (
    ValidatedPlan,
    QuerySafetyError,
    sanitize_table_name,
)


class PandasEngine:
    """Pandas-backed analytical engine for compatibility and small datasets."""

    def __init__(self, max_rows: int = 1_000_000):
        """Initialize the Pandas engine.

        Args:
            max_rows: Maximum rows to load into memory
        """
        self.max_rows = max_rows
        self._dataframes: dict[str, pd.DataFrame] = {}
        self._schemas: dict[str, list[ColumnInfo]] = {}
        self._sources: dict[str, str] = {}
        self._progress_callback = None

    def set_progress_callback(self, callback):
        """Set a callback for progress updates."""
        self._progress_callback = callback

    def _report_progress(self, state: ProgressState):
        """Report progress if callback is set."""
        if self._progress_callback:
            self._progress_callback(state)

    def _infer_schema(self, df: pd.DataFrame, sample_size: int = 10000) -> list[ColumnInfo]:
        """Infer schema from a DataFrame."""
        columns = []
        for col_name in df.columns:
            col_series = df[col_name]
            dtype = str(col_series.dtype)
            nullable = col_series.isna().any()
            unique_count = col_series.nunique()
            null_count = col_series.isna().sum()
            min_val = col_series.min() if not col_series.isna().all() else None
            max_val = col_series.max() if not col_series.isna().all() else None
            sample_values = col_series.head(5).tolist()

            columns.append(ColumnInfo(
                name=col_name,
                dtype=dtype,
                nullable=nullable,
                unique_count=unique_count,
                null_count=int(null_count),
                min_value=min_val,
                max_value=max_val,
                sample_values=sample_values,
            ))

        return columns

    def _apply_filters(self, df: pd.DataFrame, filters: list[dict]) -> pd.DataFrame:
        """Apply filter conditions to a DataFrame."""
        mask = pd.Series(True, index=df.index)

        for f in filters:
            column = f["column"]
            operator = f["operator"]
            value = f.get("value")

            if column not in df.columns:
                raise QuerySafetyError(f"Column '{column}' not found")

            if operator == "eq":
                mask &= df[column] == value
            elif operator == "ne":
                mask &= df[column] != value
            elif operator == "gt":
                mask &= df[column] > value
            elif operator == "gte":
                mask &= df[column] >= value
            elif operator == "lt":
                mask &= df[column] < value
            elif operator == "lte":
                mask &= df[column] <= value
            elif operator == "contains":
                mask &= df[column].astype(str).str.contains(str(value), na=False)
            elif operator == "starts_with":
                mask &= df[column].astype(str).str.startswith(str(value), na=False)
            elif operator == "ends_with":
                mask &= df[column].astype(str).str.endswith(str(value), na=False)
            elif operator == "in":
                mask &= df[column].isin(value)
            elif operator == "not_in":
                mask &= ~df[column].isin(value)
            elif operator == "is_null":
                mask &= df[column].isna()
            elif operator == "not_null":
                mask &= df[column].notna()
            elif operator == "between":
                if isinstance(value, list) and len(value) == 2:
                    mask &= df[column].between(value[0], value[1])

        return df[mask]

    def register_csv(
        self,
        name: str,
        path: str | Path,
        delimiter: str = ",",
        header: bool = True,
        sample_size: int = 10000,
    ) -> DatasetMetadata:
        """Register a CSV file."""
        table_name = sanitize_table_name(name)
        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {path}")

        file_size = path.stat().st_size

        start_time = time.perf_counter()

        self._report_progress(
            ProgressState(
                operation_id=f"load_{table_name}",
                operation="load_csv",
                dataset_name=table_name,
                status="starting",
                message=f"Loading CSV: {path.name}",
            )
        )

        try:
            df = pd.read_csv(path, sep=delimiter, header=0 if header else None, nrows=self.max_rows)

            if len(df) >= self.max_rows:
                raise QuerySafetyError(f"CSV exceeds max rows ({self.max_rows}). Use DuckDB engine for larger datasets.")

            columns = self._infer_schema(df, sample_size)

            self._dataframes[table_name] = df
            self._schemas[table_name] = columns
            self._sources[table_name] = str(path)

            load_time = (time.perf_counter() - start_time) * 1000

            metadata = DatasetMetadata(
                name=table_name,
                source_type=SourceType.CSV,
                source_path=str(path),
                row_count=len(df),
                column_count=len(columns),
                columns=tuple(columns),
                estimated_size_bytes=file_size,
                actual_size_bytes=file_size,
                engine_used=EngineType.PANDAS,
                processing_mode=ProcessingMode.IN_MEMORY,
                analysis_duration_ms=load_time,
            )

            self._report_progress(
                ProgressState(
                    operation_id=f"load_{table_name}",
                    operation="load_csv",
                    dataset_name=table_name,
                    status="completed",
                    processed_rows=len(df),
                    total_rows=len(df),
                    progress_percent=100.0,
                    message=f"Loaded {len(df):,} rows",
                    completed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                )
            )

            return metadata

        except Exception as e:
            self._report_progress(
                ProgressState(
                    operation_id=f"load_{table_name}",
                    operation="load_csv",
                    dataset_name=table_name,
                    status="failed",
                    error=str(e),
                )
            )
            raise

    def register_parquet(
        self,
        name: str,
        path: str | Path,
        sample_size: int = 10000,
    ) -> DatasetMetadata:
        """Register a Parquet file."""
        table_name = sanitize_table_name(name)
        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(f"Parquet file not found: {path}")

        file_size = path.stat().st_size

        start_time = time.perf_counter()

        self._report_progress(
            ProgressState(
                operation_id=f"load_{table_name}",
                operation="load_parquet",
                dataset_name=table_name,
                status="starting",
                message=f"Loading Parquet: {path.name}",
            )
        )

        try:
            df = pd.read_parquet(path)

            if len(df) >= self.max_rows:
                raise QuerySafetyError(f"Parquet exceeds max rows ({self.max_rows}). Use DuckDB engine for larger datasets.")

            columns = self._infer_schema(df, sample_size)

            self._dataframes[table_name] = df
            self._schemas[table_name] = columns
            self._sources[table_name] = str(path)

            load_time = (time.perf_counter() - start_time) * 1000

            metadata = DatasetMetadata(
                name=table_name,
                source_type=SourceType.PARQUET,
                source_path=str(path),
                row_count=len(df),
                column_count=len(columns),
                columns=tuple(columns),
                estimated_size_bytes=file_size,
                actual_size_bytes=file_size,
                engine_used=EngineType.PANDAS,
                processing_mode=ProcessingMode.IN_MEMORY,
                analysis_duration_ms=load_time,
            )

            self._report_progress(
                ProgressState(
                    operation_id=f"load_{table_name}",
                    operation="load_parquet",
                    dataset_name=table_name,
                    status="completed",
                    processed_rows=len(df),
                    total_rows=len(df),
                    progress_percent=100.0,
                    message=f"Loaded {len(df):,} rows",
                    completed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                )
            )

            return metadata

        except Exception as e:
            self._report_progress(
                ProgressState(
                    operation_id=f"load_{table_name}",
                    operation="load_parquet",
                    dataset_name=table_name,
                    status="failed",
                    error=str(e),
                )
            )
            raise

    def register_xlsx(
        self,
        name: str,
        path: str | Path,
        sheet_name: str | int = 0,
    ) -> DatasetMetadata:
        """Register an Excel sheet."""
        table_name = sanitize_table_name(name)
        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(f"Excel file not found: {path}")

        file_size = path.stat().st_size

        start_time = time.perf_counter()

        self._report_progress(
            ProgressState(
                operation_id=f"load_{table_name}",
                operation="load_xlsx",
                dataset_name=table_name,
                status="starting",
                message=f"Loading Excel: {path.name}",
            )
        )

        try:
            df = pd.read_excel(path, sheet_name=sheet_name)

            if len(df) >= self.max_rows:
                raise QuerySafetyError(f"Excel exceeds max rows ({self.max_rows}). Use DuckDB engine for larger datasets.")

            columns = self._infer_schema(df, len(df))

            self._dataframes[table_name] = df
            self._schemas[table_name] = columns
            self._sources[table_name] = str(path)

            load_time = (time.perf_counter() - start_time) * 1000

            metadata = DatasetMetadata(
                name=table_name,
                source_type=SourceType.XLSX,
                source_path=str(path),
                row_count=len(df),
                column_count=len(columns),
                columns=tuple(columns),
                estimated_size_bytes=file_size,
                actual_size_bytes=file_size,
                engine_used=EngineType.PANDAS,
                processing_mode=ProcessingMode.IN_MEMORY,
                analysis_duration_ms=load_time,
            )

            self._report_progress(
                ProgressState(
                    operation_id=f"load_{table_name}",
                    operation="load_xlsx",
                    dataset_name=table_name,
                    status="completed",
                    processed_rows=len(df),
                    total_rows=len(df),
                    progress_percent=100.0,
                    message=f"Loaded {len(df):,} rows",
                    completed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                )
            )

            return metadata

        except Exception as e:
            self._report_progress(
                ProgressState(
                    operation_id=f"load_{table_name}",
                    operation="load_xlsx",
                    dataset_name=table_name,
                    status="failed",
                    error=str(e),
                )
            )
            raise

    def register_dataframe(self, name: str, df: Any) -> DatasetMetadata:
        """Register a pandas/polars DataFrame."""
        table_name = sanitize_table_name(name)

        if hasattr(df, "to_pandas"):
            pdf = df.to_pandas()
        elif isinstance(df, pd.DataFrame):
            pdf = df
        else:
            pdf = pd.DataFrame(df)

        if len(pdf) >= self.max_rows:
            raise QuerySafetyError(f"DataFrame exceeds max rows ({self.max_rows}). Use DuckDB engine for larger datasets.")

        row_count = len(pdf)
        file_size = pdf.memory_usage(deep=True).sum()

        start_time = time.perf_counter()

        columns = self._infer_schema(pdf, row_count)

        self._dataframes[table_name] = pdf
        self._schemas[table_name] = columns
        self._sources[table_name] = "dataframe"

        load_time = (time.perf_counter() - start_time) * 1000

        return DatasetMetadata(
            name=table_name,
            source_type=SourceType.DATAFRAME,
            row_count=row_count,
            column_count=len(columns),
            columns=tuple(columns),
            estimated_size_bytes=file_size,
            actual_size_bytes=file_size,
            engine_used=EngineType.PANDAS,
            processing_mode=ProcessingMode.IN_MEMORY,
            analysis_duration_ms=load_time,
        )

    def get_metadata(self, name: str) -> Optional[DatasetMetadata]:
        """Get metadata for a registered table."""
        table_name = sanitize_table_name(name)
        if table_name not in self._dataframes:
            return None

        columns = self._schemas.get(table_name, [])
        row_count = len(self._dataframes.get(table_name, []))

        return DatasetMetadata(
            name=table_name,
            source_type=SourceType.CSV,
            source_path=self._sources.get(table_name),
            row_count=row_count,
            column_count=len(columns),
            columns=tuple(columns),
            engine_used=EngineType.PANDAS,
            processing_mode=ProcessingMode.IN_MEMORY,
        )

    def list_tables(self) -> list[str]:
        """List all registered tables."""
        return list(self._dataframes.keys())

    def drop_table(self, name: str) -> bool:
        """Drop a registered table."""
        table_name = sanitize_table_name(name)
        if table_name in self._dataframes:
            del self._dataframes[table_name]
            self._schemas.pop(table_name, None)
            self._sources.pop(table_name, None)
            return True
        return False

    def _get_df(self, name: str) -> pd.DataFrame:
        """Get DataFrame by name."""
        table_name = sanitize_table_name(name)
        if table_name not in self._dataframes:
            raise QuerySafetyError(f"Table '{table_name}' not found")
        return self._dataframes[table_name]

    def execute(self, plan: ValidatedPlan) -> AnalyticalResult:
        """Execute a validated analytical plan."""
        start_time = time.perf_counter()

        self._report_progress(
            ProgressState(
                operation_id=f"exec_{plan.operation.value}_{plan.dataset_name}",
                operation=plan.operation.value,
                dataset_name=plan.dataset_name,
                status="running",
                message=f"Executing {plan.operation.value}...",
            )
        )

        try:
            df = self._get_df(plan.dataset_name)
            df = self._apply_filters(df, plan.filters)

            if plan.operation == OperationType.ROW_COUNT:
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=[{"count": len(df)}],
                    row_count=1,
                    column_names=["count"],
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.PANDAS,
                )

            elif plan.operation == OperationType.COLUMN_SCHEMA:
                columns = self._schemas.get(plan.dataset_name, [])
                rows = [c.to_dict() for c in columns]
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=list(rows[0].keys()) if rows else [],
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.PANDAS,
                )

            elif plan.operation == OperationType.DESCRIPTIVE_STATS:
                rows = []
                for col in plan.columns:
                    series = df[col]
                    numeric = pd.to_numeric(series, errors="coerce")
                    if numeric.notna().any():
                        rows.append({
                            "column": col,
                            "count": int(numeric.notna().sum()),
                            "mean": float(numeric.mean()),
                            "std": float(numeric.std()),
                            "min": float(numeric.min()),
                            "max": float(numeric.max()),
                            "sum": float(numeric.sum()),
                        })
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=["column", "count", "mean", "std", "min", "max", "sum"],
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.PANDAS,
                )

            elif plan.operation in {OperationType.SUM, OperationType.MEAN, OperationType.MIN, OperationType.MAX, OperationType.COUNT}:
                col = plan.columns[0]
                series = pd.to_numeric(df[col], errors="coerce").dropna()
                if plan.operation == OperationType.SUM:
                    val = series.sum()
                elif plan.operation == OperationType.MEAN:
                    val = series.mean()
                elif plan.operation == OperationType.MIN:
                    val = series.min()
                elif plan.operation == OperationType.MAX:
                    val = series.max()
                else:
                    val = len(series)
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=[{"column": col, plan.operation.value: float(val)}],
                    row_count=1,
                    column_names=["column", plan.operation.value],
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.PANDAS,
                )

            elif plan.operation in {OperationType.GROUPBY, OperationType.MULTI_GROUPBY}:
                agg_dict = {}
                for col, agg in plan.aggregations.items():
                    agg_dict[col] = agg.value
                result = df.groupby(plan.group_by).agg(agg_dict).reset_index()

                if plan.sort_by:
                    result = result.sort_values(plan.sort_by, ascending=plan.sort_ascending)

                if plan.limit:
                    result = result.head(plan.limit)

                rows = result.to_dict(orient="records")
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=list(result.columns),
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.PANDAS,
                )

            elif plan.operation == OperationType.FILTER:
                if plan.columns:
                    df = df[plan.columns]
                if plan.limit:
                    df = df.head(plan.limit)
                rows = df.to_dict(orient="records")
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=list(df.columns),
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.PANDAS,
                )

            elif plan.operation in {OperationType.TOP_N, OperationType.BOTTOM_N}:
                col = plan.columns[0]
                ascending = plan.operation == OperationType.BOTTOM_N
                if plan.group_by:
                    agg = plan.aggregations.get(col, AggregationFunction.SUM)
                    grouped = df.groupby(plan.group_by)[col].agg(agg.value).reset_index()
                    grouped = grouped.sort_values(col, ascending=ascending)
                else:
                    grouped = df.sort_values(col, ascending=ascending)
                grouped = grouped.head(plan.limit or 10)
                rows = grouped.to_dict(orient="records")
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=list(grouped.columns),
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.PANDAS,
                )

            elif plan.operation in {OperationType.DATE_GROUP, OperationType.TIME_SERIES_AGG}:
                date_col = plan.date_column
                metric_col = plan.columns[0] if plan.columns else None
                freq = plan.frequency or "month"
                agg = plan.aggregations.get(metric_col, AggregationFunction.SUM) if metric_col else AggregationFunction.COUNT

                df = df.copy()
                df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
                df = df.dropna(subset=[date_col])

                if metric_col:
                    df = df.groupby(df[date_col].dt.to_period(freq))[metric_col].agg(agg.value).reset_index()
                    df[date_col] = df[date_col].astype(str)
                else:
                    df = df.groupby(df[date_col].dt.to_period(freq)).size().reset_index(name="count")
                    df[date_col] = df[date_col].astype(str)

                df = df.sort_values(date_col)
                rows = df.to_dict(orient="records")
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=list(df.columns),
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.PANDAS,
                )

            elif plan.operation == OperationType.DISTINCT_COUNT:
                col = plan.columns[0]
                count = df[col].nunique()
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=[{"column": col, "distinct_count": int(count)}],
                    row_count=1,
                    column_names=["column", "distinct_count"],
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.PANDAS,
                )

            elif plan.operation == OperationType.CORRELATION:
                col1 = plan.columns[0]
                col2 = plan.parameters.get("other_column", "")
                corr = pd.to_numeric(df[col1], errors="coerce").corr(pd.to_numeric(df[col2], errors="coerce"))
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=[{"column": col1, "other_column": col2, "correlation": float(corr)}],
                    row_count=1,
                    column_names=["column", "other_column", "correlation"],
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.PANDAS,
                )

            elif plan.operation == OperationType.MISSING_ANALYSIS:
                cols_to_check = plan.columns if plan.columns else list(df.columns)
                rows = []
                for col in cols_to_check:
                    missing = int(df[col].isna().sum())
                    rows.append({
                        "column": col,
                        "missing": missing,
                        "missing_percent": round(missing / len(df) * 100, 2) if len(df) > 0 else 0.0,
                    })
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=["column", "missing", "missing_percent"],
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.PANDAS,
                )

            elif plan.operation == OperationType.DUPLICATE_ANALYSIS:
                dup_count = int(df.duplicated().sum())
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=[{"duplicate_rows": dup_count}],
                    row_count=1,
                    column_names=["duplicate_rows"],
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.PANDAS,
                )

            elif plan.operation == OperationType.OUTLIER_SUMMARY:
                col = plan.columns[0]
                series = pd.to_numeric(df[col], errors="coerce").dropna()
                q1 = series.quantile(0.25)
                q3 = series.quantile(0.75)
                iqr = q3 - q1
                low = q1 - 1.5 * iqr
                high = q3 + 1.5 * iqr
                outlier_count = int(((series < low) | (series > high)).sum())
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=[{"column": col, "lower": float(low), "upper": float(high), "count": outlier_count, "q1": float(q1), "q3": float(q3), "iqr": float(iqr)}],
                    row_count=1,
                    column_names=["column", "lower", "upper", "count", "q1", "q3", "iqr"],
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.PANDAS,
                )

            elif plan.operation == OperationType.RANKED_GROUP:
                metric_col = plan.columns[0]
                agg = plan.aggregations.get(metric_col, AggregationFunction.SUM)
                result = df.groupby(plan.group_by)[metric_col].agg(agg.value).reset_index()
                result = result.sort_values(metric_col, ascending=plan.sort_ascending)
                if plan.limit:
                    result = result.head(plan.limit)
                rows = result.to_dict(orient="records")
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=list(result.columns),
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.PANDAS,
                )

            elif plan.operation == OperationType.PCT_SHARE:
                group_col = plan.group_by[0]
                metric_col = plan.columns[0]
                result = df.groupby(group_col)[metric_col].sum().reset_index(name="value")
                total = result["value"].sum()
                result["percentage"] = result["value"] / total * 100
                result = result.sort_values("percentage", ascending=False)
                if plan.limit:
                    result = result.head(plan.limit)
                rows = result.to_dict(orient="records")
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=list(result.columns),
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.PANDAS,
                )

            elif plan.operation == OperationType.PIVOT:
                row_col = plan.parameters.get("row", "")
                col_col = plan.parameters.get("column", "")
                metric_col = plan.columns[0]
                agg = plan.aggregations.get(metric_col, AggregationFunction.SUM)
                result = df.pivot_table(index=row_col, columns=col_col, values=metric_col, aggfunc=agg.value, fill_value=0).reset_index()
                rows = result.to_dict(orient="records")
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=list(result.columns),
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.PANDAS,
                )

            elif plan.operation == OperationType.GROWTH_RATE:
                if not plan.date_column:
                    raise QuerySafetyError("GROWTH_RATE requires date_column")
                freq = plan.frequency or "month"
                metric_col = plan.columns[0]
                agg = plan.aggregations.get(metric_col, AggregationFunction.SUM)

                df = df.copy()
                df[plan.date_column] = pd.to_datetime(df[plan.date_column], errors="coerce")
                df = df.dropna(subset=[plan.date_column])
                df = df.groupby(df[plan.date_column].dt.to_period(freq))[metric_col].agg(agg.value).reset_index()
                df[plan.date_column] = df[plan.date_column].astype(str)
                df = df.sort_values(plan.date_column)

                rows = []
                for i, row in df.iloc[1:].iterrows():
                    prev_val = df.iloc[i-1][agg.value]
                    curr_val = row[agg.value]
                    growth = (curr_val - prev_val) / abs(prev_val) * 100 if prev_val != 0 else None
                    row_dict = row.to_dict()
                    row_dict["growth_percent"] = growth
                    rows.append(row_dict)

                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=list(rows[0].keys()) if rows else [],
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.PANDAS,
                )

            elif plan.operation == OperationType.CUMULATIVE:
                if not plan.date_column:
                    raise QuerySafetyError("CUMULATIVE requires date_column")
                freq = plan.frequency or "month"
                metric_col = plan.columns[0]
                agg = plan.aggregations.get(metric_col, AggregationFunction.SUM)

                df = df.copy()
                df[plan.date_column] = pd.to_datetime(df[plan.date_column], errors="coerce")
                df = df.dropna(subset=[plan.date_column])
                df = df.groupby(df[plan.date_column].dt.to_period(freq))[metric_col].agg(agg.value).reset_index()
                df[plan.date_column] = df[plan.date_column].astype(str)
                df = df.sort_values(plan.date_column)
                df["cumulative"] = df[agg.value].cumsum()

                rows = df.to_dict(orient="records")
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=list(df.columns),
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.PANDAS,
                )

            else:
                raise QuerySafetyError(f"Operation {plan.operation.value} not implemented")

        except QuerySafetyError:
            raise
        except Exception as e:
            self._report_progress(
                ProgressState(
                    operation_id=f"exec_{plan.operation.value}_{plan.dataset_name}",
                    operation=plan.operation.value,
                    dataset_name=plan.dataset_name,
                    status="failed",
                    error=str(e),
                )
            )
            return AnalyticalResult(
                success=False,
                operation=plan.operation,
                dataset_name=plan.dataset_name,
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
                engine_used=EngineType.PANDAS,
                error=str(e),
            )
        finally:
            self._report_progress(
                ProgressState(
                    operation_id=f"exec_{plan.operation.value}_{plan.dataset_name}",
                    operation=plan.operation.value,
                    dataset_name=plan.dataset_name,
                    status="completed",
                    progress_percent=100.0,
                    completed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                )
            )

    def sample_table(self, name: str, method: SamplingMethod, fraction: float, seed: int | None = None) -> str:
        """Create a sampled DataFrame."""
        table_name = sanitize_table_name(name)
        sampled_name = f"{table_name}_sampled_{int(time.time())}"
        df = self._get_df(table_name)

        if method == SamplingMethod.SYSTEM:
            sampled = df.sample(frac=fraction, replace=False, random_state=seed)
        elif method == SamplingMethod.BERNOULLI:
            sampled = df.sample(frac=fraction, replace=False, random_state=seed)
        elif method == SamplingMethod.RESERVOIR:
            n = int(len(df) * fraction)
            sampled = df.sample(n=n, replace=False, random_state=seed)

        self._dataframes[sampled_name] = sampled
        self._schemas[sampled_name] = self._schemas[table_name]
        self._sources[sampled_name] = f"sample_of_{table_name}"

        return sampled_name

    def close(self):
        """Close the engine and clear all data."""
        self._dataframes.clear()
        self._schemas.clear()
        self._sources.clear()