"""Data loading and management."""

from .data_loader import DataLoader
from .data_manager import DataManager
from .scalable_store import (
    DuckDBDatasetStore,
    LEGACY_STORE,
    DUCKDB_STORE,
    selected_dataset_store,
    DatasetStoreSelectionError,
)
from .types import DatasetInfo, DatasetLoadResult

__all__ = [
    "DataLoader",
    "DataManager",
    "DuckDBDatasetStore",
    "LEGACY_STORE",
    "DUCKDB_STORE",
    "selected_dataset_store",
    "DatasetStoreSelectionError",
    "DatasetInfo",
    "DatasetLoadResult",
]