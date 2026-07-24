from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Iterable, Optional, TYPE_CHECKING
from .warehouse import snapshot_generation_stale

if TYPE_CHECKING:
    from .models import AnalysisRequest


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = BASE_DIR / "data" / "user_risk_snapshot.json"
RISK_CALCULATION_VERSION = "turnover_plus_concurrent_exposure_v4"
MAX_SQL_EXCLUDED_LOGIN_FILTER_SIZE = 20000


def snapshot_path() -> Path:
    return Path(os.getenv("ABOOK_RISK_SNAPSHOT_PATH", str(DEFAULT_SNAPSHOT_PATH)))


@dataclass(frozen=True)
class RiskSnapshot:
    path: Path
    selection_start: Optional[str]
    selection_end: Optional[str]
    platforms: tuple[str, ...]
    records: tuple[dict[str, Any], ...]
    status: str
    missing_platforms: tuple[str, ...] = ()

    def allowed_logins(
        self,
        max_leverage_p95_ratio: float | None,
        max_high_leverage_holding_seconds: float | None = None,
    ) -> set[tuple[str, int]]:
        if self.status not in {"ready", "partial"}:
            return set()
        allowed: set[tuple[str, int]] = set()
        for record in self.records:
            status = record.get("balance_status")
            leverage_p95 = record.get("leverage_p95_ratio", record.get("peak_leverage_ratio"))
            if status != "positive" or max_leverage_p95_ratio is None:
                allowed.add((str(record["platform"]), int(record["login"])))
            elif leverage_p95 is not None:
                short_hold_exception = (
                    max_high_leverage_holding_seconds is not None
                    and max_high_leverage_holding_seconds > 0
                    and record.get("median_holding_seconds") is not None
                    and float(record["median_holding_seconds"]) <= max_high_leverage_holding_seconds
                )
                if float(leverage_p95) <= max_leverage_p95_ratio or short_hold_exception:
                    allowed.add((str(record["platform"]), int(record["login"])))
        return allowed

    def excluded_logins(
        self,
        max_leverage_p95_ratio: float | None,
        max_high_leverage_holding_seconds: float | None = None,
    ) -> set[tuple[str, int]]:
        if self.status not in {"ready", "partial"} or max_leverage_p95_ratio is None:
            return set()
        return {
            (str(record["platform"]), int(record["login"]))
            for record in self.records
            if record.get("balance_status") == "positive"
            and record.get("leverage_p95_ratio", record.get("peak_leverage_ratio")) is not None
            and float(record.get("leverage_p95_ratio", record.get("peak_leverage_ratio"))) > max_leverage_p95_ratio
            and not (
                max_high_leverage_holding_seconds is not None
                and max_high_leverage_holding_seconds > 0
                and record.get("median_holding_seconds") is not None
                and float(record["median_holding_seconds"]) <= max_high_leverage_holding_seconds
            )
        }

    def enrich_rows(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        indexed = {
            (str(record["platform"]), int(record["login"])): record
            for record in self.records
        }
        enriched = []
        for row in rows:
            record = indexed.get((str(row["platform"]), int(row["login"])))
            item = dict(row)
            if record is None:
                item.update({
                    "risk_balance_prev_month": None,
                    "risk_average_open_degree": None,
                    "risk_peak_leverage_ratio": None,
                    "risk_leverage_p95_ratio": None,
                    "risk_turnover_leverage_p95_ratio": None,
                    "risk_concurrent_leverage_p95_ratio": None,
                    "risk_exposure_status": "snapshot_record_missing" if self.status == "ready" else f"snapshot_{self.status}",
                    "risk_median_holding_seconds": None,
                    "risk_balance_status": "snapshot_record_missing" if self.status == "ready" else f"snapshot_{self.status}",
                })
            else:
                item.update({
                    "risk_balance_prev_month": record.get("balance_latest", record.get("balance_prev_month")),
                    "risk_average_open_degree": record.get("average_open_degree"),
                    "risk_peak_leverage_ratio": record.get("peak_leverage_ratio"),
                    "risk_leverage_p95_ratio": record.get("leverage_p95_ratio", record.get("peak_leverage_ratio")),
                    "risk_turnover_leverage_p95_ratio": record.get("turnover_leverage_p95_ratio"),
                    "risk_concurrent_leverage_p95_ratio": record.get("concurrent_leverage_p95_ratio"),
                    "risk_exposure_status": record.get("exposure_status"),
                    "risk_median_holding_seconds": record.get("median_holding_seconds"),
                    "risk_balance_status": record.get("balance_status"),
                })
            enriched.append(item)
        return enriched

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "calculation_version": RISK_CALCULATION_VERSION,
            "path": str(self.path),
            "selection_start": self.selection_start,
            "selection_end": self.selection_end,
            "platforms": list(self.platforms),
            "missing_platforms": list(self.missing_platforms),
            "records": len(self.records),
        }


