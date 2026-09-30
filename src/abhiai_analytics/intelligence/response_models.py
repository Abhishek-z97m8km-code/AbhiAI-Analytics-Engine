"""Typed Phase 5C business-response contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from .models import Evidence, Limitation
from .query_models import QueryStatus


class ResponseMode(str, Enum):
    CONCISE = "concise"
    STANDARD = "standard"
    DETAILED = "detailed"


class ResponseLanguage(str, Enum):
    AUTO = "auto"
    ENGLISH = "english"
    HINDI = "hindi"
    HINGLISH = "hinglish"


class GenerationMethod(str, Enum):
    DETERMINISTIC = "deterministic"
    LOCAL_LLM = "local_llm"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


class FaithfulnessStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PASSED = "passed"
    FAILED_FALLBACK = "failed_fallback"


@dataclass(frozen=True)
class BusinessResponse:
    status: QueryStatus
    answer: str
    summary: str
    key_findings: tuple[str, ...]
    supporting_evidence: tuple[Evidence, ...]
    limitations: tuple[Limitation, ...]
    language: ResponseLanguage
    detail_level: ResponseMode
    generation_method: GenerationMethod
    faithfulness_status: FaithfulnessStatus
    source_result_id: str
    source_evidence_ids: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        def convert(value):
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [convert(item) for item in value]
            return value
        return convert(asdict(self))
