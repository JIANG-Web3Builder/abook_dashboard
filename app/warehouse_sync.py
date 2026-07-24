from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
import os
import re
from uuid import uuid4

from .config import Settings, get_settings
from .queries import ALLOWED_PLATFORMS
from .warehouse import (
    SyncPlan,
    build_sync_plan,
    load_manifest,
    new_generation,
    publish_manifest,
    warehouse_lock,
)


PARTITIONED_TABLES = {
    "ods_mt5_deals",
    "dwd_matched_trades",
    "ods_mt4_daily_balance",
    "ods_mt5_daily_balance",
    "dws_account_martingale_window",
    "dws_account_daily_window",
}
TABLES = (
    "ods_mt4_users",
    "ods_mt5_users",
    "ods_mt5_deals",
    "dwd_matched_trades",
    "ods_mt4_daily_balance",
    "ods_mt5_daily_balance",
    "dws_account_martingale_window",
    "dws_account_daily_window",
)


def _exclusive_end(value: date) -> str:
    return (value + timedelta(days=1)).isoformat()


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).replace("Z", "+00:00").replace("/", "-")
    try:
        return datetime.fromisoformat(text.replace(" ", "T")).date()
    except ValueError:
        return date.fromisoformat(text[:10])


def partition_month_for_row(row: dict[str, Any]) -> str:
    value = row.get("exit_time") or row.get("entry_time") or row.get("time")
    if value is None:
        value = row.get("datetime") or row.get("window_end") or row.get("trade_date")
    if value is None:
        raise ValueError("row has no usable partition time")
    parsed = _parse_date(value)
    return f"{parsed.year:04d}-{parsed.month:02d}"


def _months_from_root(root: Path) -> set[str]:
    result: set[str] = set()
    for table in PARTITIONED_TABLES:
        for path in (root / table).glob("month=*"):
            match = re.fullmatch(r"month=(\d{4}-\d{2})", path.name)
            if match:
                result.add(match.group(1))
    return result


def _platforms_for_table(table: str, platforms: list[str]) -> list[str]:
    selected = sorted(set(platforms))
    if table in {"ods_mt4_users", "ods_mt4_daily_balance"}:
        return ["mt4"] if "mt4" in selected else []
    if table in {"ods_mt5_users", "ods_mt5_deals", "ods_mt5_daily_balance"}:
        return [platform for platform in selected if platform in {"mt5", "hh_mt5"}]
    return selected


