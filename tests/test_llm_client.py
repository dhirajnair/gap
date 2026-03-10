from __future__ import annotations

import unittest

from src.llm_client import OpenRouterLLMClient


class TestExtractSQL(unittest.TestCase):

    def test_json_format(self):
        result = OpenRouterLLMClient._extract_sql('{"sql": "SELECT * FROM t"}')
        self.assertEqual(result, "SELECT * FROM t")

    def test_json_null(self):
        result = OpenRouterLLMClient._extract_sql('{"sql": null}')
        self.assertIsNone(result)

    def test_raw_select(self):
        result = OpenRouterLLMClient._extract_sql("Here is the query: SELECT id FROM t WHERE age > 5")
        self.assertTrue(result.startswith("SELECT"))

    def test_no_sql(self):
        result = OpenRouterLLMClient._extract_sql("I cannot generate a query for this.")
        self.assertIsNone(result)

    def test_empty_string(self):
        result = OpenRouterLLMClient._extract_sql("")
        self.assertIsNone(result)

    def test_json_empty_sql(self):
        result = OpenRouterLLMClient._extract_sql('{"sql": ""}')
        self.assertIsNone(result)

    def test_json_whitespace_sql(self):
        result = OpenRouterLLMClient._extract_sql('{"sql": "   "}')
        self.assertIsNone(result)

    def test_select_case_insensitive(self):
        result = OpenRouterLLMClient._extract_sql("The answer is: select count(*) from t")
        self.assertIsNotNone(result)
        self.assertTrue(result.lower().startswith("select"))


if __name__ == "__main__":
    unittest.main()
