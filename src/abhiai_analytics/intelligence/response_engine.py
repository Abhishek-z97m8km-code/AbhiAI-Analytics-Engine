"""Grounded deterministic and optional local-LLM business responses."""

from __future__ import annotations

import hashlib
import json
import re
import time

from .query_interpreter import OllamaServiceError
from .faithfulness import FaithfulnessValidator
from .models import Limitation
from .query_models import AnalyticsOperation, QueryStatus
from .response_formatting import fact_value
from .response_models import BusinessResponse, FaithfulnessStatus, GenerationMethod, ResponseLanguage, ResponseMode

MAX_LLM_CONTEXT_CHARS = 12_000
MAX_LLM_ATTEMPTS = 2


class BusinessResponseEngine:
    def respond(self, result, *, mode: ResponseMode | str = ResponseMode.STANDARD,
                language: ResponseLanguage | str = ResponseLanguage.AUTO, service=None) -> BusinessResponse:
        mode = ResponseMode(mode)
        language = self._resolve_language(ResponseLanguage(language), result.original_question)
        render_start = time.perf_counter()
        deterministic = self._render(result, mode, language)
        render_seconds = time.perf_counter() - render_start
        source_id = hashlib.sha256(json.dumps(result.to_dict(), sort_keys=True, default=str).encode()).hexdigest()[:24]
        evidence_ids = tuple(_evidence_id(item) for item in result.evidence)
        metadata = {**result.metadata, "render_seconds": render_seconds, "llm_attempts": 0, "llm_seconds": 0.0, "validation_seconds": 0.0, "fallback_seconds": 0.0, "llm_input_chars": 0}
        if service is None or result.status != QueryStatus.ANSWERED:
            return self._build(result, deterministic, mode, language, GenerationMethod.DETERMINISTIC,
                FaithfulnessStatus.NOT_REQUIRED, source_id, evidence_ids, metadata)

        payload = self._source_payload(result, mode, language, evidence_ids)
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:MAX_LLM_CONTEXT_CHARS]
        metadata["llm_input_chars"] = len(serialized)
        validator = FaithfulnessValidator()
        failures = []
        llm_start = time.perf_counter()
        for attempt in range(1, MAX_LLM_ATTEMPTS + 1):
            metadata["llm_attempts"] = attempt
            try:
                content = service.chat(self._prompt(serialized, strict=attempt == 2), json_format=True, options={"temperature": 0})
                generated = self._parse_llm(content, evidence_ids)
            except (OllamaServiceError, ValueError, TypeError, json.JSONDecodeError) as error:
                failures.append(type(error).__name__)
                continue
            validate_start = time.perf_counter()
            validation = validator.validate(" ".join([generated["answer"], *generated["key_findings"], *generated["limitations"]]), result)
            metadata["validation_seconds"] += time.perf_counter() - validate_start
            if validation.valid:
                metadata["llm_seconds"] = time.perf_counter() - llm_start
                text = {"answer": generated["answer"], "summary": generated["answer"], "findings": tuple(generated["key_findings"])}
                return self._build(result, text, mode, language, GenerationMethod.LOCAL_LLM,
                    FaithfulnessStatus.PASSED, source_id, evidence_ids, metadata)
            failures.extend(validation.reasons)
        metadata["llm_seconds"] = time.perf_counter() - llm_start
        fallback_start = time.perf_counter()
        metadata["fallback_reasons"] = tuple(dict.fromkeys(failures))
        metadata["fallback_seconds"] = time.perf_counter() - fallback_start
        return self._build(result, deterministic, mode, language, GenerationMethod.DETERMINISTIC_FALLBACK,
            FaithfulnessStatus.FAILED_FALLBACK, source_id, evidence_ids, metadata)

    def _render(self, result, mode, language):
        if _forecast_question(result.original_question):
            answer = "The current analysis describes historical results; it does not include a forecasting model."
            return {"answer": answer, "summary": answer, "findings": ()}
        if result.status == QueryStatus.NEEDS_CLARIFICATION:
            answer = result.suggested_clarification or "Please clarify which metric or dimension you mean."
            return {"answer": answer, "summary": answer, "findings": ()}
        if result.status in {QueryStatus.UNSUPPORTED, QueryStatus.INTERPRETATION_UNAVAILABLE, QueryStatus.ERROR}:
            message = result.limitations[0].message if result.limitations else "This question is not supported by the current analytics layer."
            return {"answer": message, "summary": message, "findings": ()}
        if result.status == QueryStatus.INSUFFICIENT_DATA:
            message = _friendly_limit(result.limitations[-1]) if result.limitations else "The available data is not sufficient to answer this question."
            return {"answer": message, "summary": message, "findings": ()}

        findings = tuple(self._fact_sentence(fact, language) for fact in result.facts)
        if not findings:
            findings = ("The structured analysis completed, but no answerable fact was produced.",)
        if mode == ResponseMode.CONCISE:
            answer = findings[0]
        elif mode == ResponseMode.STANDARD:
            answer = " ".join(findings[:3])
            priority = ("external_cause_unknown", "no_anomaly_signal", "currency_unknown")
            useful = []
            for code in priority:
                match = next((item for item in result.limitations if item.code == code), None)
                if match and (code != "currency_unknown" or any(
                    fact.metric in {"revenue", "cost", "profit"} and not isinstance(fact.value, str)
                    for fact in result.facts
                )):
                    useful.append(_friendly_limit(match))
                    break
            if useful:
                answer += " " + useful[0]
        else:
            answer = "What happened: " + " ".join(findings) + " Evidence: " + "; ".join(item.description for item in result.evidence[:5]) + "."
            useful = [_friendly_limit(item) for item in result.limitations[:5]]
            if useful:
                answer += " Limitations: " + " ".join(useful)
        answer = self._localize_shell(answer, language)
        return {"answer": answer, "summary": findings[0], "findings": findings}

    @staticmethod
    def _fact_sentence(fact, language):
        value = fact_value(fact.metric, fact.value, fact.percentage)
        if fact.dimension_value and fact.percentage is not None:
            return f"{fact.dimension_value} contributed {value} of {fact.metric}."
        if fact.dimension_value:
            return f"{fact.dimension_value} had {fact.metric} of {value}."
        if fact.period:
            return f"For {fact.period}, {fact.metric or fact.label} was {value}."
        if isinstance(fact.value, str):
            text = fact.value.rstrip()
            if text.endswith((".", "!", "?")):
                return f"{fact.label}: {text}"
            return f"{fact.label}: {text}."
        return f"{fact.label} was {value}."

    @staticmethod
    def _localize_shell(text, language):
        if language == ResponseLanguage.HINGLISH:
            return "Data ke hisaab se, " + text
        if language == ResponseLanguage.HINDI:
            return "डेटा के अनुसार: " + text
        return text

    @staticmethod
    def _resolve_language(language, question):
        if language != ResponseLanguage.AUTO:
            return language
        if re.search(r"[\u0900-\u097f]", question):
            return ResponseLanguage.HINDI
        if re.search(r"\b(?:kitna|kitni|sabse|batao|kyu|kyun|hua|hai|karo|kam|zyada)\b", question, re.I):
            return ResponseLanguage.HINGLISH
        return ResponseLanguage.ENGLISH

    @staticmethod
    def _source_payload(result, mode, language, evidence_ids):
        return {"status": result.status.value, "intent": result.intent.operation.value if result.intent else None,
            "facts": [fact.__dict__ for fact in result.facts], "evidence": [{"id": eid, "description": item.description, "values": item.values, "method": item.method} for eid, item in zip(evidence_ids, result.evidence)],
            "limitations": [item.message for item in result.limitations], "mode": mode.value, "language": language.value}

    @staticmethod
    def _prompt(source, strict=False):
        instruction = "Use only the supplied facts. Do not add numbers, currency, categories, periods, causes, forecasts, or recommendations."
        if strict:
            instruction += " Copy factual values exactly or omit them."
        return [{"role": "system", "content": instruction + " Return JSON with exactly answer, key_findings, limitations, source_ids."}, {"role": "user", "content": source}]

    @staticmethod
    def _parse_llm(content, evidence_ids):
        payload = json.loads(content)
        if not isinstance(payload, dict) or set(payload) != {"answer", "key_findings", "limitations", "source_ids"}:
            raise ValueError("invalid_response_schema")
        if not isinstance(payload["answer"], str) or not all(isinstance(payload[key], list) and all(isinstance(item, str) for item in payload[key]) for key in ("key_findings", "limitations", "source_ids")):
            raise ValueError("invalid_response_types")
        if not set(payload["source_ids"]).issubset(set(evidence_ids)):
            raise ValueError("invalid_source_ids")
        return payload

    @staticmethod
    def _build(result, text, mode, language, method, faithfulness, source_id, evidence_ids, metadata):
        return BusinessResponse(result.status, text["answer"], text["summary"], tuple(text["findings"]),
            result.evidence, result.limitations, language, mode, method, faithfulness, source_id, evidence_ids, metadata)


def _evidence_id(evidence):
    return hashlib.sha256(json.dumps({"description": evidence.description, "values": evidence.values, "method": evidence.method}, sort_keys=True, default=str).encode()).hexdigest()[:20]


def _friendly_limit(item: Limitation):
    mapping = {"external_cause_unknown": "The data supports the measured relationship, but it does not establish the external reason.",
        "currency_unknown": "The dataset does not specify a currency, so the value is shown without one.",
        "no_anomaly_signal": "No value met the current conservative anomaly threshold.",
        "zero_revenue_margin": "Margin cannot be calculated because revenue is zero."}
    return mapping.get(item.code, item.message)


def _forecast_question(question):
    return bool(re.search(r"\b(?:will|forecast|predict|next year|next quarter|double)\b", question, re.I))
