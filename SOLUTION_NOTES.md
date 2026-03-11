# Solution Notes

## What Changed

### EPIC 1 — Foundation
- Fixed `result["status"]` → `result.status` in benchmark script (dataclass, not dict).
- Added `python-dotenv` loading in `src/__init__.py` so `.env` is picked up before any module reads `OPENROUTER_API_KEY`.
- Pinned dependency versions in `requirements.txt`.

### EPIC 2 — Token Counting
- Parse `usage.prompt_tokens` / `usage.completion_tokens` from the OpenRouter API response in `_chat()`.
- Fallback: estimate tokens via `words × 1.3` when the API omits usage data.
- Accumulate stats per call; `pop_stats()` returns and resets counters.

### EPIC 3 — SQL Generation Quality
- Schema introspection at init via `PRAGMA table_info(...)`.
- Compact schema in system prompt: `table(col1,col2,...)`.
- Structured JSON output: `{"sql": "<query>"}` or `{"sql": null}`.
- `_extract_sql()` handles JSON, markdown fences, and raw SQL fallback.

### EPIC 4 — SQL Validation
- 6-layer validation: SELECT/WITH-only, DML blocklist, dangerous patterns (PRAGMA, system tables, comments), multi-statement rejection, table allowlist, `EXPLAIN` syntax check.
- Each rejection returns a specific error message.

### EPIC 5 + 11 — Observability (Langfuse)
- Replaced planned hand-rolled tracing with Langfuse `@observe()` decorators.
- `_chat()` → `@observe(as_type="generation")` auto-captures model, tokens, latency.
- `run()` → trace metadata (request_id, question) + SQL validation scores.
- Graceful no-op when Langfuse is not configured (try/except import).

### EPIC 6 — Error Handling
- Retries with exponential backoff (2 retries, 1s base) for transient LLM failures.
- SQL execution errors → status="error", rows cleared, answer gen handles gracefully.
- Empty/null result handling: zero rows → "Query executed, but no rows returned."
- Input sanitization: strip, truncate at 1000 chars, empty → unanswerable.
- Timeout: 30s on SQLite connections.

### EPIC 7 — Efficiency
- Prompt compression: compact schema format, terse system prompts.
- Result truncation: max 20 rows to answer generation.
- Response caching: in-memory cache keyed on serialized messages.
- Schema caching: loaded once at pipeline init.
- CTE support: `WITH ... SELECT` allowed through validator.

### EPIC 8 — Testing
- `test_validation.py`: 14 tests for every validation rule.
- `test_llm_client.py`: 8 tests for SQL extraction (JSON, markdown, raw, edge cases).
- `test_token_counting.py`: 4 tests for estimation and pop_stats.
- `test_edge_cases.py`: 4 tests (empty, whitespace, long input, output contract).
- `conftest.py`: sys.path fix for test imports.

### EPIC 10 — Multi-Turn Conversation
- `ConversationManager`: stores turns, heuristic follow-up detection, context enrichment.
- `AnalyticsPipeline.run_conversation()`: enrich → run → record.
- `test_conversation.py`: 15 tests including 4-turn chain.

## Why These Changes

| Decision | Rationale |
|----------|-----------|
| Compact schema in prompt | Minimise prompt tokens while giving the LLM enough info to generate correct SQL. |
| Structured JSON output | Reliable SQL extraction vs. parsing free-text; eliminates markdown/explanation waste. |
| Multi-layer validation | Defence in depth — each layer catches a different class of bad SQL. |
| Langfuse over custom tracing | 50K free obs/month, `@observe()` decorator = 3 lines of code, framework-agnostic. |
| Heuristic follow-up detection | No extra LLM call; fast, deterministic, good enough for pronoun/pattern cases. |
| Response caching | Identical prompts (common in benchmarks and retries) skip the LLM entirely. |
| CTE support in validator | Compressed prompts cause the LLM to generate `WITH ... SELECT` CTEs. |

## Tradeoffs

| Tradeoff | Accepted risk |
|----------|---------------|
| In-memory cache | Lost on restart; fine for single-process, would need Redis for multi-process. |
| Heuristic follow-up detection | Misses subtle follow-ups; LLM classifier would be more accurate but adds latency + cost. |
| Table-level validation only | Column-level allowlist would catch more errors but adds complexity. |
| No streaming | Simpler implementation; streaming would reduce perceived latency. |

## Measured Impact

**Baseline (before any changes):**
- Average latency: 9030 ms
- p50 latency: 9162 ms
- p95 latency: 12183 ms
- Success rate: 0% (no schema context → invalid SQL)