def _table_query(table: str, start: date, end: date, platforms: list[str], client: Any) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {
        "platforms": _platforms_for_table(table, platforms),
        "start": f"{start.isoformat()} 00:00:00",
        "end_exclusive": f"{_exclusive_end(end)} 00:00:00",
    }
    if table == "ods_mt5_users":
        return """
        SELECT toString(platform) AS platform, toUInt64(login) AS login,
               `group`, toUInt8(is_deleted) AS is_deleted
        FROM risk.ods_mt5_users FINAL
        """, params
    if table == "ods_mt4_users":
        return """
        SELECT 'mt4' AS platform, toUInt64(login) AS login,
               `group`, toUInt8(is_deleted) AS is_deleted
        FROM risk.ods_mt4_users FINAL
        """, params
    if table == "ods_mt5_deals":
        return """
        SELECT toString(platform) AS platform, toUInt64(login) AS login,
               toUInt64(deal) AS deal, toDateTime64(time, 3) AS time, toInt32(action) AS action,
               toInt32(entry) AS entry, symbol, price, profit, storage,
               commission, fee, volume, volume_ext, toUInt8(is_deleted) AS is_deleted
        FROM risk.ods_mt5_deals FINAL
        WHERE platform IN {platforms:Array(String)}
          AND time >= {start:DateTime}
          AND time < {end_exclusive:DateTime}
          AND action IN (0, 1, 2, 3)
        """, params
    if table == "dwd_matched_trades":
        return """
        SELECT toString(platform) AS platform, toUInt64(login) AS login, symbol,
               direction, toDateTime64(entry_time, 3) AS entry_time,
               toDateTime64(exit_time, 3) AS exit_time, entry_price, exit_price, volume,
               profit, holding_seconds, turnover, toUInt64(entry_deal_id) AS entry_deal_id,
               toUInt64(exit_deal_id) AS exit_deal_id
        FROM risk.dwd_matched_trades FINAL
        WHERE platform IN {platforms:Array(String)}
          AND ((exit_time >= {start:DateTime} AND exit_time < {end_exclusive:DateTime})
            OR (isNull(exit_time) AND entry_time >= {start:DateTime}
                AND entry_time < {end_exclusive:DateTime}))
        """, params
    if table == "ods_mt4_daily_balance":
        params.pop("platforms")
        return """
        SELECT 'mt4' AS platform, toUInt64(login) AS login,
               toDateTime64(datetime, 3) AS datetime, balance
        FROM risk.ods_mt4_daily_balance
        WHERE datetime >= {start:DateTime} AND datetime < {end_exclusive:DateTime}
        """, params
    if table == "ods_mt5_daily_balance":
        return """
        SELECT toString(platform) AS platform, toUInt64(login) AS login,
               toDateTime64(datetime, 3) AS datetime, balance, toUInt8(is_deleted) AS is_deleted
        FROM risk.ods_mt5_daily_balance FINAL
        WHERE platform IN {platforms:Array(String)}
          AND datetime >= {start:DateTime} AND datetime < {end_exclusive:DateTime}
        """, params
    if table == "dws_account_martingale_window":
        return """
        SELECT toString(platform) AS platform, toUInt64(login) AS login,
               window_type, toDateTime64(window_start, 3) AS window_start,
               toDateTime64(window_end, 3) AS window_end, avg_volume_escalation,
               averaging_down_ratio, pyramiding_ratio, sequence_trade_ratio,
               sequence_win_rate, sequence_total_profit, averaging_down_profit,
               avg_gap_time, max_sequence_length, averaging_down_count,
               total_sequences, avg_sequence_length
        FROM risk.dws_account_martingale_window FINAL
        WHERE platform IN {platforms:Array(String)}
          AND window_end >= {start:DateTime} AND window_end < {end_exclusive:DateTime}
        """, params
    if table == "dws_account_daily_window":
        schema = client.query("DESCRIBE TABLE risk.dws_account_daily_window")
        columns = {str(row[0]) for row in schema.result_rows}
        def choose(candidates: tuple[str, ...], label: str) -> str:
            for candidate in candidates:
                if candidate in columns:
                    return candidate
            raise RuntimeError(f"daily window table missing {label}")
        platform = choose(("platform", "source_platform"), "platform")
        login = choose(("login", "account", "login_id"), "login")
        trade_date = choose(("trade_date", "window_end", "window_start"), "trade date")
        profit = choose(("daily_profit", "profit", "client_net_pnl", "net_profit", "pnl"), "daily profit")
        trades = choose(("daily_trades", "trade_count", "trades"), "daily trade count")
        window_type = "window_type" if "window_type" in columns else None
        window_start = "window_start" if "window_start" in columns else trade_date
        window_end = "window_end" if "window_end" in columns else trade_date
        params["start"] = start.isoformat()
        params["end_exclusive"] = _exclusive_end(end)
        platform_filter = f'AND "{platform}" IN {{platforms:Array(String)}}'
        custom_filter = f'AND "{window_type}" = \'CUSTOM\'' if window_type else ""
        return f"""
        SELECT "{platform}" AS platform, toUInt64("{login}") AS login,
               {f'"{window_type}"' if window_type else "'CUSTOM'"} AS window_type,
               toDate("{window_start}") AS window_start,
               toDate("{window_end}") AS window_end,
               toDate("{trade_date}") AS trade_date,
               toFloat64("{profit}") AS daily_profit,
               toUInt64("{trades}") AS daily_trades
        FROM risk.dws_account_daily_window FINAL
        WHERE toDate("{trade_date}") >= {{start:Date}}
          AND toDate("{trade_date}") < {{end_exclusive:Date}}
          {platform_filter}
          {custom_filter}
        """, params
    raise ValueError(f"unsupported Warehouse table: {table}")


def _write_arrow_query(
    *,
    client: Any,
    query: str,
    params: dict[str, Any],
    target: Path,
) -> int:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("pyarrow is required to build the local Warehouse") from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    writer = None
    row_count = 0
    schema = None
    try:
        with client.query_arrow_stream(query, parameters=params) as stream:
            for batch in stream:
                schema = batch.schema
                table = pa.Table.from_batches([batch], schema=schema)
                if writer is None:
                    writer = pq.ParquetWriter(temporary, schema)
                writer.write_table(table)
                row_count += table.num_rows
        if writer is None:
            table = client.query_arrow(query, parameters=params)
            schema = table.schema
            pq.write_table(table, temporary)
            row_count = table.num_rows
        else:
            writer.close()
            writer = None
        os.replace(temporary, target)
        return row_count
    finally:
        if writer is not None:
            writer.close()
        temporary.unlink(missing_ok=True)


def _merge_interval(existing: list[dict[str, str]], start: date, end: date) -> list[dict[str, str]]:
    intervals = [*existing, {"start": start.isoformat(), "end": end.isoformat()}]
    intervals.sort(key=lambda item: item["start"])
    merged: list[dict[str, str]] = []
    for item in intervals:
        current_start = date.fromisoformat(item["start"])
        current_end = date.fromisoformat(item["end"])
        if not merged or current_start > date.fromisoformat(merged[-1]["end"]) + timedelta(days=1):
            merged.append({"start": item["start"], "end": item["end"]})
        else:
            merged[-1]["end"] = max(date.fromisoformat(merged[-1]["end"]), current_end).isoformat()
    return merged


