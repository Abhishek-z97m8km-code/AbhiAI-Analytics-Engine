"""Bounded local CSV/XLSX loading; formulas and macros are never executed."""

import csv
from pathlib import Path

from .types import DatasetInfo, DatasetLoadResult
from .path_parser import parse_local_path


DATA_EXTENSIONS = {".csv": "CSV", ".xlsx": "XLSX"}
MAX_DATA_FILE_SIZE_BYTES = 20 * 1024 * 1024
MAX_ROWS_LOADED = 100_000
MAX_COLUMNS = 200
MAX_SHEETS = 20


class DataLoader:
    """Load a supported spreadsheet into memory without modifying its source."""

    def open(self, requested_path):
        parsed, error = parse_local_path(requested_path)
        if error:
            return DatasetLoadResult(False, error=error)
        try:
            path = parsed.resolve(strict=True)
        except (OSError, RuntimeError):
            return DatasetLoadResult(False, error="That file could not be found.")
        if not path.is_file():
            return DatasetLoadResult(False, error="That path is not a regular file.")
        file_type = DATA_EXTENSIONS.get(path.suffix.casefold())
        if file_type is None:
            return DatasetLoadResult(False, error="Unsupported data file. Use CSV or XLSX.")
        try:
            size = path.stat().st_size
        except OSError:
            return DatasetLoadResult(False, error="That data file could not be read.")
        if size > MAX_DATA_FILE_SIZE_BYTES:
            return DatasetLoadResult(False, error="That data file is too large to open safely.")
        try:
            frames, delimiter = self._read_csv(path) if file_type == "CSV" else self._read_xlsx(path)
            shapes = {name: frame.shape for name, frame in frames.items()}
            active_sheet = next(iter(frames))
            info = DatasetInfo(path, file_type, size, tuple(frames), active_sheet, shapes, delimiter)
            return DatasetLoadResult(True, info, frames)
        except ValueError as exc:
            return DatasetLoadResult(False, error=str(exc))
        except Exception:
            return DatasetLoadResult(False, error=f"Could not read this {file_type} file.")

    @staticmethod
    def _validate_frame(frame, label):
        if frame.empty:
            raise ValueError(f"{label} has no data rows.")
        if len(frame) > MAX_ROWS_LOADED:
            raise ValueError(f"{label} has too many rows to open safely.")
        if len(frame.columns) > MAX_COLUMNS:
            raise ValueError(f"{label} has too many columns to open safely.")
        if not len(frame.columns):
            raise ValueError(f"{label} has no header row.")
        columns = [str(column).strip() for column in frame.columns]
        if any(not column for column in columns) or len(set(columns)) != len(columns):
            raise ValueError(f"{label} has empty or duplicate column names.")
        frame.columns = columns
        return frame

    def _read_csv(self, path):
        import pandas as pd

        try:
            sample = path.read_text(encoding="utf-8-sig", errors="strict")[:8192]
        except UnicodeDecodeError:
            raise ValueError("CSV files must be valid UTF-8 text.")
        if not sample.strip():
            raise ValueError("CSV file is empty.")
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
        try:
            frame = pd.read_csv(path, sep=delimiter, encoding="utf-8-sig", nrows=MAX_ROWS_LOADED + 1)
        except Exception:
            raise ValueError("Could not parse this CSV file.")
        return {"Data": self._validate_frame(frame, "CSV file")}, delimiter

    def _read_xlsx(self, path):
        import pandas as pd

        try:
            workbook = pd.ExcelFile(path, engine="openpyxl")
        except Exception:
            raise ValueError("Could not read this XLSX file. It may be malformed or encrypted.")
        if not workbook.sheet_names:
            raise ValueError("XLSX workbook has no sheets.")
        if len(workbook.sheet_names) > MAX_SHEETS:
            raise ValueError("XLSX workbook has too many sheets to open safely.")
        frames = {}
        try:
            for sheet in workbook.sheet_names:
                try:
                    frame = pd.read_excel(workbook, sheet_name=sheet, nrows=MAX_ROWS_LOADED + 1)
                except Exception:
                    raise ValueError(f"Could not read sheet '{sheet}'.")
                frames[sheet] = self._validate_frame(frame, f"Sheet '{sheet}'")
        finally:
            workbook.close()
        return frames, None