**After Iteration 1:**
- Average latency: 7810 ms
- p50 latency: 351 ms (cache hits on repeated runs)
- p95 latency: 28147 ms (cold LLM calls with reasoning model)
- Success rate: 83.33%
- Average tokens per request: ~300 (compressed prompts + caching)

## Next Steps
1. Column-level SQL validation.
2. LLM-based follow-up detection for edge cases.
3. Disk/Redis response cache for multi-process deployments.
4. Streaming responses for lower perceived latency.

---

## Iteration 2 (audit-driven fixes)

Changes driven by strict audit against README requirements (see `temp/FEEDBACK.md`).

### What Changed

| Change | Files | Why |
|--------|-------|-----|
| Added Python `logging` throughout | `src/pipeline.py`, `src/llm_client.py` | README Task 4 requires "tracing, metrics, **and logging**" — zero log statements existed. |
| Added `ResultValidator` | `src/pipeline.py` | README Task 5 requires "result validation" — sanity checks on query results (column consistency, negative counts). |
| Added answer quality check | `src/pipeline.py` | README Task 5 requires "answer quality checks" — warns on suspiciously short answers when data exists. |
| Wired `_sanitize_rows()` | `src/llm_client.py` | Was dead code (defined but never called). Now called in `generate_answer()` before `json.dumps()`. |
| Removed unused `self._cache` in pipeline | `src/pipeline.py` | Dead code — caching actually lives in `OpenRouterLLMClient`. |
| Bounded LRU cache | `src/llm_client.py` | Unbounded `dict` → `_LRUCache(128)` to prevent OOM in long-running processes. |
| Hash-based cache key | `src/llm_client.py` | `sha256(json.dumps(...))` instead of storing full serialized messages as keys. |
| Thread-safe `pop_stats()` | `src/llm_client.py` | Added `threading.Lock` around stats read+reset to prevent data loss in concurrent use. |
| Removed double retry in `generate_sql()` | `src/llm_client.py` | Outer retry loop (2 attempts) × inner `_chat()` retry (3 attempts) = up to 6 API calls. Removed outer loop; `_chat()` retries handle transient failures. |
| Aligned fetch limit | `src/pipeline.py` | `fetchmany(100)` → `fetchmany(50)` — was fetching 100 rows but only using 20 for answer generation. |
| Extracted `UNANSWERABLE_MSG` constant | `src/types.py` | Same string was hardcoded in 3 places — now a single constant. |
| Safer PRAGMA in `_load_schema` | `src/pipeline.py` | Uses f-string with `""` escaping for `PRAGMA table_info()` (SQLite PRAGMAs do not support parameterized queries). |
| Added tests | `tests/test_cache_and_retry.py`, `tests/test_result_validation.py` | LRU cache (5 tests), retry behaviour (2 tests), cache integration (1 test), result validation (6 tests). |
| Updated CHECKLIST.md | `CHECKLIST.md` | Fixed inaccurate claims (logging, sanitize_rows, test count). |

---

## Iteration 3 (audit-driven fixes)

Changes driven by strict audit against README requirements (see `temp/ITERATION3.md`).

### What Changed

| Change | Files | Why |
|--------|-------|-----|
| Configured Python logging | `src/__init__.py` | `logging.getLogger(__name__)` was used but no handler was configured — INFO/DEBUG logs silently dropped. Added `logging.basicConfig(level=logging.INFO)`. |
| Fixed `ResultValidator` dead logic branch | `src/pipeline.py` | Non-numeric aggregation check was nested inside `isinstance(val, (int, float))` guard — always False. Moved to `else` branch so it fires on non-numeric values in AVG/SUM columns. |
| Improved answer quality check | `src/pipeline.py` | Previous check only warned on answers < 5 chars. Now also verifies numeric values from SQL results appear in the answer (hallucination detection). |
| Aligned fetch/answer row limit | `src/pipeline.py` | `_MAX_ROWS_FOR_ANSWER` was 50 but `generate_answer()` only used `rows[:20]`. Aligned both to 20 — no wasted row fetching. |
| Fixed SOLUTION_NOTES inaccuracy | `SOLUTION_NOTES.md` | Iteration 2 claimed "parameterized PRAGMA" — code only uses f-string with `""` escaping. Corrected. |
| Removed stale `p._cache = {}` | `tests/test_conversation.py` | Dead code — `_cache` was removed from `AnalyticsPipeline` in Iteration 2. |
| Updated CHECKLIST.md | `CHECKLIST.md` | Updated logging, answer quality, and efficiency descriptions to reflect actual implementation. |

### Not Changed (by design)

| Item | Reason |
|------|--------|
| `LANGFUSE_BASE_URL` env var | Works correctly with the Langfuse SDK — no mismatch. |
| `max_tokens=4096` | Required for reasoning models that fill available context; model stops at EOS regardless. |
