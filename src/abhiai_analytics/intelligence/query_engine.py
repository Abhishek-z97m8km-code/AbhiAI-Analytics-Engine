"""Validated execution of typed analytics intents over one bounded context."""

from __future__ import annotations

import re

from .anomalies import mad_anomalies
from .business_insights import BusinessInsightEngine
from .comparisons import finite_number
from .integration import collect_analytics_context
from .models import Evidence, InsightCategory, Limitation
from .query_interpreter import interpret_question
from .query_models import AnalyticsFact, AnalyticsOperation, BusinessQuestionResult, QueryStatus


class BusinessQuestionEngine:
    """Interpret and answer read-only business questions without arbitrary SQL."""

    def ask(self, manager, question: str, dataset_id: str | None = None, *, semantic_service=None) -> BusinessQuestionResult:
        try:
            context = collect_analytics_context(manager, dataset_id, granularity="monthly")
        except ValueError as error:
            return BusinessQuestionResult(str(question), QueryStatus.INSUFFICIENT_DATA, None,
                limitations=(Limitation("dataset_unavailable", str(error)),))
        interpretation = interpret_question(question, context["schema"], semantic_service=semantic_service)
        if interpretation.status != QueryStatus.ANSWERED or interpretation.intent is None:
            limitations = (interpretation.limitation,) if interpretation.limitation else ()
            return BusinessQuestionResult(str(question), interpretation.status, interpretation.intent,
                limitations=limitations, ambiguities=interpretation.ambiguities,
                suggested_clarification=interpretation.suggested_clarification,
                backend=context["backend"], metadata=self._metadata(context))

        report = BusinessInsightEngine().analyze(
            dataset_id=context["dataset_id"], backend=context["backend"], kpis=context["kpis"],
            groups=context["groups"], time_rows=context["time_rows"], currency=None,
            limitations=context["limitations"], metadata=context["metadata"],
        )
        intent = interpretation.intent
        handlers = {
            AnalyticsOperation.KPI_LOOKUP: self._kpi,
            AnalyticsOperation.RANKING: self._ranking,
            AnalyticsOperation.COMPARISON: self._comparison,
            AnalyticsOperation.TREND: self._trend,
            AnalyticsOperation.CONTRIBUTION: self._contribution,
            AnalyticsOperation.PROFITABILITY: self._profitability,
            AnalyticsOperation.ANOMALY: self._anomaly,
            AnalyticsOperation.DIAGNOSTIC: self._diagnostic,
            AnalyticsOperation.SUMMARY: self._summary,
        }
        facts, evidence, answer, extra_limits = handlers[intent.operation](intent, context, report, str(question))
        limitations = tuple(_dedupe_limits([*report.limitations, *extra_limits]))
        status = QueryStatus.ANSWERED if facts else QueryStatus.INSUFFICIENT_DATA
        if not facts and not extra_limits:
            limitations += (Limitation("no_supported_result", "The available evidence does not support the requested result."),)
        return BusinessQuestionResult(str(question), status, intent, tuple(facts), tuple(evidence),
            limitations, answer=answer if facts else None, backend=context["backend"], metadata=self._metadata(context))

    @staticmethod
    def _kpi(intent, context, report, question):
        value = finite_number(context["kpis"].get(intent.metric))
        if value is None:
            return [], [], None, [Limitation("missing_metric", f"{intent.metric} is unavailable.")]
        evidence = Evidence("Full-dataset aggregate", {intent.metric: value})
        fact = AnalyticsFact(f"Total {intent.metric}", intent.metric, value)
        return [fact], [evidence], f"Total {intent.metric}: {value:,.2f}.", []

    @staticmethod
    def _ranking(intent, context, report, question):
        rows = context["groups"].get(intent.dimension, [])
        points = [(str(row.get("group_value")), finite_number(row.get(intent.metric))) for row in rows]
        points = [(label, value) for label, value in points if value is not None]
        if not points:
            return [], [], None, [Limitation("missing_group_metric", f"{intent.metric} by {intent.dimension} is unavailable.")]
        reverse = intent.sort_direction == "descending"
        selected = sorted(points, key=lambda item: item[1], reverse=reverse)[:intent.limit]
        facts = [AnalyticsFact(f"{label} {intent.metric}", intent.metric, value, intent.dimension, label) for label, value in selected]
        evidence = Evidence("Bounded grouped aggregate ranking", {"groups_compared": len(points), "direction": intent.sort_direction, "limit": intent.limit})
        answer = "; ".join(f"{item.dimension_value}: {item.value:,.2f}" for item in facts)
        return facts, [evidence], answer + ".", []

    @staticmethod
    def _comparison(intent, context, report, question):
        if re.search(r"\bfaster\b", question, re.I):
            relationship = [item for item in report.insights if item.category == InsightCategory.PROFITABILITY and item.title == "Revenue-cost-profit relationship"]
            return _from_insights(relationship[-1:], "At least two valid periods with revenue, cost, and profit are required.")
        if intent.secondary_metric:
            facts = []
            values = {}
            for metric in (intent.metric, intent.secondary_metric):
                value = finite_number(context["kpis"].get(metric))
                if value is not None:
                    facts.append(AnalyticsFact(f"Total {metric}", metric, value))
                    values[metric] = value
            if len(facts) == 2:
                return facts, [Evidence("Full-dataset aggregates", values)], "; ".join(f"{fact.metric}: {fact.value:,.2f}" for fact in facts) + ".", []
        matches = [item for item in report.insights if item.category == InsightCategory.COMPARISON and item.metric == intent.metric]
        relationship = [item for item in report.insights if item.category == InsightCategory.PROFITABILITY and item.title == "Revenue-cost-profit relationship"]
        selected = (matches[-1:] or relationship[-1:])
        return _from_insights(selected, "No supported period comparison is available.")

    @staticmethod
    def _trend(intent, context, report, question):
        selected = [item for item in report.insights if item.category == InsightCategory.TREND and item.metric == intent.metric]
        return _from_insights(selected[-1:], f"At least four valid {intent.metric} periods are required for a trend.")

    @staticmethod
    def _contribution(intent, context, report, question):
        rows = context["groups"].get(intent.dimension, [])
        points = [(str(row.get("group_value")), finite_number(row.get(intent.metric))) for row in rows]
        points = [(label, value) for label, value in points if value is not None]
        denominator = sum(value for _, value in points)
        if not points or denominator == 0:
            return [], [], None, [Limitation("undefined_contribution", "Contribution is unavailable because the grouped total is missing or zero.")]
        normalized_question = _normalize(question)
        requested = next(((label, value) for label, value in points if _contains(normalized_question, _normalize(label))), None)
        selected = requested or max(points, key=lambda item: item[1])
        percentage = selected[1] / denominator * 100.0
        fact = AnalyticsFact(f"{selected[0]} contribution", intent.metric, selected[1], intent.dimension, selected[0], percentage)
        evidence = Evidence("Group aggregate divided by represented grouped total", {"numerator": selected[1], "denominator": denominator})
        return [fact], [evidence], f"{selected[0]} contributed {percentage:.2f}% of {intent.metric}.", []

    @staticmethod
    def _profitability(intent, context, report, question):
        selected = [item for item in report.insights if item.category == InsightCategory.PROFITABILITY]
        return _from_insights(selected, "Profitability evidence is unavailable.")

    @staticmethod
    def _anomaly(intent, context, report, question):
        if intent.dimension:
            rows = context["groups"].get(intent.dimension, [])
            candidates = mad_anomalies(rows, intent.metric, "group_value")
            facts = [AnalyticsFact(f"Unusual {intent.metric}", intent.metric, row["value"], intent.dimension, row["label"]) for row in candidates]
            evidence = [Evidence("Grouped robust-z anomaly", row, "median absolute deviation; |robust z| >= 3.5") for row in candidates]
            if not facts:
                return [], [], None, [Limitation("no_anomaly_signal", "No group met the conservative MAD anomaly threshold, or fewer than five valid groups exist.")]
            return facts, evidence, f"{len(facts)} statistically unusual group value(s) were identified.", []
        selected = [item for item in report.insights if item.category == InsightCategory.ANOMALY and item.metric == intent.metric]
        return _from_insights(selected, "No period met the conservative MAD anomaly threshold, or too few periods exist.")

    @staticmethod
    def _diagnostic(intent, context, report, question):
        comparison = [item for item in report.insights if item.category == InsightCategory.COMPARISON and item.metric in {intent.metric, "revenue", "cost"}]
        relationships = [item for item in report.insights if item.category == InsightCategory.PROFITABILITY and item.title == "Revenue-cost-profit relationship"]
        selected = comparison[-3:] + relationships[-1:]
        facts, evidence, answer, limits = _from_insights(selected, "A diagnostic requires at least two valid periods and supported component metrics.")
        limits.append(Limitation("external_cause_unknown", "The dataset supports mathematical drivers but does not establish the external reason for the change."))
        return facts, evidence, answer, limits

    @staticmethod
    def _summary(intent, context, report, question):
        selected = [item for item in report.insights if item.category == InsightCategory.KPI][:6]
        return _from_insights(selected, "No supported KPI summary is available.")

    @staticmethod
    def _metadata(context):
        return {**context["metadata"], "query_layer": "phase_5b", "raw_rows_returned": 0}


def _from_insights(insights, missing_message):
    if not insights:
        return [], [], None, [Limitation("insufficient_evidence", missing_message)]
    facts = []
    for item in insights:
        value = item.current_value
        if value is None and item.category == InsightCategory.TREND and item.evidence:
            value = item.evidence[0].values.get("classification")
        if value is None:
            value = item.summary
        facts.append(AnalyticsFact(item.title, item.metric, value, period=item.period, percentage=item.percentage_change))
    evidence = [entry for item in insights for entry in item.evidence]
    return facts, evidence, " ".join(item.summary for item in insights), []


def _dedupe_limits(items):
    return list({item.code: item for item in items}.values())


def _normalize(value):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _contains(text, phrase):
    return bool(re.search(r"(?:^|\s)" + re.escape(phrase) + r"(?:$|\s)", text))
