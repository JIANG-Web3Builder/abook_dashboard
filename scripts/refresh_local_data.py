from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
import time
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import clickhouse_connect

from app.config import get_settings, load_env_file, Settings
from app.queries import ALLOWED_PLATFORMS
from app.snapshot_refresh import refresh_snapshots
from app.warehouse import load_manifest
from app.warehouse_sync import sync_warehouse


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync the remote risk Warehouse and rebuild local dashboard snapshots"
    )
    parser.add_argument("--selection-start", required=True, type=date.fromisoformat)
    parser.add_argument("--selection-end", required=True, type=date.fromisoformat)
    parser.add_argument("--validation-start", required=True, type=date.fromisoformat)
    parser.add_argument("--validation-end", required=True, type=date.fromisoformat)
    parser.add_argument("--platform", action="append", dest="platforms")
    parser.add_argument("--sync-only", action="store_true")
    parser.add_argument("--snapshots-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reconcile-month", action="append", default=[])
    args = parser.parse_args(argv)
    if args.sync_only and args.snapshots_only:
        parser.error("--sync-only and --snapshots-only cannot be used together")
    if args.selection_end >= args.validation_start:
        parser.error("selection must end before validation starts")
    args.platforms = sorted(set(args.platforms or ALLOWED_PLATFORMS))
    invalid = set(args.platforms) - ALLOWED_PLATFORMS
    if invalid:
        parser.error(f"unsupported platform: {sorted(invalid)}")
    return args


def _remote_client(settings: Settings) -> Any:
    if not settings.configured:
        raise RuntimeError("CLICKHOUSE_PASSWORD is not configured")
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_database,
        secure=settings.clickhouse_secure,
        compress=settings.clickhouse_compress,
        apply_server_timezone=settings.clickhouse_use_server_time_zone_for_dates,
    )


def run_refresh(args: argparse.Namespace, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    if args.snapshots_only:
        manifest = load_manifest(settings.warehouse_path / "manifest.json")
        sync_result: dict[str, Any] = {
            "status": "skipped",
            "generation": manifest.get("generation"),
            "pull_months": [],
            "tables": {},
        }
    else:
        client = None if args.dry_run else _remote_client(settings)
        sync_result = sync_warehouse(
            selection_start=args.selection_start,
            selection_end=args.selection_end,
            validation_start=args.validation_start,
            validation_end=args.validation_end,
            platforms=args.platforms,
            settings=settings,
            client=client,
            dry_run=args.dry_run,
            reconcile_months=args.reconcile_month,
        )
    if args.sync_only or args.dry_run:
        return {"sync": sync_result, "snapshots": {"status": "skipped"}}
    snapshot_result = refresh_snapshots(
        args.selection_start.isoformat(),
        args.selection_end.isoformat(),
        args.platforms,
        validation_start=args.validation_start.isoformat(),
        validation_end=args.validation_end.isoformat(),
        warehouse_generation=sync_result.get("generation"),
    )
    return {"sync": sync_result, "snapshots": snapshot_result}


def main(argv: Sequence[str] | None = None) -> int:
    load_env_file()
    started = time.monotonic()
    try:
        result = run_refresh(parse_args(argv))
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
