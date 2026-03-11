# Solution Notes

We took an **iterative approach**: starting with an initial implementation covering 11 EPICs (see [Addendum: EPIC Plan](#addendum-epic-plan)), then progressing through several audit-driven refinement passes (Iterations 2–6) to align closely with README requirements. Each iteration focused on improving correctness, observability, and efficiency. The following summary consolidates all changes, organized by area.

---

## What Changed (Consolidated)

### Foundation & Setup
- Fixed benchmark script: `result["status"]` → `result.status` (dataclass, not dict).
- Environment loading: `load_dotenv()` moved to explicit `init_env()` in entry points (`scripts/benchmark.py`, `tests/conftest.py`) — no side effects on `import src`.
- Pinned dependency versions in `requirements.txt`.
- Extracted `UNANSWERABLE_MSG` constant in `src/types.py` (was hardcoded in 3 places).

### Token Counting
- Parse `usage.prompt_tokens` / `usage.completion_tokens` from OpenRouter API response in `_chat()`.
- Fallback: estimate tokens via `words × 1.3` when API omits usage data.
- Stats returned per call and aggregated in `PipelineOutput.total_llm_stats` for evaluation.

### SQL Generation Quality
- Schema introspection at init via `PRAGMA table_info(...)` with safe f-string escaping for table names.
- Compact schema in system prompt: `table(col1:INTEGER,col2:TEXT,...)` — includes column types for correct casts.
- Structured JSON output: `{"sql": "<query>"}` or `{"sql": null}`.
- `_extract_sql()` handles JSON, markdown fences, and raw SQL fallback; pre-compiled regex (`_MD_SQL_RE`) at module level.

### SQL Validation
- 7-layer validation: SELECT/WITH-only, DML blocklist, dangerous patterns (PRAGMA, system tables, comments), multi-statement rejection, table allowlist, **column allowlist** (validates `table.column` and columns in aggregates exist in schema), `EXPLAIN` syntax check.
- Improved table extraction: comma-joins (`FROM t1, t2`), CTE aliases excluded from allowlist check.
- Each rejection returns a specific error message.

### Result & Answer Validation
- `ResultValidator`: column consistency across rows, negative COUNT detection, non-numeric values in AVG/SUM columns (only on aggregation result columns, not GROUP BY keys).
- Answer quality: warns on suspiciously short answers; verifies numeric values from SQL results appear in the answer (hallucination detection, tolerance-based for floats).

### Observability
- Langfuse `@observe()` decorators on `run()`, `generate_sql()`, `generate_answer()`, `_chat()`; trace metadata (request_id, question), SQL validation scores.
- Python `logging` throughout pipeline and LLM client; `logging.basicConfig(level=logging.INFO)` in `src/__init__.py`.
- Auto-generated `request_id` (12-char hex) when caller omits it; `_empty_result()` uses actual model name (not `"none"`).

### Error Handling & Resilience
- Retries with exponential backoff (2 retries, 1s base) for transient LLM failures; single retry layer in `_chat()` (no double retry).
- SQL execution errors → status="error", rows cleared; empty results → "Query executed, but no rows returned."
- Input sanitization: strip, truncate at 1000 chars, empty → unanswerable.
- Timeout: 30s on SQLite connections.

### Efficiency
- Prompt compression: compact schema with types, terse system prompts.
- Result truncation: max 20 rows to answer generation; fetch limit aligned to 20 (no wasted fetching).
- Response caching: bounded LRU cache (128 entries), hash key includes messages + temperature + max_tokens.
- Schema cached at init; CTE support (`WITH ... SELECT`) allowed through validator.

### LLM Client Details
- `_sanitize_rows()`: preserves `None` as JSON `null` (no `"N/A"` substitution that misleads the LLM).
- Thread-safe cache with `threading.Lock`; removed unused `self._cache` from pipeline (caching lives in `OpenRouterLLMClient`).

### Multi-Turn Conversation (Optional)
- `ConversationManager`: stores turns, heuristic follow-up detection (pronoun, conjunction, pattern — no ≤4-word heuristic), context enrichment.
- `run_conversation()`: enrich → run → record; last 3 turns in context prompt.

### Testing
- `test_validation.py` (17), `test_llm_client.py` (8), `test_token_counting.py` (4), `test_edge_cases.py` (4), `test_cache_and_retry.py` (8), `test_result_validation.py` (6), `test_conversation.py` (15).
- `conftest.py`: sys.path fix, `init_env()` for test setup.

---

## Why These Changes

| Decision | Rationale |
|----------|-----------|
| Compact schema with types | Minimise prompt tokens while giving the LLM enough info for correct SQL (INTEGER vs TEXT). |
| Structured JSON output | Reliable SQL extraction vs. parsing free-text; eliminates markdown/explanation waste. |
| Multi-layer validation | Defence in depth — each layer catches a different class of bad SQL. |
| Langfuse over custom tracing | 50K free obs/month, `@observe()` decorator = 3 lines of code, framework-agnostic. |
| Heuristic follow-up detection | No extra LLM call; fast, deterministic, good enough for pronoun/pattern cases. |
| Response caching | Identical prompts (common in benchmarks and retries) skip the LLM entirely. |
| CTE support in validator | Compressed prompts cause the LLM to generate `WITH ... SELECT` CTEs. |
| Explicit `init_env()` | Avoids file I/O and CWD-dependent behaviour on `import src`. |

---

## Tradeoffs

| Tradeoff | Accepted risk |
|----------|---------------|
| In-memory cache | Lost on restart; fine for single-process, would need Redis for multi-process. |
| Heuristic follow-up detection | Misses subtle follow-ups; LLM classifier would be more accurate but adds latency + cost. |
| Column validation scope | Covers qualified (`table.col`) and aggregate columns; bare SELECT columns rely on EXPLAIN. |
| No streaming | Simpler implementation; streaming would reduce perceived latency. |

---

## Measured Impact

**Baseline (before any changes):**
- Average latency: 9030 ms
- p50 latency: 9162 ms
- p95 latency: 12183 ms
- Success rate: 0% (no schema context → invalid SQL)

**After implementation:**
- Average latency: 7810 ms
- p50 latency: 351 ms (cache hits on repeated runs; cold-start higher)
- p95 latency: 28147 ms (cold LLM calls with reasoning model)
- Success rate: 83.33%
- Average tokens per request: ~300 (compressed prompts + caching)

---

## Next Steps

1. LLM-based follow-up detection for edge cases.
2. Disk/Redis response cache for multi-process deployments.
3. Streaming responses for lower perceived latency.

---

## Addendum: EPIC Plan (from Iteration 1)

| EPIC | Goal | Key stories |
|------|------|-------------|
| **1** | Foundation & bug fixes | Benchmark fix, dotenv loading, pinned deps, data setup |
| **2** | Token counting (hard req.) | Parse API usage, fallback heuristic, stats per call |
| **3** | SQL generation quality | Schema introspection, schema in prompt, JSON output, unanswerable handling |
| **4** | SQL validation | Block non-SELECT, syntax (EXPLAIN), table allowlist, dangerous patterns |
| **5** | Observability | Logging, request_id, metrics, LLM call logging |
| **6** | Error handling | Retries, SQL error recovery, empty results, input sanitization, timeouts |
| **7** | Efficiency | Prompt compression, max_tokens tuning, result truncation, schema cache, response cache |
| **8** | Testing | Validation tests, extraction tests, token tests, edge cases, public tests green |
| **9** | Documentation | CHECKLIST.md, SOLUTION_NOTES.md, benchmarks |
| **10** | Multi-turn (optional) | Conversation state, follow-up detection, context-aware SQL, ambiguity resolution |
| **11** | Langfuse observability | Replace custom tracing with `@observe()`, trace metadata, validation scores |

**Execution order:** 1 → 2 → 3 → 4 → (5 ‖ 11) → 6 → 7 → 8 → 9 → 10
