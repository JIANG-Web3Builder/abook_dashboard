from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from scripts.build_avg_profit_snapshot import build_snapshot as avg_profit_build_snapshot
from scripts.build_martingale_snapshot import build_snapshot as martingale_build_snapshot
from scripts.build_user_risk_snapshot import build_snapshot as risk_build_snapshot

from .avg_profit import snapshot_path as avg_profit_snapshot_path
from .analysis_cache import analysis_session_cache, direction_analytics_cache, newcomer_analytics_cache
from .martingale import snapshot_path as martingale_snapshot_path
from .risk import snapshot_path as risk_snapshot_path

if TYPE_CHECKING:
    from .config import Settings
    from .models import AnalysisRequest


_REFRESH_LOCK = threading.RLock()


def snapshot_paths() -> dict[str, Path]:
    return {
        "risk": risk_snapshot_path(),
        "avg_profit": avg_profit_snapshot_path(),
        "martingale": martingale_snapshot_path(),
    }


def _serialise(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2) + "\n"


def _temporary_path(path: Path, token: str, kind: str) -> Path:
    return path.with_name(f".{path.name}.{token}.{kind}")


def _restore_originals(originals: dict[Path, bytes | None], token: str) -> None:
    for path, content in originals.items():
        if content is None:
            path.unlink(missing_ok=True)
            continue
        restore_path = _temporary_path(path, token, "restore")
        restore_path.write_bytes(content)
        os.replace(restore_path, path)


def snapshot_status_for_request(
    request: "AnalysisRequest",
    *,
    settings: "Settings | None" = None,
) -> dict[str, object]:
    """Describe whether local snapshots match the request and Warehouse generation."""
    if settings is None:
        from .config import get_settings

        settings = get_settings()
    if settings.data_source != "local":
        return {
            "source": "remote",
            "status": "remote",
            "reason": None,
            "refreshed": False,
            "warehouse_generation": None,
            "snapshots": {},
        }

    from .warehouse import load_manifest

    manifest = load_manifest(Path(settings.warehouse_path) / "manifest.json")
    generation = manifest.get("generation")
    selection_start = request.selection.start.isoformat()
    selection_end = request.selection.end.isoformat()
    requested_platforms = set(request.platforms)
    snapshots: dict[str, dict[str, object]] = {}
    reasons: list[str] = []

    for name, path in snapshot_paths().items():
        info: dict[str, object] = {"status": "missing", "path": str(path)}
        if not path.exists():
            reasons.append("missing")
            snapshots[name] = info
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            reasons.append("invalid")
            snapshots[name] = info | {"status": "invalid"}
            continue
        if not isinstance(payload, dict):
            reasons.append("invalid")
            snapshots[name] = info | {"status": "invalid"}
            continue

        snapshot_generation = payload.get("warehouse_generation")
        snapshot_selection_start = payload.get("selection_start")
        snapshot_selection_end = payload.get("selection_end")
        snapshot_platforms = payload.get("platforms")
        info = {
            "status": "ready",
            "path": str(path),
            "warehouse_generation": snapshot_generation,
            "selection_start": snapshot_selection_start,
            "selection_end": snapshot_selection_end,
            "platforms": snapshot_platforms,
            "validation_start": payload.get("validation_start"),
            "validation_end": payload.get("validation_end"),
        }
        if generation and snapshot_generation != generation:
            info["status"] = "stale_generation"
            reasons.append("warehouse_generation")
        elif snapshot_selection_start != selection_start or snapshot_selection_end != selection_end:
            info["status"] = "stale_window"
            reasons.append("selection_window")
        elif isinstance(snapshot_platforms, list) and not requested_platforms.issubset(set(snapshot_platforms)):
            info["status"] = "stale_platforms"
            reasons.append("platforms")
        snapshots[name] = info

    reason = next(
        (candidate for candidate in ("missing", "invalid", "warehouse_generation", "selection_window", "platforms") if candidate in reasons),
        None,
    )
    return {
        "source": "local",
        "status": "ready" if reason is None else "stale",
        "reason": reason,
        "refreshed": False,
        "warehouse_generation": generation,
        "selection_start": selection_start,
        "selection_end": selection_end,
        "validation_start": request.validation.start.isoformat(),
        "validation_end": request.validation.end.isoformat(),
        "snapshots": snapshots,
    }


