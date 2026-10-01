"""Deterministic reasoning over bounded KPI, group, and time aggregates."""

from __future__ import annotations

from uuid import uuid5, NAMESPACE_URL

from .anomalies import mad_anomalies
from .comparisons import compare, finite_number
from .models import BusinessInsight, Evidence, EvidenceStrength, InsightCategory, InsightReport, InterpretationType, Limitation
from .trends import MIN_TREND_PERIODS, classify_trend


METRICS = ("revenue", "cost", "profit", "units", "orders", "margin")


class BusinessInsightEngine:
    """Convert precomputed aggregates into bounded, causal-safe findings."""

    def analyze(self, *, dataset_id: str, backend: str, kpis: dict, groups: dict[str, list[dict]] | None = None,
                time_rows: list[dict] | None = None, currency: str | None = None,
                limitations: list[Limitation] | None = None, metadata: dict | None = None) -> InsightReport:
        insights: list[BusinessInsight] = []
        limits = list(limitations or [])
        groups = groups or {}
        time_rows = time_rows or []
        for metric in METRICS:
            value = finite_number(kpis.get(metric))
            if value is not None:
                insights.append(self._insight(dataset_id, InsightCategory.KPI, f"{metric.title()} overview",
                    f"Observed {metric} is {value:,.2f}.", InterpretationType.OBSERVATION,
                    EvidenceStrength.STRONG, metric=metric, current=value,
                    evidence=Evidence(f"Full-dataset {metric} aggregate", {metric: value})))

        self._period_insights(dataset_id, time_rows, insights, limits)
        for dimension, rows in groups.items():
            self._dimension_insights(dataset_id, dimension, rows, insights)
        self._profitability_insights(dataset_id, kpis, time_rows, insights, limits)

        if currency is None and any(finite_number(kpis.get(metric)) is not None for metric in ("revenue", "cost", "profit")):
            limits.append(Limitation("currency_unknown", "Currency metadata is unavailable; monetary values are currency-neutral.", ("formatting",)))
        limits.append(Limitation("causality_not_established", "The available aggregates describe outcomes and mathematical relationships, not external causal reasons.", ("explanation",)))
        for limitation in limits:
            insights.append(self._insight(dataset_id, InsightCategory.LIMITATION, "Analysis limitation", limitation.message,
                InterpretationType.INSUFFICIENT_EVIDENCE, EvidenceStrength.NOT_APPLICABLE,
                evidence=Evidence("Capability/schema assessment", {"code": limitation.code}, "availability check"),
                limitations=(limitation.code,)))
        return InsightReport(dataset_id, backend, currency, tuple(insights), tuple(_dedupe_limits(limits)), metadata or {})

    def _period_insights(self, dataset_id, rows, insights, limits):
        if not rows:
            limits.append(Limitation("no_time_series", "No valid date series is available for period comparisons or trends.", ("comparison", "trend", "time_anomaly")))
            return
        if len(rows) < 2:
            limits.append(Limitation("no_previous_period", "Only one valid period exists; no previous-period comparison is possible.", ("comparison",)))
        for metric in ("revenue", "cost", "profit", "units", "orders", "margin"):
            available = [(row.get("period"), finite_number(row.get(metric))) for row in rows]
            available = [(period, value) for period, value in available if value is not None]
            if len(available) >= 2:
                previous, current = available[-2], available[-1]
                result = compare(current[1], previous[1])
                pct = result["percentage_change"]
                suffix = "percentage change is undefined because the previous value is zero" if pct is None else f"a {abs(pct):.2f}% change"
                insights.append(self._insight(dataset_id, InsightCategory.COMPARISON, f"{metric.title()} period comparison",
                    f"{metric.title()} {result['direction']} from {previous[1]:,.2f} to {current[1]:,.2f}; {suffix}.",
                    InterpretationType.DERIVED_RELATIONSHIP, EvidenceStrength.STRONG, metric=metric, current=current[1], comparison=previous[1],
                    absolute=result["absolute_change"], percentage=pct, period=str(current[0]),
                    evidence=Evidence("Adjacent observed periods", {"previous_period": previous[0], "current_period": current[0]})))
            trend = classify_trend([row.get(metric) for row in rows])
            if trend["classification"] == "insufficient_data":
                if any(value is not None for _, value in available):
                    limits.append(Limitation(f"short_{metric}_trend", f"Only {trend['periods']} valid {metric} periods exist; at least {MIN_TREND_PERIODS} are required.", ("trend",)))
            else:
                insights.append(self._insight(dataset_id, InsightCategory.TREND, f"{metric.title()} trend",
                    f"{metric.title()} is descriptively {trend['classification']} across {trend['periods']} periods; this is not a forecast.",
                    InterpretationType.DERIVED_RELATIONSHIP, EvidenceStrength(trend["strength"]), metric=metric,
                    evidence=Evidence("Ordered aggregate series", trend, "least-squares slope and coefficient of variation")))
            for anomaly in mad_anomalies(rows, metric, "period"):
                insights.append(self._insight(dataset_id, InsightCategory.ANOMALY, f"Unusual {metric} period",
                    f"Period {anomaly['label']} is unusual relative to the series median using robust z-score (MAD).",
                    InterpretationType.OBSERVATION, EvidenceStrength.MODERATE, metric=metric, current=anomaly["value"], period=anomaly["label"],
                    evidence=Evidence("Robust aggregate-series anomaly", anomaly, "median absolute deviation; |robust z| >= 3.5")))

    def _dimension_insights(self, dataset_id, dimension, rows, insights):
        for metric in ("revenue", "profit", "units", "orders", "margin"):
            points = [(str(row.get("group_value")), finite_number(row.get(metric))) for row in rows]
            points = [(label, value) for label, value in points if value is not None]
            if not points:
                continue
            highest = max(points, key=lambda item: item[1])
            insights.append(self._insight(dataset_id, InsightCategory.DIMENSION, f"Highest {metric} by {dimension}",
                f"{highest[0]} has the highest {metric} at {highest[1]:,.2f}.", InterpretationType.OBSERVATION,
                EvidenceStrength.STRONG, metric=metric, current=highest[1], dimensions={dimension: highest[0]},
                evidence=Evidence("Bounded grouped aggregate ranking", {"groups_compared": len(points), "highest_group": highest[0]})))
            denominator = sum(value for _, value in points)
            if metric != "margin" and denominator != 0:
                share = highest[1] / denominator * 100.0
                insights.append(self._insight(dataset_id, InsightCategory.CONTRIBUTION, f"Largest {metric} contribution by {dimension}",
                    f"{highest[0]} contributes {share:.2f}% of observed {metric} across represented groups.",
                    InterpretationType.DERIVED_RELATIONSHIP, EvidenceStrength.STRONG, metric=metric, current=highest[1], percentage=share,
                    dimensions={dimension: highest[0]}, evidence=Evidence("Group value divided by grouped total", {"numerator": highest[1], "denominator": denominator})))

    def _profitability_insights(self, dataset_id, kpis, rows, insights, limits):
        revenue, cost, profit = (finite_number(kpis.get(key)) for key in ("revenue", "cost", "profit"))
        if profit is None and revenue is not None and cost is not None:
            profit = revenue - cost
            insights.append(self._insight(dataset_id, InsightCategory.PROFITABILITY, "Derived profit",
                f"Profit is derived as revenue minus cost: {profit:,.2f}.", InterpretationType.DERIVED_RELATIONSHIP,
                EvidenceStrength.STRONG, metric="profit", current=profit,
                evidence=Evidence("Revenue minus cost", {"revenue": revenue, "cost": cost, "profit": profit}, "arithmetic derivation")))
        if revenue == 0:
            limits.append(Limitation("zero_revenue_margin", "Profit margin is undefined because revenue is zero.", ("margin",)))
        elif revenue is not None and profit is not None:
            margin = profit / revenue * 100.0
            insights.append(self._insight(dataset_id, InsightCategory.PROFITABILITY, "Profit margin",
                f"Profit represents {margin:.2f}% of revenue.", InterpretationType.DERIVED_RELATIONSHIP,
                EvidenceStrength.STRONG, metric="margin", current=margin,
                evidence=Evidence("Profit divided by revenue", {"profit": profit, "revenue": revenue}, "ratio derivation")))
        if len(rows) >= 2:
            prior, current = rows[-2], rows[-1]
            revenue_change = compare(current.get("revenue"), prior.get("revenue"))
            cost_change = compare(current.get("cost"), prior.get("cost"))
            profit_change = compare(current.get("profit"), prior.get("profit"))
            rp, cp, pp = revenue_change["percentage_change"], cost_change["percentage_change"], profit_change["percentage_change"]
            if rp is not None and cp is not None and pp is not None:
                if rp > 0 and pp < 0:
                    summary = "Profit declined despite revenue growth; the available aggregates show costs absorbed more of revenue."
                elif cp > rp:
                    summary = "Costs changed faster than revenue, which mathematically pressured profit performance."
                elif rp > cp:
                    summary = "Revenue changed faster than costs, which mathematically supported profit performance."
                else:
                    summary = "Revenue and costs changed at the same rate, so their relative growth did not change profit margin."

                insights.append(
                    self._insight(
                        dataset_id,
                        InsightCategory.PROFITABILITY,
                        "Revenue-cost-profit relationship",
                        summary,
                        InterpretationType.DERIVED_RELATIONSHIP,
                        EvidenceStrength.STRONG,
                        evidence=Evidence(
                            "Adjacent-period percentage changes",
                            {
                                "revenue_change_percent": rp,
                                "cost_change_percent": cp,
                                "profit_change_percent": pp,
                            },
                        ),
                    )
                )
    def _insight(self, dataset_id, category, title, summary, interpretation, strength, *, evidence, metric=None,
                 current=None, comparison=None, absolute=None, percentage=None, dimensions=None, period=None,
                 importance="informational", limitations=()):
        stable = f"{dataset_id}:{category.value}:{title}:{metric}:{period}:{dimensions}"
        return BusinessInsight(str(uuid5(NAMESPACE_URL, stable)), category, title, summary, interpretation, strength,
            (evidence,), metric, current, comparison, absolute, percentage, dimensions or {}, period, importance, tuple(limitations))


def _dedupe_limits(items):
    return list({item.code: item for item in items}.values())
