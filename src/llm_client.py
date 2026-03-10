from __future__ import annotations

import json
import os
import time
from typing import Any

from src.types import SQLGenerationOutput, AnswerGenerationOutput

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

DEFAULT_MODEL = "openai/gpt-5-nano"


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

    _MAX_RETRIES = 2
    _RETRY_BACKOFF = 1.0

    @observe(as_type="generation")
    def _chat(self, messages: list[dict[str, str]], temperature: float, max_tokens: int) -> str:
        cache_key = json.dumps(messages, sort_keys=True)
        if cache_key in self._cache:
            return self._cache[cache_key]

        last_exc: Exception | None = None
        for attempt in range(1 + self._MAX_RETRIES):
            try:
                res = self._client.chat.send(
                    messages=messages,
                    model=self.model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=False,
                )
                break
            except Exception as exc:
                last_exc = exc
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
        self._cache[cache_key] = result
        

        if prompt_tokens == 0:
            prompt_tokens = self._estimate_tokens(messages)
        if completion_tokens == 0:
            completion_tokens = self._estimate_tokens_text(content)

        self._stats["llm_calls"] += 1
        self._stats["prompt_tokens"] += prompt_tokens
        self._stats["completion_tokens"] += completion_tokens
        self._stats["total_tokens"] += prompt_tokens + completion_tokens

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
        import re
        cleaned = text.strip()

        # Strip markdown code fences if present
        md_match = re.search(r"```(?:sql|json)?\s*\n?(.*?)```", cleaned, re.DOTALL)
        if md_match:
            cleaned = md_match.group(1).strip()

        # Try JSON parse first
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

        # Fallback: find SQL statement (SELECT or DML for downstream validation)
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
            col_str = ",".join(cols.keys()) if isinstance(cols, dict) else str(cols)
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
        user_prompt = question

        start = time.perf_counter()
        error = None
        sql = None
        max_attempts = 2

        for attempt in range(max_attempts):
            try:
                text = self._chat(
                    messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                    temperature=0.0,
                    max_tokens=4096,
                )
                sql = self._extract_sql(text)
                if sql is not None or attempt == max_attempts - 1:
                    break
            except Exception as exc:
                error = str(exc)
                break

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
        """Replace None values with readable placeholders for LLM consumption."""
        return [{k: ("N/A" if v is None else v) for k, v in row.items()} for row in rows]

    @observe()
    def generate_answer(self, question: str, sql: str | None, rows: list[dict[str, Any]]) -> AnswerGenerationOutput:
        if not sql:
            return AnswerGenerationOutput(
                answer="I cannot answer this with the available table and schema. Please rephrase using known survey fields.",
                timing_ms=0.0,
                llm_stats={"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": self.model},
                error=None,
            )
        if not rows:
            return AnswerGenerationOutput(
                answer="Query executed, but no rows were returned.",
                timing_ms=0.0,
                llm_stats={"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": self.model},
                error=None,
            )

        system_prompt = "Answer concisely using only the provided data. Do not invent data."
        truncated_rows = rows[:20]
        user_prompt = (
            f"Q: {question}\nSQL: {sql}\n"
            f"Data: {json.dumps(truncated_rows, ensure_ascii=True)}\n"
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
        out = {
            "llm_calls": self._stats.get("llm_calls", 0),
            "prompt_tokens": self._stats.get("prompt_tokens", 0),
            "completion_tokens": self._stats.get("completion_tokens", 0),
            "total_tokens": self._stats.get("total_tokens", 0),
        }
        self._stats = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return out


def build_default_llm_client() -> OpenRouterLLMClient:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required.")
    return OpenRouterLLMClient(api_key=api_key)
