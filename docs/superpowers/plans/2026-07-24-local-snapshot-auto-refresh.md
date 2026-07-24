# Local Snapshot Auto-Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Dashboard detect when the local analysis snapshots no longer match the selected period, rebuild them from the local Warehouse automatically on analysis, and expose a manual rebuild button.

**Architecture:** Keep snapshot freshness logic in `app/snapshot_refresh.py`. It will compare all three snapshot files against the current Warehouse generation and the requested selection window; validation dates remain metadata only and do not trigger a rebuild. The analysis endpoint will call the shared ensure function before loading filters, while a separate local-only refresh endpoint supports the sidebar button. The Vue sidebar will display the mismatch and refresh state based on `/api/warehouse/status`.

**Tech Stack:** FastAPI, Pydantic, Python JSON snapshots, Vue 3, TypeScript, Vitest/Vue build contracts, pytest.

## Global Constraints

- Automatic and manual rebuilds must read the local Warehouse only; they must not download remote Parquet data or add remote refresh controls.
- Existing snapshot files must remain intact if any builder fails.
- Changing validation dates or analysis rules alone must not rebuild the selection snapshots.
- The Dashboard must visibly say that a rebuild is needed and provide `重建当前筛选期快照`.
- Preserve existing local-data coverage and stale-generation behavior.

---

### Task 1: Snapshot freshness contract

**Files:**
- Modify: `app/snapshot_refresh.py`
- Test: `tests/test_snapshot_refresh.py`

**Interfaces:**
- Produce `snapshot_status_for_request(request, settings=None) -> dict[str, object]`.
- Produce `ensure_local_snapshots_for_request(request, settings=None) -> dict[str, object]`.
- The returned status includes `status`, `refreshed`, `reason`, `warehouse_generation`, and per-snapshot `selection_start`, `selection_end`, and `status`.

- [ ] **Step 1: Write failing tests** for a ready selection window, a changed selection window, and a validation-only change. Assert that a selection change triggers `refresh_snapshots`, while a validation change does not.
- [ ] **Step 2: Run `pytest tests/test_snapshot_refresh.py -q`** and confirm the new tests fail because the freshness functions do not yet exist.
- [ ] **Step 3: Implement the status comparison and locked ensure function.** Read the local manifest and snapshot JSON, classify missing/invalid/generation-mismatch/window-mismatch files, and call the existing atomic `refresh_snapshots` only for local data when needed. Re-check status while holding `_REFRESH_LOCK` so concurrent analysis requests do not rebuild twice.
- [ ] **Step 4: Run the focused snapshot tests** and confirm they pass, including the existing rollback tests.

### Task 2: Backend API integration

**Files:**
- Modify: `app/main.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- `POST /api/warehouse/snapshots/refresh` accepts `AnalysisRequest` and returns the shared refresh result.
- `POST /api/abook/analysis` invokes `ensure_local_snapshots_for_request` before loading risk, avg-profit, and martingale filters and includes the refresh result in the response as `snapshot_refresh`.
- `GET /api/warehouse/status` exposes each snapshot’s stored selection and validation dates.

- [ ] **Step 1: Write failing API tests** for the refresh endpoint, analysis calling the ensure function, and Warehouse status exposing the stored window fields.
- [ ] **Step 2: Run the focused API tests** and confirm they fail on the missing route/response fields.
- [ ] **Step 3: Add the local-only refresh endpoint and analysis hook.** Return a clear HTTP 409/503 error for remote source or unavailable Warehouse metadata; preserve existing coverage error mapping and exception behavior.
- [ ] **Step 4: Run `pytest tests/test_api.py tests/test_snapshot_refresh.py -q`** and confirm the backend contract passes.

### Task 3: Dashboard warning and manual rebuild

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/components/FilterSidebar.vue`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Add `refreshLocalSnapshots(request)` in `frontend/src/api.ts` for the local refresh route.
- Add snapshot window fields to `WarehouseStatus`.
- App computes `snapshotNeedsRefresh` from the local status and current selection dates, handles `snapshotRefreshing`, and refreshes Warehouse status after manual/automatic analysis.
- FilterSidebar emits `refresh-snapshots` and renders `重建当前筛选期快照` when the selection snapshot is missing, stale, invalid, or for another selection window.

- [ ] **Step 1: Add failing frontend contract assertions** for the API route, manual button, event wiring, and mismatch state.
- [ ] **Step 2: Run `pytest tests/test_frontend_contract.py -q`** and confirm the assertions fail.
- [ ] **Step 3: Implement the API function, types, App handler, and sidebar warning/button.** Keep the warning scoped to selection dates so validation-date edits do not falsely request a rebuild.
- [ ] **Step 4: Run the frontend contract tests and `npm run build`** from `frontend/`.

### Task 4: End-to-end verification and regression check

**Files:**
- Modify: `README.md` or `data/README.md` only if the final refresh behavior needs documentation.

- [ ] **Step 1: Run the full backend suite with `pytest -q`.**
- [ ] **Step 2: Verify the live local API with `curl` or the project’s existing local server command.** Confirm that a request using `2026-05-14` or `2026-05-15` no longer returns all Abook users blocked solely by stale snapshots.
- [ ] **Step 3: Start the Dashboard and verify the sidebar warning, manual button, successful rebuild status, and normal apply flow.**
- [ ] **Step 4: Inspect the diff for accidental remote-download behavior, broad layout regressions, or unrelated edits, then report test/build results and any remaining data-source limitations.

## Self-Review Checklist

- [ ] Selection-window mismatch, missing files, invalid JSON, and Warehouse generation mismatch all trigger the same local rebuild path.
- [ ] Validation-window and rule-only changes do not rebuild snapshots.
- [ ] Failed builders leave every existing snapshot unchanged.
- [ ] The manual button is visible only when the local snapshot needs rebuilding and is disabled while rebuilding.
- [ ] Remote mode remains read-only and has no local refresh attempt.
- [ ] API and frontend types use the same field names.
