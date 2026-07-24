from datetime import date, datetime

import pytest

from app.config import Settings
from app.local_query import LocalQueryExecutor, render_local_query
from app.repository import LocalWarehouseRepository
from app.query_client import LocalClientAdapter
from app.warehouse import WarehouseCoverageError, publish_manifest
from app.queries import build_analysis_query


def _write_analysis_fixture(root):
    import pyarrow as pa
    import pyarrow.parquet as pq

    users_schema = pa.schema([
        ("platform", pa.string()), ("login", pa.uint64()), ("group", pa.string()), ("is_deleted", pa.uint8()),
    ])
    users = pa.table({
        "platform": pa.array(["mt5"], type=pa.string()),
        "login": pa.array([1001], type=pa.uint64()),
        "group": pa.array(["real\\\\FPlive"], type=pa.string()),
        "is_deleted": pa.array([0], type=pa.uint8()),
    }, schema=users_schema)
    empty_users = pa.table({
        "platform": pa.array([], type=pa.string()),
        "login": pa.array([], type=pa.uint64()),
        "group": pa.array([], type=pa.string()),
        "is_deleted": pa.array([], type=pa.uint8()),
    }, schema=users_schema)
    users_path = root / "ods_mt5_users" / "part.parquet"
    users_path.parent.mkdir(parents=True)
    pq.write_table(users, users_path)
    empty_users_path = root / "ods_mt4_users" / "part.parquet"
    empty_users_path.parent.mkdir(parents=True)
    pq.write_table(empty_users, empty_users_path)

    matched_schema = pa.schema([
        ("platform", pa.string()), ("login", pa.uint64()), ("symbol", pa.string()), ("direction", pa.string()),
        ("entry_time", pa.timestamp("us")), ("exit_time", pa.timestamp("us")),
        ("entry_price", pa.float64()), ("exit_price", pa.float64()), ("volume", pa.float64()),
        ("profit", pa.float64()), ("holding_seconds", pa.int64()), ("turnover", pa.float64()),
        ("entry_deal_id", pa.uint64()), ("exit_deal_id", pa.uint64()),
    ])
    matched = pa.table({
        "platform": ["mt5"], "login": pa.array([1001], type=pa.uint64()), "symbol": ["EURUSD"], "direction": ["Long"],
        "entry_time": pa.array([datetime(2026, 5, 1)], type=pa.timestamp("us")),
        "exit_time": pa.array([datetime(2026, 5, 2)], type=pa.timestamp("us")),
        "entry_price": [1.1], "exit_price": [1.2], "volume": [1.0], "profit": [100.0],
        "holding_seconds": [3600], "turnover": [100000.0], "entry_deal_id": pa.array([1], type=pa.uint64()),
        "exit_deal_id": pa.array([2], type=pa.uint64()),
    }, schema=matched_schema)
    matched_path = root / "dwd_matched_trades" / "month=2026-05" / "part.parquet"
    matched_path.parent.mkdir(parents=True)
    pq.write_table(matched, matched_path)

    deals_schema = pa.schema([
        ("platform", pa.string()), ("login", pa.uint64()), ("deal", pa.uint64()), ("time", pa.uint32()),
        ("action", pa.int32()), ("entry", pa.int32()), ("symbol", pa.string()), ("price", pa.float64()),
        ("profit", pa.float64()), ("storage", pa.float64()), ("commission", pa.float64()), ("fee", pa.float64()),
        ("volume", pa.float64()), ("volume_ext", pa.float64()), ("is_deleted", pa.uint8()),
    ])
    deals = pa.table({
        "platform": ["mt5"], "login": pa.array([1001], type=pa.uint64()), "deal": pa.array([1], type=pa.uint64()),
        # clickhouse-connect may expose ClickHouse DateTime as epoch seconds
        # in Arrow. Local SQL must remain compatible with that canonical form.
        "time": pa.array([datetime(2026, 5, 2).timestamp()], type=pa.uint32()), "action": pa.array([0], type=pa.int32()), "entry": pa.array([0], type=pa.int32()),
        "symbol": ["EURUSD"], "price": [1.2], "profit": [100.0], "storage": [0.0], "commission": [0.0],
        "fee": [0.0], "volume": [1.0], "volume_ext": [1.0], "is_deleted": pa.array([0], type=pa.uint8()),
    }, schema=deals_schema)
    deals_path = root / "ods_mt5_deals" / "month=2026-05" / "part.parquet"
    deals_path.parent.mkdir(parents=True)
    pq.write_table(deals, deals_path)


