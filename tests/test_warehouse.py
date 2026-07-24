from datetime import date
import json

import pytest

from app.warehouse import (
    WarehouseCoverageError,
    build_sync_plan,
    ensure_coverage,
    load_manifest,
    publish_manifest,
    table_platforms,
)
from app.warehouse_sync import partition_month_for_row
from app.warehouse_sync import _write_arrow_query, sync_warehouse
from app.config import Settings


def test_build_sync_plan_rewrites_tail_and_preserves_older_months():
    plan = build_sync_plan(
        selection_start=date(2026, 5, 1),
        selection_end=date(2026, 6, 30),
        validation_start=date(2026, 7, 1),
        validation_end=date(2026, 7, 23),
        existing_months={"2026-05", "2026-06", "2026-07"},
        tail_days=7,
        lookback_months=3,
    )

    assert plan.target_end == date(2026, 7, 23)
    assert plan.pull_months == ("2026-02", "2026-03", "2026-04", "2026-07")
    assert plan.preserved_months == ("2026-05", "2026-06")


def test_build_sync_plan_adds_missing_months_without_deleting_existing():
    plan = build_sync_plan(
        selection_start=date(2026, 8, 1),
        selection_end=date(2026, 8, 31),
        validation_start=date(2026, 9, 1),
        validation_end=date(2026, 9, 2),
        existing_months={"2026-05", "2026-06", "2026-07"},
        tail_days=7,
        lookback_months=3,
    )

    assert plan.pull_months == ("2026-08", "2026-09")
    assert plan.preserved_months == ("2026-05", "2026-06", "2026-07")


def test_ensure_coverage_requires_every_requested_platform_and_date():
    manifest = {
        "schema_version": 1,
        "generation": "g1",
        "tables": {
            "dwd_matched_trades": {
                "coverage": {
                    "mt5": [{"start": "2026-05-01", "end": "2026-07-23"}],
                }
            }
        },
    }

    with pytest.raises(WarehouseCoverageError, match="hh_mt5"):
        ensure_coverage(
            manifest,
            start=date(2026, 7, 1),
            end=date(2026, 7, 23),
            platforms=["mt5", "hh_mt5"],
            tables=["dwd_matched_trades"],
        )


def test_ensure_coverage_skips_platforms_not_present_in_a_source_table():
    manifest = {
        "schema_version": 1,
        "generation": "g1",
        "tables": {
            "ods_mt5_deals": {
                "coverage": {
                    "mt5": [{"start": "2026-05-01", "end": "2026-07-23"}],
                }
            }
        },
    }

    ensure_coverage(
        manifest,
        start=date(2026, 7, 1),
        end=date(2026, 7, 23),
        platforms=["mt4", "mt5"],
        tables=["ods_mt5_deals"],
    )
    assert table_platforms("ods_mt5_deals") == ("mt5", "hh_mt5")


def test_publish_manifest_is_atomic_and_loadable(tmp_path):
    path = tmp_path / "warehouse" / "manifest.json"
    payload = {"schema_version": 1, "generation": "g2", "tables": {}}

    publish_manifest(path, payload)

    assert json.loads(path.read_text()) == payload
    assert load_manifest(path) == payload
    assert not list(path.parent.glob("*.tmp"))


def test_partition_month_for_matched_trade_uses_entry_time_for_open_rows():
    assert partition_month_for_row(
        {"entry_time": "2026-05-17 12:00:00", "exit_time": None}
    ) == "2026-05"
    assert partition_month_for_row(
        {"entry_time": "2026-05-17 12:00:00", "exit_time": "2026-07-03 12:00:00"}
    ) == "2026-07"


def test_sync_warehouse_dry_run_never_requires_remote_client(tmp_path):
    settings = Settings(
        clickhouse_host="host",
        clickhouse_port=8123,
        clickhouse_database="risk",
        clickhouse_user="user",
        clickhouse_password="password",
        clickhouse_secure=False,
        warehouse_path=tmp_path,
    )

    result = sync_warehouse(
        selection_start=date(2026, 5, 1),
        selection_end=date(2026, 6, 30),
        validation_start=date(2026, 7, 1),
        validation_end=date(2026, 7, 23),
        platforms=["mt5"],
        settings=settings,
        dry_run=True,
    )

    assert result["status"] == "dry_run"
    assert result["pull_months"] == ["2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07"]


def test_write_arrow_query_publishes_parquet_atomically(tmp_path):
    import pyarrow as pa

    class Stream:
        def __enter__(self):
            return iter([pa.RecordBatch.from_arrays([pa.array([1, 2])], ["login"])])

        def __exit__(self, exc_type, exc, tb):
            return False

    class Client:
        def query_arrow_stream(self, query, parameters):
            return Stream()

    target = tmp_path / "month=2026-07" / "part.parquet"
    rows = _write_arrow_query(client=Client(), query="SELECT 1", params={}, target=target)

    assert rows == 2
    assert target.exists()
    assert not list(target.parent.glob("*.tmp"))
