"""Session-only active dataset/workbook state with multi-dataset registry."""

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .data_loader import DataLoader
from .types import DatasetInfo, DatasetLoadResult
from .scalable_store import (
    DATASET_STORE_ENV,
    DUCKDB_STORE,
    LEGACY_STORE,
    DuckDBDatasetStore,
    selected_dataset_store,
)


@dataclass(frozen=True)
class DatasetRecord:
    """Immutable record for a registered dataset."""
    id: str
    info: DatasetInfo
    frames: dict
    original_frames: dict
    active_version: str
    analysis_context: dict
    backend: str = LEGACY_STORE


class DataManager:
    def __init__(self, loader=None):
        self.loader = loader or DataLoader()
        self._datasets: dict[str, DatasetRecord] = {}
        self._active_id: str | None = None
        self._duckdb_store: Optional[DuckDBDatasetStore] = None

    def _ensure_duckdb_store(self) -> DuckDBDatasetStore:
        if self._duckdb_store is None:
            self._duckdb_store = DuckDBDatasetStore()
        return self._duckdb_store

    @property
    def active(self):
        return self._active_id is not None and self._active_id in self._datasets

    @property
    def active_id(self):
        return self._active_id

    @property
    def active_record(self):
        if self._active_id and self._active_id in self._datasets:
            return self._datasets[self._active_id]
        return None

    @property
    def active_sheet(self):
        record = self.active_record
        return record.info.active_sheet if record else None

    @property
    def info(self):
        record = self.active_record
        return record.info if record else None

    @property
    def frames(self):
        record = self.active_record
        return record.frames if record else {}

    @property
    def original_frames(self):
        record = self.active_record
        return record.original_frames if record else {}

    @property
    def active_version(self):
        record = self.active_record
        return record.active_version if record else "original"

    @property
    def analysis_context(self):
        record = self.active_record
        return record.analysis_context if record else {}

    @analysis_context.setter
    def analysis_context(self, value: dict):
        record = self.active_record
        if record:
            self._datasets[record.id] = DatasetRecord(
                record.id, record.info, record.frames, record.original_frames,
                record.active_version, value, record.backend
            )

    def list_datasets(self):
        """Return summary info for all registered datasets."""
        records = sorted(
            self._datasets.values(),
            key=lambda record: record.id != self._active_id,
        )
        summaries = []
        for record in records:
            if record.backend == DUCKDB_STORE:
                metadata = self._ensure_duckdb_store().metadata(record.id)
                columns = [column.name for column in metadata.columns]
                numeric_columns = sum(
                    any(token in column.dtype.upper() for token in (
                        "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT",
                        "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "REAL",
                    ))
                    for column in metadata.columns
                )
            else:
                frame = record.frames.get(record.info.active_sheet) if record.frames else None
                columns = list(frame.columns) if frame is not None else []
                numeric_columns = 0
            summaries.append(
            {
                "id": record.id,
                "name": record.info.path.name,
                "file_type": record.info.file_type,
                "size_bytes": record.info.size_bytes,
                "sheets": list(record.info.sheets),
                "active_sheet": record.info.active_sheet,
                "shapes": record.info.shapes,
                "columns": columns,
                "active_version": record.active_version,
                "cleaning_steps": 0,
                "missing_values": 0,
                "numeric_columns": numeric_columns,
                "backend": record.backend,
            }
            )
        return summaries

    def open(self, path, mode: str | None = None):
        """Open a dataset. Default is legacy pandas; pass mode='duckdb' for scalable store."""
        selected_mode = selected_dataset_store(mode)
        if selected_mode == DUCKDB_STORE:
            return self.open_duckdb(path)
        result = self.loader.open(path)
        if result.success:
            dataset_id = str(uuid.uuid4())[:8]
            record = DatasetRecord(
                id=dataset_id,
                info=result.info,
                frames=result.frames,
                original_frames={k: v.copy() for k, v in result.frames.items()},
                active_version="original",
                analysis_context={},
                backend=LEGACY_STORE,
            )
            self._datasets[dataset_id] = record
            self._active_id = dataset_id
            return result
        return result

    def open_duckdb(self, path):
        """Explicitly open a CSV with the scalable DuckDB store."""
        store = self._ensure_duckdb_store()
        # Generate a valid DuckDB identifier (must start with letter/underscore)
        dataset_id = "d" + str(uuid.uuid4()).replace("-", "")[:7]
        metadata = store.register_csv(dataset_id, path)
        # Build DatasetInfo compatible with existing structure
        source_path = Path(metadata.source_path) if metadata.source_path else Path(path).expanduser().resolve()
        info = DatasetInfo(
            path=source_path,
            file_type="CSV",
            size_bytes=metadata.actual_size_bytes or 0,
            sheets=("data",),
            active_sheet="data",
            shapes={"data": (metadata.row_count, metadata.column_count)},
            delimiter=",",
        )
        record = DatasetRecord(
            id=dataset_id,
            info=info,
            frames={},
            original_frames={},
            active_version="original",
            analysis_context={},
            backend=DUCKDB_STORE,
        )
        self._datasets[dataset_id] = record
        self._active_id = dataset_id
        return DatasetLoadResult(success=True, info=info, frames={}, error=None)

    def close(self, dataset_id: str | None = None):
        target_id = dataset_id or self._active_id
        if not target_id or target_id not in self._datasets:
            return None
        previous = self._datasets.pop(target_id)
        if self._active_id == target_id:
            self._active_id = next(iter(self._datasets)) if self._datasets else None
        return previous

    def select_dataset(self, dataset_id: str):
        if dataset_id not in self._datasets:
            return None, "Dataset not found."
        self._active_id = dataset_id
        self._datasets[dataset_id] = DatasetRecord(
            self._datasets[dataset_id].id,
            self._datasets[dataset_id].info,
            self._datasets[dataset_id].frames,
            self._datasets[dataset_id].original_frames,
            self._datasets[dataset_id].active_version,
            {},
            self._datasets[dataset_id].backend,
        )
        return dataset_id, None

    def select_sheet(self, requested):
        record = self.active_record
        if not record:
            return None, "No spreadsheet is currently open."
        text = str(requested).strip().casefold()
        names = list(record.info.sheets)
        if text.isdigit() and 1 <= int(text) <= len(names):
            selected = names[int(text) - 1]
        else:
            matches = [name for name in names if name.casefold() == text]
            matches += [name for name in names if text and text in name.casefold() and name not in matches]
            if len(matches) != 1:
                return None, "Please specify one available sheet: " + ", ".join(names)
            selected = matches[0]
        new_info = type(record.info)(record.info.path, record.info.file_type, record.info.size_bytes, record.info.sheets, selected, record.info.shapes, record.info.delimiter)
        self._datasets[record.id] = DatasetRecord(
            record.id, new_info, record.frames, record.original_frames,
            record.active_version, {},
            record.backend
        )
        return selected, None

    def frame(self, sheet=None):
        record = self.active_record
        if not record:
            return None
        if record.backend == DUCKDB_STORE:
            return None
        return record.frames.get(sheet or record.info.active_sheet)

    def get_frame(self, dataset_id: str, sheet: str | None = None):
        record = self._datasets.get(dataset_id)
        if not record:
            return None
        if record.backend == DUCKDB_STORE:
            return None
        return record.frames.get(sheet or record.info.active_sheet)

    def get_original_frame(self, dataset_id: str, sheet: str | None = None):
        record = self._datasets.get(dataset_id)
        if not record:
            return None
        if record.backend == DUCKDB_STORE:
            return None
        return record.original_frames.get(sheet or record.info.active_sheet)

    def get_record(self, dataset_id: str):
        return self._datasets.get(dataset_id)

    def generate_business_insights(self, dataset_id: str | None = None, *, granularity: str = "monthly"):
        """Generate Phase 5A structured insights while preserving the dataset backend."""
        from ..intelligence.integration import analyze_dataset
        return analyze_dataset(self, dataset_id, granularity=granularity)

    def ask_business_question(self, question: str, dataset_id: str | None = None, *, semantic_service=None):
        """Answer one read-only business question through the typed Phase 5B layer."""
        from ..intelligence.query_engine import BusinessQuestionEngine
        return BusinessQuestionEngine().ask(
            self, question, dataset_id, semantic_service=semantic_service
        )

    def explain_business_question(self, question: str, dataset_id: str | None = None, *,
                                  mode="standard", language="auto", semantic_service=None,
                                  response_service=None):
        """Run Phase 5B, then render a grounded Phase 5C business response."""
        from ..intelligence.response_engine import BusinessResponseEngine
        result = self.ask_business_question(
            question, dataset_id, semantic_service=semantic_service
        )
        return BusinessResponseEngine().respond(
            result, mode=mode, language=language, service=response_service
        )

    def generate_business_report(self, report_type="business_overview", detail_level="standard",
                                 language="english", dataset_id=None, response_service=None):
        """Build a typed Phase 5D report without rendering or persistence."""
        from ..intelligence.report_engine import BusinessReportEngine
        return BusinessReportEngine().generate(self, report_type=report_type, detail_level=detail_level,
            language=language, dataset_id=dataset_id, response_service=response_service)

    def get_preview(self, dataset_id: str, limit: int = 100):
        """Get preview rows for a dataset, works for both pandas and DuckDB backends."""
        record = self._datasets.get(dataset_id)
        if not record:
            return None
        if record.backend == DUCKDB_STORE:
            store = self._ensure_duckdb_store()
            return store.preview(dataset_id, limit)
        frame = self.get_frame(dataset_id)
        if frame is None:
            return None
        preview_rows = min(limit, len(frame))
        return frame.head(preview_rows).to_dict(orient="records")

    def info_dict(self, dataset_id: str | None = None):
        record = self._datasets.get(dataset_id) if dataset_id else self.active_record
        if not record:
            return None
        if record.backend == DUCKDB_STORE:
            return {
                "filename": record.info.path.name,
                "file_type": record.info.file_type,
                "size_bytes": record.info.size_bytes,
                "sheets": record.info.sheets,
                "active_sheet": record.info.active_sheet,
                "shapes": record.info.shapes,
                "columns": [],
                "delimiter": record.info.delimiter,
                "active_version": record.active_version,
                "cleaning_steps": 0,
                "backend": DUCKDB_STORE,
            }
        frame = self.frame() if dataset_id is None else self.get_frame(dataset_id)
        if frame is None:
            return None
        return {
            "filename": record.info.path.name,
            "file_type": record.info.file_type,
            "size_bytes": record.info.size_bytes,
            "sheets": record.info.sheets,
            "active_sheet": record.info.active_sheet,
            "shapes": record.info.shapes,
            "columns": list(frame.columns),
            "delimiter": record.info.delimiter,
            "active_version": record.active_version,
            "cleaning_steps": 0,
            "backend": LEGACY_STORE,
        }


def _select_candidate(answer, candidates):
    """Accept only a known candidate, an unambiguous normalized form, or an ordinal."""
    text = _normalise_answer(answer)
    ordinals = {"first": 0, "first one": 0, "1": 0, "one": 0, "second": 1, "second one": 1, "2": 1, "two": 1, "third": 2, "third one": 2, "3": 2, "three": 2}
    if text in ordinals and ordinals[text] < len(candidates):
        return candidates[ordinals[text]]
    matches = [candidate for candidate in candidates if _normalise_answer(candidate) == text]
    return matches[0] if len(matches) == 1 else None


def _normalise_answer(value):
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(value)).casefold()
    text = re.sub(r"^(?:use\s+)?(?:the\s+)?", "", text).strip()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())