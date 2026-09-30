"""DuckDB engine implementation for the Large-Scale Analytics Engine."""

import time
from pathlib import Path
from typing import Any, Optional
import duckdb

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
    ParameterizedQueryBuilder,
    QuerySafetyError,
    sanitize_table_name,
)


def _quote_identifier(value: str) -> str:
    """Quote a schema-derived DuckDB identifier without changing its name."""
    return '"' + value.replace('"', '""') + '"'


class DuckDBEngine:
    """DuckDB-backed analytical engine for large-scale data processing."""

    def __init__(
        self,
        database_path: str = ":memory:",
        memory_limit: str = "4GB",
        threads: int = 4,
        enable_progress: bool = False,
    ):
        """Initialize the DuckDB engine.

        Args:
            database_path: Path to DuckDB database file, or :memory: for in-memory
            memory_limit: Memory limit for DuckDB operations
            threads: Number of threads for parallel execution
            enable_progress: Whether to enable progress reporting
        """
        self.database_path = database_path
        self.memory_limit = memory_limit
        self.threads = threads
        self.enable_progress = enable_progress
        self._connection: Optional[duckdb.DuckDBPyConnection] = None
        self._registered_tables: dict[str, str] = {}
        self._table_schemas: dict[str, list[ColumnInfo]] = {}
        self._table_row_counts: dict[str, int] = {}
        self._progress_callback = None

    def connect(self) -> duckdb.DuckDBPyConnection:
        """Get or create DuckDB connection."""
        if self._connection is None:
            self._connection = duckdb.connect(self.database_path)
            self._connection.execute(f"SET memory_limit = '{self.memory_limit}'")
            self._connection.execute(f"SET threads = {self.threads}")
            self._connection.execute("SET preserve_insertion_order = false")
        return self._connection

    def close(self):
        """Close the connection."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None
            self._registered_tables.clear()
            self._table_schemas.clear()
            self._table_row_counts.clear()

    def set_progress_callback(self, callback):
        """Set a callback for progress updates."""
        self._progress_callback = callback

    def _report_progress(self, state: ProgressState):
        """Report progress if callback is set."""
        if self._progress_callback:
            self._progress_callback(state)

    def register_csv(
        self,
        name: str,
        path: str | Path,
        delimiter: str = ",",
        header: bool = True,
        sample_size: int = 10000,
    ) -> DatasetMetadata:
        """Register a CSV file as a table.

        Args:
            name: Table name
            path: Path to CSV file
            delimiter: CSV delimiter
            header: Whether CSV has header row
            sample_size: Rows to sample for schema inference

        Returns:
            DatasetMetadata for the registered table
        """
        conn = self.connect()
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
            path_str = str(path).replace("'", "''")
            header_str = "true" if header else "false"
            conn.execute(f"CREATE OR REPLACE VIEW {table_name} AS SELECT * FROM read_csv_auto('{path_str}', delim='{delimiter}', header={header_str})")

            row_count_result = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
            row_count = row_count_result[0] if row_count_result else 0

            self._report_progress(
                ProgressState(
                    operation_id=f"load_{table_name}",
                    operation="load_csv",
                    dataset_name=table_name,
                    status="schema_inference",
                    processed_rows=row_count,
                    total_rows=row_count,
                    progress_percent=50.0,
                    message="Inferring schema...",
                )
            )

            schema_info = conn.execute(f"DESCRIBE {table_name}").fetchall()
            columns = []
            for col_info in schema_info:
                col_name = col_info[0]
                col_type = col_info[1]
                nullable = col_info[2] == "YES" if len(col_info) > 2 else True

                quoted_col = _quote_identifier(col_name)
                sample_result = conn.execute(f"SELECT {quoted_col} FROM {table_name} LIMIT ?", [sample_size]).fetchall()
                sample_values = [row[0] for row in sample_result] if sample_result else []

                null_result = conn.execute(f"SELECT COUNT(*) FROM {table_name} WHERE {quoted_col} IS NULL").fetchone()
                null_count = null_result[0] if null_result else 0

                unique_result = conn.execute(f"SELECT COUNT(DISTINCT {quoted_col}) FROM {table_name}").fetchone()
                unique_count = unique_result[0] if unique_result else 0

                min_result = conn.execute(f"SELECT MIN({quoted_col}) FROM {table_name}").fetchone()
                min_val = min_result[0] if min_result else None

                max_result = conn.execute(f"SELECT MAX({quoted_col}) FROM {table_name}").fetchone()
                max_val = max_result[0] if max_result else None

                columns.append(ColumnInfo(
                    name=col_name,
                    dtype=col_type,
                    nullable=nullable,
                    unique_count=unique_count,
                    null_count=null_count,
                    min_value=min_val,
                    max_value=max_val,
                    sample_values=sample_values[:5],
                ))

            self._registered_tables[table_name] = str(path)
            self._table_schemas[table_name] = columns
            self._table_row_counts[table_name] = row_count

            load_time = (time.perf_counter() - start_time) * 1000

            metadata = DatasetMetadata(
                name=table_name,
                source_type=SourceType.CSV,
                source_path=str(path),
                row_count=row_count,
                column_count=len(columns),
                columns=tuple(columns),
                estimated_size_bytes=file_size,
                actual_size_bytes=file_size,
                engine_used=EngineType.DUCKDB,
                processing_mode=ProcessingMode.DISK_BACKED,
                analysis_duration_ms=load_time,
            )

            self._report_progress(
                ProgressState(
                    operation_id=f"load_{table_name}",
                    operation="load_csv",
                    dataset_name=table_name,
                    status="completed",
                    processed_rows=row_count,
                    total_rows=row_count,
                    progress_percent=100.0,
                    message=f"Loaded {row_count:,} rows",
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
        """Register a Parquet file as a table."""
        conn = self.connect()
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
            path_str = str(path).replace("'", "''")
            conn.execute(f"CREATE OR REPLACE VIEW {table_name} AS SELECT * FROM read_parquet('{path_str}')")

            row_count_result = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
            row_count = row_count_result[0] if row_count_result else 0

            schema_info = conn.execute(f"DESCRIBE {table_name}").fetchall()
            columns = []
            for col_info in schema_info:
                col_name = col_info[0]
                col_type = col_info[1]
                nullable = col_info[2] == "YES" if len(col_info) > 2 else True

                quoted_col = _quote_identifier(col_name)
                sample_result = conn.execute(f"SELECT {quoted_col} FROM {table_name} LIMIT ?", [sample_size]).fetchall()
                sample_values = [row[0] for row in sample_result] if sample_result else []

                null_result = conn.execute(f"SELECT COUNT(*) FROM {table_name} WHERE {quoted_col} IS NULL").fetchone()
                null_count = null_result[0] if null_result else 0

                unique_result = conn.execute(f"SELECT COUNT(DISTINCT {quoted_col}) FROM {table_name}").fetchone()
                unique_count = unique_result[0] if unique_result else 0

                min_result = conn.execute(f"SELECT MIN({quoted_col}) FROM {table_name}").fetchone()
                min_val = min_result[0] if min_result else None

                max_result = conn.execute(f"SELECT MAX({quoted_col}) FROM {table_name}").fetchone()
                max_val = max_result[0] if max_result else None

                columns.append(ColumnInfo(
                    name=col_name,
                    dtype=col_type,
                    nullable=nullable,
                    unique_count=unique_count,
                    null_count=null_count,
                    min_value=min_val,
                    max_value=max_val,
                    sample_values=sample_values[:5],
                ))

            self._registered_tables[table_name] = str(path)
            self._table_schemas[table_name] = columns
            self._table_row_counts[table_name] = row_count

            load_time = (time.perf_counter() - start_time) * 1000

            metadata = DatasetMetadata(
                name=table_name,
                source_type=SourceType.PARQUET,
                source_path=str(path),
                row_count=row_count,
                column_count=len(columns),
                columns=tuple(columns),
                estimated_size_bytes=file_size,
                actual_size_bytes=file_size,
                engine_used=EngineType.DUCKDB,
                processing_mode=ProcessingMode.DISK_BACKED,
                analysis_duration_ms=load_time,
            )

            self._report_progress(
                ProgressState(
                    operation_id=f"load_{table_name}",
                    operation="load_parquet",
                    dataset_name=table_name,
                    status="completed",
                    processed_rows=row_count,
                    total_rows=row_count,
                    progress_percent=100.0,
                    message=f"Loaded {row_count:,} rows",
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
        sheet_name: str = 0,
    ) -> DatasetMetadata:
        """Register an Excel sheet as a table (loads into memory first)."""
        import pandas as pd

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

            row_count = len(df)

            self._report_progress(
                ProgressState(
                    operation_id=f"load_{table_name}",
                    operation="load_xlsx",
                    dataset_name=table_name,
                    status="converting",
                    processed_rows=row_count,
                    total_rows=row_count,
                    progress_percent=50.0,
                    message="Converting to DuckDB...",
                )
            )

            conn = self.connect()
            conn.register("temp_df", df)
            conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM temp_df")
            conn.unregister("temp_df")

            schema_info = conn.execute(f"DESCRIBE {table_name}").fetchall()
            columns = []
            for col_info in schema_info:
                col_name = col_info[0]
                col_type = col_info[1]
                nullable = col_info[2] == "YES" if len(col_info) > 2 else True

                sample_values = df[col_name].head(5).tolist()
                null_count = int(df[col_name].isna().sum())
                unique_count = int(df[col_name].nunique())
                min_val = df[col_name].min() if not df[col_name].isna().all() else None
                max_val = df[col_name].max() if not df[col_name].isna().all() else None

                columns.append(ColumnInfo(
                    name=col_name,
                    dtype=col_type,
                    nullable=nullable,
                    unique_count=unique_count,
                    null_count=null_count,
                    min_value=min_val,
                    max_value=max_val,
                    sample_values=sample_values,
                ))

            self._registered_tables[table_name] = str(path)
            self._table_schemas[table_name] = columns
            self._table_row_counts[table_name] = row_count

            load_time = (time.perf_counter() - start_time) * 1000

            metadata = DatasetMetadata(
                name=table_name,
                source_type=SourceType.XLSX,
                source_path=str(path),
                row_count=row_count,
                column_count=len(columns),
                columns=tuple(columns),
                estimated_size_bytes=file_size,
                actual_size_bytes=file_size,
                engine_used=EngineType.DUCKDB,
                processing_mode=ProcessingMode.IN_MEMORY,
                analysis_duration_ms=load_time,
            )

            self._report_progress(
                ProgressState(
                    operation_id=f"load_{table_name}",
                    operation="load_xlsx",
                    dataset_name=table_name,
                    status="completed",
                    processed_rows=row_count,
                    total_rows=row_count,
                    progress_percent=100.0,
                    message=f"Loaded {row_count:,} rows",
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
        """Register a pandas/polars DataFrame as a table."""
        import pandas as pd

        table_name = sanitize_table_name(name)

        if hasattr(df, "to_pandas"):
            pdf = df.to_pandas()
        elif isinstance(df, pd.DataFrame):
            pdf = df
        else:
            pdf = pd.DataFrame(df)

        row_count = len(pdf)
        file_size = pdf.memory_usage(deep=True).sum()

        start_time = time.perf_counter()

        conn = self.connect()
        conn.register("temp_df", pdf)
        conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM temp_df")
        conn.unregister("temp_df")

        schema_info = conn.execute(f"DESCRIBE {table_name}").fetchall()
        columns = []
        for col_info in schema_info:
            col_name = col_info[0]
            col_type = col_info[1]
            nullable = col_info[2] == "YES" if len(col_info) > 2 else True

            sample_values = pdf[col_name].head(5).tolist()
            null_count = int(pdf[col_name].isna().sum())
            unique_count = int(pdf[col_name].nunique())
            min_val = pdf[col_name].min() if not pdf[col_name].isna().all() else None
            max_val = pdf[col_name].max() if not pdf[col_name].isna().all() else None

            columns.append(ColumnInfo(
                name=col_name,
                dtype=col_type,
                nullable=nullable,
                unique_count=unique_count,
                null_count=null_count,
                min_value=min_val,
                max_value=max_val,
                sample_values=sample_values,
            ))

        self._registered_tables[table_name] = "dataframe"
        self._table_schemas[table_name] = columns
        self._table_row_counts[table_name] = row_count

        load_time = (time.perf_counter() - start_time) * 1000

        return DatasetMetadata(
            name=table_name,
            source_type=SourceType.DATAFRAME,
            row_count=row_count,
            column_count=len(columns),
            columns=tuple(columns),
            estimated_size_bytes=file_size,
            actual_size_bytes=file_size,
            engine_used=EngineType.DUCKDB,
            processing_mode=ProcessingMode.IN_MEMORY,
            analysis_duration_ms=load_time,
        )

    def get_metadata(self, name: str) -> Optional[DatasetMetadata]:
        """Get metadata for a registered table."""
        table_name = sanitize_table_name(name)
        if table_name not in self._registered_tables:
            return None

        columns = self._table_schemas.get(table_name, [])
        row_count = self._table_row_counts.get(table_name, 0)

        return DatasetMetadata(
            name=table_name,
            source_type=SourceType.CSV,
            source_path=self._registered_tables.get(table_name),
            row_count=row_count,
            column_count=len(columns),
            columns=tuple(columns),
            engine_used=EngineType.DUCKDB,
            processing_mode=ProcessingMode.DISK_BACKED,
        )

    def list_tables(self) -> list[str]:
        """List all registered tables."""
        return list(self._registered_tables.keys())

    def drop_table(self, name: str) -> bool:
        """Drop a registered table."""
        table_name = sanitize_table_name(name)
        if table_name in self._registered_tables:
            conn = self.connect()
            conn.execute(f"DROP VIEW IF EXISTS {table_name}")
            del self._registered_tables[table_name]
            self._table_schemas.pop(table_name, None)
            self._table_row_counts.pop(table_name, None)
            return True
        return False

    def execute(self, plan: ValidatedPlan) -> AnalyticalResult:
        """Execute a validated analytical plan."""
        conn = self.connect()
        builder = ParameterizedQueryBuilder(EngineType.DUCKDB)

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
            table_name = sanitize_table_name(plan.dataset_name)
            available_columns = {c.name for c in self._table_schemas.get(table_name, [])}

            if plan.operation == OperationType.ROW_COUNT:
                query, params = builder.build_count_query(table_name, plan.filters)
                result = conn.execute(query, params).fetchone()
                row_count = result[0] if result else 0
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=[{"count": row_count}],
                    row_count=1,
                    column_names=["count"],
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.DUCKDB,
                )

            elif plan.operation == OperationType.COLUMN_SCHEMA:
                columns = self._table_schemas.get(table_name, [])
                rows = [c.to_dict() for c in columns]
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=list(rows[0].keys()) if rows else [],
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.DUCKDB,
                )

            elif plan.operation == OperationType.DESCRIPTIVE_STATS:
                query, params = builder.build_descriptive_stats_query(table_name, plan.columns, plan.filters)
                result = conn.execute(query, params).fetchall()
                cols = [desc[0] for desc in conn.description]
                rows = [dict(zip(cols, row)) for row in result]
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=cols,
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.DUCKDB,
                )

            elif plan.operation in {OperationType.SUM, OperationType.MEAN, OperationType.MIN, OperationType.MAX, OperationType.COUNT}:
                agg_map = {
                    OperationType.SUM: AggregationFunction.SUM,
                    OperationType.MEAN: AggregationFunction.MEAN,
                    OperationType.MIN: AggregationFunction.MIN,
                    OperationType.MAX: AggregationFunction.MAX,
                    OperationType.COUNT: AggregationFunction.COUNT,
                }
                agg = agg_map[plan.operation]
                col = plan.columns[0]
                where_clause, params = builder._build_where_clause(plan.filters)
                query = f"SELECT {agg.value}({col}) AS {col}_{agg.value} FROM {table_name}"
                if where_clause:
                    query += f" WHERE {where_clause}"
                result = conn.execute(query, params).fetchall()
                cols = [desc[0] for desc in conn.description]
                rows = [dict(zip(cols, row)) for row in result]
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=cols,
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.DUCKDB,
                )

            elif plan.operation in {OperationType.GROUPBY, OperationType.MULTI_GROUPBY}:
                query, params = builder.build_groupby_query(
                    table_name,
                    plan.group_by,
                    plan.aggregations,
                    plan.filters,
                    plan.sort_by,
                    plan.sort_ascending,
                    plan.limit,
                )
                result = conn.execute(query, params).fetchall()
                cols = [desc[0] for desc in conn.description]
                rows = [dict(zip(cols, row)) for row in result]
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=cols,
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.DUCKDB,
                )

            elif plan.operation == OperationType.FILTER:
                query, params = builder.build_filter_query(table_name, plan.columns, plan.filters, plan.limit)
                result = conn.execute(query, params).fetchall()
                cols = [desc[0] for desc in conn.description]
                rows = [dict(zip(cols, row)) for row in result]
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=cols,
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.DUCKDB,
                )

            elif plan.operation in {OperationType.TOP_N, OperationType.BOTTOM_N}:
                ascending = plan.operation == OperationType.BOTTOM_N
                metric_col = plan.columns[0] if plan.columns else ""

                if plan.group_by:
                    # TOP N with GROUP BY - need to aggregate first
                    agg = plan.aggregations.get(metric_col, AggregationFunction.SUM)
                    agg_func = f"{agg.value}({metric_col})"
                    where_clause, params = builder._build_where_clause(plan.filters)

                    query = f"SELECT {', '.join(plan.group_by)}, {agg_func} AS {metric_col}_{agg.value} FROM {table_name}"
                    if where_clause:
                        query += f" WHERE {where_clause}"
                    query += f" GROUP BY {', '.join(plan.group_by)}"
                    order = "ASC" if ascending else "DESC"
                    query += f" ORDER BY {metric_col}_{agg.value} {order} LIMIT {plan.limit or 10}"
                else:
                    query, params = builder.build_top_n_query(
                        table_name,
                        metric_col,
                        None,
                        ascending,
                        plan.limit or 10,
                        plan.filters,
                    )

                result = conn.execute(query, params).fetchall()
                cols = [desc[0] for desc in conn.description]
                rows = [dict(zip(cols, row)) for row in result]
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=cols,
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.DUCKDB,
                )

            elif plan.operation in {OperationType.DATE_GROUP, OperationType.TIME_SERIES_AGG}:
                agg = AggregationFunction(plan.parameters.get("aggregation", "sum"))
                query, params = builder.build_date_group_query(
                    table_name,
                    plan.date_column or "",
                    plan.columns[0] if plan.columns else None,
                    plan.frequency or "month",
                    agg,
                    plan.filters,
                )
                result = conn.execute(query, params).fetchall()
                cols = [desc[0] for desc in conn.description]
                rows = [dict(zip(cols, row)) for row in result]
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=cols,
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.DUCKDB,
                )

            elif plan.operation == OperationType.DISTINCT_COUNT:
                col = plan.columns[0] if plan.columns else ""
                query = f"SELECT COUNT(DISTINCT {col}) AS distinct_count FROM {table_name}"
                where_clause, params = builder._build_where_clause(plan.filters)
                if where_clause:
                    query += f" WHERE {where_clause}"
                result = conn.execute(query, params).fetchone()
                count = result[0] if result else 0
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=[{"column": col, "distinct_count": count}],
                    row_count=1,
                    column_names=["column", "distinct_count"],
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.DUCKDB,
                )

            elif plan.operation == OperationType.CORRELATION:
                col1 = plan.columns[0] if plan.columns else ""
                col2 = plan.parameters.get("other_column", "")
                query = f"SELECT CORR({col1}, {col2}) AS correlation FROM {table_name} WHERE {col1} IS NOT NULL AND {col2} IS NOT NULL"
                result = conn.execute(query).fetchone()
                corr = result[0] if result else 0.0
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=[{"column": col1, "other_column": col2, "correlation": corr}],
                    row_count=1,
                    column_names=["column", "other_column", "correlation"],
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.DUCKDB,
                )

            elif plan.operation == OperationType.MISSING_ANALYSIS:
                if plan.columns:
                    cols_to_check = plan.columns
                else:
                    cols_to_check = [c.name for c in self._table_schemas.get(table_name, [])]

                rows = []
                for col in cols_to_check:
                    query = f"SELECT COUNT(*) AS total, COUNT({col}) AS non_null FROM {table_name}"
                    result = conn.execute(query).fetchone()
                    total, non_null = result if result else (0, 0)
                    missing = total - non_null
                    rows.append({
                        "column": col,
                        "missing": missing,
                        "missing_percent": round(missing / total * 100, 2) if total > 0 else 0.0,
                    })
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=["column", "missing", "missing_percent"],
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.DUCKDB,
                )

            elif plan.operation == OperationType.DUPLICATE_ANALYSIS:
                query = f"SELECT COUNT(*) - COUNT(DISTINCT *) AS duplicate_rows FROM {table_name}"
                result = conn.execute(query).fetchone()
                dup_count = result[0] if result else 0
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=[{"duplicate_rows": dup_count}],
                    row_count=1,
                    column_names=["duplicate_rows"],
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.DUCKDB,
                )

            elif plan.operation == OperationType.OUTLIER_SUMMARY:
                col = plan.columns[0] if plan.columns else ""
                q1_query = f"SELECT QUANTILE({col}, 0.25) FROM {table_name}"
                q3_query = f"SELECT QUANTILE({col}, 0.75) FROM {table_name}"
                q1_result = conn.execute(q1_query).fetchone()
                q3_result = conn.execute(q3_query).fetchone()
                q1 = q1_result[0] if q1_result else 0
                q3 = q3_result[0] if q3_result else 0
                iqr = q3 - q1
                low = q1 - 1.5 * iqr
                high = q3 + 1.5 * iqr

                count_query = f"SELECT COUNT(*) FROM {table_name} WHERE {col} < ? OR {col} > ?"
                count_result = conn.execute(count_query, [low, high]).fetchone()
                outlier_count = count_result[0] if count_result else 0

                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=[{"column": col, "lower": low, "upper": high, "count": outlier_count, "q1": q1, "q3": q3, "iqr": iqr}],
                    row_count=1,
                    column_names=["column", "lower", "upper", "count", "q1", "q3", "iqr"],
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.DUCKDB,
                )

            elif plan.operation == OperationType.RANKED_GROUP:
                agg = plan.aggregations.get(plan.columns[0], AggregationFunction.SUM) if plan.columns else AggregationFunction.SUM
                query, params = builder.build_groupby_query(
                    table_name,
                    plan.group_by,
                    {plan.columns[0]: agg},
                    plan.filters,
                    [f"{plan.columns[0]}_{agg.value}"],
                    not plan.sort_ascending,
                    plan.limit or 10,
                )
                result = conn.execute(query, params).fetchall()
                cols = [desc[0] for desc in conn.description]
                rows = [dict(zip(cols, row)) for row in result]
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=cols,
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.DUCKDB,
                )

            elif plan.operation == OperationType.PCT_SHARE:
                group_col = plan.group_by[0] if plan.group_by else ""
                metric_col = plan.columns[0] if plan.columns else ""
                query = f"""
                    SELECT {group_col}, SUM({metric_col}) AS value,
                           SUM({metric_col}) * 100.0 / SUM(SUM({metric_col})) OVER () AS percentage
                    FROM {table_name}
                    WHERE {metric_col} IS NOT NULL
                    GROUP BY {group_col}
                    ORDER BY percentage DESC
                """
                where_clause, params = builder._build_where_clause(plan.filters)
                if where_clause:
                    query = query.replace("WHERE", f"WHERE {where_clause} AND")
                if plan.limit:
                    query += f" LIMIT {plan.limit}"
                result = conn.execute(query, params).fetchall()
                cols = [desc[0] for desc in conn.description]
                rows = [dict(zip(cols, row)) for row in result]
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=cols,
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.DUCKDB,
                )

            elif plan.operation == OperationType.PIVOT:
                row_col = plan.parameters.get("row", "")
                col_col = plan.parameters.get("column", "")
                metric_col = plan.columns[0] if plan.columns else ""
                agg = plan.aggregations.get(metric_col, AggregationFunction.SUM)

                query = f"""
                    PIVOT {table_name}
                    ON {col_col}
                    USING {agg.value}({metric_col})
                    GROUP BY {row_col}
                """
                where_clause, params = builder._build_where_clause(plan.filters)
                if where_clause:
                    query = f"PIVOT (SELECT * FROM {table_name} WHERE {where_clause}) ON {col_col} USING {agg.value}({metric_col}) GROUP BY {row_col}"

                result = conn.execute(query, params).fetchall()
                cols = [desc[0] for desc in conn.description]
                rows = [dict(zip(cols, row)) for row in result]
                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=rows,
                    row_count=len(rows),
                    column_names=cols,
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.DUCKDB,
                )

            elif plan.operation == OperationType.GROWTH_RATE:
                if not plan.date_column:
                    raise QuerySafetyError("GROWTH_RATE requires date_column")
                freq = plan.frequency or "month"
                agg = plan.aggregations.get(plan.columns[0], AggregationFunction.SUM) if plan.columns else AggregationFunction.SUM

                date_query, params = builder.build_date_group_query(
                    table_name, plan.date_column, plan.columns[0] if plan.columns else None, freq, agg, plan.filters
                )
                result = conn.execute(date_query, params).fetchall()
                cols = [desc[0] for desc in conn.description]
                rows = [dict(zip(cols, row)) for row in result]

                growth_rows = []
                for i, row in enumerate(rows[1:], 1):
                    prev_val = rows[i-1].get("sum", rows[i-1].get("mean", rows[i-1].get("count", 0)))
                    curr_val = row.get("sum", row.get("mean", row.get("count", 0)))
                    if prev_val != 0:
                        growth = (curr_val - prev_val) / abs(prev_val) * 100
                    else:
                        growth = None
                    growth_row = row.copy()
                    growth_row["growth_percent"] = growth
                    growth_rows.append(growth_row)

                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=growth_rows,
                    row_count=len(growth_rows),
                    column_names=list(growth_rows[0].keys()) if growth_rows else [],
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.DUCKDB,
                )

            elif plan.operation == OperationType.CUMULATIVE:
                if not plan.date_column:
                    raise QuerySafetyError("CUMULATIVE requires date_column")
                freq = plan.frequency or "month"
                agg = plan.aggregations.get(plan.columns[0], AggregationFunction.SUM) if plan.columns else AggregationFunction.SUM

                date_query, params = builder.build_date_group_query(
                    table_name, plan.date_column, plan.columns[0] if plan.columns else None, freq, agg, plan.filters
                )
                result = conn.execute(date_query, params).fetchall()
                cols = [desc[0] for desc in conn.description]
                rows = [dict(zip(cols, row)) for row in result]

                cumulative_rows = []
                total = 0
                for row in rows:
                    val = row.get("sum", row.get("mean", row.get("count", 0)))
                    total += val
                    cum_row = row.copy()
                    cum_row["cumulative"] = total
                    cumulative_rows.append(cum_row)

                return AnalyticalResult(
                    success=True,
                    operation=plan.operation,
                    dataset_name=plan.dataset_name,
                    rows=cumulative_rows,
                    row_count=len(cumulative_rows),
                    column_names=list(cumulative_rows[0].keys()) if cumulative_rows else [],
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    engine_used=EngineType.DUCKDB,
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
                engine_used=EngineType.DUCKDB,
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
        """Create a sampled view of a table.

        Args:
            name: Source table name
            method: Sampling method
            fraction: Sampling fraction (0.0 to 1.0)
            seed: Random seed for reproducibility

        Returns:
            Name of the sampled view
        """
        table_name = sanitize_table_name(name)
        sampled_name = f"{table_name}_sampled_{int(time.time())}"

        conn = self.connect()

        if method == SamplingMethod.SYSTEM:
            conn.execute(f"CREATE OR REPLACE VIEW {sampled_name} AS SELECT * FROM {table_name} USING SAMPLE {fraction * 100}% (SYSTEM)")
        elif method == SamplingMethod.BERNOULLI:
            conn.execute(f"CREATE OR REPLACE VIEW {sampled_name} AS SELECT * FROM {table_name} USING SAMPLE {fraction * 100}% (BERNOULLI)")
        elif method == SamplingMethod.RESERVOIR:
            row_count = self._table_row_counts.get(table_name, 0)
            sample_size = int(row_count * fraction)
            conn.execute(f"CREATE OR REPLACE VIEW {sampled_name} AS SELECT * FROM {table_name} USING SAMPLE {sample_size} ROWS (RESERVOIR)")

        # Copy schema and update row count
        self._table_schemas[sampled_name] = self._table_schemas[table_name]
        self._table_row_counts[sampled_name] = int(self._table_row_counts.get(table_name, 0) * fraction)
        self._registered_tables[sampled_name] = f"sample_of_{table_name}"

        return sampled_name