def ensure_local_snapshots_for_request(
    request: "AnalysisRequest",
    *,
    settings: "Settings | None" = None,
) -> dict[str, object]:
    """Return ready snapshot metadata, rebuilding local snapshots when stale."""
    if settings is None:
        from .config import get_settings

        settings = get_settings()
    status = snapshot_status_for_request(request, settings=settings)
    if settings.data_source != "local" or status["status"] == "ready":
        return status

    with _REFRESH_LOCK:
        # A different request may have rebuilt the files while this request
        # was waiting for the in-process lock.
        status = snapshot_status_for_request(request, settings=settings)
        if status["status"] == "ready":
            return status
        result = refresh_snapshots(
            str(status["selection_start"]),
            str(status["selection_end"]),
            list(request.platforms),
            validation_start=str(status["validation_start"]),
            validation_end=str(status["validation_end"]),
            warehouse_generation=(
                str(status["warehouse_generation"])
                if status["warehouse_generation"]
                else None
            ),
        )
        result["refreshed"] = result.get("status") == "ready"
        result["reason"] = status.get("reason")
        if result.get("status") == "ready":
            result["source"] = "local"
        return result


def refresh_snapshots(
    selection_start: str,
    selection_end: str,
    platforms: list[str],
    *,
    validation_start: str | None = None,
    validation_end: str | None = None,
    warehouse_generation: str | None = None,
) -> dict[str, object]:
    """Build and atomically publish all local analysis snapshots."""
    builders: dict[str, Callable[[str, str, list[str]], dict[str, Any]]] = {
        "risk": risk_build_snapshot,
        "avg_profit": avg_profit_build_snapshot,
        "martingale": martingale_build_snapshot,
    }
    validation_start = validation_start or selection_start
    validation_end = validation_end or selection_end
    with _REFRESH_LOCK:
        paths = snapshot_paths()
        if len(set(paths.values())) != len(paths):
            return {
                "status": "error",
                "error": "snapshot paths must be unique",
                "snapshots": {},
            }

        token = uuid.uuid4().hex
        staged: dict[str, Path] = {}
        originals: dict[Path, bytes | None] = {}
        try:
            payloads = {}
            for name, builder in builders.items():
                payload = dict(builder(selection_start, selection_end, list(platforms)))
                payload["selection_start"] = selection_start
                payload["selection_end"] = selection_end
                payload["validation_start"] = validation_start
                payload["validation_end"] = validation_end
                if warehouse_generation:
                    payload["warehouse_generation"] = warehouse_generation
                payloads[name] = payload
            for name, path in paths.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                originals[path] = path.read_bytes() if path.exists() else None
                staged_path = _temporary_path(path, token, "tmp")
                staged_path.write_text(_serialise(payloads[name]), encoding="utf-8")
                staged[name] = staged_path

            for name, path in paths.items():
                os.replace(staged[name], path)

            analysis_session_cache.clear()
            direction_analytics_cache.clear()
            newcomer_analytics_cache.clear()

            return {
                "status": "ready",
                "selection_start": selection_start,
                "selection_end": selection_end,
                "validation_start": validation_start,
                "validation_end": validation_end,
                "warehouse_generation": warehouse_generation,
                "snapshots": {
                    name: {
                        "status": "ready",
                        "path": str(paths[name]),
                        "records": len(payloads[name].get("records", [])),
                        "selection_start": selection_start,
                        "selection_end": selection_end,
                    }
                    for name in paths
                },
            }
        except Exception as exc:
            if originals:
                try:
                    _restore_originals(originals, token)
                except Exception as restore_exc:
                    return {
                        "status": "error",
                        "error": f"{exc}; snapshot rollback failed: {restore_exc}",
                        "snapshots": {},
                    }
            return {"status": "error", "error": str(exc), "snapshots": {}}
        finally:
            for temporary in staged.values():
                temporary.unlink(missing_ok=True)
            for path in paths.values():
                _temporary_path(path, token, "restore").unlink(missing_ok=True)
