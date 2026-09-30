"""Large-Scale Analytics Engine for AbhiAI Analytics Portfolio.

This package provides a scalable analytical layer supporting DuckDB and Pandas
backends for processing datasets with millions of rows.
"""

from .types import (
    EngineType,
    SourceType,
    ProcessingMode,
    OperationType,
    AggregationFunction,
    FilterOperator,
    SamplingMethod,
    ColumnInfo,
    DatasetMetadata,
    AnalyticalRequest,
    AnalyticalResult,
    ProgressState,
    BenchmarkResult,
)
from .safety import (
    ValidatedPlan,
    ParameterizedQueryBuilder,
    QuerySafetyError,
    validate_identifier,
    validate_operation,
    validate_aggregation,
    validate_filter_operator,
    validate_columns,
    validate_group_by,
    validate_filters,
    validate_limit,
    validate_sort,
    sanitize_table_name,
)
from .duckdb_engine import DuckDBEngine
from .pandas_engine import PandasEngine

__all__ = [
    "EngineType",
    "SourceType",
    "ProcessingMode",
    "OperationType",
    "AggregationFunction",
    "FilterOperator",
    "SamplingMethod",
    "ColumnInfo",
    "DatasetMetadata",
    "AnalyticalRequest",
    "AnalyticalResult",
    "ProgressState",
    "BenchmarkResult",
    "ValidatedPlan",
    "QuerySafetyError",
    "ParameterizedQueryBuilder",
    "validate_identifier",
    "validate_operation",
    "validate_aggregation",
    "validate_filter_operator",
    "validate_columns",
    "validate_group_by",
    "validate_filters",
    "validate_limit",
    "validate_sort",
    "sanitize_table_name",
    "DuckDBEngine",
    "PandasEngine",
]

__version__ = "1.0.0"