def test_render_local_query_replaces_tables_removes_final_and_renders_parameters(tmp_path):
    query = """
    SELECT *
    FROM risk.dwd_matched_trades AS mt FINAL
    WHERE mt.platform IN {platforms:Array(String)}
      AND mt.exit_time >= {start:Date}
    """

    rendered = render_local_query(
        query,
        {"platforms": ["mt5", "hh_mt5"], "start": "2026-07-01"},
        tmp_path,
    )

    assert "FINAL" not in rendered
    assert "risk.dwd_matched_trades" not in rendered
    assert "file(" in rendered and "dwd_matched_trades" in rendered
    assert "['mt5', 'hh_mt5']" in rendered
    assert "toDate('2026-07-01')" in rendered


def test_render_local_query_uses_non_partitioned_user_glob(tmp_path):
    rendered = render_local_query(
        "SELECT * FROM risk.ods_mt5_users FINAL",
        {},
        tmp_path,
    )

    assert "ods_mt5_users/*.parquet" in rendered


def test_local_query_executor_decodes_json_each_row_result(tmp_path):
    executor = LocalQueryExecutor(
        tmp_path,
        query_runner=lambda query: '{"platform":"mt5","login":7}\n',
    )

    assert executor.rows("SELECT 1", {}) == [{"platform": "mt5", "login": 7}]


def test_local_repository_rejects_requests_outside_manifest_coverage(tmp_path):
    publish_manifest(
        tmp_path / "manifest.json",
        {
            "schema_version": 1,
            "generation": "g1",
            "tables": {
                "dwd_matched_trades": {
                    "coverage": {
                        "mt5": [{"start": "2026-05-01", "end": "2026-07-23"}],
                    }
                },
                "ods_mt5_deals": {
                    "coverage": {
                        "mt5": [{"start": "2026-05-01", "end": "2026-07-23"}],
                    }
                },
            },
        },
    )
    settings = Settings(
        clickhouse_host="host",
        clickhouse_port=8123,
        clickhouse_database="risk",
        clickhouse_user="user",
        clickhouse_password="password",
        clickhouse_secure=False,
        warehouse_path=tmp_path,
    )
    repository = LocalWarehouseRepository(settings, executor=LocalQueryExecutor(tmp_path, lambda _: ""))

    with pytest.raises(WarehouseCoverageError):
        repository._validate_coverage(
            date(2026, 7, 24), date(2026, 7, 24), ["mt5"], ["dwd_matched_trades"]
        )


def test_local_client_adapter_preserves_clickhouse_result_shape(tmp_path):
    client = LocalClientAdapter(
        tmp_path,
        executor=LocalQueryExecutor(
            tmp_path,
            lambda _: '{"platform":"mt5","login":7}\n',
        ),
    )

    result = client.query("SELECT platform, login FROM risk.dwd_matched_trades", {})

    assert result.column_names == ("platform", "login")
    assert result.result_rows == [("mt5", 7)]


def test_local_executor_runs_analysis_query_against_canonical_warehouse_types(tmp_path):
    _write_analysis_fixture(tmp_path)
    query, params = build_analysis_query(
        platforms=["mt5"],
        start="2026-05-01",
        end="2026-05-31",
        lookback_months=1,
        selection_start="2026-05-01",
        selection_end="2026-05-15",
        validation_start="2026-05-16",
        validation_end="2026-05-31",
    )

    rows = LocalQueryExecutor(tmp_path).rows(query, params)

    assert rows and rows[0]["platform"] == "mt5"
