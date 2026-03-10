from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from src.llm_client import OpenRouterLLMClient, build_default_llm_client
from src.types import (
    SQLValidationOutput,
    SQLExecutionOutput,
    PipelineOutput,
)

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


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = BASE_DIR / "data" / "gaming_mental_health.sqlite"


class SQLValidationError(Exception):
    pass


class SQLValidator:
    _BLOCKED_KEYWORDS = re.compile(
        r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|REPLACE)\b",
        re.IGNORECASE,
    )
    _DANGEROUS_PATTERNS = re.compile(
        r"\b(PRAGMA|sqlite_master|sqlite_temp_master)\b|--|/\*",
        re.IGNORECASE,
    )

    @classmethod
    def validate(cls, sql: str | None, db_path: Path | None = None, allowed_tables: set[str] | None = None) -> SQLValidationOutput:
        start = time.perf_counter()

        if sql is None:
            return SQLValidationOutput(
                is_valid=False,
                validated_sql=None,
                error="No SQL provided",
                timing_ms=(time.perf_counter() - start) * 1000,
            )

        normalized = sql.strip().rstrip(";")

        # Block non-SELECT statements
        if not normalized.upper().startswith("SELECT"):
            return SQLValidationOutput(
                is_valid=False,
                validated_sql=None,
                error="Only SELECT statements are allowed",
                timing_ms=(time.perf_counter() - start) * 1000,
            )

        # Block DML/DDL keywords
        if cls._BLOCKED_KEYWORDS.search(normalized):
            return SQLValidationOutput(
                is_valid=False,
                validated_sql=None,
                error="Statement contains blocked keyword",
                timing_ms=(time.perf_counter() - start) * 1000,
            )

        # Block dangerous patterns (PRAGMA, system tables, comments)
        if cls._DANGEROUS_PATTERNS.search(normalized):
            return SQLValidationOutput(
                is_valid=False,
                validated_sql=None,
                error="Statement contains dangerous pattern",
                timing_ms=(time.perf_counter() - start) * 1000,
            )

        # Block multi-statement (semicolons in the middle)
        if ";" in normalized:
            return SQLValidationOutput(
                is_valid=False,
                validated_sql=None,
                error="Multiple statements not allowed",
                timing_ms=(time.perf_counter() - start) * 1000,
            )

        # Table allowlist check
        if allowed_tables:
            from_match = re.findall(r'\bFROM\s+"?(\w+)"?', normalized, re.IGNORECASE)
            join_match = re.findall(r'\bJOIN\s+"?(\w+)"?', normalized, re.IGNORECASE)
            referenced = set(from_match + join_match)
            disallowed = referenced - allowed_tables
            if disallowed:
                return SQLValidationOutput(
                    is_valid=False,
                    validated_sql=None,
                    error=f"References disallowed table(s): {', '.join(sorted(disallowed))}",
                    timing_ms=(time.perf_counter() - start) * 1000,
                )

        # Syntax validation via EXPLAIN
        if db_path and db_path.exists():
            try:
                with sqlite3.connect(db_path) as conn:
                    conn.execute(f"EXPLAIN {normalized}")
            except sqlite3.Error as e:
                return SQLValidationOutput(
                    is_valid=False,
                    validated_sql=None,
                    error=f"SQL syntax error: {e}",
                    timing_ms=(time.perf_counter() - start) * 1000,
                )

        return SQLValidationOutput(
            is_valid=True,
            validated_sql=normalized,
            error=None,
            timing_ms=(time.perf_counter() - start) * 1000,
        )


class SQLiteExecutor:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)

    def run(self, sql: str | None) -> SQLExecutionOutput:
        start = time.perf_counter()
        error = None
        rows = []
        row_count = 0

        if sql is None:
            return SQLExecutionOutput(
                rows=[],
                row_count=0,
                timing_ms=(time.perf_counter() - start) * 1000,
                error=None,
            )

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(sql)
                rows = [dict(r) for r in cur.fetchmany(100)]
                row_count = len(rows)
        except Exception as exc:
            error = str(exc)
            rows = []
            row_count = 0

        return SQLExecutionOutput(
            rows=rows,
            row_count=row_count,
            timing_ms=(time.perf_counter() - start) * 1000,
            error=error,
        )


