from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Iterable
from .warehouse import snapshot_generation_stale


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = BASE_DIR / "data" / "avg_profit_snapshot.json"


def snapshot_path() -> Path:
    return Path(os.getenv("ABOOK_AVG_PROFIT_SNAPSHOT_PATH", str(DEFAULT_SNAPSHOT_PATH)))


@dataclass(frozen=True)
class AvgProfitSnapshot:
    path: Path
    selection_start: str | None
    selection_end: str | None
    platforms: tuple[str, ...]
    records: tuple[dict[str, Any], ...]
    status: str
    source_table: str | None = None
    calculation: str | None = None
    missing_platforms: tuple[str, ...] = ()

    def indexed_records(self) -> dict[tuple[str, int], dict[str, Any]]:
        return {(str(row["platform"]), int(row["login"])): row for row in self.records}

    def allows(self, key: tuple[str, int], minimum: float) -> bool:
        if minimum <= 0:
            return True
        if self.status != "ready":
            return False
        record = self.indexed_records().get((str(key[0]), int(key[1])))
        return record is not None and record.get("avg_profit") is not None and float(record["avg_profit"]) > minimum

    def enrich_rows(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        indexed = self.indexed_records()
        enriched = []
        for row in rows:
            item = dict(row)
            record = indexed.get((str(row.get("platform")), int(row.get("login", 0))))
            item["avg_profit"] = None if record is None else record.get("avg_profit")
            item["avg_profit_status"] = self.status if record is not None else "snapshot_record_missing"
            enriched.append(item)
        return enriched

    def summary(self) -> dict[str, Any]:
        source_dates = [
            str(row.get("source_min"))[:10]
            for row in self.records
            if row.get("source_min")
        ] + [
            str(row.get("source_max"))[:10]
            for row in self.records
            if row.get("source_max")
        ]
        return {
            "status": self.status,
            "path": str(self.path),
            "selection_start": self.selection_start,
            "selection_end": self.selection_end,
            "platforms": list(self.platforms),
            "missing_platforms": list(self.missing_platforms),
            "records": len(self.records),
            "source_table": self.source_table,
            "calculation": self.calculation,
            "source_min": min(source_dates) if source_dates else None,
            "source_max": max(source_dates) if source_dates else None,
        }


def load_avg_profit_snapshot(path: Path, selection_start: str, selection_end: str, platforms: list[str]) -> AvgProfitSnapshot:
    if not path.exists():
        return AvgProfitSnapshot(path, None, None, tuple(), tuple(), "missing")
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return AvgProfitSnapshot(path, None, None, tuple(), tuple(), "invalid")
    snapshot_platforms = tuple(sorted(str(value) for value in payload.get("platforms", [])))
    requested = tuple(sorted(str(value) for value in platforms))
    dates_match = payload.get("selection_start") == selection_start and payload.get("selection_end") == selection_end
    missing_platforms = tuple(sorted(set(requested) - set(snapshot_platforms)))
    status = "stale" if not dates_match or snapshot_generation_stale(payload, path=path, snapshot_name="avg_profit") else "partial" if missing_platforms else "ready"
    records = tuple(payload.get("records", [])) if status in {"ready", "partial"} else tuple()
    return AvgProfitSnapshot(
        path=path,
        selection_start=payload.get("selection_start"),
        selection_end=payload.get("selection_end"),
        platforms=snapshot_platforms,
        records=records,
        status=status,
        source_table=payload.get("source_table"),
        calculation=payload.get("calculation"),
        missing_platforms=missing_platforms,
    )


def build_avg_profit_filter(request: Any) -> AvgProfitSnapshot:
    return load_avg_profit_snapshot(
        snapshot_path(),
        request.selection.start.isoformat(),
        request.selection.end.isoformat(),
        request.platforms,
    )
