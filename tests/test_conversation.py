from __future__ import annotations

import unittest
from unittest.mock import MagicMock
from pathlib import Path

from src.conversation import ConversationManager
from src.pipeline import AnalyticsPipeline
from src.types import PipelineOutput


class TestConversationManager(unittest.TestCase):

    def setUp(self):
        self.cm = ConversationManager()

    # --- State management ---

    def test_initial_state(self):
        self.assertEqual(self.cm.turn_count, 0)
        self.assertEqual(self.cm.turns, [])

    def test_add_turn(self):
        self.cm.add_turn("What is X?", "SELECT x FROM t", "X is 42")
        self.assertEqual(self.cm.turn_count, 1)
        self.assertEqual(self.cm.turns[0].question, "What is X?")

    def test_clear(self):
        self.cm.add_turn("q", "s", "a")
        self.cm.clear()
        self.assertEqual(self.cm.turn_count, 0)

    # --- Follow-up detection ---

    def test_no_history_not_followup(self):
        self.assertFalse(self.cm.is_followup("What is the average age?"))

    def test_pronoun_start_is_followup(self):
        self.cm.add_turn("q", "s", "a")
        self.assertTrue(self.cm.is_followup("It should be sorted by name"))

    def test_conjunction_start_is_followup(self):
        self.cm.add_turn("q", "s", "a")
        self.assertTrue(self.cm.is_followup("And filter by age > 20"))

    def test_pattern_what_about_is_followup(self):
        self.cm.add_turn("q", "s", "a")
        self.assertTrue(self.cm.is_followup("What about males specifically?"))

    def test_pattern_now_sort_is_followup(self):
        self.cm.add_turn("q", "s", "a")
        self.assertTrue(self.cm.is_followup("Now sort by anxiety score"))

    def test_short_question_not_followup(self):
        self.cm.add_turn("q", "s", "a")
        self.assertFalse(self.cm.is_followup("Only males"))

    def test_standalone_question_not_followup(self):
        self.cm.add_turn("q", "s", "a")
        self.assertFalse(self.cm.is_followup(
            "What is the addiction level distribution by gender?"
        ))

    # --- Context enrichment ---

    def test_standalone_returns_original(self):
        question = "What is the addiction level distribution by gender?"
        self.assertEqual(self.cm.build_context_prompt(question), question)

    def test_followup_includes_history(self):
        self.cm.add_turn(
            "What is the addiction level distribution by gender?",
            "SELECT gender, AVG(addiction_level) FROM t GROUP BY gender",
            "Males 5.2, Females 4.8",
        )
        prompt = self.cm.build_context_prompt("What about males specifically?")
        self.assertIn("Conversation history:", prompt)
        self.assertIn("Q1:", prompt)
        self.assertIn("SQL1:", prompt)
        self.assertIn("A1:", prompt)
        self.assertIn("Follow-up question: What about males specifically?", prompt)

    def test_history_limited_to_last_3(self):
        for i in range(5):
            self.cm.add_turn(f"q{i}", f"sql{i}", f"a{i}")
        prompt = self.cm.build_context_prompt("Now sort it")
        self.assertNotIn("q0", prompt)
        self.assertNotIn("q1", prompt)
        self.assertIn("q2", prompt)
        self.assertIn("q3", prompt)
        self.assertIn("q4", prompt)


class TestMultiTurnPipeline(unittest.TestCase):
    """Integration test using mocked LLM to verify the full conversation flow."""

    def _make_pipeline(self):
        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        p = AnalyticsPipeline.__new__(AnalyticsPipeline)
        p.db_path = Path("/tmp/nonexistent.sqlite")
        p.llm = mock_llm
        p.executor = MagicMock()
        p.executor.run.return_value = MagicMock(
            rows=[{"age_group": "18-24", "avg": 5.1}],
            row_count=1, timing_ms=0.0, error=None,
        )
        p.schema = {"tables": {}}
        p._allowed_tables = set()
        p._validation_conn = None
        return p

    def _mock_sql_return(self, mock_llm, sql):
        mock_llm.generate_sql.return_value = MagicMock(
            sql=sql, timing_ms=1.0, error=None,
            llm_stats={"llm_calls": 1, "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "model": "test"},
        )
        mock_llm.generate_answer.return_value = MagicMock(
            answer="Answer based on results.", timing_ms=1.0, error=None,
            llm_stats={"llm_calls": 1, "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "model": "test"},
        )

    def test_four_turn_conversation(self):
        p = self._make_pipeline()
        cm = ConversationManager()

        # Turn 1: standalone
        self._mock_sql_return(p.llm, "SELECT gender, AVG(addiction_level) FROM t GROUP BY gender")
        r1 = p.run_conversation("What is the addiction level distribution by gender?", cm)
        self.assertIsInstance(r1, PipelineOutput)
        self.assertEqual(cm.turn_count, 1)
        q1_arg = p.llm.generate_sql.call_args[0][0]
        self.assertNotIn("Conversation history:", q1_arg)

        # Turn 2: follow-up — "what about"
        self._mock_sql_return(p.llm, "SELECT AVG(addiction_level) FROM t WHERE gender='Male'")
        r2 = p.run_conversation("What about males specifically?", cm)
        self.assertEqual(cm.turn_count, 2)
        q2_arg = p.llm.generate_sql.call_args[0][0]
        self.assertIn("Conversation history:", q2_arg)

        # Turn 3: follow-up — "now sort"
        self._mock_sql_return(p.llm, "SELECT * FROM t ORDER BY anxiety_score DESC")
        r3 = p.run_conversation("Now sort by anxiety score", cm)
        self.assertEqual(cm.turn_count, 3)
        q3_arg = p.llm.generate_sql.call_args[0][0]
        self.assertIn("Conversation history:", q3_arg)

        # Turn 4: fresh standalone question — no context injected
        self._mock_sql_return(p.llm, "SELECT COUNT(*) FROM t")
        r4 = p.run_conversation("How many total respondents are there in the survey?", cm)
        self.assertEqual(cm.turn_count, 4)
        q4_arg = p.llm.generate_sql.call_args[0][0]
        self.assertNotIn("Conversation history:", q4_arg)

    def test_run_conversation_records_turn(self):
        p = self._make_pipeline()
        cm = ConversationManager()
        self._mock_sql_return(p.llm, "SELECT 1")
        p.run_conversation("test", cm)
        self.assertEqual(cm.turn_count, 1)
        self.assertEqual(cm.turns[0].question, "test")
        self.assertTrue(cm.turns[0].sql.startswith("SELECT 1"))


if __name__ == "__main__":
    unittest.main()
