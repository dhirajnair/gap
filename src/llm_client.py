from __future__ import annotations

import json
import os
import time
from typing import Any

from src.types import SQLGenerationOutput, AnswerGenerationOutput

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

    def _chat(self, messages: list[dict[str, str]], temperature: float, max_tokens: int) -> str:
        res = self._client.chat.send(
            messages=messages,
            model=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )

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

        if prompt_tokens == 0:
            prompt_tokens = self._estimate_tokens(messages)
        if completion_tokens == 0:
            completion_tokens = self._estimate_tokens_text(content)

        self._stats["llm_calls"] += 1
        self._stats["prompt_tokens"] += prompt_tokens
        self._stats["completion_tokens"] += completion_tokens
        self._stats["total_tokens"] += prompt_tokens + completion_tokens

        return content.strip()

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
    def _build_schema_text(context: dict) -> str:
        tables = context.get("tables", {})
        if not tables:
            return "No schema available."
        parts = []
        for tbl, cols in tables.items():
            col_list = ", ".join(f"{c} ({t})" for c, t in cols.items())
            parts.append(f"Table: {tbl}\nColumns: {col_list}")
        return "\n".join(parts)

    def generate_sql(self, question: str, context: dict) -> SQLGenerationOutput:
        schema_text = self._build_schema_text(context)
        system_prompt = (
            "You are a SQLite SQL generator. Rules:\n"
            "- Use only the table and columns listed below\n"
            "- Use SQLite syntax\n"
            "- Reply with ONLY a JSON object: {\"sql\": \"<query>\"}\n"
            "- If the question asks about groups or categories, use GROUP BY on the relevant column\n"
            "- If the question asks for a non-SELECT operation (DELETE, INSERT, UPDATE, DROP, etc.), "
            "still generate that SQL literally so it can be validated downstream\n"
            "- Only reply {\"sql\": null} if the required data columns are truly absent from the schema\n\n"
            f"Schema:\n{schema_text}"
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

        system_prompt = (
            "You are a concise analytics assistant. "
            "Use only the provided SQL results. Do not invent data."
        )
        user_prompt = (
            f"Question:\n{question}\n\nSQL:\n{sql}\n\n"
            f"Rows (JSON):\n{json.dumps(rows[:30], ensure_ascii=True)}\n\n"
            "Write a concise answer in plain English."
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
