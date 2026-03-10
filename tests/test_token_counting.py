from __future__ import annotations

import unittest

from src.llm_client import OpenRouterLLMClient


class TestTokenEstimation(unittest.TestCase):
    def test_estimate_tokens_text_nonempty(self):
        result = OpenRouterLLMClient._estimate_tokens_text("hello world this is a test")
        self.assertGreater(result, 0)

    def test_estimate_tokens_text_empty(self):
        result = OpenRouterLLMClient._estimate_tokens_text("")
        self.assertEqual(result, 1)

    def test_estimate_tokens_messages(self):
        messages = [
            {"role": "system", "content": "You are a SQL assistant."},
            {"role": "user", "content": "Generate a query for top 5 ages"},
        ]
        result = OpenRouterLLMClient._estimate_tokens(messages)
        self.assertGreater(result, 10)

    def test_pop_stats_resets(self):
        client = OpenRouterLLMClient.__new__(OpenRouterLLMClient)
        client._stats = {"llm_calls": 3, "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        out = client.pop_stats()
        self.assertEqual(out["llm_calls"], 3)
        self.assertEqual(out["total_tokens"], 150)
        out2 = client.pop_stats()
        self.assertEqual(out2["llm_calls"], 0)
        self.assertEqual(out2["total_tokens"], 0)


if __name__ == "__main__":
    unittest.main()
