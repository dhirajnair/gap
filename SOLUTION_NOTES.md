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

## Next Steps
1. Run `python3 scripts/benchmark.py --runs 3` and fill in benchmark numbers in CHECKLIST.md.
2. Column-level SQL validation.
3. LLM-based follow-up detection for edge cases.
4. Disk/Redis response cache for multi-process deployments.
5. Streaming responses for lower perceived latency.
