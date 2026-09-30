"""Query safety layer for the Large-Scale Analytics Engine.

This module provides validated analytical plans and parameterized query construction
to prevent arbitrary SQL execution from user input.
"""

import re
from dataclasses import dataclass
from typing import Any
from .types import (
    OperationType,
    AggregationFunction,
    FilterOperator,
    EngineType,
    AnalyticalRequest,
)


ALLOWED_OPERATIONS = {
    OperationType.ROW_COUNT,
    OperationType.COLUMN_SCHEMA,
    OperationType.DESCRIPTIVE_STATS,
    OperationType.SUM,
    OperationType.MEAN,
    OperationType.MIN,
    OperationType.MAX,
    OperationType.COUNT,
    OperationType.DISTINCT_COUNT,
    OperationType.GROUPBY,
    OperationType.MULTI_GROUPBY,
    OperationType.FILTER,
    OperationType.SORT,
    OperationType.TOP_N,
    OperationType.BOTTOM_N,
    OperationType.DATE_GROUP,
    OperationType.TIME_SERIES_AGG,
    OperationType.GROWTH_RATE,
    OperationType.CUMULATIVE,
    OperationType.PCT_CHANGE,
    OperationType.PCT_SHARE,
    OperationType.PIVOT,
    OperationType.CORRELATION,
    OperationType.MISSING_ANALYSIS,
    OperationType.DUPLICATE_ANALYSIS,
    OperationType.OUTLIER_SUMMARY,
    OperationType.RANKED_GROUP,
    OperationType.KPI_CALCULATION,
}

ALLOWED_AGGREGATIONS = {
    AggregationFunction.SUM,
    AggregationFunction.MEAN,
    AggregationFunction.MEDIAN,
    AggregationFunction.MIN,
    AggregationFunction.MAX,
    AggregationFunction.COUNT,
    AggregationFunction.DISTINCT_COUNT,
    AggregationFunction.STD,
    AggregationFunction.VAR,
    AggregationFunction.FIRST,
    AggregationFunction.LAST,
}

ALLOWED_FILTER_OPERATORS = {
    FilterOperator.EQ,
    FilterOperator.NE,
    FilterOperator.GT,
    FilterOperator.GTE,
    FilterOperator.LT,
    FilterOperator.LTE,
    FilterOperator.CONTAINS,
    FilterOperator.STARTS_WITH,
    FilterOperator.ENDS_WITH,
    FilterOperator.IN,
    FilterOperator.NOT_IN,
    FilterOperator.IS_NULL,
    FilterOperator.NOT_NULL,
    FilterOperator.BETWEEN,
}

MAX_GROUP_BY_COLUMNS = 10
MAX_FILTER_CONDITIONS = 10
MAX_RESULT_LIMIT = 10000
MAX_PIVOT_ROWS = 1000
MAX_PIVOT_COLUMNS = 50

IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class QuerySafetyError(Exception):
    """Raised when a query fails safety validation."""

    pass


def validate_identifier(name: str, context: str = "identifier") -> str:
    """Validate a column/table identifier against allowed pattern.

    Args:
        name: The identifier to validate
        context: Context for error messages

    Returns:
        The validated identifier

    Raises:
        QuerySafetyError: If identifier is invalid
    """
    if not isinstance(name, str):
        raise QuerySafetyError(f"{context} must be a string")
    if not name:
        raise QuerySafetyError(f"{context} cannot be empty")
    if len(name) > 128:
        raise QuerySafetyError(f"{context} too long (max 128 chars)")
    if not IDENTIFIER_PATTERN.fullmatch(name):
        raise QuerySafetyError(
            f"Invalid {context}: '{name}'. Must be alphanumeric/underscore, starting with letter/underscore."
        )
    return name


def validate_operation(operation: OperationType) -> OperationType:
    """Validate that an operation is allowed.

    Args:
        operation: The operation to validate

    Returns:
        The validated operation

    Raises:
        QuerySafetyError: If operation is not allowed
    """
    if operation not in ALLOWED_OPERATIONS:
        raise QuerySafetyError(f"Operation '{operation.value}' is not allowed")
    return operation


def validate_aggregation(agg: AggregationFunction) -> AggregationFunction:
    """Validate that an aggregation function is allowed.

    Args:
        agg: The aggregation to validate

    Returns:
        The validated aggregation

    Raises:
        QuerySafetyError: If aggregation is not allowed
    """
    if agg not in ALLOWED_AGGREGATIONS:
        raise QuerySafetyError(f"Aggregation '{agg.value}' is not allowed")
    return agg


