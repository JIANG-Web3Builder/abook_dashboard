from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Optional
from .warehouse import snapshot_generation_stale


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = BASE_DIR / "data" / "martingale_snapshot.json"

RISK_LEVELS = ("extreme", "high", "medium", "low")
DETECTION_STATUSES = ("none", "suspected", "confirmed")
DEFAULT_EXCLUDED_LEVELS = ("extreme", "high", "medium", "low")


def _valid_record(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    if not isinstance(record.get("platform"), str):
        return False
    try:
        int(record["login"])
    except (KeyError, TypeError, ValueError):
        return False
    if record.get("risk_level") not in RISK_LEVELS:
        return False
    if record.get("martingale_detection_status") not in DETECTION_STATUSES:
        return False
    for field in ("confirmed_windows", "confirmed_extreme_windows", "expanded_windows"):
        value = record.get(field)
        if not isinstance(value, int) or value < 0:
            return False
    layer_hits = record.get("layer_hits", {})
    return isinstance(layer_hits, dict) and all(isinstance(value, bool) for value in layer_hits.values())


def snapshot_path() -> Path:
    return Path(os.getenv("ABOOK_MARTINGALE_SNAPSHOT_PATH", str(DEFAULT_SNAPSHOT_PATH)))


@dataclass(frozen=True)
class MartingaleSnapshot:
    path: Path
    selection_start: Optional[str]
    selection_end: Optional[str]
    platforms: tuple[str, ...]
    window_type: str
    records: tuple[dict[str, Any], ...]
    status: str
    missing_platforms: tuple[str, ...] = ()

    def indexed_records(self) -> dict[tuple[str, int], dict[str, Any]]:
        return {
            (str(record["platform"]), int(record["login"])): record
            for record in self.records
        }

    def enrich_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        indexed = self.indexed_records() if self.status in {"ready", "partial"} else {}
        enriched = []
        for row in rows:
            key = (str(row["platform"]), int(row["login"]))
            record = indexed.get(key)
            item = dict(row)
            item.update({
                "martingale_status": self.status,
                "martingale_risk_level": record.get("risk_level") if record else None,
                "martingale_detection_status": (
                    record.get("martingale_detection_status", "none") if record else "none"
                ),
                "martingale_layer_hits": dict(record.get("layer_hits", {})) if record else {},
                "martingale_record": dict(record) if record else None,
            })
            enriched.append(item)
        return enriched

    def blocked_logins(self, excluded_levels: tuple[str, ...] | list[str] | set[str]) -> set[tuple[str, int]]:
        if self.status not in {"ready", "partial"}:
            return set()
        levels = set(excluded_levels)
        return {
            (str(record["platform"]), int(record["login"]))
            for record in self.records
            if record.get("risk_level") in levels
            and record.get("martingale_detection_status") == "confirmed"
        }

    def level_counts(self) -> dict[str, int]:
        counts = {level: 0 for level in RISK_LEVELS}
        for record in self.records:
            level = record.get("risk_level")
            if level in counts:
                counts[level] += 1
        return counts

    def summary(self, excluded_levels: tuple[str, ...] | list[str] | set[str] = DEFAULT_EXCLUDED_LEVELS) -> dict[str, Any]:
        confirmed_level_counts = {level: 0 for level in RISK_LEVELS}
        for record in self.records:
            if record.get("martingale_detection_status") == "confirmed" and record.get("risk_level") in confirmed_level_counts:
                confirmed_level_counts[record["risk_level"]] += 1
        return {
            "status": self.status,
            "message": (
                "马丁过滤已生效" if self.status == "ready"
                else "马丁过滤部分生效，缺少平台快照" if self.status == "partial"
                else f"马丁过滤未生效：快照状态为 {self.status}"
            ),
            "path": str(self.path),
            "selection_start": self.selection_start,
            "selection_end": self.selection_end,
            "platforms": list(self.platforms),
            "missing_platforms": list(self.missing_platforms),
            "window_type": self.window_type,
            "records": len(self.records),
            "level_counts": self.level_counts(),
            "confirmed_level_counts": confirmed_level_counts,
            "excluded_levels": sorted(set(excluded_levels)),
            "blocked_users": len(self.blocked_logins(excluded_levels)),
            "confirmed_users": sum(
                1 for record in self.records
                if record.get("martingale_detection_status") == "confirmed"
            ),
            "suspected_users": sum(
                1 for record in self.records
                if record.get("martingale_detection_status") == "suspected"
            ),
        }


def load_martingale_snapshot(
    path: Path,
    selection_start: str,
    selection_end: str,
    platforms: list[str],
) -> MartingaleSnapshot:
    if not path.exists():
        return MartingaleSnapshot(path, None, None, tuple(), "", tuple(), "missing")
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return MartingaleSnapshot(path, None, None, tuple(), "", tuple(), "invalid")
    snapshot_platforms = tuple(sorted(str(value) for value in payload.get("platforms", [])))
    requested_platforms = tuple(sorted(str(value) for value in platforms))
    dates_match = payload.get("selection_start") == selection_start and payload.get("selection_end") == selection_end
    missing_platforms = tuple(sorted(set(requested_platforms) - set(snapshot_platforms)))
    status = "stale" if not dates_match or snapshot_generation_stale(payload, path=path, snapshot_name="martingale") else "partial" if missing_platforms else "ready"
    raw_records = payload.get("records", [])
    if not isinstance(raw_records, list):
        return MartingaleSnapshot(path, None, None, tuple(), "", tuple(), "invalid")
    if not all(_valid_record(record) for record in raw_records):
        return MartingaleSnapshot(path, None, None, tuple(), "", tuple(), "invalid")
    records = tuple(raw_records) if status in {"ready", "partial"} else tuple()
    return MartingaleSnapshot(
        path=path,
        selection_start=payload.get("selection_start"),
        selection_end=payload.get("selection_end"),
        platforms=snapshot_platforms,
        window_type=str(payload.get("window_type", "")),
        records=records,
        status=status,
        missing_platforms=missing_platforms,
    )


def build_martingale_filter(request: "Any") -> MartingaleSnapshot:
    """Load the snapshot matched to the request selection window."""
    return load_martingale_snapshot(
        snapshot_path(),
        request.selection.start.isoformat(),
        request.selection.end.isoformat(),
        request.platforms,
    )
