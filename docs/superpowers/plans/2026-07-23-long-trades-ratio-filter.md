# Long Trades Ratio Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable selection-period long-trades ratio filtering to Abook routing, its parameter sidebar, and the qualification funnel.

**Architecture:** Reuse the existing `long_trades` and `short_trades` fields from the account-period aggregation. Pass two validated thresholds through the existing request-to-service path, apply the inclusive interval to normal Abook routing, and expose the result through the existing selection metrics, flags, rules payload, and funnel stages.

**Tech Stack:** Python, Pydantic, FastAPI service functions, Vue 3, TypeScript, pytest, Vite.

## Global Constraints

- Ratio is computed only from the selection period.
- Ratio is exactly `long_trades / (long_trades + short_trades)`.
- Default inclusive range is `0.4 <= ratio <= 0.6`.
- Personal/news candidate overrides and martingale precedence keep their current semantics.
- Do not overwrite unrelated existing working-tree changes.

### Task 1: Validate and expose ratio rules

**Files:**
- Modify: `/Users/jianghe/abook_hedging/app/models.py`
- Modify: `/Users/jianghe/abook_hedging/frontend/src/App.vue`
- Test: `/Users/jianghe/abook_hedging/tests/test_models.py`

**Interfaces:**
- Produces `AnalysisRules.min_long_trades_ratio` and `AnalysisRules.max_long_trades_ratio`.

- [ ] **Step 1: Write failing model tests**

Add assertions for defaults and a validator test that rejects a minimum above the maximum.

- [ ] **Step 2: Run the focused model tests**

Run `pytest tests/test_models.py -q`; expect the new assertions to fail because the fields do not exist.

- [ ] **Step 3: Implement the Pydantic fields and range validator**

Use `Field(default=0.4, ge=0, le=1)` and `Field(default=0.6, ge=0, le=1)`, then reject `min_long_trades_ratio > max_long_trades_ratio` in the existing `AnalysisRules` model validator.

- [ ] **Step 4: Add frontend defaults**

Add the same two keys to `defaultRules` so reset and initial requests send the backend defaults explicitly.

- [ ] **Step 5: Run the focused model tests again**

Run `pytest tests/test_models.py -q`; expect all tests to pass.

### Task 2: Add service-level ratio calculation, routing, and funnel stage

**Files:**
- Modify: `/Users/jianghe/abook_hedging/app/service.py`
- Modify: `/Users/jianghe/abook_hedging/app/main.py`
- Test: `/Users/jianghe/abook_hedging/tests/test_service.py`
- Test: `/Users/jianghe/abook_hedging/tests/test_api.py`

**Interfaces:**
- `build_two_stage_payload(..., min_long_trades_ratio: float = 0.4, max_long_trades_ratio: float = 0.6)`.
- `classify_accounts` forwards both thresholds from `AnalysisRules`.

- [ ] **Step 1: Write failing service tests**

Create selection rows with `long_trades=4, short_trades=6`, `long_trades=6, short_trades=4`, and `long_trades=7, short_trades=3`; assert the first two pass inclusively, the last fails, and a row with `long_trades=7, short_trades=0` yields ratio `1.0` rather than using `matched_trades` as denominator.

- [ ] **Step 2: Run focused tests to verify failure**

Run `pytest tests/test_service.py -q`; expect failure because the service has no ratio rule or metric.

- [ ] **Step 3: Implement ratio aggregation and routing**

Expose `long_trades_ratio` from `_aggregate_account`, apply the inclusive interval to the normal route, and append `long_trades_ratio` to `selection_flags` when the route fails it.

- [ ] **Step 4: Thread rules from the API**

Pass the request thresholds from `app/main.py` into `build_two_stage_payload`; ensure `classify_accounts` passes the same values to the payload builder.

- [ ] **Step 5: Add the funnel stage and rule metadata**

Insert `direction_balance_passed` after `sample_qualified`, count failures using `long_trades_ratio`, and include both thresholds in `payload["rules"]`.

- [ ] **Step 6: Run focused backend tests**

Run `pytest tests/test_service.py tests/test_api.py -q`; expect all tests to pass.

### Task 3: Add parameter controls and funnel presentation

**Files:**
- Modify: `/Users/jianghe/abook_hedging/frontend/src/components/FilterSidebar.vue`
- Modify: `/Users/jianghe/abook_hedging/frontend/src/components/SelectionFunnel.vue`
- Modify: `/Users/jianghe/abook_hedging/tests/test_frontend_contract.py`

- [ ] **Step 1: Write failing frontend contract assertions**

Assert that the sidebar has both ratio controls and the funnel has the new stage key, label, reason, and threshold keys.

- [ ] **Step 2: Run the focused frontend contract test**

Run `pytest tests/test_frontend_contract.py -q`; expect the new assertions to fail.

- [ ] **Step 3: Implement the controls and funnel copy**

Add number inputs for minimum/maximum ratio with `0–1` bounds and `0.01` step. Add `direction_balance_passed`, the failure label, and a criterion showing the inclusive configured interval.

- [ ] **Step 4: Run frontend contract tests and build**

Run `pytest tests/test_frontend_contract.py -q` and `cd frontend && pnpm build`; expect both commands to pass.

### Task 4: Full verification

**Files:**
- Modify: `/Users/jianghe/abook_hedging/README.md` only if the current default-rule documentation needs the new criterion.

- [ ] **Step 1: Run the full backend test suite**

Run `pytest -q` and inspect the complete result for failures.

- [ ] **Step 2: Run the frontend production build**

Run `cd frontend && pnpm build` and inspect the exit code and output.

- [ ] **Step 3: Review the diff**

Run `git diff --check` and `git diff --stat`; confirm only the intended ratio feature files changed in addition to the design/plan documents.
