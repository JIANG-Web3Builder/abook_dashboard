from __future__ import annotations

from pathlib import Path
import re
import json
from typing import Any


_TABLES = (
    "ods_mt4_users",
    "ods_mt5_users",
    "ods_mt5_deals",
    "dwd_matched_trades",
    "ods_mt4_daily_balance",
    "ods_mt5_daily_balance",
    "dws_account_martingale_window",
    "dws_account_daily_window",
)
_PARAMETER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*):([A-Za-z0-9_]+(?:\([A-Za-z0-9_]+\))?)\}")


def _sql_string(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _sql_value(value: Any, type_name: str) -> str:
    if value is None:
        return "NULL"
    if type_name.startswith("Array("):
        inner = type_name[6:-1]
        return "[" + ", ".join(_sql_value(item, inner) for item in value) + "]"
    if type_name in {"Date", "Date32"}:
        return f"toDate({_sql_string(value)})"
    if type_name.startswith("DateTime"):
        return f"toDateTime({_sql_string(value)})"
    if type_name in {"String", "FixedString"}:
        return _sql_string(value)
    if type_name.startswith("UInt") or type_name.startswith("Int"):
        return str(int(value))
    if type_name.startswith("Float") or type_name == "Decimal":
        return str(float(value))
    if type_name == "Bool" or type_name == "UInt8":
        return "1" if bool(value) else "0"
    raise ValueError(f"unsupported local query parameter type: {type_name}")


def _table_source(root: Path, table: str) -> str:
    table_root = (root / table).resolve()
    if table in {"ods_mt4_users", "ods_mt5_users"}:
        pattern = table_root / "*.parquet"
    else:
        pattern = table_root / "**" / "*.parquet"
    path = str(pattern).replace("'", "''")
    return f"file('{path}', Parquet)"


def render_local_query(query: str, parameters: dict[str, Any], root: Path) -> str:
    rendered = re.sub(r"\bFINAL\b", "", query, flags=re.IGNORECASE)
    for table in _TABLES:
        rendered = re.sub(
            rf"\brisk\.{re.escape(table)}\b",
            _table_source(root, table),
            rendered,
        )
    # clickhouse-connect can materialize a ClickHouse DateTime column as
    # epoch seconds (UInt32) in Arrow/Parquet. Normalize Deals time at the
    # local SQL boundary so comparisons with Date and DateTime parameters are
    # type-safe, including Warehouses written by older sync versions.
    rendered = re.sub(r"\bd\.time\b", "toDateTime(d.time)", rendered)
    rendered = rendered.replace("toDate(time)", "toDate(toDateTime(time))")
    rendered = rendered.replace("toStartOfMonth(time)", "toStartOfMonth(toDateTime(time))")

    def replace_parameter(match: re.Match[str]) -> str:
        name, type_name = match.groups()
        if name not in parameters:
            raise ValueError(f"missing local query parameter: {name}")
        return _sql_value(parameters[name], type_name)

    return _PARAMETER.sub(replace_parameter, rendered)


class LocalQueryExecutor:
    """Execute rendered SQL through chdb and return API-shaped row dictionaries."""

    def __init__(self, root: Path, query_runner: Any | None = None):
        self.root = root.resolve()
        if query_runner is None:
            try:
                import chdb
            except ImportError as exc:
                raise RuntimeError("chdb is required for ABOOK_DATA_SOURCE=local") from exc
            query_runner = lambda sql: chdb.query(sql, "JSONEachRow")
        self._query_runner = query_runner

    @staticmethod
    def _result_text(result: Any) -> str:
        if isinstance(result, bytes):
            return result.decode("utf-8")
        if isinstance(result, str):
            return result
        bytes_method = getattr(result, "bytes", None)
        if callable(bytes_method):
            value = bytes_method()
            return value.decode("utf-8") if isinstance(value, bytes) else str(value)
        return str(result)

    def rows(self, query: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        rendered = render_local_query(query, parameters, self.root)
        result = self._query_runner(rendered)
        rows: list[dict[str, Any]] = []
        for line in self._result_text(result).splitlines():
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise RuntimeError("local query did not return JSON object rows")
                rows.append(value)
        return rows
