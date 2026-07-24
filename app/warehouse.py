from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4


class WarehouseError(RuntimeError):
    """Base error for local Warehouse operations."""


class WarehouseCoverageError(WarehouseError):
    """Raised when local data cannot satisfy a requested date/platform range."""

    def __init__(self, missing: list[dict[str, Any]]):
        self.missing = missing
        detail = "; ".join(
            f"{item['table']}[{item['platform']}] {item['start']}..{item['end']}"
            for item in missing
        )
        super().__init__(f"local Warehouse coverage is incomplete: {detail}")


@dataclass(frozen=True)
class SyncPlan:
    target_start: date
    target_end: date
    pull_months: tuple[str, ...]
    preserved_months: tuple[str, ...]


def table_platforms(table: str) -> tuple[str, ...]:
    """Return source platforms represented by a normalized Warehouse table."""
    if table in {"ods_mt4_users", "ods_mt4_daily_balance"}:
        return ("mt4",)
    if table in {"ods_mt5_users", "ods_mt5_deals", "ods_mt5_daily_balance"}:
        return ("mt5", "hh_mt5")
    return ("mt4", "mt5", "hh_mt5")


def empty_manifest() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generation": None,
        "updated_at": None,
        "tables": {},
    }


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_manifest()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WarehouseError(f"invalid Warehouse manifest: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise WarehouseError(f"unsupported Warehouse manifest: {path}")
    return payload


def publish_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def warehouse_lock(root: Path) -> Iterator[None]:
    """Coordinate CLI and web processes with a filesystem lock."""
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".sync.lock"
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _subtract_months(value: date, months: int) -> date:
    if months < 0:
        raise ValueError("months must be non-negative")
    absolute = value.year * 12 + value.month - 1 - months
    year, month_index = divmod(absolute, 12)
    month = month_index + 1
    last_day = (date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(value.day, last_day))


def _month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _months_between(start: date, end: date) -> list[str]:
    current = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    result: list[str] = []
    while current <= last:
        result.append(_month_key(current))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return result


def build_sync_plan(
    *,
    selection_start: date,
    selection_end: date,
    validation_start: date,
    validation_end: date,
    existing_months: set[str],
    tail_days: int = 7,
    lookback_months: int = 3,
) -> SyncPlan:
    if tail_days < 1:
        raise ValueError("tail_days must be at least 1")
    if selection_end >= validation_start:
        raise ValueError("selection must end before validation starts")
    target_start = _subtract_months(min(selection_start, validation_start), lookback_months)
    target_end = max(selection_end, validation_end)
    needed_months = _months_between(target_start, target_end)
    tail_start = target_end - timedelta(days=tail_days - 1)
    rewrite_months = set(_months_between(tail_start, target_end))
    pull_months = tuple(
        month for month in needed_months if month not in existing_months or month in rewrite_months
    )
    preserved_months = tuple(sorted(existing_months - set(pull_months)))
    return SyncPlan(
        target_start=target_start,
        target_end=target_end,
        pull_months=pull_months,
        preserved_months=preserved_months,
    )


def _interval_covers(intervals: list[dict[str, str]], start: date, end: date) -> bool:
    ranges = sorted(
        (date.fromisoformat(item["start"]), date.fromisoformat(item["end"]))
        for item in intervals
    )
    cursor = start
    for interval_start, interval_end in ranges:
        if interval_end < cursor:
            continue
        if interval_start > cursor:
            return False
        cursor = max(cursor, interval_end + timedelta(days=1))
        if cursor > end:
            return True
    return cursor > end


def ensure_coverage(
    manifest: dict[str, Any],
    *,
    start: date,
    end: date,
    platforms: list[str],
    tables: list[str],
) -> None:
    missing: list[dict[str, Any]] = []
    for table in tables:
        table_payload = manifest.get("tables", {}).get(table, {})
        coverage = table_payload.get("coverage", {})
        for platform in platforms:
            if platform not in table_platforms(table):
                continue
            intervals = coverage.get(platform, [])
            if not _interval_covers(intervals, start, end):
                missing.append({
                    "table": table,
                    "platform": platform,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                })
    if missing:
        raise WarehouseCoverageError(missing)


def ensure_tables(manifest: dict[str, Any], tables: list[str]) -> None:
    missing = [table for table in tables if table not in manifest.get("tables", {})]
    if missing:
        raise WarehouseCoverageError([
            {"table": table, "platform": "*", "start": "unknown", "end": "unknown"}
            for table in missing
        ])


def new_generation() -> str:
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z") + "-" + uuid4().hex[:8]


def snapshot_generation_stale(
    payload: dict[str, Any],
    *,
    path: Path | None = None,
    snapshot_name: str | None = None,
) -> bool:
    snapshot_generation = payload.get("warehouse_generation")
    from .config import get_settings

    settings = get_settings()
    if settings.data_source != "local":
        return False
    manifest_generation = load_manifest(Path(settings.warehouse_path) / "manifest.json").get("generation")
    # Legacy snapshots without a generation remain compatible. All snapshots
    # written by the refresh CLI carry a generation, so a generation mismatch
    # is the reliable stale signal without breaking isolated/custom snapshot
    # files used by callers and tests.
    if not snapshot_generation:
        return False
    return bool(manifest_generation and manifest_generation != snapshot_generation)