def _update_manifest_table(
    manifest: dict[str, Any],
    table: str,
    platforms: list[str],
    start: date,
    end: date,
    rows: int,
    month: str | None = None,
) -> None:
    payload = manifest.setdefault("tables", {}).setdefault(table, {"coverage": {}, "rows": 0, "months": []})
    if month is None:
        payload["rows"] = rows
    else:
        monthly_rows = payload.setdefault("monthly_rows", {})
        monthly_rows[month] = rows
        payload["rows"] = sum(int(value) for value in monthly_rows.values())
        payload["months"] = sorted(monthly_rows)
    for platform in _platforms_for_table(table, platforms):
        current = payload.setdefault("coverage", {}).get(platform, [])
        payload["coverage"][platform] = _merge_interval(current, start, end)


def sync_warehouse(
    *,
    selection_start: date,
    selection_end: date,
    validation_start: date,
    validation_end: date,
    platforms: list[str],
    settings: Settings | None = None,
    client: Any | None = None,
    dry_run: bool = False,
    reconcile_months: Iterable[str] = (),
) -> dict[str, Any]:
    settings = settings or get_settings()
    root = Path(settings.warehouse_path).resolve()
    existing_months = _months_from_root(root)
    plan = build_sync_plan(
        selection_start=selection_start,
        selection_end=selection_end,
        validation_start=validation_start,
        validation_end=validation_end,
        existing_months=existing_months,
        tail_days=settings.warehouse_tail_days,
        lookback_months=settings.warehouse_lookback_months,
    )
    pull_months = tuple(sorted(set(plan.pull_months) | set(reconcile_months)))
    result: dict[str, Any] = {
        "status": "dry_run" if dry_run else "ready",
        "target_start": plan.target_start.isoformat(),
        "target_end": plan.target_end.isoformat(),
        "pull_months": list(pull_months),
        "preserved_months": list(plan.preserved_months),
        "tables": {},
    }
    if dry_run:
        return result
    if client is None:
        raise RuntimeError("sync_warehouse requires a remote ClickHouse client")

    with warehouse_lock(root):
        manifest = load_manifest(root / "manifest.json")
        for table in ("ods_mt4_users", "ods_mt5_users"):
            table_platforms = _platforms_for_table(table, platforms)
            if not table_platforms:
                continue
            query, params = _table_query(table, plan.target_start, plan.target_end, platforms, client)
            target = root / table / "part.parquet"
            rows = _write_arrow_query(client=client, query=query, params=params, target=target)
            _update_manifest_table(manifest, table, table_platforms, plan.target_start, plan.target_end, rows)
            result["tables"][table] = {"rows": rows, "months": []}

        # A monthly Parquet file is shared by platforms. Pull all source
        # platforms when rewriting a month so a targeted snapshot refresh
        # cannot accidentally erase another platform's rows from that month.
        warehouse_platforms = sorted(ALLOWED_PLATFORMS)
        for table in PARTITIONED_TABLES:
            if not _platforms_for_table(table, warehouse_platforms):
                continue
            table_result = {"rows": 0, "months": []}
            for month in pull_months:
                month_start = date.fromisoformat(f"{month}-01")
                if month_start.month == 12:
                    next_month = date(month_start.year + 1, 1, 1)
                else:
                    next_month = date(month_start.year, month_start.month + 1, 1)
                month_end = next_month - timedelta(days=1)
                query, params = _table_query(table, month_start, month_end, warehouse_platforms, client)
                table_root = root / table / f"month={month}"
                target = table_root / "part.parquet"
                rows = _write_arrow_query(client=client, query=query, params=params, target=target)
                table_result["rows"] += rows
                table_result["months"].append(month)
                _update_manifest_table(
                    manifest, table, warehouse_platforms, month_start, month_end, rows, month=month
                )
            result["tables"][table] = table_result

        manifest["generation"] = new_generation()
        manifest["updated_at"] = datetime.now().astimezone().isoformat()
        manifest["platforms"] = warehouse_platforms
        manifest["data_start"] = plan.target_start.isoformat()
        manifest["data_end"] = plan.target_end.isoformat()
        manifest["sync"] = {
            "tail_repost_days": settings.warehouse_tail_days,
            "lookback_months": settings.warehouse_lookback_months,
            "pull_months": list(pull_months),
        }
        publish_manifest(root / "manifest.json", manifest)
        result["generation"] = manifest["generation"]
        result["updated_at"] = manifest["updated_at"]
    return result
