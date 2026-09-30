"""Phase 5C grounded business-response tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from abhiai_analytics.data import DataManager
from abhiai_analytics.intelligence.faithfulness import FaithfulnessValidator
from abhiai_analytics.intelligence.models import Evidence, Limitation
from abhiai_analytics.intelligence.query_models import AnalyticsFact, BusinessQuestionResult, QueryStatus
from abhiai_analytics.intelligence.response_formatting import format_number
from abhiai_analytics.intelligence.response_models import FaithfulnessStatus, GenerationMethod, ResponseLanguage
from abhiai_analytics.intelligence.query_interpreter import OllamaServiceError


class _ResponseService:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.prompts = []

    def chat(self, messages, **kwargs):
        self.prompts.append(messages)
        value = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        if isinstance(value, Exception):
            raise value
        return value


class BusinessResponseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "sales.csv"
        rows = []
        for month in range(1, 7):
            for region, multiplier in (("North", 1), ("South", 2), ("East", 3), ("West", 4), ("Central", 5), ("Islands", 15)):
                revenue = month * 100 * multiplier
                cost = revenue * (0.5 if region != "Islands" else 0.8)
                rows.append({"Order Date": f"2026-{month:02d}-01", "Region": region,
                    "Item Type": "A" if multiplier % 2 else "B",
                    "Total Revenue": revenue, "Total Cost": cost, "Total Profit": revenue - cost,
                    "Units Sold": month * multiplier})
        pd.DataFrame(rows).to_csv(self.path, index=False)
        self.manager = DataManager(); self.assertTrue(self.manager.open(self.path).success)

    def tearDown(self):
        self.temp.cleanup()

    def test_kpi_modes_number_currency_and_traceability(self):
        concise = self.manager.explain_business_question("What is total revenue?", mode="concise")
        standard = self.manager.explain_business_question("What is total revenue?", mode="standard")
        detailed = self.manager.explain_business_question("What is total revenue?", mode="detailed")
        self.assertIn("K", concise.answer)
        self.assertNotRegex(concise.answer, r"[$₹€£]|\b(?:USD|INR|EUR|GBP)\b")
        self.assertLess(len(concise.answer), len(detailed.answer))
        self.assertIn("Evidence:", detailed.answer)
        self.assertTrue(standard.source_result_id)
        self.assertEqual(len(standard.source_evidence_ids), len(standard.supporting_evidence))
        self.assertEqual(standard.generation_method, GenerationMethod.DETERMINISTIC)

    def test_operation_rendering_and_why_limitation(self):
        questions = (
            "Which region generated the most profit?",
            "How did profit change?",
            "How is revenue trending?",
            "Which region contributed most to revenue?",
            "Why did profit change?",
            "Explain the business performance simply.",
        )
        for question in questions:
            response = self.manager.explain_business_question(question)
            self.assertTrue(response.answer, question)
        why = self.manager.explain_business_question("Why did profit change?", mode="detailed")
        self.assertIn("external reason", why.answer)
        anomaly = self.manager.explain_business_question("Are there unusual regions?")
        self.assertTrue(anomaly.answer)

    def test_ambiguity_missing_zero_and_forecast_are_useful(self):
        ambiguous = self.manager.explain_business_question("What contributed most to revenue?")
        self.assertIn("Which dimension", ambiguous.answer)
        forecast = self.manager.explain_business_question("Will revenue double next year?")
        self.assertIn("forecasting model", forecast.answer)
        sparse = Path(self.temp.name) / "sparse.csv"
        pd.DataFrame({"Region": ["N", "S"], "Revenue": [0, 0]}).to_csv(sparse, index=False)
        self.manager.open(sparse)
        missing = self.manager.explain_business_question("What is profit?")
        self.assertTrue(missing.answer)
        zero = self.manager.explain_business_question("Which region contributed most to revenue?")
        self.assertIn("zero", zero.answer)

    def test_english_hindi_hinglish_and_auto(self):
        english = self.manager.explain_business_question("What is total revenue?")
        hindi = self.manager.explain_business_question("What is total revenue?", language="hindi")
        hinglish = self.manager.explain_business_question("Total revenue kitna hai?")
        self.assertEqual(english.language, ResponseLanguage.ENGLISH)
        self.assertEqual(hindi.language, ResponseLanguage.HINDI)
        self.assertTrue(hindi.answer.startswith("डेटा के अनुसार"))
        self.assertEqual(hinglish.language, ResponseLanguage.HINGLISH)
        self.assertTrue(hinglish.answer.startswith("Data ke hisaab se"))

    def test_number_formatting(self):
        self.assertEqual(format_number(66185806881.22), "66.19B")
        self.assertEqual(format_number(1323716137624.376), "1.32T")
        self.assertEqual(format_number(29.5047199217, percentage=True), "29.50%")
        self.assertEqual(format_number(1234, compact=False), "1,234")
        self.assertEqual(format_number(50, currency="INR"), "INR 50")

    def test_model_unavailable_invalid_json_and_retry_fallback(self):
        unavailable = _ResponseService([OllamaServiceError("unavailable"), OllamaServiceError("unavailable")])
        response = self.manager.explain_business_question("What is total revenue?", response_service=unavailable)
        self.assertEqual(response.generation_method, GenerationMethod.DETERMINISTIC_FALLBACK)
        self.assertEqual(response.metadata["llm_attempts"], 2)
        invalid = _ResponseService(["not json", "[]"])
        response = self.manager.explain_business_question("What is total revenue?", response_service=invalid)
        self.assertEqual(response.faithfulness_status, FaithfulnessStatus.FAILED_FALLBACK)

    def test_safe_local_paraphrase_passes(self):
        base = self.manager.ask_business_question("What is total revenue?")
        deterministic = self.manager.explain_business_question("What is total revenue?")
        payload = json.dumps({"answer": deterministic.answer, "key_findings": [deterministic.answer],
            "limitations": [], "source_ids": list(deterministic.source_evidence_ids)})
        service = _ResponseService([payload])
        response = self.manager.explain_business_question("What is total revenue?", response_service=service)
        self.assertEqual(response.generation_method, GenerationMethod.LOCAL_LLM)
        self.assertEqual(response.faithfulness_status, FaithfulnessStatus.PASSED)
        self.assertLessEqual(response.metadata["llm_input_chars"], 12000)

    def test_numeric_currency_category_cause_forecast_hallucinations_fail(self):
        deterministic = self.manager.explain_business_question("Which region has the highest revenue?")
        source_ids = list(deterministic.source_evidence_ids)
        false_answers = (
            "Revenue was 90B.",
            "Revenue was $66.19B.",
            "South had the highest revenue.",
            "Revenue fell because customers preferred competitors.",
            "Revenue will grow 20% next year.",
            "Margin was 35%.",
            "Revenue was highest in 2035.",
        )
        for false_answer in false_answers:
            payload = json.dumps({"answer": false_answer, "key_findings": [], "limitations": [], "source_ids": source_ids})
            service = _ResponseService([payload, payload])
            response = self.manager.explain_business_question("Which region has the highest revenue?", response_service=service)
            self.assertEqual(response.generation_method, GenerationMethod.DETERMINISTIC_FALLBACK, false_answer)
            self.assertEqual(service.calls, 2)

    def test_safe_percentage_paraphrases(self):
        result = BusinessQuestionResult("How did revenue change?", QueryStatus.ANSWERED, None,
            facts=(AnalyticsFact("Revenue change", "revenue", 100, percentage=-8.2),),
            evidence=(Evidence("Period change", {"percentage_change": -8.2}),), metadata={})
        validator = FaithfulnessValidator()
        self.assertTrue(validator.validate("Revenue fell by 8.2%.", result).valid)
        self.assertTrue(validator.validate("Revenue was down 8.20%.", result).valid)

    def test_duckdb_path_and_bounds(self):
        manager = DataManager(); self.assertTrue(manager.open(self.path, mode="duckdb").success)
        response = manager.explain_business_question("Which region generated the most profit?")
        self.assertEqual(response.metadata["query_layer"], "phase_5b")
        self.assertEqual(response.metadata["raw_rows_returned"], 0)
        self.assertLessEqual(response.metadata["aggregate_rows"], 11000)
        self.assertIsNone(manager.frame())


if __name__ == "__main__":
    unittest.main()