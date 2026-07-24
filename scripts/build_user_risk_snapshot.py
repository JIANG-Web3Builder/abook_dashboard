from __future__ import annotations

import argparse
from bisect import bisect_right
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import clickhouse_connect

from app.config import get_settings, load_env_file
from app.exposure import build_unmatched_open_events, reconstruct_peak_exposure
from app.query_client import LocalClientAdapter
from app.queries import ALLOWED_PLATFORMS, USER_SOURCE_SQL
from app.risk import RISK_CALCULATION_VERSION, snapshot_path


def _end_exclusive(value: str) -> str:
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip().replace("/", "-").replace("Z", "+00:00")
    for candidate in (text, text.replace(" ", "T")):
        try:
            return datetime.fromisoformat(candidate).replace(tzinfo=None)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise ValueError(f"unsupported balance datetime: {value}")


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) * 0.95 + 0.999999999) - 1)))
    return float(ordered[index])


def calculate_daily_risk(
    trade_rows: list[dict[str, Any]],
    balance_rows: list[dict[str, Any]],
) -> dict[tuple[str, int], dict[str, Any]]:
    """Match each turnover day to the latest known daily balance as of that day."""
    daily_balances: dict[tuple[str, int, date], tuple[datetime, float]] = {}
    for row in balance_rows:
        try:
            key = (str(row["platform"]), int(row["login"]))
            timestamp = _parse_datetime(row["datetime"])
            balance = float(row.get("balance") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        day_key = (*key, timestamp.date())
        previous = daily_balances.get(day_key)
        if previous is None or timestamp > previous[0]:
            daily_balances[day_key] = (timestamp, balance)

    by_account_balances: dict[tuple[str, int], list[tuple[date, float]]] = {}
    for (platform, login, balance_date), (_, balance) in daily_balances.items():
        by_account_balances.setdefault((platform, login), []).append((balance_date, balance))
    for values in by_account_balances.values():
        values.sort(key=lambda item: item[0])

    turnover_by_day: dict[tuple[str, int, date], float] = {}
    for row in trade_rows:
        try:
            key = (str(row["platform"]), int(row["login"]))
            trade_day = _parse_datetime(row["trade_date"]).date()
            turnover = float(row.get("daily_turnover", row.get("daily_position_value")) or 0)
        except (KeyError, TypeError, ValueError):
            continue
        turnover_by_day[(key[0], key[1], trade_day)] = turnover_by_day.get((key[0], key[1], trade_day), 0.0) + turnover

    result: dict[tuple[str, int], dict[str, Any]] = {}
    for (platform, login, trade_day), daily_turnover in turnover_by_day.items():
        key = (platform, login)
        result.setdefault(key, {
            "total_position_value": 0.0,
            "active_days": 0,
            "peak_daily_position_value": 0.0,
            "leverage_values": [],
            "balance_latest": None,
            "balance_status": "missing_daily_balance",
        })
        record = result[key]
        record["total_position_value"] += daily_turnover
        record["total_turnover"] = record.get("total_turnover", 0.0) + daily_turnover
        record["active_days"] += 1
        record["peak_daily_position_value"] = max(record["peak_daily_position_value"], daily_turnover)
        record["peak_daily_turnover"] = max(record.get("peak_daily_turnover", 0.0), daily_turnover)
        balances = by_account_balances.get(key, [])
        balance_dates = [item[0] for item in balances]
        position = bisect_right(balance_dates, trade_day) - 1
        if position < 0:
            continue
        balance = balances[position][1]
        record["balance_latest"] = balance
        if balance <= 0:
            continue
        record["balance_status"] = "positive"
        record["leverage_values"].append(daily_turnover / balance)

    for record in result.values():
        values = record["leverage_values"]
        record["average_open_degree"] = sum(values) / len(values) if values else None
        record["peak_leverage_ratio"] = max(values) if values else None
        record["leverage_p95_ratio"] = _p95(values)
        record["turnover_leverage_p95_ratio"] = record["leverage_p95_ratio"]
        record["turnover_peak_leverage_ratio"] = record["peak_leverage_ratio"]
        if record["balance_status"] == "missing_daily_balance" and record["balance_latest"] is not None:
            record["balance_status"] = "unknown_nonpositive_balance"
    return result


def apply_concurrent_exposure(
    daily_risk: dict[tuple[str, int], dict[str, Any]],
    exposure: dict[tuple[str, int], dict[str, Any]],
) -> dict[tuple[str, int], dict[str, Any]]:
    """Add concurrent open exposure while retaining turnover-based leverage.

    The screening leverage is the conservative maximum of daily turnover
    leverage and reconstructed concurrent exposure leverage. Both components
    remain in the snapshot so the UI can explain why a user was filtered.
    """
    for key, item in exposure.items():
        record = daily_risk.setdefault(key, {
            "total_position_value": 0.0,
            "total_turnover": 0.0,
            "active_days": 0,
            "peak_daily_position_value": 0.0,
            "leverage_values": [],
            "balance_latest": None,
            "balance_status": "missing_daily_balance",
            "leverage_p95_ratio": None,
            "peak_leverage_ratio": None,
        })
        record["peak_concurrent_position_value"] = item.get("peak_position_value")
        record["peak_concurrent_leverage_ratio"] = item.get("peak_leverage_ratio")
        record["concurrent_leverage_p95_ratio"] = item.get("leverage_p95_ratio")
        record["exposure_status"] = item.get("exposure_status")
        current_p95 = record.get("leverage_p95_ratio")
        concurrent_p95 = item.get("leverage_p95_ratio")
        if concurrent_p95 is not None:
            record["leverage_p95_ratio"] = max(float(current_p95 or 0), float(concurrent_p95))
        current_peak = record.get("peak_leverage_ratio")
        concurrent_peak = item.get("peak_leverage_ratio")
        if concurrent_peak is not None:
            record["peak_leverage_ratio"] = max(float(current_peak or 0), float(concurrent_peak))
    return daily_risk


def _describe_columns(client: Any, table: str) -> set[str]:
    result = client.query(f"DESCRIBE TABLE {table}")
    return {str(row[0]) for row in result.result_rows}


def _find_column(columns: set[str], candidates: tuple[str, ...], table: str) -> str:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    raise RuntimeError(f"{table} is missing one of columns: {', '.join(candidates)}")


def _chunks(values: list[int], size: int = 2000) -> list[list[int]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def _fetch_balance_rows(
    client: Any,
    table: str,
    table_platform: str,
    selected: list[str],
    logins: list[int],
    selection_end: str,
) -> list[dict[str, Any]]:
    mt5_selected = table_platform == "mt5" and bool({"mt5", "hh_mt5"} & set(selected))
    if not logins or (table_platform != "mt5" and table_platform not in selected) or (table_platform == "mt5" and not mt5_selected):
        return []
    columns = _describe_columns(client, table)
    login_column = _find_column(columns, ("login",), table)
    datetime_column = _find_column(columns, ("datetime", "date_time", "time"), table)
    balance_column = _find_column(columns, ("balance", "account_balance", "balance_amount", "equity"), table)
    has_platform = "platform" in columns
    platform_expr = "toString(`platform`)" if has_platform else f"'{table_platform}'"
    platform_filter = "AND has({platforms:Array(String)}, toString(`platform`))" if has_platform else ""
    deleted_filter = "AND b.is_deleted = 0" if "is_deleted" in columns else ""
    balance_login = f"toUInt64(b.`{login_column}`)"
    balance_platform = "toString(b.`platform`)" if has_platform else f"'{table_platform}'"
    query = f"""
    WITH selected_users AS (
        SELECT platform, toUInt64(login) AS login
        FROM {USER_SOURCE_SQL} AS user_source
        WHERE is_deleted = 0
          AND has({{platforms:Array(String)}}, platform)
          AND positionCaseInsensitive(`group`, 'test') = 0
          AND positionCaseInsensitive(`group`, 'demo') = 0
        GROUP BY platform, login
    )
    SELECT
        {balance_platform} AS platform,
        {balance_login} AS login,
        toString(b.`{datetime_column}`) AS datetime,
        toFloat64(b.`{balance_column}`) AS balance
    -- The MT4/MT5 daily balance tables are plain MergeTree tables and do not
    -- support FINAL. The balance sequence is reconstructed by login + parsed
    -- datetime in calculate_daily_risk(), so FINAL is neither valid nor needed.
    FROM {table} AS b
    INNER JOIN selected_users AS u
      ON u.platform = {balance_platform} AND u.login = {balance_login}
    WHERE {balance_platform} IN {{platforms:Array(String)}}
      {deleted_filter}
      AND parseDateTimeBestEffortOrNull(toString(b.`{datetime_column}`)) < {{selection_end_exclusive:DateTime}}
    """
    result = client.query(query, parameters={
        "platforms": selected,
        "selection_end_exclusive": f"{_end_exclusive(selection_end)} 00:00:00",
    })
    return [dict(zip(result.column_names, values)) for values in result.result_rows]


def _fetch_matched_exposure_rows(
    client: Any,
    selected: list[str],
    selection_start: str,
    selection_end: str,
) -> list[dict[str, Any]]:
    query = """
    SELECT
        mt.platform, mt.login, mt.symbol, mt.direction,
        mt.entry_time, mt.exit_time, mt.entry_price, mt.volume,
        mt.entry_deal_id, mt.exit_deal_id
    FROM risk.dwd_matched_trades AS mt FINAL
    WHERE mt.platform IN {platforms:Array(String)}
      AND mt.entry_time < {selection_end_exclusive:Date}
      AND (mt.exit_time >= {selection_start:Date} OR isNull(mt.exit_time))
    """
    result = client.query(query, parameters={
        "platforms": selected,
        "selection_start": selection_start,
        "selection_end_exclusive": _end_exclusive(selection_end),
    })
    return [dict(zip(result.column_names, values)) for values in result.result_rows]


def _fetch_raw_open_deal_rows(
    client: Any,
    table: str,
    table_platform: str,
    selected: list[str],
    logins: list[int],
    selection_end: str,
) -> list[dict[str, Any]]:
    mt5_selected = table_platform == "mt5" and bool({"mt5", "hh_mt5"} & set(selected))
    if not logins or (table_platform != "mt5" and table_platform not in selected) or (table_platform == "mt5" and not mt5_selected):
        return []
    try:
        columns = _describe_columns(client, table)
        login_column = _find_column(columns, ("login",), table)
        time_column = _find_column(columns, ("time", "datetime", "date_time"), table)
        price_column = _find_column(columns, ("price", "deal_price", "open_price"), table)
        volume_column = _find_column(columns, ("volume", "deal_volume", "lots"), table)
        entry_column = _find_column(columns, ("entry", "entry_type"), table)
        deal_column = _find_column(columns, ("deal_id", "deal", "ticket", "id"), table)
    except Exception:
        return []
    has_platform = "platform" in columns
    platform_expr = "toString(`platform`)" if has_platform else f"'{table_platform}'"
    platform_filter = "AND has({platforms:Array(String)}, toString(`platform`))" if has_platform else ""
    deleted_filter = "AND is_deleted = 0" if "is_deleted" in columns else ""
    query = f"""
    SELECT
        {platform_expr} AS platform,
        toUInt64(`{login_column}`) AS login,
        toUInt64(`{deal_column}`) AS deal_id,
        toInt32(`{entry_column}`) AS entry,
        toString(`{time_column}`) AS time,
        toFloat64(`{price_column}`) AS price,
        toFloat64(`{volume_column}`) AS volume
    FROM {table} FINAL
    WHERE has({{logins:Array(UInt64)}}, toUInt64(`{login_column}`))
      {platform_filter}
      {deleted_filter}
      AND toInt32(`{entry_column}`) = 0
      AND parseDateTimeBestEffortOrNull(toString(`{time_column}`)) < {{selection_end_exclusive:DateTime}}
      AND toFloat64(`{price_column}`) > 0
      AND toFloat64(`{volume_column}`) > 0
    """
    rows: list[dict[str, Any]] = []
    for login_batch in _chunks(sorted(set(logins))):
        params: dict[str, Any] = {
            "logins": login_batch,
            "selection_end_exclusive": f"{_end_exclusive(selection_end)} 00:00:00",
        }
        if has_platform:
            params["platforms"] = selected
        try:
            result = client.query(query, parameters=params)
        except Exception:
            # The raw deal source is optional. Matched rows still provide a
            # conservative exposure estimate when this table is unavailable.
            return rows
        rows.extend(dict(zip(result.column_names, values)) for values in result.result_rows)
    return rows


def build_snapshot(selection_start: str, selection_end: str, platforms: list[str]) -> dict:
    settings = get_settings()
    if settings.data_source == "remote" and not settings.configured:
        raise RuntimeError("CLICKHOUSE_PASSWORD is not configured")
    selected = sorted(set(platforms) & ALLOWED_PLATFORMS)
    if settings.data_source == "local":
        client = LocalClientAdapter(settings.warehouse_path)
    else:
        client = clickhouse_connect.get_client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            username=settings.clickhouse_user,
            password=settings.clickhouse_password,
            database=settings.clickhouse_database,
            secure=settings.clickhouse_secure,
        )
    users_query = f"""
    SELECT platform, login, any(`group`) AS account_group
    FROM {USER_SOURCE_SQL} AS user_source
    WHERE is_deleted = 0
      AND has({{platforms:Array(String)}}, platform)
      AND positionCaseInsensitive(`group`, 'test') = 0
      AND positionCaseInsensitive(`group`, 'demo') = 0
    GROUP BY platform, login
    ORDER BY platform, login
    """
    users_result = client.query(users_query, parameters={"platforms": selected})
    users = [dict(zip(users_result.column_names, values)) for values in users_result.result_rows]
    logins = sorted({int(row["login"]) for row in users})

    daily_query = f"""
    WITH users AS (
        SELECT platform, login
        FROM {USER_SOURCE_SQL} AS user_source
        WHERE is_deleted = 0
          AND has({{platforms:Array(String)}}, platform)
          AND positionCaseInsensitive(`group`, 'test') = 0
          AND positionCaseInsensitive(`group`, 'demo') = 0
        GROUP BY platform, login
    )
    SELECT
        mt.platform,
        mt.login,
        toDate(mt.exit_time) AS trade_date,
        sum(abs(toFloat64(mt.turnover))) AS daily_turnover
    FROM risk.dwd_matched_trades AS mt FINAL
    INNER JOIN users AS u ON mt.platform = u.platform AND mt.login = u.login
    WHERE mt.exit_time >= {{selection_start:Date}}
      AND mt.exit_time < {{selection_end_exclusive:Date}}
    GROUP BY mt.platform, mt.login, trade_date
    """
    daily_result = client.query(daily_query, parameters={
        "platforms": selected,
        "selection_start": selection_start,
        "selection_end_exclusive": _end_exclusive(selection_end),
    })
    trade_rows = [dict(zip(daily_result.column_names, values)) for values in daily_result.result_rows]
    balance_rows = []
    for table, table_platform in (("risk.ods_mt4_daily_balance", "mt4"), ("risk.ods_mt5_daily_balance", "mt5")):
        balance_rows.extend(_fetch_balance_rows(client, table, table_platform, selected, logins, selection_end))
    daily_risk = calculate_daily_risk(trade_rows, balance_rows)
    if os.getenv("ABOOK_INCLUDE_CONCURRENT_EXPOSURE", "1").lower() not in {"0", "false", "no"}:
        matched_exposure_rows = _fetch_matched_exposure_rows(client, selected, selection_start, selection_end)
        raw_open_deals = []
        # MT4 has no ods_mt4_deals table in risk; matched_trades is its
        # canonical exposure source. Keep raw-deal enrichment for MT5 only.
        for table, table_platform in (("risk.ods_mt5_deals", "mt5"),):
            raw_open_deals.extend(_fetch_raw_open_deal_rows(client, table, table_platform, selected, logins, selection_end))
        open_events = build_unmatched_open_events(raw_open_deals, matched_exposure_rows)
        concurrent_exposure = reconstruct_peak_exposure(matched_exposure_rows + open_events, balance_rows)
        apply_concurrent_exposure(daily_risk, concurrent_exposure)

    holding_query = """
    SELECT platform, login, quantileTDigest(0.5)(toFloat64(holding_seconds)) AS median_holding_seconds
    FROM risk.dwd_matched_trades AS mt FINAL
    WHERE mt.platform IN {platforms:Array(String)}
      AND mt.exit_time >= {selection_start:Date}
      AND mt.exit_time < {selection_end_exclusive:Date}
    GROUP BY platform, login
    """
    holding_result = client.query(holding_query, parameters={
        "platforms": selected,
        "selection_start": selection_start,
        "selection_end_exclusive": _end_exclusive(selection_end),
    })
    holding = {
        (str(row["platform"]), int(row["login"])): float(row.get("median_holding_seconds") or 0)
        for row in (dict(zip(holding_result.column_names, values)) for values in holding_result.result_rows)
    }

    records = []
    for user in users:
        key = (str(user["platform"]), int(user["login"]))
        risk = daily_risk.get(key, {
            "total_position_value": 0.0,
            "active_days": 0,
            "peak_daily_position_value": 0.0,
            "peak_concurrent_position_value": None,
            "average_open_degree": None,
            "peak_leverage_ratio": None,
            "leverage_p95_ratio": None,
            "turnover_leverage_p95_ratio": None,
            "turnover_peak_leverage_ratio": None,
            "peak_concurrent_leverage_ratio": None,
            "concurrent_leverage_p95_ratio": None,
            "balance_latest": None,
            "balance_status": "missing_daily_balance",
            "exposure_status": "no_exposure_events",
        })
        records.append({
            "platform": key[0],
            "login": key[1],
            "account_group": str(user.get("account_group") or ""),
            "balance_prev_month": risk.get("balance_latest"),
            "balance_latest": risk.get("balance_latest"),
            "balance_reference": "latest_daily_balance_asof_trade_day",
            "total_position_value": risk.get("total_position_value", 0.0),
            "total_turnover": risk.get("total_turnover", risk.get("total_position_value", 0.0)),
            "active_days": risk.get("active_days", 0),
            "peak_daily_position_value": risk.get("peak_daily_position_value", 0.0),
            "peak_daily_turnover": risk.get("peak_daily_turnover", risk.get("peak_daily_position_value", 0.0)),
            "peak_concurrent_position_value": risk.get("peak_concurrent_position_value"),
            "average_open_degree": risk.get("average_open_degree"),
            "peak_leverage_ratio": risk.get("peak_leverage_ratio"),
            "leverage_p95_ratio": risk.get("leverage_p95_ratio"),
            "turnover_peak_leverage_ratio": risk.get("turnover_peak_leverage_ratio"),
            "turnover_leverage_p95_ratio": risk.get("turnover_leverage_p95_ratio"),
            "peak_concurrent_leverage_ratio": risk.get("peak_concurrent_leverage_ratio"),
            "concurrent_leverage_p95_ratio": risk.get("concurrent_leverage_p95_ratio"),
            "median_holding_seconds": holding.get(key, 0.0),
            "balance_status": risk.get("balance_status", "missing_daily_balance"),
            "exposure_status": risk.get("exposure_status", "no_exposure_events"),
        })
    return {
        "selection_start": selection_start,
        "selection_end": selection_end,
        "calculation_version": RISK_CALCULATION_VERSION,
        "platforms": selected,
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the local Abook user risk snapshot")
    parser.add_argument("--selection-start", default="2026-05-01")
    parser.add_argument("--selection-end", default="2026-06-30")
    parser.add_argument("--platform", action="append", dest="platforms")
    args = parser.parse_args()
    load_env_file()
    platforms = args.platforms or sorted(ALLOWED_PLATFORMS)
    output = snapshot_path()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    payload = build_snapshot(args.selection_start, args.selection_end, platforms)
    temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
    temporary.replace(output)
    print(json.dumps({"path": str(output), "records": len(payload["records"]), "test_demo_excluded_by_sql": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