def validate_filter_operator(op: FilterOperator) -> FilterOperator:
    """Validate that a filter operator is allowed.

    Args:
        op: The filter operator to validate

    Returns:
        The validated operator

    Raises:
        QuerySafetyError: If operator is not allowed
    """
    if op not in ALLOWED_FILTER_OPERATORS:
        raise QuerySafetyError(f"Filter operator '{op.value}' is not allowed")
    return op


def validate_columns(columns: list[str], available_columns: set[str] | None = None) -> list[str]:
    """Validate column names.

    Args:
        columns: List of column names to validate
        available_columns: Optional set of available columns for existence check

    Returns:
        Validated column list

    Raises:
        QuerySafetyError: If any column is invalid
    """
    if not isinstance(columns, list):
        raise QuerySafetyError("Columns must be a list")

    validated = []
    for col in columns:
        validated_col = validate_identifier(col, "column")
        if available_columns is not None and validated_col not in available_columns:
            raise QuerySafetyError(f"Column '{validated_col}' does not exist in dataset")
        validated.append(validated_col)

    return validated


def validate_group_by(columns: list[str], available_columns: set[str] | None = None) -> list[str]:
    """Validate GROUP BY columns.

    Args:
        columns: List of GROUP BY columns
        available_columns: Optional set of available columns

    Returns:
        Validated column list

    Raises:
        QuerySafetyError: If validation fails
    """
    validated = validate_columns(columns, available_columns)
    if len(validated) > MAX_GROUP_BY_COLUMNS:
        raise QuerySafetyError(f"Too many GROUP BY columns (max {MAX_GROUP_BY_COLUMNS})")
    return validated


def validate_filters(filters: list[dict], available_columns: set[str] | None = None) -> list[dict]:
    """Validate filter conditions.

    Args:
        filters: List of filter condition dicts
        available_columns: Optional set of available columns

    Returns:
        Validated filter list

    Raises:
        QuerySafetyError: If validation fails
    """
    if not isinstance(filters, list):
        raise QuerySafetyError("Filters must be a list")

    if len(filters) > MAX_FILTER_CONDITIONS:
        raise QuerySafetyError(f"Too many filter conditions (max {MAX_FILTER_CONDITIONS})")

    validated = []
    for i, f in enumerate(filters):
        if not isinstance(f, dict):
            raise QuerySafetyError(f"Filter {i} must be an object")

        required_keys = {"column", "operator"}
        if not required_keys.issubset(f.keys()):
            raise QuerySafetyError(f"Filter {i} missing required keys: {required_keys}")

        column = validate_identifier(f["column"], f"filter column {i}")
        if available_columns is not None and column not in available_columns:
            raise QuerySafetyError(f"Filter column '{column}' does not exist")

        try:
            operator = FilterOperator(f["operator"])
        except ValueError:
            raise QuerySafetyError(f"Filter {i}: invalid operator '{f['operator']}'")

        validate_filter_operator(operator)

        if operator in {FilterOperator.IN, FilterOperator.NOT_IN}:
            if "value" not in f or not isinstance(f["value"], list):
                raise QuerySafetyError(f"Filter {i}: IN/NOT_IN requires a list value")
            if len(f["value"]) > 1000:
                raise QuerySafetyError(f"Filter {i}: IN list too large (max 1000 values)")

        validated.append({"column": column, "operator": operator.value, "value": f.get("value")})

    return validated


def validate_limit(limit: int | None) -> int | None:
    """Validate result limit.

    Args:
        limit: The limit to validate

    Returns:
        Validated limit or None

    Raises:
        QuerySafetyError: If limit is invalid
    """
    if limit is None:
        return None
    if not isinstance(limit, int):
        raise QuerySafetyError("Limit must be an integer")
    if limit < 1:
        raise QuerySafetyError("Limit must be at least 1")
    if limit > MAX_RESULT_LIMIT:
        raise QuerySafetyError(f"Limit exceeds maximum ({MAX_RESULT_LIMIT})")
    return limit


def validate_sort(sort_by: list[str], sort_ascending: bool, available_columns: set[str] | None = None) -> list[str]:
    """Validate sort specification.

    Args:
        sort_by: List of columns to sort by
        sort_ascending: Sort direction
        available_columns: Optional set of available columns

    Returns:
        Validated sort columns

    Raises:
        QuerySafetyError: If validation fails
    """
    validated = validate_columns(sort_by, available_columns)
    return validated


