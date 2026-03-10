from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from src.pipeline import AnalyticsPipeline
from src.types import PipelineOutput


class TestEdgeCases(unittest.TestCase):

    def _make_pipeline(self):
        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.generate_sql.return_value = MagicMock(
            sql=None, timing_ms=0.0, error=None,
            llm_stats={"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": "test"},
        )
        mock_llm.generate_answer.return_value = MagicMock(
            answer="I cannot answer this with the available table and schema. Please rephrase using known survey fields.",
            timing_ms=0.0, error=None,
            llm_stats={"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": "test"},
        )
        p = AnalyticsPipeline.__new__(AnalyticsPipeline)
        p.db_path = Path("/tmp/nonexistent.sqlite")
        p.llm = mock_llm
        p.executor = MagicMock()
        p.schema = {"tables": {}}
        p._allowed_tables = set()
        p.executor.run.return_value = MagicMock(rows=[], row_count=0, timing_ms=0.0, error=None)
        return p

    def test_empty_question(self):
        p = self._make_pipeline()
        result = p.run("")
        self.assertIsInstance(result, PipelineOutput)
        self.assertEqual(result.status, "unanswerable")

    def test_whitespace_only_question(self):
        p = self._make_pipeline()
        result = p.run("    \n\t  ")
        self.assertIsInstance(result, PipelineOutput)
        self.assertEqual(result.status, "unanswerable")

    def test_very_long_question_truncated(self):
        p = self._make_pipeline()
        long_q = "x" * 5000
        result = p.run(long_q)
        self.assertIsInstance(result, PipelineOutput)
        # Should have been truncated before reaching LLM
        if p.llm.generate_sql.called:
            called_q = p.llm.generate_sql.call_args[0][0]
            self.assertLessEqual(len(called_q), 1001)

    def test_output_contract(self):
        p = self._make_pipeline()
        result = p.run("test question")
        self.assertIn(result.status, {"success", "unanswerable", "invalid_sql", "error"})
        self.assertIsInstance(result.timings, dict)
        for key in ("sql_generation_ms", "sql_validation_ms", "sql_execution_ms", "answer_generation_ms", "total_ms"):
            self.assertIn(key, result.timings)


if __name__ == "__main__":
    unittest.main()
