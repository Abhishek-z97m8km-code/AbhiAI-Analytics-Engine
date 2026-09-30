"""Immutable values for the local spreadsheet subsystem."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DatasetInfo:
    path: Path
    file_type: str
    size_bytes: int
    sheets: tuple[str, ...]
    active_sheet: str
    shapes: dict
    delimiter: str | None = None


@dataclass(frozen=True)
class DatasetLoadResult:
    success: bool
    info: DatasetInfo | None = None
    frames: dict | None = None
    error: str | None = None