@dataclass
class ValidatedPlan:
    """A fully validated analytical execution plan."""

    operation: OperationType
    dataset_name: str
    columns: list[str]
    group_by: list[str]
    filters: list[dict]
    aggregations: dict[str, AggregationFunction]
    sort_by: list[str]
    sort_ascending: bool
    limit: int | None
    date_column: str | None
    frequency: str | None
    parameters: dict

    @classmethod
    def from_request(cls, request: AnalyticalRequest, available_columns: set[str] | None = None) -> "ValidatedPlan":
        """Create a validated plan from an analytical request.

        Args:
            request: The analytical request to validate
            available_columns: Optional set of available columns in the dataset

        Returns:
            A validated execution plan

        Raises:
            QuerySafetyError: If any validation fails
        """
        validate_operation(request.operation)

        columns = validate_columns(request.columns, available_columns)
        group_by = validate_group_by(request.group_by, available_columns)
        filters = validate_filters(request.filters, available_columns)
        sort_by = validate_sort(request.sort_by, request.sort_ascending, available_columns)
        limit = validate_limit(request.limit)

        aggregations = {}
        for col, agg in request.aggregations.items():
            validated_col = validate_identifier(col, "aggregation column")
            if available_columns is not None and validated_col not in available_columns:
                raise QuerySafetyError(f"Aggregation column '{validated_col}' does not exist")
            validated_agg = validate_aggregation(agg)
            aggregations[validated_col] = validated_agg

        date_column = None
        if request.date_column:
            date_column = validate_identifier(request.date_column, "date column")
            if available_columns is not None and date_column not in available_columns:
                raise QuerySafetyError(f"Date column '{date_column}' does not exist")

        return cls(
            operation=request.operation,
            dataset_name=validate_identifier(request.dataset_name, "dataset"),
            columns=columns,
            group_by=group_by,
            filters=filters,
            aggregations=aggregations,
            sort_by=sort_by,
            sort_ascending=request.sort_ascending,
            limit=limit,
            date_column=date_column,
            frequency=request.frequency,
            parameters=request.parameters,
        )


