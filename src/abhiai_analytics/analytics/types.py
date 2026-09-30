"""Type definitions for the Large-Scale Analytics Engine."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional
import json


class EngineType(str, Enum):
    """Supported analytical engine backends."""

    DUCKDB = "duckdb"
    PANDAS = "pandas"


class SourceType(str, Enum):
    """Supported data source types."""

    CSV = "csv"
    PARQUET = "parquet"
    XLSX = "xlsx"
    DATAFRAME = "dataframe"


class ProcessingMode(str, Enum):
    """Processing mode for the engine."""

    IN_MEMORY = "in_memory"
    DISK_BACKED = "disk_backed"
    STREAMING = "streaming"


class OperationType(str, Enum):
    """Supported analytical operations."""

    ROW_COUNT = "row_count"
    COLUMN_SCHEMA = "column_schema"
    DESCRIPTIVE_STATS = "descriptive_stats"
    SUM = "sum"
    MEAN = "mean"
    MIN = "min"
    MAX = "max"
    COUNT = "count"
    DISTINCT_COUNT = "distinct_count"
    GROUPBY = "groupby"
    MULTI_GROUPBY = "multi_groupby"
    FILTER = "filter"
    SORT = "sort"
    TOP_N = "top_n"
    BOTTOM_N = "bottom_n"
    DATE_GROUP = "date_group"
    TIME_SERIES_AGG = "time_series_agg"
    GROWTH_RATE = "growth_rate"
    CUMULATIVE = "cumulative"
    PCT_CHANGE = "pct_change"
    PCT_SHARE = "pct_share"
    PIVOT = "pivot"
    CORRELATION = "correlation"
    MISSING_ANALYSIS = "missing_analysis"
    DUPLICATE_ANALYSIS = "duplicate_analysis"
    OUTLIER_SUMMARY = "outlier_summary"
    RANKED_GROUP = "ranked_group"
    KPI_CALCULATION = "kpi_calculation"


class AggregationFunction(str, Enum):
    """Supported aggregation functions."""

    SUM = "sum"
    MEAN = "mean"
    MEDIAN = "median"
    MIN = "min"
    MAX = "max"
    COUNT = "count"
    DISTINCT_COUNT = "distinct_count"
    STD = "std"
    VAR = "var"
    FIRST = "first"
    LAST = "last"


class FilterOperator(str, Enum):
    """Supported filter operators."""

    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    IN = "in"
    NOT_IN = "not_in"
    IS_NULL = "is_null"
    NOT_NULL = "not_null"
    BETWEEN = "between"


class SamplingMethod(str, Enum):
    """Sampling methods for approximate operations."""

    SYSTEM = "system"
    BERNOULLI = "bernoulli"
    RESERVOIR = "reservoir"


@dataclass(frozen=True)
class ColumnInfo:
    """Column metadata information."""

    name: str
    dtype: str
    nullable: bool = True
    unique_count: Optional[int] = None
    null_count: Optional[int] = None
    min_value: Optional[Any] = None
    max_value: Optional[Any] = None
    sample_values: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "dtype": self.dtype,
            "nullable": self.nullable,
            "unique_count": self.unique_count,
            "null_count": self.null_count,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "sample_values": self.sample_values,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ColumnInfo":
        return cls(**data)


@dataclass(frozen=True)
class DatasetMetadata:
    """Complete dataset metadata for GUI consumption."""

    name: str
    source_type: SourceType
    source_path: Optional[str] = None
    row_count: int = 0
    column_count: int = 0
    columns: tuple[ColumnInfo, ...] = field(default_factory=tuple)
    estimated_size_bytes: Optional[int] = None
    actual_size_bytes: Optional[int] = None
    load_time: str = field(default_factory=lambda: datetime.now().isoformat())
    engine_used: EngineType = EngineType.DUCKDB
    processing_mode: ProcessingMode = ProcessingMode.IN_MEMORY
    analysis_duration_ms: Optional[float] = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    is_sampled: bool = False
    sample_size: Optional[int] = None
    sample_method: Optional[SamplingMethod] = None
    sample_seed: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source_type": self.source_type.value,
            "source_path": self.source_path,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "columns": [c.to_dict() for c in self.columns],
            "estimated_size_bytes": self.estimated_size_bytes,
            "actual_size_bytes": self.actual_size_bytes,
            "load_time": self.load_time,
            "engine_used": self.engine_used.value,
            "processing_mode": self.processing_mode.value,
            "analysis_duration_ms": self.analysis_duration_ms,
            "warnings": list(self.warnings),
            "is_sampled": self.is_sampled,
            "sample_size": self.sample_size,
            "sample_method": self.sample_method.value if self.sample_method else None,
            "sample_seed": self.sample_seed,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, data: dict) -> "DatasetMetadata":
        columns = tuple(ColumnInfo.from_dict(c) for c in data.get("columns", []))
        return cls(
            name=data["name"],
            source_type=SourceType(data["source_type"]),
            source_path=data.get("source_path"),
            row_count=data.get("row_count", 0),
            column_count=data.get("column_count", 0),
            columns=columns,
            estimated_size_bytes=data.get("estimated_size_bytes"),
            actual_size_bytes=data.get("actual_size_bytes"),
            load_time=data.get("load_time", datetime.now().isoformat()),
            engine_used=EngineType(data.get("engine_used", "duckdb")),
            processing_mode=ProcessingMode(data.get("processing_mode", "in_memory")),
            analysis_duration_ms=data.get("analysis_duration_ms"),
            warnings=tuple(data.get("warnings", [])),
            is_sampled=data.get("is_sampled", False),
            sample_size=data.get("sample_size"),
            sample_method=SamplingMethod(data["sample_method"]) if data.get("sample_method") else None,
            sample_seed=data.get("sample_seed"),
        )


@dataclass(frozen=True)
class AnalyticalRequest:
    """Validated analytical request."""

    operation: OperationType
    dataset_name: str
    columns: list[str] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    filters: list[dict] = field(default_factory=list)
    aggregations: dict[str, AggregationFunction] = field(default_factory=dict)
    sort_by: list[str] = field(default_factory=list)
    sort_ascending: bool = True
    limit: Optional[int] = None
    date_column: Optional[str] = None
    frequency: Optional[str] = None
    parameters: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "operation": self.operation.value,
            "dataset_name": self.dataset_name,
            "columns": self.columns,
            "group_by": self.group_by,
            "filters": self.filters,
            "aggregations": {k: v.value for k, v in self.aggregations.items()},
            "sort_by": self.sort_by,
            "sort_ascending": self.sort_ascending,
            "limit": self.limit,
            "date_column": self.date_column,
            "frequency": self.frequency,
            "parameters": self.parameters,
        }


@dataclass(frozen=True)
class AnalyticalResult:
    """Result of an analytical operation."""

    success: bool
    operation: OperationType
    dataset_name: str
    rows: list[dict] = field(default_factory=list)
    row_count: int = 0
    column_names: list[str] = field(default_factory=list)
    execution_time_ms: float = 0.0
    engine_used: EngineType = EngineType.DUCKDB
    is_sampled: bool = False
    sample_info: Optional[dict] = None
    warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "operation": self.operation.value,
            "dataset_name": self.dataset_name,
            "rows": self.rows,
            "row_count": self.row_count,
            "column_names": self.column_names,
            "execution_time_ms": self.execution_time_ms,
            "engine_used": self.engine_used.value,
            "is_sampled": self.is_sampled,
            "sample_info": self.sample_info,
            "warnings": self.warnings,
            "error": self.error,
        }

    def to_dataframe(self):
        """Convert to pandas DataFrame for compatibility."""
        import pandas as pd
        return pd.DataFrame(self.rows)


@dataclass(frozen=True)
class ProgressState:
    """Progress state for GUI reporting."""

    operation_id: str
    operation: str
    dataset_name: str
    status: str
    processed_rows: int = 0
    total_rows: Optional[int] = None
    progress_percent: Optional[float] = None
    message: str = ""
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    error: Optional[str] = None

    @property
    def is_indeterminate(self) -> bool:
        return self.total_rows is None or self.total_rows == 0

    def to_dict(self) -> dict:
        return {
            "operation_id": self.operation_id,
            "operation": self.operation,
            "dataset_name": self.dataset_name,
            "status": self.status,
            "processed_rows": self.processed_rows,
            "total_rows": self.total_rows,
            "progress_percent": self.progress_percent,
            "message": self.message,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "is_indeterminate": self.is_indeterminate,
        }


@dataclass(frozen=True)
class BenchmarkResult:
    """Benchmark result for performance measurement."""

    operation: str
    dataset_name: str
    row_count: int
    result_rows: int
    engine: EngineType
    elapsed_time_ms: float
    peak_memory_mb: Optional[float] = None
    dataset_size_mb: Optional[float] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "operation": self.operation,
            "dataset_name": self.dataset_name,
            "row_count": self.row_count,
            "result_rows": self.result_rows,
            "engine": self.engine.value,
            "elapsed_time_ms": self.elapsed_time_ms,
            "peak_memory_mb": self.peak_memory_mb,
            "dataset_size_mb": self.dataset_size_mb,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }