# 马丁精度确认改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将马丁检测从“单窗口最坏等级硬拦截”改为“重复确认窗口才硬拦截”，减少正常顺势加仓和偶发异常窗口造成的 Abook 误判。

**Architecture:** 保留 `risk.dws_account_martingale_window` 作为窗口级事实来源，在 `scripts/build_martingale_snapshot.py` 中同时计算宽松候选和完整确认候选；在 `app/martingale.py` 中把快照状态与用户级检测状态分开；在 `app/service.py` 中仅对 `martingale_detection_status == confirmed` 且等级在排除列表内的账户执行马丁硬拦截。现有 `martingale_status` 继续表示快照 `ready/missing/invalid/stale` 状态。

**Tech Stack:** Python 3.9、FastAPI、Pydantic、ClickHouse SQL、pytest、现有 Vue3/Vite 前端契约测试。

## Global Constraints

- 只使用选择期窗口，不使用 7 月验证期数据。
- 不改变杠杆、P&L 筛选条件和账户详情功能。
- 不把单个窗口异常直接升级为 confirmed 或硬拦截。
- 现有 `martingale_status` 字段继续表示快照状态，新增字段使用 `martingale_detection_status`。
- 快照缺失、损坏或过期时继续保持现有安全路由：不得解释为“没有马丁用户”。
- 所有代码修改先写失败测试；每个任务完成后运行对应测试。

---

### Task 1: 固化确认式马丁判定与快照契约

**Files:**
- Modify: `tests/test_martingale.py`
- Modify: `tests/test_api_martingale.py`
- Modify: `tests/test_routing_contract.py`
- Modify: `tests/test_queries.py`

**Interfaces:**
- Consumes: existing snapshot records and `MartingaleSnapshot.blocked_logins()`.
- Produces: executable expectations for `martingale_detection_status`, `confirmed_windows`, `confirmed_extreme_windows`, `expanded_windows` and confirmed-only blocking.

- [ ] **Step 1: Write failing snapshot tests**

Add records with explicit detection fields and assert:

```python
def _record(login, level="extreme", detection="suspected", confirmed_windows=1, hard_block=False):
    return {
        "platform": "mt5", "login": login, "risk_level": level,
        "detection_mode": "confirmed" if confirmed_windows else "expanded",
        "martingale_detection_status": detection,
        "confirmed_windows": confirmed_windows,
        "confirmed_extreme_windows": confirmed_windows if level == "extreme" else 0,
        "expanded_windows": 1,
        "hard_block": hard_block,
        "layer_hits": {"layer1": True, "layer3": True, "layer4": True},
    }

def test_single_extreme_candidate_is_suspected_and_not_blocked(tmp_path):
    snapshot = ready_snapshot(tmp_path, [_record(7)])
    assert snapshot.blocked_logins(("extreme",)) == set()
    row = snapshot.enrich_rows([{"platform": "mt5", "login": 7}])[0]
    assert row["martingale_status"] == "ready"
    assert row["martingale_detection_status"] == "suspected"

def test_repeated_confirmed_extreme_is_hard_blocked(tmp_path):
    snapshot = ready_snapshot(tmp_path, [_record(7, detection="confirmed", confirmed_windows=2, hard_block=True)])
    assert snapshot.blocked_logins(("extreme",)) == {("mt5", 7)}
```

- [ ] **Step 2: Add service/API expectations**

Change the API fixture record to a confirmed record for the existing “martingale beats personal candidate” test, then add a suspected record test:

```python
def test_suspected_martingale_does_not_override_abook_rules(tmp_path, monkeypatch):
    # Use the existing profitable FakeRepository rows and a suspected extreme record.
    # The account must remain eligible for ordinary Abook rules.
    assert account["martingale_blocked"] is False
    assert account["martingale_detection_status"] == "suspected"
```

Keep the existing `_final_book(martingale_detected=True, ...)` unit contract unchanged; the service must pass `False` for suspected records and `True` only for confirmed records.

- [ ] **Step 3: Add query contract assertions**

Require the snapshot SQL to contain the strict confirmation expressions and aggregation fields:

```python
assert "confirmed_gate" in query
assert "confirmed_extreme_candidate" in query
assert "confirmed_extreme_windows" in query
assert "expanded_windows" in query
assert "averaging_down_profit <= 0" in query
```

- [ ] **Step 4: Run the focused tests and verify failure**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/test_martingale.py tests/test_api_martingale.py tests/test_routing_contract.py tests/test_queries.py
```

Expected: FAIL because the new snapshot fields and confirmed-only block do not exist yet.

### Task 2: Implement strict window and user-level snapshot scoring

**Files:**
- Modify: `scripts/build_martingale_snapshot.py:43-194`
- Modify: `app/martingale.py:17-99`

**Interfaces:**
- Consumes: existing `risk.dws_account_martingale_window` columns and current snapshot loader.
- Produces: records containing strict candidate counts, `martingale_detection_status`, and confirmed-only `hard_block` calculation.

- [ ] **Step 1: Add strict SQL expressions**

In the `scored` CTE, preserve the existing broad `gate` as an expanded candidate, add:

```sql
(layer1 AND layer3 AND layer4) AS confirmed_gate,
(layer1 AND layer3 AND layer4
 AND escalation >= 1.5
 AND max_sequence_length >= 7
 AND sequence_total_profit < 0
 AND averaging_down_profit <= 0) AS confirmed_extreme_candidate
```

Use `confirmed_gate` for strict tiering. A confirmed gate with no higher severity receives at least `low`; a normal `gate` without `confirmed_gate` remains an expanded candidate only. Preserve broad tier counts under the existing `extreme_windows/high_windows/medium_windows/low_windows` fields for audit, and add strict counts:

```sql
uniqExactIf(window_end, confirmed_gate) AS confirmed_windows,
uniqExactIf(window_end, confirmed_extreme_candidate) AS confirmed_extreme_windows,
uniqExactIf(window_end, gate AND NOT confirmed_gate) AS expanded_windows,
countIf(confirmed_gate AND confirmed_extreme_candidate) AS confirmed_extreme_candidate_rows
```

The query must also aggregate strict high/medium/low counts or derive the user risk level from the strict candidate counts in Python. Keep `averaging_down_profit` in the row-level CTE and enforce `<= 0` for confirmed extreme.

- [ ] **Step 2: Build user state from repeated independent windows**

When serializing a record:

```python
confirmed_windows = int(row["confirmed_windows"])
candidate_windows = int(row["extreme_windows"] + row["high_windows"] + row["medium_windows"] + row["low_windows"])
if confirmed_windows >= 2:
    detection_status = "confirmed"
elif candidate_windows or confirmed_windows:
    detection_status = "suspected"
else:
    detection_status = "none"
```

Choose `risk_level` by strict severity first, then broad severity for display. Do not let broad severity change `detection_status`. A record is emitted only when it has at least one broad or strict candidate, as before.

Add to each record:

```python
"martingale_detection_status": detection_status,
"confirmed_windows": confirmed_windows,
"confirmed_extreme_windows": int(row["confirmed_extreme_windows"]),
"expanded_windows": int(row["expanded_windows"]),
```

The two-window rule relies on distinct `window_end` values from the source table; do not count duplicated rows from the same window as separate confirmations.

- [ ] **Step 3: Make snapshot blocking confirmed-only**

Update `MartingaleSnapshot.blocked_logins()` to require both the selected level and `martingale_detection_status == "confirmed"`. Add `confirmed_users` and `suspected_users` to `summary()`. Keep `martingale_status` in `enrich_rows()` as the snapshot status and add `martingale_detection_status` from the matched record.

Reject a snapshot as `invalid` if a record has a malformed new detection status or non-integer confirmation counts. Existing fixture records must be updated to include the new fields; this prevents an old snapshot schema from silently using the old hard-block behavior.

- [ ] **Step 4: Run focused tests and verify they pass**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/test_martingale.py tests/test_api_martingale.py tests/test_routing_contract.py tests/test_queries.py
```

Expected: PASS.

### Task 3: Wire confirmed-only routing and audit fields into service output

**Files:**
- Modify: `app/service.py:1300-1358`
- Modify: `tests/test_two_stage_analysis.py`
- Modify: `tests/test_service_context.py`

**Interfaces:**
- Consumes: `MartingaleSnapshot` records with `martingale_detection_status`.
- Produces: account rows where suspected records remain eligible for ordinary Abook rules and confirmed records remain Bbook-first.

- [ ] **Step 1: Add failing routing tests**

Cover both cases through `build_two_stage_payload(...)` using a ready snapshot:

```python
assert suspected_account["martingale_blocked"] is False
assert suspected_account["martingale_hard_block"] is False
assert suspected_account["book"] == "abook"

assert confirmed_account["martingale_blocked"] is True
assert confirmed_account["martingale_hard_block"] is True
assert confirmed_account["selection_source"] == "martingale_blocked"
assert confirmed_account["book"] == "bbook"
```

- [ ] **Step 2: Implement the routing guard**

Replace the current level-only condition:

```python
martingale_blocked = snapshot_unavailable or martingale_level in set(excluded_martingale_levels)
```

with:

```python
martingale_detection_status = (
    martingale_record.get("martingale_detection_status", "none")
    if martingale_record else "none"
)
martingale_hard_block = (
    martingale_detection_status == "confirmed"
    and martingale_level in set(excluded_martingale_levels)
)
martingale_blocked = snapshot_unavailable or martingale_hard_block
```

Add `martingale_detection_status`, `martingale_hard_block`, `confirmed_windows`, `confirmed_extreme_windows`, and `expanded_windows` to the account payload. Keep `martingale_blocked` true for snapshot-unavailable safety behavior.

- [ ] **Step 3: Verify route precedence and payload serialization**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/test_two_stage_analysis.py tests/test_service_context.py tests/test_api_martingale.py tests/test_routing_contract.py
```

Expected: PASS and `json.dumps(payload, allow_nan=False)` remains valid.

### Task 4: Rebuild the real snapshot and compare old/new routing

**Files:**
- Modify: `data/martingale_snapshot.json` (generated local artifact; normally gitignored)
- Create: `docs/analysis/2026-07-20-martingale-precision-validation.md`

**Interfaces:**
- Consumes: ClickHouse `risk.dws_account_martingale_window` for 2026-05-01 through 2026-06-30 and the current 5–6 month account facts.
- Produces: refreshed snapshot and an auditable comparison of old and new detection/routing counts.

- [ ] **Step 1: Run the snapshot builder**

Run with the configured ClickHouse connection:

```bash
PYTHONPATH=. .venv/bin/python scripts/build_martingale_snapshot.py \
  --selection-start 2026-05-01 \
  --selection-end 2026-06-30 \
  --platform mt5 \
  --platform hh_mt5
```

Expected output includes the refreshed path, record count, and level counts without exposing credentials.

- [ ] **Step 2: Run the real analysis using the refreshed snapshot**

Use the existing FastAPI/TestClient analysis request for selection 2026-05-01..2026-06-30, validation 2026-07-01..2026-07-16 and platforms `mt5`, `hh_mt5`. Record:

```text
old snapshot records / levels
new snapshot records / levels
confirmed users
suspected users
old hard-block users vs new hard-block users
old Abook count / July P&L vs new Abook count / July P&L
```

Do not use July data to rebuild the martingale snapshot or tune its thresholds.

- [ ] **Step 3: Inspect at least 20 former extreme accounts**

For each sampled account, compare old and new status, confirmed window count, expanded window count, maximum escalation, maximum sequence length, averaging-down ratio, averaging-down profit, and July validation P&L. Save aggregate counts and representative reasons, not credentials or secrets, in the validation note.

- [ ] **Step 4: Run the full verification suite**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q
/Users/jianghe/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node \
  node_modules/vite/bin/vite.js build
```

Expected: all pytest tests pass and the Vite build completes successfully.

- [ ] **Step 5: Review generated validation note and working tree**

Confirm the note identifies the snapshot calculation window, source table, strict confirmation rule, count deltas, and any data limitations. Confirm unrelated pre-existing user changes remain unstaged.
