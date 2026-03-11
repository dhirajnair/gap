from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any

from src.types import SQLGenerationOutput, AnswerGenerationOutput, UNANSWERABLE_MSG, MAX_ROWS_FOR_ANSWER

try:
    from langfuse.decorators import observe, langfuse_context
except ImportError:
    def observe(*args, **kwargs):
        def decorator(fn):
            return fn
        if args and callable(args[0]):
            return args[0]
        return decorator
    langfuse_context = None

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "openai/gpt-5-nano"

_CACHE_MAX_SIZE = 128


class _LRUCache:
    """Thread-safe bounded LRU cache backed by OrderedDict."""

    def __init__(self, maxsize: int = _CACHE_MAX_SIZE) -> None:
        self._data: OrderedDict[str, str] = OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
        return None

    def put(self, key: str, value: str) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            else:
                if len(self._data) >= self._maxsize:
                    self._data.popitem(last=False)
            self._data[key] = value

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


_MD_SQL_RE = re.compile(r"```(?:sql|json)?\s*\n?(.*?)```", re.DOTALL)


class OpenRouterLLMClient:
    """LLM client using the OpenRouter SDK for chat completions."""

    provider_name = "openrouter"

    def __init__(self, api_key: str, model: str | None = None) -> None:
        try:
            from openrouter import OpenRouter
        except ModuleNotFoundError as exc:
            raise RuntimeError("Missing dependency: install 'openrouter'.") from exc
        self.model = model or os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL)
        self._client = OpenRouter(api_key=api_key)
        self._stats = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._stats_lock = threading.Lock()
        self._cache = _LRUCache(_CACHE_MAX_SIZE)

    _MAX_RETRIES = 2
    _RETRY_BACKOFF = 1.0

    @observe(as_type="generation")
    def _chat(self, messages: list[dict[str, str]], temperature: float, max_tokens: int) -> str:
        raw = json.dumps(messages, sort_keys=True) + f"|t={temperature}|m={max_tokens}"
        cache_key = hashlib.sha256(raw.encode()).hexdigest()
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("Cache hit for prompt (key=%s…)", cache_key[:12])
            return cached

        for attempt in range(1 + self._MAX_RETRIES):
            try:
                logger.info("LLM call attempt %d/%d model=%s", attempt + 1, 1 + self._MAX_RETRIES, self.model)
                res = self._client.chat.send(
                    messages=messages,
                    model=self.model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=False,
                )
                break
            except Exception as exc:
                logger.warning("LLM call attempt %d failed: %s", attempt + 1, exc)
                if attempt < self._MAX_RETRIES:
                    time.sleep(self._RETRY_BACKOFF * (2 ** attempt))
                    continue
                raise RuntimeError(f"LLM call failed after {self._MAX_RETRIES + 1} attempts: {exc}") from exc

        usage = getattr(res, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

        choices = getattr(res, "choices", None) or []
        if not choices:
            raise RuntimeError("OpenRouter response contained no choices.")
        msg = getattr(choices[0], "message", None)
        content = getattr(msg, "content", None)
        if not isinstance(content, str):
            reasoning = getattr(msg, "reasoning", None)
            if isinstance(reasoning, str) and reasoning:
                content = reasoning
            else:
                raise RuntimeError("OpenRouter response content is not text.")

        result = content.strip()
        self._cache.put(cache_key, result)

        if prompt_tokens == 0:
            prompt_tokens = self._estimate_tokens(messages)
        if completion_tokens == 0:
            completion_tokens = self._estimate_tokens_text(content)

        with self._stats_lock:
            self._stats["llm_calls"] += 1
            self._stats["prompt_tokens"] += prompt_tokens
            self._stats["completion_tokens"] += completion_tokens
            self._stats["total_tokens"] += prompt_tokens + completion_tokens

        logger.info("LLM response: prompt_tokens=%d completion_tokens=%d", prompt_tokens, completion_tokens)

        if langfuse_context:
            langfuse_context.update_current_observation(
                model=self.model,
                usage={"input": prompt_tokens, "output": completion_tokens},
                metadata={"temperature": temperature, "max_tokens": max_tokens},
            )

        return result

    @staticmethod
    def _estimate_tokens_text(text: str) -> int:
        """Rough token estimate: ~1.3 tokens per word."""
        return max(1, int(len(text.split()) * 1.3))

    @staticmethod
    def _estimate_tokens(messages: list[dict[str, str]]) -> int:
        total = 0
        for msg in messages:
            total += OpenRouterLLMClient._estimate_tokens_text(msg.get("content", ""))
            total += 4  # per-message overhead
        return total

    @staticmethod
    def _extract_sql(text: str) -> str | None:
        cleaned = text.strip()

        md_match = _MD_SQL_RE.search(cleaned)
        if md_match:
            cleaned = md_match.group(1).strip()

        if cleaned.startswith("{"):
            try:
                parsed = json.loads(cleaned)
                sql = parsed.get("sql")
                if sql is None:
                    return None
                if isinstance(sql, str) and sql.strip():
                    return sql.strip()
                return None
            except json.JSONDecodeError:
                pass

        lower = cleaned.lower()
        for keyword in ("select ", "delete ", "insert ", "update ", "drop ", "alter ", "create "):
            idx = lower.find(keyword)
            if idx >= 0:
                sql = cleaned[idx:].rstrip(";").strip()
                return sql if sql else None
        return None

    @staticmethod
    def _compact_schema(context: dict) -> str:
        tables = context.get("tables", {})
        if not tables:
            return ""
        parts = []
        for tbl, cols in tables.items():
            if isinstance(cols, dict):
                col_str = ",".join(f"{c}:{t}" for c, t in cols.items())
            else:
                col_str = str(cols)
            parts.append(f"{tbl}({col_str})")
        return ";".join(parts)

    @observe()
    def generate_sql(self, question: str, context: dict) -> SQLGenerationOutput:
        schema = self._compact_schema(context)
        system_prompt = (
            "SQLite SELECT generator. Reply ONLY {\"sql\":\"<query>\"} or {\"sql\":null}.\n"
            f"Schema: {schema}" if schema else
            "SQLite SELECT generator. Reply ONLY {\"sql\":\"<query>\"} or {\"sql\":null}."
        )

        start = time.perf_counter()
        error = None
        sql = None

        try:
            logger.info("Generating SQL for question: %.80s…", question)
            text = self._chat(
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": question}],
                temperature=0.0,
                max_tokens=4096,
            )
            sql = self._extract_sql(text)
            if sql is None:
                logger.warning("SQL extraction returned None from LLM response")
        except Exception as exc:
            error = str(exc)
            logger.error("SQL generation failed: %s", error)

        timing_ms = (time.perf_counter() - start) * 1000
        llm_stats = self.pop_stats()
        llm_stats["model"] = self.model

        return SQLGenerationOutput(
            sql=sql,
            timing_ms=timing_ms,
            llm_stats=llm_stats,
            error=error,
        )

    @staticmethod
    def _sanitize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Pass rows through for LLM consumption; None becomes JSON null via json.dumps."""
        return rows

    @observe()
    def generate_answer(self, question: str, sql: str | None, rows: list[dict[str, Any]]) -> AnswerGenerationOutput:
        _zero = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": self.model}
        if not sql:
            logger.info("No SQL provided — returning unanswerable")
            return AnswerGenerationOutput(answer=UNANSWERABLE_MSG, timing_ms=0.0, llm_stats=_zero, error=None)
        if not rows:
            logger.info("SQL executed but returned no rows")
            return AnswerGenerationOutput(
                answer="Query executed, but no rows were returned.", timing_ms=0.0, llm_stats=_zero, error=None,
            )

        system_prompt = "Answer concisely using only the provided data. Do not invent data."
        sanitized = self._sanitize_rows(rows[:MAX_ROWS_FOR_ANSWER])
        user_prompt = (
            f"Q: {question}\nSQL: {sql}\n"
            f"Data: {json.dumps(sanitized, ensure_ascii=True)}\n"
            "Answer:"
        )

        start = time.perf_counter()
        error = None
        answer = ""

        try:
            answer = self._chat(
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                temperature=0.2,
                max_tokens=4096,
            )
        except Exception as exc:
            error = str(exc)
            answer = f"Error generating answer: {error}"
            logger.error("Answer generation failed: %s", error)

        timing_ms = (time.perf_counter() - start) * 1000
        llm_stats = self.pop_stats()
        llm_stats["model"] = self.model

        return AnswerGenerationOutput(
            answer=answer,
            timing_ms=timing_ms,
            llm_stats=llm_stats,
            error=error,
        )

    def pop_stats(self) -> dict[str, Any]:
        with self._stats_lock:
            out = {
                "llm_calls": self._stats["llm_calls"],
                "prompt_tokens": self._stats["prompt_tokens"],
                "completion_tokens": self._stats["completion_tokens"],
                "total_tokens": self._stats["total_tokens"],
            }
            self._stats = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return out


def build_default_llm_client() -> OpenRouterLLMClient:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required.")
    return OpenRouterLLMClient(api_key=api_key)
