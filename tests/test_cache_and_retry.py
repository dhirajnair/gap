from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.llm_client import OpenRouterLLMClient, _LRUCache


class TestLRUCache(unittest.TestCase):

    def test_basic_put_get(self):
        c = _LRUCache(maxsize=4)
        c.put("a", "1")
        self.assertEqual(c.get("a"), "1")

    def test_miss_returns_none(self):
        c = _LRUCache(maxsize=4)
        self.assertIsNone(c.get("missing"))

    def test_eviction_at_max(self):
        c = _LRUCache(maxsize=2)
        c.put("a", "1")
        c.put("b", "2")
        c.put("c", "3")
        self.assertIsNone(c.get("a"))
        self.assertEqual(c.get("b"), "2")
        self.assertEqual(c.get("c"), "3")

    def test_access_refreshes_lru(self):
        c = _LRUCache(maxsize=2)
        c.put("a", "1")
        c.put("b", "2")
        c.get("a")
        c.put("c", "3")
        self.assertEqual(c.get("a"), "1")
        self.assertIsNone(c.get("b"))

    def test_len(self):
        c = _LRUCache(maxsize=10)
        c.put("a", "1")
        c.put("b", "2")
        self.assertEqual(len(c), 2)


class TestRetryBehavior(unittest.TestCase):

    def test_retries_on_transient_failure(self):
        client = OpenRouterLLMClient.__new__(OpenRouterLLMClient)
        client.model = "test"
        client._cache = _LRUCache(4)

        mock_client = MagicMock()
        client._client = mock_client
        client._MAX_RETRIES = 2
        client._RETRY_BACKOFF = 0.01

        usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        msg = MagicMock(content="hello", reasoning=None)
        success_resp = MagicMock(usage=usage, choices=[MagicMock(message=msg)])

        mock_client.chat.send.side_effect = [
            RuntimeError("transient"),
            success_resp,
        ]

        result, stats = client._chat(
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.0,
            max_tokens=100,
        )
        self.assertEqual(result, "hello")
        self.assertEqual(stats["llm_calls"], 1)
        self.assertEqual(mock_client.chat.send.call_count, 2)

    def test_raises_after_max_retries(self):
        client = OpenRouterLLMClient.__new__(OpenRouterLLMClient)
        client.model = "test"
        client._cache = _LRUCache(4)

        mock_client = MagicMock()
        client._client = mock_client
        client._MAX_RETRIES = 1
        client._RETRY_BACKOFF = 0.01

        mock_client.chat.send.side_effect = RuntimeError("permanent")

        with self.assertRaises(RuntimeError):
            client._chat(
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.0,
                max_tokens=100,
            )
        self.assertEqual(mock_client.chat.send.call_count, 2)


class TestCacheIntegration(unittest.TestCase):

    def test_cache_hit_skips_llm(self):
        client = OpenRouterLLMClient.__new__(OpenRouterLLMClient)
        client.model = "test"
        client._cache = _LRUCache(4)

        mock_client = MagicMock()
        client._client = mock_client

        usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        msg = MagicMock(content="cached result", reasoning=None)
        success_resp = MagicMock(usage=usage, choices=[MagicMock(message=msg)])
        mock_client.chat.send.return_value = success_resp
        client._MAX_RETRIES = 0
        client._RETRY_BACKOFF = 0.01

        messages = [{"role": "user", "content": "same prompt"}]
        r1, s1 = client._chat(messages=messages, temperature=0.0, max_tokens=100)
        r2, s2 = client._chat(messages=messages, temperature=0.0, max_tokens=100)

        self.assertEqual(r1, r2)
        self.assertEqual(s1["llm_calls"], 1)
        self.assertEqual(s2["llm_calls"], 0)
        self.assertEqual(mock_client.chat.send.call_count, 1)


if __name__ == "__main__":
    unittest.main()
