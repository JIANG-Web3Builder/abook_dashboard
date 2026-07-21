# Abook Panel Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the overview, book analytics, risk, and routing panels use the same complete routed population, and expose strict `validation P&L > 0` Abook precision.

**Architecture:** Keep the existing display-only `accounts` list for the account table, add a complete `population_accounts` list for aggregate and book-analysis consumers, and make `/api/abook/book-analytics` prefer that complete population. Compute strict positive precision from the canonical routed population without changing the existing active-account precision used for lift/recall.

**Tech Stack:** FastAPI/Python service, Vue 3/TypeScript frontend, pytest, Vite static build.

## Global Constraints

- Do not modify or stage the user's unrelated working-tree changes.
- Strictly classify a profitable validation account as `client_net_pnl > 0`.
- Keep existing active-account `precision` semantics unchanged for transition/lift metrics.
- Preserve the current display behavior that hides accounts with zero P&L from account tables.

---

### Task 1: Lock the population and precision contracts with tests

**Files:**
- Modify: `tests/test_two_stage_analysis.py`
- Modify: `tests/test_book_analytics.py`
- Modify: `tests/test_frontend_contract.py`

- [ ] **Step 1: Write failing tests**

  Add assertions that a payload with a zero-P&L routed account contains it in `population_accounts` while the display `accounts` list still hides it; assert strict positive Abook precision uses the full population denominator; assert the frontend source consumes `population_accounts` for overview summaries and book analytics.

- [ ] **Step 2: Run the focused tests and verify failure**

  Run:

  ```bash
  .venv/bin/python -m pytest -q tests/test_two_stage_analysis.py tests/test_book_analytics.py tests/test_frontend_contract.py
  ```

  Expected: FAIL because `population_accounts` and strict precision are not yet exposed or consumed.

### Task 2: Expose a complete routed population from the backend

**Files:**
- Modify: `app/service.py:1835-1855`
- Modify: `app/main.py:245-275`

- [ ] **Step 1: Add the full population to the analysis payload**

  Return:

  ```python
  "population_accounts": accounts,
  "accounts": [account for account in accounts if account["has_nonzero_pnl"]],
  ```

  and preserve all existing backend group summaries, which already aggregate `accounts` before the display filter.

- [ ] **Step 2: Add strict total-denominator precision to validation summaries**

  In `_group_summary`, calculate `strict_positive = sum(value > ZERO for value in validation_values)` and expose `strict_positive_accounts` plus `strict_positive_account_rate`. Do not change `precision`, `target_total`, or `recall`.

- [ ] **Step 3: Make book analytics use the complete population**

  In `/api/abook/book-analytics`, select `analysis_payload.get("population_accounts", analysis_payload.get("accounts", []))` before deriving `abook_keys` and building analytics. This keeps old payloads compatible while preventing zero-P&L accounts from disappearing from book panels.

### Task 3: Align overview aggregation and add strict Precision display

**Files:**
- Modify: `frontend/src/components/AbookAnalysis.vue`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/types.ts`

- [ ] **Step 1: Use complete routed accounts for overview aggregates**

  Add a computed population source that prefers `data.population_accounts` and falls back to `data.accounts`; use it for Abook/Bbook summaries, while retaining the display list filter for the account table.

- [ ] **Step 2: Display strict validation Precision**

  Add a validation-card row showing `严格 Precision（P&L > 0 / Abook 总人数）` and format `strict positive accounts / total Abook accounts` as a percentage. Include zero-P&L accounts in the denominator.

- [ ] **Step 3: Pass complete Abook keys into lazy book analytics**

  Update `loadBook()` to derive `abook_accounts` from `data.population_accounts` when present, avoiding a stale or display-filtered account set.

### Task 4: Build and verify the synchronized artifact

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Run backend and frontend contract tests**

  Run:

  ```bash
  .venv/bin/python -m pytest -q
  ```

- [ ] **Step 2: Rebuild the static frontend**

  Run:

  ```bash
  /Users/jianghe/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node frontend/node_modules/vite/bin/vite.js build --outDir ../static
  ```

- [ ] **Step 3: Verify the generated bundle and working tree**

  Run:

  ```bash
  git diff --check
  git status --short --branch
  ```

  Confirm the diff contains only the intended files plus pre-existing user changes that remain unstaged.

