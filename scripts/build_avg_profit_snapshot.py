from __future__ import annotations

import argparse
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import clickhouse_connect

from app.config import get_settings, load_env_file
from app.query_client import LocalClientAdapter
from app.queries import ALLOWED_PLATFORMS
from app.avg_profit import snapshot_path


TABLE = "risk.dws_account_daily_window"


def _end_exclusive(value: str) -> str:
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()


def _choose(columns: set[str], candidates: tuple[str, ...], label: str) -> str:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    raise RuntimeError(f"{TABLE} does not expose a usable {label} column; columns={sorted(columns)}")


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
    schema = client.query(f"DESCRIBE TABLE {TABLE}")
    columns = {str(row[0]) for row in schema.result_rows}
    platform_col = _choose(columns, ("platform", "source_platform"), "platform")
    login_col = _choose(columns, ("login", "account", "login_id"), "login")
    date_col = _choose(columns, ("trade_date", "window_end", "window_start"), "trade date")
    profit_col = _choose(columns, ("daily_profit", "profit", "client_net_pnl", "net_profit", "pnl"), "daily profit")
    trades_col = _choose(columns, ("daily_trades", "trade_count", "trades"), "daily trade count")
    window_type_col = "window_type" if "window_type" in columns else None
    window_end_col = "window_end" if "window_end" in columns else date_col
    custom_condition = f'AND "{window_type_col}" = \'CUSTOM\'' if window_type_col else ""
    query = f"""
    WITH daily AS (
        SELECT
            "{platform_col}" AS platform,
            toUInt64("{login_col}") AS login,
            toDate("{date_col}") AS trade_date,
            argMax(toFloat64("{profit_col}"), "{window_end_col}") AS daily_profit,
            argMax(toUInt64("{trades_col}"), "{window_end_col}") AS daily_trades
        FROM {TABLE} FINAL
        WHERE has({{platforms:Array(String)}}, "{platform_col}")
          AND toDate("{date_col}") >= {{selection_start:Date}}
          AND toDate("{date_col}") < {{selection_end_exclusive:Date}}
          {custom_condition}
        GROUP BY platform, login, trade_date
    )
    SELECT
        platform,
        login,
        avgIf(daily_profit, daily_trades > 0) AS avg_profit,
        countIf(daily_trades > 0) AS profit_observations,
        sumIf(daily_profit, daily_trades > 0) AS total_profit,
        minIf(trade_date, daily_trades > 0) AS source_min,
        maxIf(trade_date, daily_trades > 0) AS source_max
    FROM daily
    GROUP BY platform, login
    ORDER BY platform, login
    """
    result = client.query(query, parameters={
        "platforms": selected,
        "selection_start": selection_start,
        "selection_end_exclusive": _end_exclusive(selection_end),
    })
    records = []
    for values in result.result_rows:
        row = dict(zip(result.column_names, values))
        observations = int(row.get("profit_observations") or 0)
        avg_profit = Decimal(str(row.get("avg_profit") or 0))
        if observations <= 0 or not avg_profit.is_finite():
            continue
        records.append({
            "platform": str(row["platform"]),
            "login": int(row["login"]),
            "avg_profit": float(avg_profit),
            "profit_observations": observations,
            "total_profit": float(Decimal(str(row.get("total_profit") or 0))),
            "source_min": str(row.get("source_min"))[:10],
            "source_max": str(row.get("source_max"))[:10],
        })
    return {
        "selection_start": selection_start,
        "selection_end": selection_end,
        "platforms": selected,
        "source_table": TABLE,
        "source_columns": {
            "platform": platform_col,
            "login": login_col,
            "date": date_col,
            "profit": profit_col,
            "trades": trades_col,
            "window_type": window_type_col,
        },
        "calculation": "CUSTOM daily_profit averaged once per active trade date; 7D_SLIDING rows excluded",
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the local average-profit snapshot for Abook filtering")
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
    print(json.dumps({"path": str(output), "records": len(payload["records"]), "source_table": TABLE}, ensure_ascii=False))


if __name__ == "__main__":
    main()
