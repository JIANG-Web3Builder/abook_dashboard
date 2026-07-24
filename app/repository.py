from __future__ import annotations

from typing import Any
from datetime import date
from pathlib import Path

import clickhouse_connect

from .config import Settings, get_settings
from .models import AnalysisRequest
from .queries import (
    build_account_detail_query,
    build_account_direction_summary_query,
    build_analysis_query,
    build_book_symbol_query,
    build_daily_pnl_query,
    build_direction_matched_facts_query,
    build_newcomer_daily_facts_query,
    USER_SOURCE_SQL,
)
from .local_query import LocalQueryExecutor
from .warehouse import ensure_coverage, ensure_tables, load_manifest, WarehouseCoverageError


class RepositoryConfigurationError(RuntimeError):
    pass


class ClickHouseRepository:
    def __init__(self, settings: Settings | None = None, client: Any | None = None):
        self.settings = settings or get_settings()
        if client is not None:
            self.client = client
            return
        if not self.settings.configured:
            self.client = None
            return
        self.client = clickhouse_connect.get_client(
            host=self.settings.clickhouse_host,
            port=self.settings.clickhouse_port,
            username=self.settings.clickhouse_user,
            password=self.settings.clickhouse_password,
            database=self.settings.clickhouse_database,
            secure=self.settings.clickhouse_secure,
            compress=self.settings.clickhouse_compress,
            apply_server_timezone=self.settings.clickhouse_use_server_time_zone_for_dates,
        )

    def _rows(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if self.client is None:
            raise RepositoryConfigurationError("CLICKHOUSE_PASSWORD is not configured")
        result = self.client.query(query, parameters=params)
        return [dict(zip(result.column_names, row)) for row in result.result_rows]

    def _validate_coverage(
        self,
        start: date,
        end: date,
        platforms: list[str],
        tables: list[str],
    ) -> None:
        return None

    def _validate_tables(self, tables: list[str]) -> None:
        return None

    def fetch_analysis(
        self,
        request: AnalysisRequest,
        excluded_logins: set[tuple[str, int]] | None = None,
    ) -> list[dict[str, Any]]:
        if request.lookback_months is not None:
            start = request.start or request.selection.start
            end = request.end or request.validation.end
            lookback_months = request.lookback_months
        else:
            start = min(request.selection.start, request.validation.start)
            end = max(request.selection.end, request.validation.end)
            lookback_months = 3
        self._validate_coverage(start, end, request.platforms, ["dwd_matched_trades", "ods_mt5_deals"])
        query, params = build_analysis_query(
            platforms=request.platforms,
            start=start.isoformat(),
            end=end.isoformat(),
            lookback_months=lookback_months,
            filters=request.filters.model_dump(),
            excluded_logins=excluded_logins,
            selection_start=request.selection.start.isoformat(),
            selection_end=request.selection.end.isoformat(),
            validation_start=request.validation.start.isoformat(),
            validation_end=request.validation.end.isoformat(),
        )
        return self._rows(query, params)

    def fetch_daily_pnl(
        self,
        request: AnalysisRequest,
        excluded_logins: set[tuple[str, int]] | None = None,
    ) -> list[dict[str, Any]]:
        start = min(request.selection.start, request.validation.start)
        end = max(request.selection.end, request.validation.end)
        self._validate_coverage(start, end, request.platforms, ["dwd_matched_trades", "ods_mt5_deals"])
        query, params = build_daily_pnl_query(
            platforms=request.platforms,
            start=start.isoformat(),
            end=end.isoformat(),
            filters=request.filters.model_dump(),
            excluded_logins=excluded_logins,
        )
        return self._rows(query, params)

    def fetch_direction_matched_facts(self, request: AnalysisRequest) -> list[dict[str, Any]]:
        start = min(request.selection.start, request.validation.start)
        end = max(request.selection.end, request.validation.end)
        self._validate_coverage(start, end, request.platforms, ["dwd_matched_trades"])
        query, params = build_direction_matched_facts_query(
            platforms=request.platforms,
            start=start.isoformat(),
            end=end.isoformat(),
            filters=request.filters.model_dump(),
            selection_start=request.selection.start.isoformat(),
            selection_end=request.selection.end.isoformat(),
            validation_start=request.validation.start.isoformat(),
            validation_end=request.validation.end.isoformat(),
        )
        return self._rows(query, params)

    def fetch_newcomer_daily_facts(
        self,
        request: AnalysisRequest,
        excluded_logins: set[tuple[str, int]] | None = None,
    ) -> list[dict[str, Any]]:
        start = min(request.selection.start, request.validation.start)
        end = max(request.selection.end, request.validation.end)
        self._validate_coverage(start, end, request.platforms, ["dwd_matched_trades", "ods_mt5_deals"])
        query, params = build_newcomer_daily_facts_query(
            platforms=request.platforms,
            start=start.isoformat(),
            end=end.isoformat(),
            filters=request.filters.model_dump(),
            excluded_logins=excluded_logins,
        )
        return self._rows(query, params)

    def fetch_account_detail(self, platform: str, login: int, start: str, end: str) -> list[dict[str, Any]]:
        self._validate_coverage(date.fromisoformat(start), date.fromisoformat(end), [platform], ["dwd_matched_trades"])
        query, params = build_account_detail_query(platform, login, start, end)
        return self._rows(query, params)

    def fetch_account_direction_summary(
        self,
        platform: str,
        login: int,
        start: str,
        end: str,
        selection_start: str,
        selection_end: str,
        validation_start: str,
        validation_end: str,
    ) -> list[dict[str, Any]]:
        self._validate_coverage(date.fromisoformat(start), date.fromisoformat(end), [platform], ["dwd_matched_trades"])
        query, params = build_account_direction_summary_query(
            platform,
            login,
            start,
            end,
            selection_start,
            selection_end,
            validation_start,
            validation_end,
        )
        return self._rows(query, params)

    def fetch_book_symbol_rows(self, request: AnalysisRequest, account_keys: set[tuple[str, int]] | None = None) -> list[dict[str, Any]]:
        start = min(request.selection.start, request.validation.start)
        end = max(request.selection.end, request.validation.end)
        self._validate_coverage(start, end, request.platforms, ["dwd_matched_trades"])
        query, params = build_book_symbol_query(
            platforms=request.platforms, start=start.isoformat(), end=end.isoformat(), account_keys=account_keys
        )
        return self._rows(query, params)

    def fetch_filter_options(self) -> dict[str, list[str]]:
        self._validate_tables(["ods_mt4_users", "ods_mt5_users"])
        rows = self._rows(
            f"""
            SELECT platform, `group`
            FROM {USER_SOURCE_SQL} AS user_source
            WHERE is_deleted = 0
              AND positionCaseInsensitive(`group`, 'test') = 0
              AND positionCaseInsensitive(`group`, 'demo') = 0
              AND platform IN ('mt4', 'mt5', 'hh_mt5')
            GROUP BY platform, `group`
            ORDER BY platform, `group`
            """,
            {},
        )
        return {
            "platforms": sorted({row["platform"] for row in rows}),
            "groups": sorted({row["group"] for row in rows if row["group"]}),
        }


class LocalWarehouseRepository(ClickHouseRepository):
    def __init__(self, settings: Settings | None = None, executor: Any | None = None):
        self.settings = settings or get_settings()
        self.root = Path(self.settings.warehouse_path).resolve()
        self.manifest_path = self.root / "manifest.json"
        self.executor = executor or LocalQueryExecutor(self.root)
        self.client = None

    def _rows(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self.executor.rows(query, params)

    def _validate_tables(self, tables: list[str]) -> None:
        manifest = load_manifest(self.manifest_path)
        ensure_tables(manifest, tables)

    def _validate_coverage(
        self,
        start: date,
        end: date,
        platforms: list[str],
        tables: list[str],
    ) -> None:
        self._validate_tables(tables)
        ensure_coverage(
            load_manifest(self.manifest_path),
            start=start,
            end=end,
            platforms=platforms,
            tables=tables,
        )
