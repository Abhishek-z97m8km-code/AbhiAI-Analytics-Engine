"""Phase 5A evidence-grounded business intelligence tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from abhiai_analytics.data import DataManager
from abhiai_analytics.intelligence.anomalies import mad_anomalies
from abhiai_analytics.intelligence.business_insights import BusinessInsightEngine
from abhiai_analytics.intelligence.comparisons import compare
from abhiai_analytics.intelligence.models import InterpretationType, Limitation
from abhiai_analytics.intelligence.schema import discover_schema
from abhiai_analytics.intelligence.trends import classify_trend


class IntelligencePrimitiveTests(unittest.TestCase):
    def test_conservative_schema_mapping(self):
        schema = discover_schema(["Total Revenue", "Total Cost", "Region", "Order Date", "Mystery"])
        self.assertEqual(schema.metrics["revenue"], "Total Revenue")
        self.assertEqual(schema.metrics["cost"], "Total Cost")
        self.assertEqual(schema.dimensions, ("Region",))
        self.assertEqual(schema.dates, ("Order Date",))
        self.assertNotIn("profit", schema.metrics)

    def test_comparison_percentage_and_zero_denominator(self):
        self.assertEqual(compare(120, 100)["percentage_change"], 20)
        result = compare(-5, 0)
        self.assertEqual(result["absolute_change"], -5)
        self.assertIsNone(result["percentage_change"])
        self.assertEqual(compare(float("nan"), 1)["direction"], "unknown")

    def test_trend_classification_and_minimum_data(self):
        self.assertEqual(classify_trend([1, 2, 3])["classification"], "insufficient_data")
        self.assertEqual(classify_trend([10, 20, 30, 40])["classification"], "increasing")
        self.assertEqual(classify_trend([40, 30, 20, 10])["classification"], "decreasing")
        self.assertEqual(classify_trend([10, 10, 10, 10])["classification"], "stable")
        self.assertEqual(classify_trend([1, 100, 1, 100, 1])["classification"], "volatile")

    def test_mad_anomaly_is_conservative_and_bounded(self):
        rows = [{"period": str(i), "revenue": value} for i, value in enumerate([10, 11, 9, 10, 100])]
        anomalies = mad_anomalies(rows, "revenue", "period")
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["value"], 100)
        self.assertEqual(mad_anomalies(rows[:4], "revenue", "period"), [])
        self.assertEqual(mad_anomalies([{"period": str(i), "revenue": 1} for i in range(6)], "revenue", "period"), [])


class BusinessInsightEngineTests(unittest.TestCase):
    def test_kpi_comparison_trend_contribution_profitability_and_evidence(self):
        time_rows = [
            {"period": "2026-01", "revenue": 100, "cost": 60, "profit": 40, "units": 10},
            {"period": "2026-02", "revenue": 110, "cost": 70, "profit": 40, "units": 11},
            {"period": "2026-03", "revenue": 120, "cost": 80, "profit": 40, "units": 12},
            {"period": "2026-04", "revenue": 140, "cost": 100, "profit": 40, "units": 14},
        ]
        groups = {"Region": [
            {"group_value": "North", "revenue": 300, "profit": 70, "units": 30, "margin": 23.3},
            {"group_value": "South", "revenue": 170, "profit": 90, "units": 17, "margin": 52.9},
        ]}
        report = BusinessInsightEngine().analyze(dataset_id="sales", backend="legacy",
            kpis={"revenue": 470, "cost": 310, "profit": 160, "units": 47}, groups=groups, time_rows=time_rows)
        categories = {insight.category.value for insight in report.insights}
        self.assertTrue({"kpi", "comparison", "trend", "dimension", "contribution", "profitability", "limitation"} <= categories)
        self.assertTrue(all(insight.interpretation_type != InterpretationType.POSSIBLE_EXPLANATION for insight in report.insights))
        self.assertTrue(any(item.code == "currency_unknown" for item in report.limitations))
        self.assertTrue(any(item.code == "causality_not_established" for item in report.limitations))
        self.assertEqual(report.currency, None)
        self.assertNotIn("$", str(report.to_dict()))
        self.assertNotIn("₹", str(report.to_dict()))
        contribution = next(item for item in report.insights if item.category.value == "contribution" and item.metric == "revenue")
        self.assertAlmostEqual(contribution.percentage_change, 300 / 470 * 100)

    def test_derived_profit_zero_revenue_and_missing_capabilities(self):
        report = BusinessInsightEngine().analyze(dataset_id="zero", backend="legacy",
            kpis={"revenue": 0, "cost": 10}, limitations=[Limitation("missing_date", "No date.")])
        derived = [item for item in report.insights if item.title == "Derived profit"]
        self.assertEqual(derived[0].current_value, -10)
        self.assertTrue(any(item.code == "zero_revenue_margin" for item in report.limitations))
        self.assertTrue(any(item.code == "no_time_series" for item in report.limitations))

    def test_single_category_and_negative_values_are_preserved(self):
        report = BusinessInsightEngine().analyze(dataset_id="negative", backend="legacy",
            kpis={"revenue": -100, "cost": -50, "profit": -50},
            groups={"Region": [{"group_value": "Only", "revenue": -100, "profit": -50, "margin": 50}]})
        highest = next(item for item in report.insights if item.title == "Highest revenue by Region")
        self.assertEqual(highest.current_value, -100)


class DataManagerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "sales.csv"
        pd.DataFrame({
            "Order Date": ["2026-01-01", "2026-02-01", "bad", "2026-04-01", "2026-05-01"],
            "Region": ["N", "S", "N", None, "S"],
            "Total Revenue": [100, 200, None, 400, 500],
            "Total Cost": [60, 130, 1, 260, 300],
            "Units Sold": [10, 20, 1, 40, 50],
        }).to_csv(self.path, index=False)

    def tearDown(self):
        self.temp.cleanup()

    def test_pandas_backend_derives_profit_handles_nulls_and_invalid_dates(self):
        manager = DataManager()
        result = manager.open(self.path)
        self.assertTrue(result.success)
        report = manager.generate_business_insights(granularity="monthly")
        self.assertEqual(report.backend, "legacy")
        kpis = {item.metric: item.current_value for item in report.insights if item.category.value == "kpi"}
        self.assertEqual(kpis["revenue"], 1200)
        self.assertEqual(kpis["cost"], 751)
        self.assertEqual(kpis["profit"], 449)
        self.assertTrue(any(item.code == "invalid_dates" for item in report.limitations))
        self.assertLessEqual(report.metadata["aggregate_rows"], 10005)

    def test_duckdb_backend_remains_duckdb_and_uses_bounded_aggregates(self):
        manager = DataManager()
        result = manager.open(self.path, mode="duckdb")
        self.assertTrue(result.success)
        report = manager.generate_business_insights(granularity="monthly")
        self.assertEqual(report.backend, "duckdb")
        self.assertEqual(manager.get_record(manager.active_id).backend, "duckdb")
        self.assertIsNone(manager.frame())
        self.assertEqual(report.metadata["row_count"], 5)
        self.assertLessEqual(report.metadata["aggregate_rows"], 11000)

    def test_missing_revenue_cost_profit_date_and_dimension_are_limitations(self):
        path = Path(self.temp.name) / "sparse.csv"
        pd.DataFrame({"Unknown": [1, 2]}).to_csv(path, index=False)
        manager = DataManager()
        manager.open(path)
        report = manager.generate_business_insights()
        codes = {item.code for item in report.limitations}
        self.assertTrue({"missing_revenue", "missing_cost", "missing_profit", "missing_date", "missing_dimension"} <= codes)


if __name__ == "__main__":
    unittest.main()