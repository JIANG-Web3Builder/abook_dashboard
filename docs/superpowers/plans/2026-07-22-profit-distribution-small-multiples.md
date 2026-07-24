# Profit Distribution Small Multiples Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the overlapping period-stacked P&L chart with separate per-period bar charts whose hover tooltip explains the distribution values.

**Architecture:** Keep the existing `distribution_by_period` API contract and P&L buckets. Change only `BookPerformance.vue` so each Book renders one ECharts bar chart per month/phase, with a single bar series and per-bucket colors; the existing distribution table remains the detailed tabular audit.

**Tech Stack:** Vue 3, TypeScript, ECharts, pytest source-contract tests, Vite.

## Global Constraints

- Do not modify the database or API response shape.
- Keep `selection_total` as a separate summary period, not mixed into monthly axes.
- Tooltip must show bucket label, account count, account rate, and bucket net P&L.
- Use a zero-based account-count axis and retain readable labels on laptop and mobile widths.

---

### Task 1: Lock the chart contract with a failing frontend test

**Files:**
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- The test inspects `frontend/src/components/BookPerformance.vue` and requires per-period chart containers, a single-series bar distribution, and a tooltip formatter.

- [ ] **Step 1: Add the regression assertions**

```python
def test_book_performance_renders_separate_period_profit_distribution_bars_with_tooltips():
    books = _source("components/BookPerformance.vue")
    assert "distributionPeriods" in books
    assert "distribution-grid" in books
    assert "formatter: (params" in books
    assert "account_rate" in books
    assert "net_pnl" in books
    assert "stack: 'pnl-distribution'" not in books
```

- [ ] **Step 2: Run the focused test and verify it fails for the old overlapping chart**

Run: `.venv/bin/python -m pytest -q tests/test_frontend_contract.py -k separate_period_profit_distribution`

Expected: FAIL because the current component has no `distributionPeriods`/`distribution-grid` implementation and still uses `stack: 'pnl-distribution'`.

### Task 2: Implement independent period distribution charts

**Files:**
- Modify: `frontend/src/components/BookPerformance.vue`
- Modify: `frontend/src/style.css`

**Interfaces:**
- Consume `analytics.pnl_structure[book].distribution_by_period` rows with `period`, `key`, `label`, `accounts`, `account_rate`, and `net_pnl`.
- Produce one chart container for each Book and each unique period, with tooltip details for the hovered P&L bucket.

- [ ] **Step 1: Add period grouping and chart rendering**

Use `distributionPeriods` to preserve backend period order, map `selection_total` to `5–6月合计`, and render a single bar series per chart. Use the hovered bar's row for the tooltip so the tooltip reports the exact account count, rate, and net P&L for that bucket.

- [ ] **Step 2: Replace the overlapping chart markup**

Render a `.distribution-grid` of titled chart cards inside each Book section. Keep the existing distribution table below it as the audit detail.

- [ ] **Step 3: Add responsive chart-card styling**

Use a responsive grid with a minimum card width, a fixed readable chart height, and mobile overflow behavior without shrinking chart labels to unreadable text.

- [ ] **Step 4: Run the focused frontend-contract test**

Run: `.venv/bin/python -m pytest -q tests/test_frontend_contract.py -k separate_period_profit_distribution`

Expected: PASS.

### Task 3: Verify production behavior

**Files:**
- No additional source files.

- [ ] **Step 1: Run all backend and contract tests**

Run: `.venv/bin/python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Build the frontend**

Run from `frontend/`:

```bash
PATH=/Users/jianghe/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH \
/Users/jianghe/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node \
/Users/jianghe/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/pnpm/bin/pnpm.mjs run build
```

Expected: Vite exits with code 0 and writes `static/app.js`.

- [ ] **Step 3: Inspect the real Users panel**

Open the local dashboard, select `用户结构`, and verify that each Book shows separate 5月、6月、5–6月合计、7月 charts; hover a bar and verify the tooltip shows users, percentage, and bucket net P&L.

- [ ] **Step 4: Check the final diff**

Run: `git diff --check`

Expected: no whitespace errors and no unrelated files staged.
