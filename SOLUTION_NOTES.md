# Solution Notes

## What Changed

### Foundation (EPIC 1)
- **Benchmark bug fix**: `result["status"]` changed to `result.status` — PipelineOutput is a dataclass, not a dict.
- **Dependency pinning**: Added version ranges to `requirements.txt` for reproducible builds.

### Token Counting (EPIC 2)
- **API response parsing**: Extract `usage.prompt_tokens` and `usage.completion_tokens` from OpenRouter response.
- **Stats accumulation**: Increment `_stats` counters on each `_chat()` call, track `llm_calls`.
- **Fallback estimation**: When API returns no usage data, estimate tokens (~1.3 tokens/word + 4 per-message overhead).

### SQL Generation Quality (EPIC 3)
- **Schema introspection**: Query `PRAGMA table_info` at pipeline init to get table names, columns, and types.
- **Schema context**: Pass actual schema to `generate_sql()` instead of empty `{}`.
- **Improved prompts**: System prompt includes schema, enforces SELECT-only, requests JSON output `{"sql": "..."}`.
- **Unanswerable handling**: LLM instructed to return `{"sql": null}` for questions outside schema.
- **Better extraction**: Handle markdown code fences, trailing semicolons, and explicit null detection.

### SQL Validation (EPIC 4)
- **SELECT-only**: Reject anything not starting with SELECT.
- **Blocked keywords**: Regex blocks INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, ATTACH, DETACH.
- **Dangerous patterns**: Blocks PRAGMA, sqlite_master, SQL comments (`--`, `/*`).
- **Multi-statement**: Reject semicolons within the query body.
- **Table allowlist**: Extract FROM/JOIN tables and reject references to non-existent tables.
- **Syntax validation**: EXPLAIN-based check catches syntax errors before execution.

### Observability (EPIC 5)
- **Structured logging**: Python `logging` at each pipeline stage with timing, errors, request_id.
- **Request tracing**: Auto-generated `request_id` (uuid) propagated through all stages.
- **LLM call logging**: Model, token counts, latency logged per call.

### Error Handling (EPIC 6)
- **Retry with backoff**: 2 retries with exponential backoff (1s, 2s) for LLM calls.
- **Execution recovery**: Clear sql/rows on execution error so answer gen handles gracefully.
- **NULL sanitization**: Replace None values with "N/A" in result rows.
- **Input sanitization**: Strip whitespace, reject empty questions, truncate at 1000 chars.
- **Timeout protection**: 30s connection timeout and busy_timeout for SQL execution.

### Efficiency (EPIC 7)
- **Prompt compression**: Compact schema format, terse prompts, minimal formatting.
- **max_tokens tuning**: Reduced from 240/220 to 150/150 for SQL gen and answer gen.
- **Result truncation**: 20 rows max (from 30) for answer generation.
- **Schema caching**: Loaded once at init, reused per request.
- **Response caching**: In-memory cache keyed by message content avoids duplicate API calls.

### Testing (EPIC 8)
- **SQL validation tests**: 14 tests covering all validation rules.
- **SQL extraction tests**: 8 tests for JSON/raw/null/edge-case extraction.
- **Token counting tests**: 4 tests for estimation and stats reset.
- **Edge case tests**: 4 tests for empty/long/whitespace input with mocked LLM.

## Why These Changes

1. **Token counting** is a hard requirement — efficiency evaluation depends on it.
2. **Schema context** is the single biggest quality improvement — without it, the LLM guesses column names and generates invalid SQL.
3. **SQL validation** is essential for security — the baseline accepts DELETE, DROP, etc.
4. **Observability** is non-negotiable for production — debugging blind is unacceptable.
5. **Error handling** prevents cascading failures and ensures every request returns a valid PipelineOutput.
6. **Efficiency** directly impacts user experience and cost.

## Tradeoffs

| Decision | Tradeoff |
|----------|----------|
| In-memory cache | Fast but non-persistent; lost on restart. Acceptable for single-process deployment. |
| Token estimation fallback | Approximate (~1.3x word count) but ensures non-zero stats when API omits usage. |
| max_tokens=150 | Saves tokens but may truncate complex SQL or long answers. Monitored via token stats. |
| 20-row limit for answers | Reduces prompt size but may miss data in large result sets. Sufficient for analytics summaries. |
| Regex-based SQL validation | Fast but not a full SQL parser. EXPLAIN-based check catches what regex misses. |

## Next Steps

- Add async support for concurrent pipeline requests
- Implement rate limiting for API cost control
- Add persistent caching (Redis/disk) for cross-restart efficiency
- Use tiktoken for exact token counting instead of heuristic
- Add SQL query optimization hints (e.g., LIMIT pushdown)
- Implement A/B testing framework for prompt variants