def _load_schema(db_path: Path) -> dict:
    """Extract table names, column names, and types from the SQLite database."""
    schema: dict = {"tables": {}}
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        for (table_name,) in cur.fetchall():
            cur.execute(f'PRAGMA table_info("{table_name}")')
            columns = {row[1]: row[2] for row in cur.fetchall()}
            schema["tables"][table_name] = columns
    return schema


class AnalyticsPipeline:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH, llm_client: OpenRouterLLMClient | None = None) -> None:
        self.db_path = Path(db_path)
        self.llm = llm_client or build_default_llm_client()
        self.executor = SQLiteExecutor(self.db_path)
        self._allowed_tables = self._get_table_names()
        self.schema = _load_schema(self.db_path)

    def _get_table_names(self) -> set[str]:
        if not self.db_path.exists():
            return set()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            return {row[0] for row in cur.fetchall()}


    @observe()
    def run(self, question: str, request_id: str | None = None) -> PipelineOutput:
        if langfuse_context:
            langfuse_context.update_current_trace(
                name="pipeline-run",
                input={"question": question},
                metadata={"request_id": request_id},
            )

        start = time.perf_counter()

        # Stage 1: SQL Generation
        sql_gen_output = self.llm.generate_sql(question, self.schema)
        sql = sql_gen_output.sql

        # Stage 2: SQL Validation
        validation_output = SQLValidator.validate(sql, db_path=self.db_path, allowed_tables=self._allowed_tables)
        if not validation_output.is_valid:
            sql = None

        if langfuse_context:
            langfuse_context.score_current_trace(
                name="sql_validation",
                value=1.0 if validation_output.is_valid else 0.0,
                comment=validation_output.error,
            )

        # Stage 3: SQL Execution
        execution_output = self.executor.run(sql)
        rows = execution_output.rows

        # Stage 4: Answer Generation
        answer_output = self.llm.generate_answer(question, sql, rows)

        # Determine status
        status = "success"
        if sql_gen_output.sql is None:
            status = "unanswerable"
        elif not validation_output.is_valid:
            status = "invalid_sql"
        elif execution_output.error:
            status = "error"

        # Build timings aggregate
        timings = {
            "sql_generation_ms": sql_gen_output.timing_ms,
            "sql_validation_ms": validation_output.timing_ms,
            "sql_execution_ms": execution_output.timing_ms,
            "answer_generation_ms": answer_output.timing_ms,
            "total_ms": (time.perf_counter() - start) * 1000,
        }

        # Build total LLM stats
        total_llm_stats = {
            "llm_calls": sql_gen_output.llm_stats.get("llm_calls", 0) + answer_output.llm_stats.get("llm_calls", 0),
            "prompt_tokens": sql_gen_output.llm_stats.get("prompt_tokens", 0) + answer_output.llm_stats.get("prompt_tokens", 0),
            "completion_tokens": sql_gen_output.llm_stats.get("completion_tokens", 0) + answer_output.llm_stats.get("completion_tokens", 0),
            "total_tokens": sql_gen_output.llm_stats.get("total_tokens", 0) + answer_output.llm_stats.get("total_tokens", 0),
            "model": sql_gen_output.llm_stats.get("model", "unknown"),
        }

        if langfuse_context:
            langfuse_context.update_current_trace(
                output={"status": status, "answer": answer_output.answer, "sql": sql},
            )

        return PipelineOutput(
            status=status,
            question=question,
            request_id=request_id,
            sql_generation=sql_gen_output,
            sql_validation=validation_output,
            sql_execution=execution_output,
            answer_generation=answer_output,
            sql=sql,
            rows=rows,
            answer=answer_output.answer,
            timings=timings,
            total_llm_stats=total_llm_stats,
        )