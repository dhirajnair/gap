from __future__ import annotations

import logging
from typing import Any
import re
import sqlite3
import time
import uuid
from pathlib import Path

from src.conversation import ConversationManager
from src.llm_client import OpenRouterLLMClient, build_default_llm_client
from src.types import (
    MAX_ROWS_FOR_ANSWER,
    UNANSWERABLE_MSG,
    AnswerGenerationOutput,
    PipelineOutput,
    SQLExecutionOutput,
    SQLGenerationOutput,
    SQLValidationOutput,
)

try:
    from langfuse.decorators import langfuse_context, observe  # type: ignore[import-untyped]
except ImportError:
    def observe(*args: Any, **kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            return fn
        if args and callable(args[0]):
            return args[0]
        return decorator
    langfuse_context = None  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = BASE_DIR / "data" / "gaming_mental_health.sqlite"

_MAX_ROWS_FOR_ANSWER = MAX_ROWS_FOR_ANSWER


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
    _QUALIFIED_COL_RE = re.compile(r"\b(\w+)\.(\w+)\b")
    _AGG_COL_RE = re.compile(
        r"\b(?:AVG|SUM|MIN|MAX|COUNT)\s*\(\s*(\w+)\s*\)",
        re.IGNORECASE,
    )

    @classmethod
    def _validate_columns(
        cls,
        sql: str,
        schema_columns: dict[str, set[str]],
    ) -> str | None:
        """Validate that referenced columns exist in schema. Returns error message or None if valid."""
        if not schema_columns:
            return None
        tables_lower = {t.lower(): t for t in schema_columns}
        all_columns: set[str] = set()
        for cols in schema_columns.values():
            all_columns.update(c.lower() for c in cols)

        # Qualified: table.column
        for table_part, col_part in cls._QUALIFIED_COL_RE.findall(sql):
            tbl_key = table_part.lower()
            if tbl_key not in tables_lower:
                continue  # Skip CTE aliases, etc.
            canonical_table = tables_lower[tbl_key]
            valid_cols = {c.lower() for c in schema_columns[canonical_table]}
            if col_part.lower() not in valid_cols and col_part != "*":
                return f"Column '{col_part}' does not exist in table '{canonical_table}'"

        # Unqualified in aggregates: AVG(col), SUM(col), etc. (excludes COUNT(*))
        for col in cls._AGG_COL_RE.findall(sql):
            if col.lower() == "*":
                continue
            if col.lower() not in all_columns:
                return f"Column '{col}' does not exist in any referenced table"

        return None

    @classmethod
    def validate(
        cls,
        sql: str | None,
        db_path: Path | None = None,
        allowed_tables: set[str] | None = None,
        schema_columns: dict[str, set[str]] | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> SQLValidationOutput:
        start = time.perf_counter()

        if sql is None:
            return SQLValidationOutput(
                is_valid=False, validated_sql=None,
                error="No SQL provided", timing_ms=(time.perf_counter() - start) * 1000,
            )

        normalized = sql.strip().rstrip(";}").strip()

        if not normalized.upper().startswith(("SELECT", "WITH")):
            logger.warning("SQL rejected: not a SELECT — %.60s", normalized)
            return SQLValidationOutput(
                is_valid=False, validated_sql=None,
                error="Only SELECT statements are allowed", timing_ms=(time.perf_counter() - start) * 1000,
            )

        if cls._BLOCKED_KEYWORDS.search(normalized):
            logger.warning("SQL rejected: blocked keyword — %.60s", normalized)
            return SQLValidationOutput(
                is_valid=False, validated_sql=None,
                error="Statement contains blocked keyword", timing_ms=(time.perf_counter() - start) * 1000,
            )

        if cls._DANGEROUS_PATTERNS.search(normalized):
            logger.warning("SQL rejected: dangerous pattern — %.60s", normalized)
            return SQLValidationOutput(
                is_valid=False, validated_sql=None,
                error="Statement contains dangerous pattern", timing_ms=(time.perf_counter() - start) * 1000,
            )

        if ";" in normalized:
            return SQLValidationOutput(
                is_valid=False, validated_sql=None,
                error="Multiple statements not allowed", timing_ms=(time.perf_counter() - start) * 1000,
            )

        if allowed_tables:
            from_match = re.findall(r'\bFROM\s+"?(\w+)"?', normalized, re.IGNORECASE)
            join_match = re.findall(r'\bJOIN\s+"?(\w+)"?', normalized, re.IGNORECASE)
            has_subquery = '(' in normalized
            if not has_subquery:
                comma_match = re.findall(r'\bFROM\s+(.+?)(?:\bWHERE\b|\bGROUP\b|\bORDER\b|\bLIMIT\b|\bHAVING\b|$)', normalized, re.IGNORECASE)
                for clause in comma_match:
                    for part in clause.split(","):
                        tbl = part.strip().split()[0].strip('"') if part.strip() else ""
                        if tbl and re.match(r'^\w+$', tbl):
                            from_match.append(tbl)
            cte_aliases = set(re.findall(r'\b(\w+)\s+AS\s*\(', normalized, re.IGNORECASE))
            referenced = set(from_match + join_match) - cte_aliases
            disallowed = referenced - allowed_tables
            if disallowed:
                logger.warning("SQL rejected: disallowed tables %s", disallowed)
                return SQLValidationOutput(
                    is_valid=False, validated_sql=None,
                    error=f"References disallowed table(s): {', '.join(sorted(disallowed))}",
                    timing_ms=(time.perf_counter() - start) * 1000,
                )

        # Column-level validation: ensure referenced columns exist in schema
        if schema_columns:
            col_error = cls._validate_columns(normalized, schema_columns)
            if col_error:
                logger.warning("SQL rejected: invalid column — %s", col_error)
                return SQLValidationOutput(
                    is_valid=False, validated_sql=None,
                    error=col_error,
                    timing_ms=(time.perf_counter() - start) * 1000,
                )

        _conn = conn
        if _conn is None and db_path and db_path.exists():
            _conn = sqlite3.connect(db_path)
        if _conn is not None:
            try:
                _conn.execute("EXPLAIN " + normalized)
            except sqlite3.Error as e:
                logger.warning("SQL syntax error: %s", e)
                return SQLValidationOutput(
                    is_valid=False, validated_sql=None,
                    error=f"SQL syntax error: {e}", timing_ms=(time.perf_counter() - start) * 1000,
                )
            finally:
                if conn is None and _conn is not None:
                    _conn.close()

        logger.debug("SQL validated OK: %.80s", normalized)
        return SQLValidationOutput(
            is_valid=True, validated_sql=normalized, error=None,
            timing_ms=(time.perf_counter() - start) * 1000,
        )


class ResultValidator:
    """Sanity-checks on SQL execution results for analytics pipelines."""

    @staticmethod
    def validate(rows: list[dict], sql: str | None) -> list[str]:
        warnings: list[str] = []
        if not rows or not sql:
            return warnings

        keys_first = set(rows[0].keys())
        for i, row in enumerate(rows[1:], start=1):
            if set(row.keys()) != keys_first:
                warnings.append(f"Row {i} has inconsistent columns")
                break

        sql_upper = (sql or "").upper()
        has_count = "COUNT(" in sql_upper
        has_avg = "AVG(" in sql_upper
        has_sum = "SUM(" in sql_upper

        agg_col_hints = re.findall(
            r"(?:AVG|SUM|COUNT|MIN|MAX)\s*\([^)]*\)\s+(?:AS\s+)?\"?(\w+)\"?",
            sql or "", re.IGNORECASE,
        )
        agg_col_names = {h.lower() for h in agg_col_hints}

        for row in rows:
            for col, val in row.items():
                if val is None:
                    continue
                if isinstance(val, (int, float)):
                    if has_count and "count" in col.lower() and val < 0:
                        warnings.append(f"Negative COUNT value in column '{col}': {val}")
                else:
                    if (has_avg or has_sum) and col.lower() in agg_col_names:
                        warnings.append(f"Non-numeric value in aggregation column '{col}': {val}")

        return warnings


class SQLiteExecutor:
    _TIMEOUT_SECONDS = 30

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)

    def run(self, sql: str | None) -> SQLExecutionOutput:
        start = time.perf_counter()
        error = None
        rows: list[dict] = []
        row_count = 0

        if sql is None:
            return SQLExecutionOutput(
                rows=[], row_count=0,
                timing_ms=(time.perf_counter() - start) * 1000, error=None,
            )

        try:
            with sqlite3.connect(self.db_path, timeout=self._TIMEOUT_SECONDS) as conn:
                conn.execute(f"PRAGMA busy_timeout = {self._TIMEOUT_SECONDS * 1000}")
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(sql)
                rows = [dict(r) for r in cur.fetchmany(_MAX_ROWS_FOR_ANSWER)]
                row_count = len(rows)
        except Exception as exc:
            error = str(exc)
            logger.error("SQL execution error: %s", error)

        return SQLExecutionOutput(
            rows=rows, row_count=row_count,
            timing_ms=(time.perf_counter() - start) * 1000, error=error,
        )


class AnalyticsPipeline:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH, llm_client: OpenRouterLLMClient | None = None) -> None:
        self.db_path = Path(db_path)
        self.llm = llm_client or build_default_llm_client()
        self.executor = SQLiteExecutor(self.db_path)
        self._allowed_tables = self._get_table_names()
        self._validation_conn = sqlite3.connect(self.db_path) if self.db_path.exists() else None
        self.schema = self._load_schema()

    _MAX_QUESTION_LEN = 1000
    _MAX_ENRICHED_LEN = 4000

    def _empty_result(self, question: str, request_id: str | None, start: float, reason: str) -> PipelineOutput:
        _zero_llm = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": self.llm.model}
        elapsed = (time.perf_counter() - start) * 1000
        logger.info("Empty result: reason=%s", reason)
        return PipelineOutput(
            status="unanswerable", question=question, request_id=request_id,
            sql_generation=SQLGenerationOutput(sql=None, timing_ms=0.0, llm_stats=_zero_llm, error=reason),
            sql_validation=SQLValidationOutput(is_valid=False, validated_sql=None, error=reason),
            sql_execution=SQLExecutionOutput(rows=[], row_count=0, timing_ms=0.0),
            answer_generation=AnswerGenerationOutput(answer=UNANSWERABLE_MSG, timing_ms=0.0, llm_stats=_zero_llm),
            sql=None, rows=[], answer=UNANSWERABLE_MSG,
            timings={"sql_generation_ms": 0, "sql_validation_ms": 0, "sql_execution_ms": 0, "answer_generation_ms": 0, "total_ms": elapsed},
            total_llm_stats=_zero_llm,
        )

    def _get_table_names(self) -> set[str]:
        if not self.db_path.exists():
            return set()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            return {row[0] for row in cur.fetchall()}

    def _load_schema(self) -> dict:
        """Load and cache schema at init to avoid per-request introspection."""
        schema: dict = {"tables": {}}
        if not self.db_path.exists():
            return schema
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = cur.fetchall()
            for (table_name,) in tables:
                safe_name = table_name.replace('"', '""')
                cur.execute(f'PRAGMA table_info("{safe_name}")')
                schema["tables"][table_name] = {row[1]: row[2] for row in cur.fetchall()}
        logger.info("Schema loaded: %d table(s)", len(schema["tables"]))
        return schema

    @observe()
    def run(self, question: str, request_id: str | None = None, *, _max_input_len: int = 0, _answer_question: str | None = None) -> PipelineOutput:
        if request_id is None:
            request_id = uuid.uuid4().hex[:12]
        if langfuse_context:
            langfuse_context.update_current_trace(
                name="pipeline-run",
                input={"question": question},
                metadata={"request_id": request_id},
            )

        start = time.perf_counter()
        logger.info("Pipeline run start: question=%.80s request_id=%s", question, request_id)

        question = (question or "").strip()
        if not question:
            return self._empty_result("", request_id, start, "Empty question")
        _limit = _max_input_len or self._MAX_QUESTION_LEN
        if len(question) > _limit:
            question = question[:_limit]

        # Stage 1: SQL Generation
        sql_gen_output = self.llm.generate_sql(question, self.schema)
        sql = sql_gen_output.sql

        # Stage 2: SQL Validation
        schema_columns = (
            {t: set(cols.keys()) for t, cols in self.schema.get("tables", {}).items()}
            if self.schema else None
        )
        validation_output = SQLValidator.validate(
            sql,
            db_path=self.db_path,
            allowed_tables=self._allowed_tables,
            schema_columns=schema_columns,
            conn=self._validation_conn,
        )
        if not validation_output.is_valid:
            sql = None
        else:
            sql = validation_output.validated_sql
            if sql and not re.search(r'\bLIMIT\b', sql, re.IGNORECASE):
                sql = sql + f" LIMIT {_MAX_ROWS_FOR_ANSWER}"

        if langfuse_context:
            langfuse_context.score_current_trace(
                name="sql_validation",
                value=1.0 if validation_output.is_valid else 0.0,
                comment=validation_output.error,
            )

        # Stage 3: SQL Execution
        execution_output = self.executor.run(sql)
        rows = execution_output.rows

        if execution_output.error:
            sql = None
            rows = []

        # Stage 3b: Result Validation (analytics sanity checks)
        result_warnings = ResultValidator.validate(rows, sql)
        if result_warnings:
            logger.warning("Result validation warnings: %s", result_warnings)

        # Stage 4: Answer Generation (use _answer_question for follow-ups to avoid "rewrite as query" confusing the answer LLM)
        answer_q = _answer_question if _answer_question is not None else question
        answer_output = self.llm.generate_answer(answer_q, sql, rows)

        # Stage 4b: Answer quality checks
        if rows and answer_output.answer:
            answer_text = answer_output.answer.strip()
            if len(answer_text) < 5:
                logger.warning("Answer quality: suspiciously short answer for %d data rows", len(rows))
            result_numbers: list[str] = []
            for row in rows[:_MAX_ROWS_FOR_ANSWER]:
                for val in row.values():
                    if isinstance(val, int):
                        result_numbers.append(str(val))
                    elif isinstance(val, float):
                        result_numbers.append(f"{val:.2f}")
                        result_numbers.append(str(int(val)) if val == int(val) else f"{val:.0f}")
            if result_numbers:
                found = sum(1 for n in result_numbers if n in answer_text)
                if found == 0:
                    logger.warning(
                        "Answer quality: none of %d numeric values from results appear in answer",
                        len(result_numbers),
                    )

        # Determine status
        status = "success"
        if sql_gen_output.sql is None:
            status = "unanswerable"
        elif not validation_output.is_valid:
            status = "invalid_sql"
        elif execution_output.error:
            status = "error"

        timings = {
            "sql_generation_ms": sql_gen_output.timing_ms,
            "sql_validation_ms": validation_output.timing_ms,
            "sql_execution_ms": execution_output.timing_ms,
            "answer_generation_ms": answer_output.timing_ms,
            "total_ms": (time.perf_counter() - start) * 1000,
        }

        total_llm_stats = {
            "llm_calls": sql_gen_output.llm_stats.get("llm_calls", 0) + answer_output.llm_stats.get("llm_calls", 0),
            "prompt_tokens": sql_gen_output.llm_stats.get("prompt_tokens", 0) + answer_output.llm_stats.get("prompt_tokens", 0),
            "completion_tokens": sql_gen_output.llm_stats.get("completion_tokens", 0) + answer_output.llm_stats.get("completion_tokens", 0),
            "total_tokens": sql_gen_output.llm_stats.get("total_tokens", 0) + answer_output.llm_stats.get("total_tokens", 0),
            "model": sql_gen_output.llm_stats.get("model", "unknown"),
        }

        logger.info(
            "Pipeline run complete: status=%s total_ms=%.1f tokens=%d",
            status, timings["total_ms"], total_llm_stats["total_tokens"],
        )

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

    def run_conversation(
        self,
        question: str,
        conversation: ConversationManager,
        request_id: str | None = None,
    ) -> PipelineOutput:
        """Run a question through the pipeline with multi-turn conversation context."""
        raw = (question or "").strip()
        if len(raw) > self._MAX_QUESTION_LEN:
            raw = raw[: self._MAX_QUESTION_LEN]
        enriched = conversation.build_context_prompt(raw)
        result: PipelineOutput = self.run(
            enriched,
            request_id=request_id,
            _max_input_len=self._MAX_ENRICHED_LEN,
            _answer_question=raw,
        )
        conversation.add_turn(raw, result.sql, result.answer)
        return result
