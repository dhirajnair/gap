from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from pathlib import Path

from src.llm_client import OpenRouterLLMClient, build_default_llm_client

logger = logging.getLogger(__name__)
from src.types import (
    SQLValidationOutput,
    SQLExecutionOutput,
    PipelineOutput,
)


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = BASE_DIR / "data" / "gaming_mental_health.sqlite"


class SQLValidationError(Exception):
    pass


class SQLValidator:
    @classmethod
    def validate(cls, sql: str | None) -> SQLValidationOutput:
        start = time.perf_counter()

        if sql is None:
            return SQLValidationOutput(
                is_valid=False,
                validated_sql=None,
                error="No SQL provided",
                timing_ms=(time.perf_counter() - start) * 1000,
            )

        # TODO: Implement SQL validation logic
        # Consider what validation is needed for this use case

        return SQLValidationOutput(
            is_valid=True,
            validated_sql=sql,
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


class AnalyticsPipeline:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH, llm_client: OpenRouterLLMClient | None = None) -> None:
        self.db_path = Path(db_path)
        self.llm = llm_client or build_default_llm_client()
        self.executor = SQLiteExecutor(self.db_path)

    def run(self, question: str, request_id: str | None = None) -> PipelineOutput:
        request_id = request_id or uuid.uuid4().hex[:12]
        start = time.perf_counter()
        logger.info("pipeline.start", extra={"request_id": request_id, "question": question[:200]})

        # Stage 1: SQL Generation
        sql_gen_output = self.llm.generate_sql(question, {})
        sql = sql_gen_output.sql
        logger.info("stage.sql_generation", extra={
            "request_id": request_id, "sql": sql[:200] if sql else None,
            "timing_ms": round(sql_gen_output.timing_ms, 1), "error": sql_gen_output.error,
        })

        # Stage 2: SQL Validation
        validation_output = SQLValidator.validate(sql)
        if not validation_output.is_valid:
            sql = None
        logger.info("stage.sql_validation", extra={
            "request_id": request_id, "is_valid": validation_output.is_valid,
            "error": validation_output.error,
        })

        # Stage 3: SQL Execution
        execution_output = self.executor.run(sql)
        rows = execution_output.rows
        logger.info("stage.sql_execution", extra={
            "request_id": request_id, "row_count": execution_output.row_count,
            "timing_ms": round(execution_output.timing_ms, 1), "error": execution_output.error,
        })

        # Stage 4: Answer Generation
        answer_output = self.llm.generate_answer(question, sql, rows)
        logger.info("stage.answer_generation", extra={
            "request_id": request_id,
            "timing_ms": round(answer_output.timing_ms, 1), "error": answer_output.error,
        })

        # Determine status
        status = "success"
        if sql_gen_output.sql is None and sql_gen_output.error:
            status = "unanswerable"
        elif not validation_output.is_valid:
            status = "invalid_sql"
        elif execution_output.error:
            status = "error"
        elif sql is None:
            status = "unanswerable"

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

        logger.info("pipeline.complete", extra={
            "request_id": request_id, "status": status,
            "total_ms": round(timings["total_ms"], 1),
            "total_tokens": total_llm_stats.get("total_tokens", 0),
        })

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