@dataclass(frozen=True)
class LocalRiskFilter:
    snapshot: RiskSnapshot
    allowed_logins: Optional[set[tuple[str, int]]] = None
    sql_login_filter_applied: bool = True
    excluded_logins: Optional[set[tuple[str, int]]] = None

    @property
    def status(self) -> str:
        return self.snapshot.status

    def summary(self) -> dict[str, Any]:
        if not self.sql_login_filter_applied:
            mode = "service_post_filter"
        elif self.excluded_logins is not None:
            mode = "local_login_exclusion"
        else:
            mode = "local_login_intersection"
        return {
            **self.snapshot.summary(),
            "sql_login_filter_applied": self.sql_login_filter_applied,
            "filter_mode": mode,
        }

    def selected_logins(self, request: "AnalysisRequest") -> Optional[set[tuple[str, int]]]:
        if self.allowed_logins is None:
            return None
        if not request.filters.logins:
            return self.allowed_logins
        explicit = {(platform, int(login)) for platform in request.platforms for login in request.filters.logins}
        return self.allowed_logins & explicit

    def is_empty_for(self, request: "AnalysisRequest") -> bool:
        selected = self.selected_logins(request)
        return selected is not None and not selected

    def apply(self, request: "AnalysisRequest") -> "AnalysisRequest":
        if self.allowed_logins is None:
            return request
        copied = request.model_copy(deep=True)
        selected = self.selected_logins(request)
        if selected is None:
            return copied
        copied.filters.logins = sorted(login for _, login in selected)
        return copied


def load_risk_snapshot(
    path: Path,
    selection_start: str,
    selection_end: str,
    platforms: list[str],
) -> RiskSnapshot:
    if not path.exists():
        return RiskSnapshot(path, None, None, tuple(), tuple(), "missing")
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return RiskSnapshot(path, None, None, tuple(), tuple(), "invalid")
    snapshot_platforms = tuple(sorted(str(value) for value in payload.get("platforms", [])))
    requested_platforms = tuple(sorted(str(value) for value in platforms))
    dates_match = payload.get("selection_start") == selection_start and payload.get("selection_end") == selection_end
    missing_platforms = tuple(sorted(set(requested_platforms) - set(snapshot_platforms)))
    version_match = payload.get("calculation_version") == RISK_CALCULATION_VERSION
    status = "stale" if not dates_match or not version_match or snapshot_generation_stale(payload, path=path, snapshot_name="risk") else "partial" if missing_platforms else "ready"
    records = tuple(payload.get("records", [])) if status in {"ready", "partial"} else tuple()
    return RiskSnapshot(
        path=path,
        selection_start=payload.get("selection_start"),
        selection_end=payload.get("selection_end"),
        platforms=snapshot_platforms,
        records=records,
        status=status,
        missing_platforms=missing_platforms,
    )


def build_local_risk_filter(request: "AnalysisRequest") -> LocalRiskFilter:
    snapshot = load_risk_snapshot(
        snapshot_path(),
        request.selection.start.isoformat(),
        request.selection.end.isoformat(),
        request.platforms,
    )
    allowed = None
    excluded = None
    sql_filter_applied = False
    if snapshot.status in {"ready", "partial"}:
        candidate_allowed = snapshot.allowed_logins(
            request.rules.max_leverage_p95_ratio,
            request.rules.max_high_leverage_holding_seconds,
        )
        explicit_logins = request.filters.logins
        if explicit_logins:
            explicit_pairs = {(platform, int(login)) for platform in request.platforms for login in explicit_logins}
            allowed = candidate_allowed & explicit_pairs
            sql_filter_applied = True
        else:
            candidate_excluded = snapshot.excluded_logins(
                request.rules.max_leverage_p95_ratio,
                request.rules.max_high_leverage_holding_seconds,
            )
            if len(candidate_excluded) <= MAX_SQL_EXCLUDED_LOGIN_FILTER_SIZE:
                excluded = candidate_excluded
                sql_filter_applied = True
            elif len(candidate_allowed) <= 2000:
                allowed = candidate_allowed
                sql_filter_applied = True
    return LocalRiskFilter(
        snapshot=snapshot,
        allowed_logins=allowed,
        sql_login_filter_applied=sql_filter_applied,
        excluded_logins=excluded,
    )
