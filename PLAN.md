# PLAN.md — Implementation Plan

---

## EPIC 1: Foundation & Bug Fixes

> Get the baseline running correctly end-to-end.

| # | Story | Description | File(s) |
|---|-------|-------------|---------|
| 1.1 | Fix benchmark bug | `result["status"]` → `result.status` (dataclass, not dict) | `scripts/benchmark.py:53` |
| 1.2 | Data setup & verification | Download CSV, run `gaming_csv_to_db.py`, verify SQLite DB created with all 39 columns | `data/`, `scripts/gaming_csv_to_db.py` |
| 1.3 | Add `dotenv` loading | Ensure `.env` is loaded before any module needs `OPENROUTER_API_KEY` | `src/__init__.py` |
| 1.4 | Pin dependency versions | Add version pins in `requirements.txt` for reproducible builds | `requirements.txt` |

---

## EPIC 2: Token Counting (Hard Requirement)

> Implement actual token tracking so efficiency evaluation works.

| # | Story | Description | File(s) |
|---|-------|-------------|---------|
| 2.1 | Extract token usage from OpenRouter response | Parse `usage.prompt_tokens`, `usage.completion_tokens`, `usage.total_tokens` from the API response object inside `_chat()` | `src/llm_client.py` |
| 2.2 | Accumulate stats per call | Increment `self._stats` counters and `llm_calls` on every `_chat()` invocation | `src/llm_client.py` |
| 2.3 | Fallback token estimation | If API response lacks usage data, estimate tokens via `tiktoken` or simple heuristic (words × 1.3) | `src/llm_client.py` |
| 2.4 | Verify token stats in tests | Assert `total_llm_stats` fields are `> 0` for successful runs | `tests/` |

---

## EPIC 3: SQL Generation Quality

> Provide schema context and improve prompts so the LLM generates correct SQL.

| # | Story | Description | File(s) |
|---|-------|-------------|---------|
| 3.1 | Build schema introspection | Query SQLite `PRAGMA table_info(...)` to extract table name, column names, and types at init | `src/pipeline.py` |
| 3.2 | Pass schema context to LLM | Replace empty `{}` in `generate_sql(question, context)` with actual schema dict | `src/pipeline.py` |
| 3.3 | Improve system prompt | Include table name, column list, sample values, and explicit instructions (SELECT only, use correct column names, SQLite dialect) | `src/llm_client.py` |
| 3.4 | Structured output format | Request JSON `{"sql": "..."}` to improve SQL extraction reliability | `src/llm_client.py` |
| 3.5 | Handle unanswerable questions | Instruct LLM to return `{"sql": null}` when the question cannot be answered from the schema | `src/llm_client.py` |

---

## EPIC 4: SQL Validation

> Implement real validation logic (currently a stub that always returns `is_valid=True`).

| # | Story | Description | File(s) |
|---|-------|-------------|---------|
| 4.1 | Block non-SELECT statements | Reject INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, ATTACH, DETACH | `src/pipeline.py` |
| 4.2 | Syntax validation | Use `sqlite3` EXPLAIN or parse to verify SQL syntax before execution | `src/pipeline.py` |
| 4.3 | Table/column allowlist check | Validate referenced tables and columns exist in the schema | `src/pipeline.py` |
| 4.4 | Dangerous pattern detection | Block `PRAGMA`, subqueries to system tables, semicolons (multi-statement), comments | `src/pipeline.py` |
| 4.5 | Return meaningful errors | Populate `SQLValidationOutput.error` with specific rejection reason | `src/pipeline.py` |

---

## EPIC 5: Observability

> Add logging, metrics, and tracing for production visibility.

| # | Story | Description | File(s) |
|---|-------|-------------|---------|
| 5.1 | Structured logging | Add Python `logging` with structured JSON format; log each pipeline stage entry/exit, LLM calls, errors | `src/pipeline.py`, `src/llm_client.py` |
| 5.2 | Request-scoped tracing | Generate `request_id` if not provided; propagate through all stages; include in all log entries | `src/pipeline.py` |
| 5.3 | Metrics collection | Track and expose: latency per stage, token counts, success/failure rates, SQL validation rejection rate | `src/pipeline.py` |
| 5.4 | LLM call logging | Log model, prompt length, token usage, latency for every LLM call | `src/llm_client.py` |

---

## EPIC 6: Error Handling & Edge Cases

> Gracefully handle failures at every stage.

