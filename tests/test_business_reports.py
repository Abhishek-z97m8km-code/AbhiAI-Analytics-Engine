"""Phase 5D structured business-report tests."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from abhiai_analytics.data import DataManager
from abhiai_analytics.intelligence.report_models import (
    BusinessReport, ReportChartType, ReportDetailLevel, ReportStatus, ReportType,
    SectionType,
)
from abhiai_analytics.intelligence.report_validator import validate_report
from abhiai_analytics.intelligence.response_models import GenerationMethod


class _Service:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return self.value


class BusinessReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "sales.csv"
        rows = []
        for month in range(1, 7):
            for region, scale in (("North", 1), ("South", 2), ("East", 4), ("Islands", 14)):
                revenue = month * 100 * scale
                cost = revenue * (0.5 if region != "Islands" else 0.8)
                rows.append({"Order Date": f"2026-{month:02d}-01", "Region": region,
                    "Item Type": "A" if scale % 2 else "B", "Total Revenue": revenue,
                    "Total Cost": cost, "Total Profit": revenue-cost, "Units Sold": month*scale})
        pd.DataFrame(rows).to_csv(self.path, index=False)
        self.manager = DataManager()
        self.assertTrue(self.manager.open(self.path).success)

    def tearDown(self):
        self.temp.cleanup()

    def test_contract_identity_serialization_and_traceability(self):
        report = self.manager.generate_business_report()
        self.assertEqual(report.report_type, ReportType.BUSINESS_OVERVIEW)
        self.assertEqual(report.dataset.filename, "sales.csv")
        self.assertNotIn(str(self.path.parent), json.dumps(report.to_dict()))
        restored = BusinessReport.from_dict(json.loads(json.dumps(report.to_dict())))
        self.assertEqual(restored.report_id, report.report_id)
        self.assertEqual(restored.source_evidence_ids, report.source_evidence_ids)
        self.assertEqual(restored.chart_specs, report.chart_specs)
        self.assertEqual(restored.limitations, report.limitations)
        self.assertTrue(validate_report(restored).valid)
        evidence = set(report.source_evidence_ids)
        self.assertTrue(all(set(k.source_evidence_ids) <= evidence for k in report.kpis))
        self.assertTrue(all(set(f.source_evidence_ids) <= evidence for f in report.key_findings))
        self.assertTrue(all(set(c.source_evidence_ids) <= evidence for c in report.chart_specs))

    def test_report_types_sections_and_supported_kpis(self):
        for report_type in ReportType:
            report = self.manager.generate_business_report(report_type.value)
            self.assertNotEqual(report.status, ReportStatus.INSUFFICIENT_DATA)
            self.assertEqual(report.report_type, report_type)
            self.assertEqual({k.metric for k in report.kpis}, {"revenue", "cost", "profit", "units", "margin"})
            section_types = {s.section_type for s in report.sections}
            self.assertIn(SectionType.REVENUE_ANALYSIS, section_types)
            self.assertIn(SectionType.COST_ANALYSIS, section_types)
            self.assertIn(SectionType.PROFITABILITY, section_types)
            self.assertIn(SectionType.LIMITATIONS, section_types)
            self.assertIsNone(report.currency)

    def test_detail_levels_are_bounded_and_use_same_kpis(self):
        reports = [self.manager.generate_business_report(detail_level=level.value) for level in ReportDetailLevel]
        self.assertEqual(*({(k.metric, k.raw_value) for k in report.kpis} for report in reports))
        self.assertLessEqual(len(reports[0].key_findings), 5)
        self.assertLessEqual(len(reports[1].key_findings), 10)
        self.assertLessEqual(len(reports[2].key_findings), 20)
        self.assertLessEqual(len(reports[0].chart_specs), 3)
        self.assertLessEqual(len(reports[2].chart_specs), 10)

    def test_language_reuses_response_architecture(self):
        hindi = self.manager.generate_business_report(language="hindi")
        hinglish = self.manager.generate_business_report(language="hinglish")
        self.assertTrue(hindi.executive_summary.startswith("डेटा के अनुसार"))
        self.assertTrue(hinglish.executive_summary.startswith("Data ke hisaab se"))

    def test_deterministic_chart_selection_and_bounds(self):
        report = self.manager.generate_business_report(detail_level="detailed")
        types = {chart.chart_type for chart in report.chart_specs}
        self.assertIn(ReportChartType.KPI, types)
        self.assertIn(ReportChartType.LINE, types)
        self.assertIn(ReportChartType.BAR, types)
        self.assertTrue(all(len(chart.data) <= 120 for chart in report.chart_specs))
        self.assertEqual(report.generation_metadata["raw_rows"], 0)
        self.assertLessEqual(report.generation_metadata["aggregate_rows"], 11000)

    def test_missing_schema_adapts_without_fake_sections(self):
        sparse = Path(self.temp.name) / "sparse.csv"
        pd.DataFrame({"Region": ["N", "S"], "Revenue": [10, 20]}).to_csv(sparse, index=False)
        self.manager.open(sparse)
        report = self.manager.generate_business_report("financial_performance")
        self.assertEqual(report.status, ReportStatus.INSUFFICIENT_DATA)
        self.assertNotIn("profit", {k.metric for k in report.kpis})
        sections = {s.section_type for s in report.sections}
        self.assertNotIn(SectionType.COST_ANALYSIS, sections)
        self.assertNotIn(SectionType.PROFITABILITY, sections)
        codes = {item.code for item in report.limitations}
        self.assertTrue({"missing_cost", "missing_profit", "missing_date"} <= codes)
        self.assertIn("unsupported_report_type", codes)

    def test_bad_model_output_fails_closed(self):
        for text in ("Revenue was 90B.", "Revenue was $39.9K.",
                     "Europe had the highest profit.",
                     "Profit declined because competitors cut prices.",
                     "Revenue will increase 25% next year."):
            payload = json.dumps({"answer": text, "key_findings": [], "limitations": [], "source_ids": []})
            service = _Service(payload)
            report = self.manager.generate_business_report(response_service=service)
            self.assertEqual(report.generation_method, GenerationMethod.DETERMINISTIC_FALLBACK, text)
            self.assertNotEqual(report.executive_summary, text)
            self.assertLessEqual(service.calls, 2)

    def test_invalid_model_json_and_orphan_prevention(self):
        report = self.manager.generate_business_report(response_service=_Service("not json"))
        self.assertEqual(report.generation_method, GenerationMethod.DETERMINISTIC_FALLBACK)
        broken = replace(report, source_evidence_ids=())
        result = validate_report(broken)
        self.assertFalse(result.valid)
        self.assertTrue(any(error.startswith("orphan_") or error == "invalid_chart" for error in result.errors))

    def test_duckdb_stays_lazy(self):
        manager = DataManager()
        self.assertTrue(manager.open(self.path, mode="duckdb").success)
        report = manager.generate_business_report()
        self.assertEqual(report.dataset.backend, "duckdb")
        self.assertIsNone(manager.frame())
        self.assertTrue(report.generation_metadata["valid"])


if __name__ == "__main__":
    unittest.main()