class ParameterizedQueryBuilder:
    """Builds parameterized queries for different engines."""

    def __init__(self, engine: EngineType):
        self.engine = engine

    def _quote_identifier(self, value: str) -> str:
        """Quote a schema-derived DuckDB identifier without changing its name."""
        return '"' + value.replace('"', '""') + '"'

    def build_count_query(self, table: str, filters: list[dict]) -> tuple[str, list]:
        """Build a parameterized row count query."""
        validated_table = validate_identifier(table, "table")
        where_clause, params = self._build_where_clause(filters)
        query = f"SELECT COUNT(*) FROM {validated_table}"
        if where_clause:
            query += f" WHERE {where_clause}"
        return query, params

    def build_descriptive_stats_query(
        self, table: str, columns: list[str], filters: list[dict]
    ) -> tuple[str, list]:
        """Build a parameterized descriptive statistics query."""
        validated_table = validate_identifier(table, "table")
        validated_columns = validate_columns(columns)

        stats_exprs = []
        for col in validated_columns:
            stats_exprs.extend([
                f"COUNT({col}) AS {col}_count",
                f"MIN({col}) AS {col}_min",
                f"MAX({col}) AS {col}_max",
                f"AVG({col}) AS {col}_mean",
                f"SUM({col}) AS {col}_sum",
            ])

        where_clause, params = self._build_where_clause(filters)
        query = f"SELECT {', '.join(stats_exprs)} FROM {validated_table}"
        if where_clause:
            query += f" WHERE {where_clause}"
        return query, params

    def build_groupby_query(
        self,
        table: str,
        group_by: list[str],
        aggregations: dict[str, AggregationFunction],
        filters: list[dict],
        sort_by: list[str],
        sort_ascending: bool,
        limit: int | None,
    ) -> tuple[str, list]:
        """Build a parameterized GROUP BY query."""
        validated_table = validate_identifier(table, "table")
        validated_group_by = validate_group_by(group_by)
        validated_columns = list(aggregations.keys())
        validated_columns.extend(validated_group_by)

        select_cols = validated_group_by[:]
        for col, agg in aggregations.items():
            select_cols.append(f"{agg.value}({col}) AS {col}_{agg.value}")

        where_clause, params = self._build_where_clause(filters)

        query = f"SELECT {', '.join(select_cols)} FROM {validated_table}"
        if where_clause:
            query += f" WHERE {where_clause}"
        query += f" GROUP BY {', '.join(validated_group_by)}"

        if sort_by:
            validated_sort = validate_sort(sort_by, sort_ascending)
            order = "ASC" if sort_ascending else "DESC"
            query += f" ORDER BY {', '.join(f'{c} {order}' for c in validated_sort)}"

        if limit:
            query += f" LIMIT {limit}"

        return query, params

    def build_filter_query(
        self, table: str, columns: list[str], filters: list[dict], limit: int | None
    ) -> tuple[str, list]:
        """Build a parameterized filter query."""
        validated_table = validate_identifier(table, "table")
        validated_columns = validate_columns(columns) if columns else ["*"]

        where_clause, params = self._build_where_clause(filters)

        query = f"SELECT {', '.join(validated_columns)} FROM {validated_table}"
        if where_clause:
            query += f" WHERE {where_clause}"

        if limit:
            query += f" LIMIT {limit}"

        return query, params

    def build_top_n_query(
        self,
        table: str,
        metric_column: str,
        group_by: list[str] | None,
        ascending: bool,
        limit: int,
        filters: list[dict],
    ) -> tuple[str, list]:
        """Build a parameterized TOP N query."""
        validated_table = validate_identifier(table, "table")
        validated_metric = validate_identifier(metric_column, "metric column")
        validated_group_by = validate_group_by(group_by) if group_by else []

        select_cols = validated_group_by + [validated_metric]
        where_clause, params = self._build_where_clause(filters)

        query = f"SELECT {', '.join(select_cols)} FROM {validated_table}"
        if where_clause:
            query += f" WHERE {where_clause}"

        if validated_group_by:
            query += f" GROUP BY {', '.join(validated_group_by)}"

        order = "ASC" if ascending else "DESC"
        query += f" ORDER BY {validated_metric} {order} LIMIT {limit}"

        return query, params

    def build_date_group_query(
        self,
        table: str,
        date_column: str,
        metric_column: str | None,
        frequency: str,
        aggregation: AggregationFunction,
        filters: list[dict],
    ) -> tuple[str, list]:
        """Build a parameterized date grouping query."""
        validated_table = validate_identifier(table, "table")
        validated_date = validate_identifier(date_column, "date column")
        validated_metric = validate_identifier(metric_column, "metric column") if metric_column else None

        freq_map = {
            "day": "DAY",
            "week": "WEEK",
            "month": "MONTH",
            "quarter": "QUARTER",
            "year": "YEAR",
        }
        freq = freq_map.get(frequency.lower(), "MONTH")

        if self.engine == EngineType.DUCKDB:
            date_trunc = f"DATE_TRUNC('{freq}', {validated_date})"
        else:
            date_trunc = f"DATE_TRUNC('{freq}', {validated_date})"

        if validated_metric:
            select_cols = [f"{date_trunc} AS period", f"{aggregation.value}({validated_metric}) AS {aggregation.value}"]
        else:
            select_cols = [f"{date_trunc} AS period", "COUNT(*) AS count"]

        where_clause, params = self._build_where_clause(filters)

        query = f"SELECT {', '.join(select_cols)} FROM {validated_table}"
        if where_clause:
            query += f" WHERE {where_clause}"
        query += f" GROUP BY {date_trunc} ORDER BY period"

        return query, params

    def _build_where_clause(self, filters: list[dict]) -> tuple[str, list]:
        """Build a WHERE clause with parameterized values."""
        if not filters:
            return "", []

        conditions = []
        params = []

        for f in filters:
            column = f["column"]
            operator = f["operator"]
            value = f.get("value")

            if operator in ("is_null", "not_null"):
                conditions.append(f"{column} IS {'NOT ' if operator == 'not_null' else ''}NULL")
            elif operator in ("in", "not_in"):
                placeholders = ", ".join(["?"] * len(value))
                conditions.append(f"{column} {'NOT ' if operator == 'not_in' else ''}IN ({placeholders})")
                params.extend(value)
            elif operator == "between":
                if not isinstance(value, list) or len(value) != 2:
                    raise QuerySafetyError("BETWEEN requires a list of two values")
                conditions.append(f"{column} BETWEEN ? AND ?")
                params.extend(value)
            elif operator == "contains":
                conditions.append(f"{column} LIKE ?")
                params.append(f"%{value}%")
            elif operator == "starts_with":
                conditions.append(f"{column} LIKE ?")
                params.append(f"{value}%")
            elif operator == "ends_with":
                conditions.append(f"{column} LIKE ?")
                params.append(f"%{value}")
            else:
                op_map = {
                    "eq": "=",
                    "ne": "!=",
                    "gt": ">",
                    "gte": ">=",
                    "lt": "<",
                    "lte": "<=",
                }
                conditions.append(f"{column} {op_map[operator]} ?")
                params.append(value)

        return " AND ".join(conditions), params


def sanitize_table_name(name: str) -> str:
    """Sanitize a table name for safe use in queries."""
    validated = validate_identifier(name, "table")
    return validated