| # | Story | Description | File(s) |
|---|-------|-------------|---------|
| 6.1 | LLM call retries | Add retry with backoff for transient OpenRouter failures (rate limits, timeouts) | `src/llm_client.py` |
| 6.2 | SQL execution error recovery | Catch execution errors, set appropriate status, return helpful answer | `src/pipeline.py` |
| 6.3 | Empty/null result handling | Handle zero-row results, NULL values in results gracefully | `src/pipeline.py`, `src/llm_client.py` |
| 6.4 | Input sanitization | Strip/normalize input questions; handle empty strings, excessively long inputs | `src/pipeline.py` |
| 6.5 | Timeout protection | Add timeouts for LLM calls and SQL execution to prevent hanging | `src/llm_client.py`, `src/pipeline.py` |

---

## EPIC 7: Efficiency Optimization

> Reduce latency and token usage while preserving quality.

| # | Story | Description | File(s) |
|---|-------|-------------|---------|
| 7.1 | Prompt compression | Minimize system/user prompt tokens — remove redundancy, use terse schema representation | `src/llm_client.py` |
| 7.2 | Tune `max_tokens` | Right-size `max_tokens` for SQL gen (shorter) and answer gen (shorter) to reduce waste | `src/llm_client.py` |
| 7.3 | Result truncation | Limit rows sent to answer generation; summarize large result sets before passing to LLM | `src/llm_client.py` |
| 7.4 | Schema caching | Cache schema introspection at pipeline init instead of per-request | `src/pipeline.py` |
| 7.5 | Response caching (optional) | Cache LLM responses for identical questions to avoid redundant calls | `src/llm_client.py` |

---

## EPIC 8: Testing

> Expand test coverage without modifying existing public tests.

| # | Story | Description | File(s) |
|---|-------|-------------|---------|
| 8.1 | Unit tests — SQL validation | Test each validation rule: non-SELECT rejection, syntax errors, column checks | `tests/test_validation.py` |
| 8.2 | Unit tests — SQL extraction | Test `_extract_sql()` with various LLM output formats (JSON, markdown, raw) | `tests/test_llm_client.py` |
| 8.3 | Unit tests — token counting | Verify stats accumulation and `pop_stats()` reset behavior | `tests/test_llm_client.py` |
| 8.4 | Edge case tests | Test empty input, very long input, non-English input, SQL injection attempts | `tests/test_edge_cases.py` |
| 8.5 | Run public tests green | Ensure all existing `test_public.py` tests pass | `tests/test_public.py` |

---

## EPIC 9: Documentation & Deliverables

> Complete all required deliverable artifacts.

| # | Story | Description | File(s) |
|---|-------|-------------|---------|
| 9.1 | Complete CHECKLIST.md | Fill in all sections with descriptions of what was implemented | `CHECKLIST.md` |
| 9.2 | Write SOLUTION_NOTES.md | Document changes, rationale, before/after benchmarks, tradeoffs, next steps | `SOLUTION_NOTES.md` |
| 9.3 | Run final benchmarks | Execute `benchmark.py --runs 3`, record baseline vs. optimized metrics | `scripts/benchmark.py` |

---

## EPIC 10 (Optional): Multi-Turn Conversation

> Support follow-up questions that reference prior context.

| # | Story | Description | File(s) |
|---|-------|-------------|---------|
| 10.1 | Conversation state manager | Store conversation history (questions, SQL, results) per session | `src/conversation.py` |
| 10.2 | Follow-up detection | Classify whether a new question is standalone or a follow-up (pronoun references, "what about", "now sort by") | `src/conversation.py` |
| 10.3 | Context-aware SQL generation | Inject prior SQL/results into the prompt for follow-up questions | `src/llm_client.py` |
| 10.4 | Ambiguity resolution | Resolve references like "males specifically" by rewriting the question with full context | `src/conversation.py` |
| 10.5 | Tests for multi-turn | Test a 3-4 turn conversation chain with follow-ups | `tests/test_conversation.py` |

---

## Execution Order

```
EPIC 1 (Foundation)
  └─► EPIC 2 (Token Counting)
        └─► EPIC 3 (SQL Generation Quality)
              └─► EPIC 4 (SQL Validation)
                    └─► EPIC 5 (Observability)  ← can parallel with EPIC 6
                    └─► EPIC 6 (Error Handling)  ← can parallel with EPIC 5
                          └─► EPIC 7 (Efficiency)
                                └─► EPIC 8 (Testing)
                                      └─► EPIC 9 (Documentation)
                                            └─► EPIC 10 (Optional: Multi-Turn)
```

**Estimated effort:** EPICs 1–9 fit within the 4–6 hour timebox. EPIC 10 is stretch.
