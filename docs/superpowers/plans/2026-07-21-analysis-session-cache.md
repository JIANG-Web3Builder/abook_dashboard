# Analysis Session Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse the intermediate data produced by the overview analysis when loading Book Analytics, without changing ClickHouse tables or removing test/demo filtering.

**Architecture:** The analysis endpoint stores the latest analysis execution context in a bounded in-process TTL cache and returns an opaque `analysis_token`. The frontend sends that token to Book Analytics; the endpoint reuses cached payload/accounts/daily rows and only performs the remaining Book-specific symbol query. If the token is absent, expired, or mismatched, the existing full-analysis fallback remains available.

**Tech Stack:** FastAPI, Pydantic, Python standard library cache primitives, Vue 3, TypeScript, pytest.

## Global Constraints

- Do not modify ClickHouse schemas, materialized views, or source tables.
- Keep case-insensitive `test`/`demo` account exclusion unchanged.
- Cache entries must expire and be bounded so stale analysis is not retained indefinitely.
- Preserve the existing `/api/abook/book-analytics` request behavior when no valid token is supplied.

---

### Task 1: Add the cache contract and failing backend tests

**Files:**
- Create: `app/analysis_cache.py`
- Modify: `app/models.py`
- Modify: `tests/test_api_book_analytics.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- `AnalysisSessionCache.put(session) -> str`
- `AnalysisSessionCache.get(token, signature) -> AnalysisSession | None`
- `BookAnalyticsRequest.analysis_token: str | None`
- Analysis response includes optional `analysis_token`.

- [x] **Step 1: Write failing tests**

Add tests that assert a Book Analytics request carrying the overview token does not call `fetch_analysis` again, and that an expired/missing token falls back to the old path. Add a request-model test asserting `analysis_token` is accepted.

- [x] **Step 2: Run the focused tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_api_book_analytics.py tests/test_api.py -k "analysis_token or cache"
```

Expected: FAIL because the request field, cache, and token-aware endpoint path do not exist.

- [x] **Step 3: Implement the minimal cache data model**

Use a dataclass containing token, request signature, creation time, payload, population accounts, daily rows, and overview daily rows. Use `OrderedDict` plus a lock, evicting the oldest entry when capacity is exceeded and returning `None` after TTL expiry.

- [x] **Step 4: Run the focused tests to verify the cache contract**

Run the same focused command and expect the cache/model tests to pass; endpoint tests may remain red until Task 2.

### Task 2: Store analysis sessions and reuse them in Book Analytics

**Files:**
- Modify: `app/main.py`
- Modify: `app/models.py`
- Modify: `tests/test_api_book_analytics.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Internal `_execute_analysis(request, repository) -> tuple[dict, AnalysisSession]`.
- `analysis()` stores a session and adds `analysis_token` to the response.
- `book_analytics()` reads a matching cached session before invoking the fallback `analysis()` path.

- [x] **Step 1: Add a regression test for one analysis query across Tab navigation**

Use a spy repository with counters. First call `/api/abook/analysis`, capture `analysis_token`, then call `/api/abook/book-analytics` with that token and assert `fetch_analysis` was called only by the first request. Assert the Book response still contains all four blocks.

- [x] **Step 2: Run the regression test and confirm it fails**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_api_book_analytics.py -k "reuses_analysis_session"
```

Expected: FAIL because Book Analytics currently invokes `analysis()` again.

- [x] **Step 3: Reuse the existing analysis execution without changing its calculations**

Move the current body of `analysis()` into `_execute_analysis()`, returning both the existing payload and all intermediate rows needed by `build_book_analytics`. Keep exception handling at the endpoint boundary.

- [x] **Step 4: Store the session and attach the token**

Build a deterministic signature from the serialized `AnalysisRequest` and store the execution session in the bounded cache. Add `analysis_token` only as an additive response field.

- [x] **Step 5: Reuse the session in `book_analytics()`**

When `request.analysis_token` is present and the signature matches, use the cached payload/accounts/daily rows. Otherwise execute the existing fallback path. Keep symbol queries after the cache lookup, because they are Book-specific.

- [x] **Step 6: Run the focused tests to verify the endpoint behavior**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_api_book_analytics.py tests/test_api.py -k "analysis_token or cache or book_analytics"
```

Expected: PASS.

### Task 3: Wire the token through the Vue client

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.vue`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- `AnalysisPayload.analysis_token?: string`
- `fetchBookAnalytics(request, accounts, analysisToken?)` sends `analysis_token`.

- [x] **Step 1: Write failing frontend contract tests**

Assert that the source includes `analysis_token` in the analysis payload type, sends it in the Book Analytics request, and passes `data.analysis_token` from `loadBook()`.

- [x] **Step 2: Run the contract tests and confirm they fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_frontend_contract.py -k "analysis_token"
```

Expected: FAIL because the token is not currently typed or sent.

- [x] **Step 3: Add the additive frontend wiring**

Store the returned token inside `data`, pass it from `loadBook()`, and keep the existing account list and fallback behavior unchanged.

- [x] **Step 4: Run frontend contract tests**

Run the focused command and expect PASS.

### Task 4: Verify regressions and inspect the final diff

**Files:**
- No new files.

- [x] **Step 1: Run the full Python test suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [x] **Step 2: Run formatting and diff checks**

```bash
git diff --check
git status --short
```

- [x] **Step 3: Review the changed data flow**

Confirm that test/demo predicates remain present in query builders, cache misses still use the old endpoint path, and no ClickHouse schema or source-table file changed.
