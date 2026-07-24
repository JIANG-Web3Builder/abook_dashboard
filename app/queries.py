from __future__ import annotations

from datetime import date, timedelta
from typing import Any


ALLOWED_PLATFORMS = {"mt4", "mt5", "hh_mt5"}

# MT4 users live in a separate legacy table without a platform column. Keep a
# single normalized source so every dashboard query applies the same account
# population boundary to MT4, MT5, and HH MT5.
USER_SOURCE_SQL = """
(
    SELECT platform, toUInt64(login) AS login, `group`, is_deleted
    FROM risk.ods_mt5_users FINAL
    UNION ALL
    SELECT 'mt4' AS platform, toUInt64(login) AS login, `group`, is_deleted
    FROM risk.ods_mt4_users FINAL
)
"""


def _date_end_exclusive(end: str) -> str:
    return (date.fromisoformat(end) + timedelta(days=1)).isoformat()


def build_analysis_query(
    *,
    platforms: list[str],
    start: str,
    end: str,
    lookback_months: int,
    filters: dict[str, Any] | None = None,
    selection_start: str | None = None,
    selection_end: str | None = None,
    validation_start: str | None = None,
    validation_end: str | None = None,
    excluded_logins: set[tuple[str, int]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build one monthly fact query covering both selection and validation windows."""
    clean_platforms = [platform for platform in platforms if platform in ALLOWED_PLATFORMS]
    if not clean_platforms:
        clean_platforms = sorted(ALLOWED_PLATFORMS)
    if lookback_months not in (1, 2, 3):
        raise ValueError("lookback_months must be 1, 2, or 3")

    filters = filters or {}
    params: dict[str, Any] = {
        "platforms": clean_platforms,
        "start": start,
        "end_exclusive": _date_end_exclusive(end),
        "lookback_months": lookback_months,
        "selection_start": selection_start or start,
        "selection_end_exclusive": _date_end_exclusive(selection_end or end),
        "validation_start": validation_start or start,
        "validation_end_exclusive": _date_end_exclusive(validation_end or end),
    }
    user_conditions = ["is_deleted = 0", "has({platforms:Array(String)}, platform)"]
    user_conditions.append("positionCaseInsensitive(`group`, 'test') = 0")
    user_conditions.append("positionCaseInsensitive(`group`, 'demo') = 0")
    values = filters.get("groups") or []
    if values:
        params["group_0"] = values
        user_conditions.append("has({group_0:Array(String)}, `group`)")
    if filters.get("logins"):
        login_values = sorted({int(login) for login in filters["logins"]})
        if len(login_values) <= 2000:
            params["login_0"] = login_values
            user_conditions.append("has({login_0:Array(UInt64)}, login)")
        else:
            # clickhouse_connect puts query parameters in the HTTP URL. A large
            # allowed-login array causes HTTP 414, so embed only validated ints
            # in the query text; the list originates from the local snapshot.
            user_conditions.append(f"login IN ({','.join(str(login) for login in login_values)})")
    if excluded_logins:
        by_platform: dict[str, list[int]] = {}
        for platform, login in sorted(excluded_logins):
            by_platform.setdefault(str(platform), []).append(int(login))
        excluded_predicates = [
            f"(platform = '{platform}' AND login IN ({','.join(str(login) for login in sorted(logins))}))"
            for platform, logins in sorted(by_platform.items())
        ]
        user_conditions.append("NOT (" + " OR ".join(excluded_predicates) + ")")

    user_where = " AND ".join(user_conditions)
    query = f"""
WITH users AS (
    SELECT
        platform,
        login,
        any(`group`) AS account_group
    FROM {USER_SOURCE_SQL} AS user_source
    WHERE {user_where}
    GROUP BY platform, login
), periods AS (
    SELECT
        'selection' AS phase,
        toDate({{selection_start:Date}}) AS period_start,
        toDate({{selection_end_exclusive:Date}}) AS period_end
    UNION ALL
    SELECT
        'validation' AS phase,
        toDate({{validation_start:Date}}) AS period_start,
        toDate({{validation_end_exclusive:Date}}) AS period_end
), period_months AS (
    SELECT
        phase,
        addMonths(toStartOfMonth(period_start), n.number) AS month_start
    FROM periods
    CROSS JOIN numbers(36) AS n
    WHERE n.number <= dateDiff(
        'month',
        toStartOfMonth(period_start),
        toStartOfMonth(period_end - toIntervalDay(1))
    )
), matched AS (
    SELECT
        mt.platform AS platform,
        mt.login AS login,
        period.phase AS phase,
        toStartOfMonth(exit_time) AS month_start,
        count() AS matched_trades,
        countIf(profit > 0) AS winning_trades,
        countIf(profit < 0) AS losing_trades,
        sum(volume) AS matched_volume,
        sum(profit) AS market_pnl,
        sumIf(profit, profit > 0) AS gross_wins,
        sumIf(profit, profit < 0) AS gross_losses,
        avg(holding_seconds) AS avg_holding_seconds,
        quantileTDigest(0.5)(toFloat64(holding_seconds)) AS median_holding_seconds,
        countIf(direction = 'Long') AS long_trades,
        countIf(direction = 'Short') AS short_trades,
        uniqExact(symbol) AS symbols_traded
    FROM risk.dwd_matched_trades AS mt FINAL
    INNER JOIN users AS u
        ON mt.platform = u.platform AND mt.login = u.login
    CROSS JOIN periods AS period
    WHERE mt.platform IN {{platforms:Array(String)}}
      AND mt.exit_time >= {{start:Date}}
      AND mt.exit_time < {{end_exclusive:Date}}
      AND mt.exit_time >= period.period_start
      AND mt.exit_time < period.period_end
    GROUP BY mt.platform, mt.login, period.phase, month_start
), deal_daily AS (
    SELECT
        d.platform AS platform,
        d.login AS login,
        period.phase AS phase,
        toDate(time) AS trade_date,
        toStartOfMonth(time) AS month_start,
        countIf(action IN (0, 1)) AS trade_rows,
        toFloat64(sumIf(profit, action IN (0, 1))) AS daily_deal_market_pnl,
        toFloat64(sumIf(profit, action IN (0, 1) AND profit > 0)) AS daily_gross_wins,
        toFloat64(sumIf(profit, action IN (0, 1) AND profit < 0)) AS daily_gross_losses,
        toFloat64(sumIf(storage + commission + fee, action IN (0, 1))) AS daily_costs,
        toFloat64(sumIf(profit + storage + commission + fee, action IN (0, 1))) AS daily_client_net_pnl,
        toFloat64(sumIf(profit, action IN (2, 3))) AS daily_funding_pnl
    FROM risk.ods_mt5_deals AS d FINAL
    INNER JOIN users AS u
        ON d.platform = u.platform AND d.login = u.login
    CROSS JOIN periods AS period
    WHERE d.is_deleted = 0
      AND d.platform IN {{platforms:Array(String)}}
      AND d.time >= {{start:Date}}
      AND d.time < {{end_exclusive:Date}}
      AND d.time >= period.period_start
      AND d.time < period.period_end
    GROUP BY d.platform, d.login, period.phase, trade_date, month_start
    UNION ALL
    SELECT
        mt.platform AS platform,
        mt.login AS login,
        period.phase AS phase,
        toDate(mt.exit_time) AS trade_date,
        toStartOfMonth(mt.exit_time) AS month_start,
        count() AS trade_rows,
        sum(toFloat64(mt.profit)) AS daily_deal_market_pnl,
        sumIf(toFloat64(mt.profit), mt.profit > 0) AS daily_gross_wins,
        sumIf(toFloat64(mt.profit), mt.profit < 0) AS daily_gross_losses,
        0.0 AS daily_costs,
        sum(toFloat64(mt.profit)) AS daily_client_net_pnl,
        0.0 AS daily_funding_pnl
    FROM risk.dwd_matched_trades AS mt FINAL
    INNER JOIN users AS u
        ON mt.platform = u.platform AND mt.login = u.login
    CROSS JOIN periods AS period
    WHERE mt.platform = 'mt4'
      AND mt.exit_time >= {{start:Date}}
      AND mt.exit_time < {{end_exclusive:Date}}
      AND mt.exit_time >= period.period_start
      AND mt.exit_time < period.period_end
    GROUP BY mt.platform, mt.login, period.phase, trade_date, month_start
), deal_costs AS (
    SELECT
        platform,
        login,
        phase,
        month_start,
        sum(daily_deal_market_pnl) AS deal_market_pnl,
        sum(daily_gross_wins) AS gross_wins,
        sum(daily_gross_losses) AS gross_losses,
        sum(daily_costs) AS costs,
        sum(daily_client_net_pnl) AS client_net_pnl,
        sum(daily_funding_pnl) AS funding_pnl,
        countIf(trade_rows > 0) AS active_trade_days,
        sum(daily_client_net_pnl) AS daily_profit_sum,
        countIf(trade_rows > 0 AND daily_client_net_pnl > 0) AS positive_profit_days,
        countIf(trade_rows > 0 AND daily_client_net_pnl < 0) AS negative_profit_days,
        countIf(trade_rows > 0 AND daily_client_net_pnl = 0) AS flat_profit_days
    FROM deal_daily
    GROUP BY platform, login, phase, month_start
), account_daily AS (
    SELECT
        platform,
        login,
        phase,
        month_start,
        countIf(trade_rows > 0) AS daily_active_days,
        countIf(trade_rows > 0 AND daily_client_net_pnl > 0) AS daily_positive_days,
        countIf(trade_rows > 0 AND daily_client_net_pnl < 0) AS daily_negative_days,
        countIf(trade_rows > 0 AND daily_client_net_pnl = 0) AS daily_flat_days,
        sumIf(daily_client_net_pnl, trade_rows > 0 AND daily_client_net_pnl > 0) AS daily_positive_sum,
        sumIf(daily_client_net_pnl, trade_rows > 0 AND daily_client_net_pnl < 0) AS daily_negative_sum,
        maxIf(daily_client_net_pnl, trade_rows > 0 AND daily_client_net_pnl > 0) AS max_positive_day,
        minIf(daily_client_net_pnl, trade_rows > 0 AND daily_client_net_pnl < 0) AS min_negative_day,
        sumIf(toFloat64(daily_client_net_pnl), trade_rows > 0) AS daily_pnl_sum_for_variance,
        sumIf(toFloat64(daily_client_net_pnl) * toFloat64(daily_client_net_pnl), trade_rows > 0) AS daily_pnl_square_sum,
        countIf(trade_rows > 0) AS daily_variance_count,
        sumIf(abs(daily_client_net_pnl), trade_rows > 0) AS daily_abs_sum
    FROM deal_daily
    GROUP BY platform, login, phase, month_start
), account_months AS (
    SELECT
        u.platform,
        u.login,
        u.account_group,
        pm.phase,
        pm.month_start
    FROM users AS u
    CROSS JOIN period_months AS pm
), population AS (
    SELECT
        uniqExact(tuple(platform, login)) AS unique_account_count,
        count() AS account_row_count
    FROM users
), period_matched_stats AS (
    SELECT
        mt.platform,
        mt.login,
        quantileTDigestIf(0.5)(toFloat64(mt.holding_seconds), mt.exit_time >= {{selection_start:Date}} AND mt.exit_time < {{selection_end_exclusive:Date}}) AS selection_median_holding_seconds,
        quantileTDigestIf(0.5)(toFloat64(mt.holding_seconds), mt.exit_time >= {{validation_start:Date}} AND mt.exit_time < {{validation_end_exclusive:Date}}) AS validation_median_holding_seconds,
        uniqExactIf(mt.symbol, mt.exit_time >= {{selection_start:Date}} AND mt.exit_time < {{selection_end_exclusive:Date}}) AS selection_symbols_traded,
        uniqExactIf(mt.symbol, mt.exit_time >= {{validation_start:Date}} AND mt.exit_time < {{validation_end_exclusive:Date}}) AS validation_symbols_traded
    FROM risk.dwd_matched_trades AS mt FINAL
    INNER JOIN users AS u
        ON mt.platform = u.platform AND mt.login = u.login
    WHERE mt.platform IN {{platforms:Array(String)}}
      AND mt.exit_time >= {{start:Date}}
      AND mt.exit_time < {{end_exclusive:Date}}
    GROUP BY mt.platform, mt.login
), trade_source_coverage AS (
    SELECT
        min(exit_time) AS source_matched_min,
        max(exit_time) AS source_matched_max
    FROM risk.dwd_matched_trades AS mt FINAL
    INNER JOIN users AS u
        ON mt.platform = u.platform AND mt.login = u.login
    WHERE mt.platform IN {{platforms:Array(String)}}
      AND mt.exit_time >= {{start:Date}}
      AND mt.exit_time < {{end_exclusive:Date}}
), deal_source_coverage AS (
    SELECT
        min(source_time) AS source_deal_raw_min,
        max(source_time) AS source_deal_raw_max,
        minIf(source_time, is_deleted = 0) AS source_deal_min,
        maxIf(source_time, is_deleted = 0) AS source_deal_max,
        sum(is_deleted) AS source_deal_deleted_rows
    FROM (
        SELECT
            d.time AS source_time,
            toUInt8(d.is_deleted) AS is_deleted
        FROM risk.ods_mt5_deals AS d FINAL
        INNER JOIN users AS u
            ON d.platform = u.platform AND d.login = u.login
        WHERE d.platform IN {{platforms:Array(String)}}
          AND d.time >= {{start:Date}}
          AND d.time < {{end_exclusive:Date}}
        UNION ALL
        SELECT
            mt.exit_time AS source_time,
            toUInt8(0) AS is_deleted
        FROM risk.dwd_matched_trades AS mt FINAL
        INNER JOIN users AS u
            ON mt.platform = u.platform AND mt.login = u.login
        WHERE mt.platform = 'mt4'
          AND mt.exit_time >= {{start:Date}}
          AND mt.exit_time < {{end_exclusive:Date}}
    )
)
SELECT
    k.platform AS platform,
    k.login AS login,
    k.account_group AS account_group,
    k.phase AS phase,
    k.month_start AS month_start,
    p.unique_account_count AS population_unique_accounts,
    p.account_row_count AS population_account_rows,
    ts.source_matched_min AS source_matched_min,
    ts.source_matched_max AS source_matched_max,
    ds.source_deal_raw_min AS source_deal_raw_min,
    ds.source_deal_raw_max AS source_deal_raw_max,
    ds.source_deal_min AS source_deal_min,
    ds.source_deal_max AS source_deal_max,
    ds.source_deal_deleted_rows AS source_deal_deleted_rows,
    coalesce(m.matched_trades, 0) AS matched_trades,
    coalesce(m.winning_trades, 0) AS winning_trades,
    coalesce(m.losing_trades, 0) AS losing_trades,
    coalesce(m.matched_volume, 0) AS matched_volume,
    coalesce(m.market_pnl, 0) AS matched_market_pnl,
    coalesce(d.deal_market_pnl, 0) AS market_pnl,
    coalesce(d.gross_wins, 0) AS gross_wins,
    coalesce(d.gross_losses, 0) AS gross_losses,
    coalesce(d.deal_market_pnl, 0) AS deal_market_pnl,
    coalesce(d.costs, 0) AS costs,
    coalesce(d.client_net_pnl, 0) AS client_net_pnl,
    coalesce(d.funding_pnl, 0) AS funding_pnl,
    coalesce(d.active_trade_days, 0) AS active_trade_days,
    coalesce(d.daily_profit_sum, 0) AS daily_profit_sum,
    coalesce(d.positive_profit_days, 0) AS positive_profit_days,
    coalesce(d.negative_profit_days, 0) AS negative_profit_days,
    coalesce(d.flat_profit_days, 0) AS flat_profit_days,
    coalesce(a.daily_active_days, 0) AS daily_active_days,
    coalesce(a.daily_positive_days, 0) AS daily_positive_days,
    coalesce(a.daily_negative_days, 0) AS daily_negative_days,
    coalesce(a.daily_flat_days, 0) AS daily_flat_days,
    coalesce(a.daily_positive_sum, 0) AS daily_positive_sum,
    coalesce(a.daily_negative_sum, 0) AS daily_negative_sum,
    coalesce(a.max_positive_day, 0) AS max_positive_day,
    coalesce(a.min_negative_day, 0) AS min_negative_day,
    coalesce(a.daily_pnl_sum_for_variance, 0) AS daily_pnl_sum_for_variance,
    coalesce(a.daily_pnl_square_sum, 0) AS daily_pnl_square_sum,
    coalesce(a.daily_variance_count, 0) AS daily_variance_count,
    coalesce(a.daily_abs_sum, 0) AS daily_abs_sum,
    coalesce(m.avg_holding_seconds, 0) AS avg_holding_seconds,
    coalesce(m.median_holding_seconds, 0) AS median_holding_seconds,
    coalesce(m.long_trades, 0) AS long_trades,
    coalesce(m.short_trades, 0) AS short_trades,
    coalesce(m.symbols_traded, 0) AS symbols_traded,
    if(isNaN(period_stats.selection_median_holding_seconds), 0, coalesce(period_stats.selection_median_holding_seconds, 0)) AS selection_median_holding_seconds,
    if(isNaN(period_stats.validation_median_holding_seconds), 0, coalesce(period_stats.validation_median_holding_seconds, 0)) AS validation_median_holding_seconds,
    coalesce(period_stats.selection_symbols_traded, 0) AS selection_symbols_traded,
    coalesce(period_stats.validation_symbols_traded, 0) AS validation_symbols_traded
FROM account_months AS k
CROSS JOIN population AS p
CROSS JOIN trade_source_coverage AS ts
CROSS JOIN deal_source_coverage AS ds
LEFT JOIN matched AS m
    ON k.platform = m.platform AND k.login = m.login AND k.phase = m.phase AND k.month_start = m.month_start
LEFT JOIN deal_costs AS d
    ON k.platform = d.platform AND k.login = d.login AND k.phase = d.phase AND k.month_start = d.month_start
LEFT JOIN account_daily AS a
    ON k.platform = a.platform AND k.login = a.login AND k.phase = a.phase AND k.month_start = a.month_start
LEFT JOIN period_matched_stats AS period_stats
    ON k.platform = period_stats.platform AND k.login = period_stats.login
ORDER BY k.platform, k.login, k.month_start
"""
    return query, params


def build_daily_pnl_query(
    *,
    platforms: list[str],
    start: str,
    end: str,
    filters: dict[str, Any] | None = None,
    excluded_logins: set[tuple[str, int]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build account-day canonical Deals P&L rows for a dashboard date range."""
    clean_platforms = [platform for platform in platforms if platform in ALLOWED_PLATFORMS]
    if not clean_platforms:
        clean_platforms = sorted(ALLOWED_PLATFORMS)

    filters = filters or {}
    params: dict[str, Any] = {
        "platforms": clean_platforms,
        "start": start,
        "end_exclusive": _date_end_exclusive(end),
    }
    user_conditions = ["is_deleted = 0", "has({platforms:Array(String)}, platform)"]
    user_conditions.append("positionCaseInsensitive(`group`, 'test') = 0")
    user_conditions.append("positionCaseInsensitive(`group`, 'demo') = 0")
    values = filters.get("groups") or []
    if values:
        params["group_0"] = values
        user_conditions.append("has({group_0:Array(String)}, `group`)")
    if filters.get("logins"):
        login_values = sorted({int(login) for login in filters["logins"]})
        if len(login_values) <= 2000:
            params["login_0"] = login_values
            user_conditions.append("has({login_0:Array(UInt64)}, login)")
        else:
            user_conditions.append(f"login IN ({','.join(str(login) for login in login_values)})")
    if excluded_logins:
        by_platform: dict[str, list[int]] = {}
        for platform, login in sorted(excluded_logins):
            by_platform.setdefault(str(platform), []).append(int(login))
        excluded_predicates = [
            f"(platform = '{platform}' AND login IN ({','.join(str(login) for login in sorted(logins))}))"
            for platform, logins in sorted(by_platform.items())
        ]
        user_conditions.append("NOT (" + " OR ".join(excluded_predicates) + ")")

    query = f"""
WITH users AS (
    SELECT
        platform,
        login
    FROM {USER_SOURCE_SQL} AS user_source
    WHERE {" AND ".join(user_conditions)}
    GROUP BY platform, login
)
SELECT
    d.platform AS platform,
    d.login AS login,
    toDate(d.time) AS trade_date,
    toFloat64(sumIf(profit + storage + commission + fee, action IN (0, 1))) AS client_net_pnl,
    toFloat64(sumIf(profit, action IN (0, 1))) AS market_pnl,
    countIf(action IN (0, 1)) AS matched_trades
FROM risk.ods_mt5_deals AS d FINAL
INNER JOIN users AS u
    ON d.platform = u.platform AND d.login = u.login
WHERE d.is_deleted = 0
  AND d.platform IN {{platforms:Array(String)}}
  AND d.time >= {{start:Date}}
  AND d.time < {{end_exclusive:Date}}
GROUP BY d.platform, d.login, trade_date
UNION ALL
SELECT
    mt.platform AS platform,
    mt.login AS login,
    toDate(mt.exit_time) AS trade_date,
    sum(toFloat64(mt.profit)) AS client_net_pnl,
    sum(toFloat64(mt.profit)) AS market_pnl,
    count() AS matched_trades
FROM risk.dwd_matched_trades AS mt FINAL
INNER JOIN users AS u
    ON mt.platform = u.platform AND mt.login = u.login
WHERE mt.platform = 'mt4'
  AND mt.exit_time >= {{start:Date}}
  AND mt.exit_time < {{end_exclusive:Date}}
GROUP BY mt.platform, mt.login, trade_date
ORDER BY platform, login, trade_date
"""
    return query, params


def build_direction_matched_facts_query(
    *,
    platforms: list[str],
    start: str,
    end: str,
    filters: dict[str, Any] | None = None,
    excluded_logins: set[tuple[str, int]] | None = None,
    selection_start: str | None = None,
    selection_end: str | None = None,
    validation_start: str | None = None,
    validation_end: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build monthly, daily, and symbol facts from matched trades by direction.

    Direction analytics intentionally has no Deals join: every P&L field in
    this query is the direction-specific ``matched.profit`` equivalent.
    """
    clean_platforms = [platform for platform in platforms if platform in ALLOWED_PLATFORMS]
    if not clean_platforms:
        clean_platforms = sorted(ALLOWED_PLATFORMS)

    filters = filters or {}
    params: dict[str, Any] = {
        "platforms": clean_platforms,
        "start": start,
        "end_exclusive": _date_end_exclusive(end),
    }
    user_conditions = ["is_deleted = 0", "has({platforms:Array(String)}, platform)"]
    user_conditions.extend([
        "positionCaseInsensitive(`group`, 'test') = 0",
        "positionCaseInsensitive(`group`, 'demo') = 0",
    ])
    values = filters.get("groups") or []
    if values:
        params["group_0"] = values
        user_conditions.append("has({group_0:Array(String)}, `group`)")
    if filters.get("logins"):
        login_values = sorted({int(login) for login in filters["logins"]})
        if len(login_values) <= 2000:
            params["login_0"] = login_values
            user_conditions.append("has({login_0:Array(UInt64)}, login)")
        else:
            user_conditions.append(f"login IN ({','.join(str(login) for login in login_values)})")
    if excluded_logins:
        by_platform: dict[str, list[int]] = {}
        for platform, login in sorted(excluded_logins):
            by_platform.setdefault(str(platform), []).append(int(login))
        predicates = [
            f"(platform = '{platform}' AND login IN ({','.join(str(login) for login in sorted(logins))}))"
            for platform, logins in sorted(by_platform.items())
        ]
        user_conditions.append("NOT (" + " OR ".join(predicates) + ")")

    user_where = " AND ".join(user_conditions)
    needs_user_join = bool(values or filters.get("logins") or excluded_logins)
    user_cte = (
        f"users AS (\n    SELECT platform, login, any(`group`) AS account_group\n"
        f"    FROM {USER_SOURCE_SQL} AS user_source\n"
        f"    WHERE {user_where}\n    GROUP BY platform, login\n), "
        if needs_user_join else ""
    )
    account_group_expr = "u.account_group" if needs_user_join else "''"
    group_prefix = "mt.platform, mt.login, u.account_group, " if needs_user_join else "mt.platform, mt.login, "
    user_join = "INNER JOIN users AS u ON mt.platform = u.platform AND mt.login = u.login" if needs_user_join else ""
    query = f"""
WITH {user_cte}periods AS (
    SELECT 'selection' AS phase, toDate({{selection_start:Date}}) AS period_start,
           toDate({{selection_end_exclusive:Date}}) AS period_end
    UNION ALL
    SELECT 'validation' AS phase, toDate({{validation_start:Date}}) AS period_start,
           toDate({{validation_end_exclusive:Date}}) AS period_end
)
SELECT mt.platform AS platform,
       mt.login AS login,
       {account_group_expr} AS account_group,
       period.phase AS phase,
       mt.direction AS direction,
       if(grouping(toStartOfMonth(mt.exit_time)) = 1,
          toStartOfMonth(min(mt.exit_time)),
          toStartOfMonth(mt.exit_time)) AS month_start,
       if(grouping(toDate(mt.exit_time)) = 1,
          toDate('1970-01-01'),
          toDate(mt.exit_time)) AS trade_date,
       if(grouping(mt.symbol) = 1, '', mt.symbol) AS symbol,
       multiIf(
           grouping(mt.symbol) = 0, 'symbol',
           grouping(toDate(mt.exit_time)) = 0, 'day',
           grouping(toStartOfMonth(mt.exit_time)) = 0, 'month',
           'phase'
       ) AS fact_level,
       count() AS matched_trades,
       countIf(mt.profit > 0) AS winning_trades,
       countIf(mt.profit < 0) AS losing_trades,
       sum(toFloat64(mt.profit)) AS side_pnl,
       sum(toFloat64(mt.volume)) AS matched_volume,
       sumIf(toFloat64(mt.profit), mt.profit > 0) AS gross_wins,
       sumIf(toFloat64(mt.profit), mt.profit < 0) AS gross_losses,
       avg(toFloat64(mt.holding_seconds)) AS avg_holding_seconds,
       quantileTDigest(0.5)(toFloat64(mt.holding_seconds)) AS median_holding_seconds
FROM risk.dwd_matched_trades AS mt FINAL
{user_join}
CROSS JOIN periods AS period
WHERE mt.platform IN {{platforms:Array(String)}}
  AND mt.direction IN ('Long', 'Short')
  AND mt.exit_time >= {{start:Date}}
  AND mt.exit_time < {{end_exclusive:Date}}
  AND mt.exit_time >= period.period_start
  AND mt.exit_time < period.period_end
GROUP BY GROUPING SETS (
    ({group_prefix}period.phase, mt.direction, toStartOfMonth(mt.exit_time)),
    ({group_prefix}period.phase, mt.direction, toStartOfMonth(mt.exit_time), toDate(mt.exit_time)),
    ({group_prefix}period.phase, mt.direction, mt.symbol),
    ({group_prefix}period.phase, mt.direction)
)
ORDER BY platform, login, direction, phase, fact_level, month_start, trade_date, symbol
"""
    params.update({
        "selection_start": selection_start or start,
        "selection_end_exclusive": _date_end_exclusive(selection_end or end),
        "validation_start": validation_start or start,
        "validation_end_exclusive": _date_end_exclusive(validation_end or end),
    })
    return query, params


def build_account_detail_query(platform: str, login: int, start: str, end: str) -> tuple[str, dict[str, Any]]:
    """Return trade-level detail for one account."""
    if platform not in ALLOWED_PLATFORMS:
        raise ValueError("unsupported platform")
    query = f"""
    SELECT
        platform, login, symbol, direction, entry_time, exit_time,
        entry_price, exit_price, volume, turnover, profit, holding_seconds,
        entry_deal_id, exit_deal_id
    FROM risk.dwd_matched_trades AS m FINAL
    INNER JOIN {USER_SOURCE_SQL} AS u
      ON m.platform = u.platform AND m.login = u.login
    WHERE u.is_deleted = 0
      AND positionCaseInsensitive(u.`group`, 'test') = 0
      AND positionCaseInsensitive(u.`group`, 'demo') = 0
      AND m.platform = {{platform:String}}
      AND m.login = {{login:UInt64}}
      AND m.exit_time >= {{start:Date}}
      AND m.exit_time < {{end_exclusive:Date}}
    ORDER BY exit_time DESC
    LIMIT 5000
    """
    return query, {
        "platform": platform,
        "login": login,
        "start": start,
        "end_exclusive": _date_end_exclusive(end),
    }


def build_account_direction_summary_query(
    platform: str,
    login: int,
    start: str,
    end: str,
    selection_start: str,
    selection_end: str,
    validation_start: str,
    validation_end: str,
) -> tuple[str, dict[str, Any]]:
    """Return uncapped matched Long/Short aggregates for one account."""
    if platform not in ALLOWED_PLATFORMS:
        raise ValueError("unsupported platform")
    query = f"""
    WITH periods AS (
        SELECT 'selection' AS phase,
               toDate({{selection_start:Date}}) AS period_start,
               toDate({{selection_end_exclusive:Date}}) AS period_end
        UNION ALL
        SELECT 'validation' AS phase,
               toDate({{validation_start:Date}}) AS period_start,
               toDate({{validation_end_exclusive:Date}}) AS period_end
    )
    SELECT
        period.phase AS phase,
        mt.direction AS direction,
        count() AS matched_trades,
        countIf(mt.profit > 0) AS winning_trades,
        countIf(mt.profit < 0) AS losing_trades,
        sum(toFloat64(mt.profit)) AS side_pnl,
        sumIf(toFloat64(mt.profit), mt.profit > 0) AS gross_wins,
        sumIf(toFloat64(mt.profit), mt.profit < 0) AS gross_losses
    FROM risk.dwd_matched_trades AS mt FINAL
    INNER JOIN {USER_SOURCE_SQL} AS u
      ON mt.platform = u.platform AND mt.login = u.login
    CROSS JOIN periods AS period
    WHERE u.is_deleted = 0
      AND positionCaseInsensitive(u.`group`, 'test') = 0
      AND positionCaseInsensitive(u.`group`, 'demo') = 0
      AND mt.platform = {{platform:String}}
      AND mt.login = {{login:UInt64}}
      AND mt.direction IN ('Long', 'Short')
      AND mt.exit_time >= {{start:Date}}
      AND mt.exit_time < {{end_exclusive:Date}}
      AND mt.exit_time >= period.period_start
      AND mt.exit_time < period.period_end
    GROUP BY phase, direction
    ORDER BY phase, direction
    """
    return query, {
        "platform": platform,
        "login": login,
        "start": start,
        "end_exclusive": _date_end_exclusive(end),
        "selection_start": selection_start,
        "selection_end_exclusive": _date_end_exclusive(selection_end),
        "validation_start": validation_start,
        "validation_end_exclusive": _date_end_exclusive(validation_end),
    }


def _book_key_predicate(account_keys: set[tuple[str, int]] | None) -> str:
    if not account_keys:
        return ""
    by_platform: dict[str, list[int]] = {}
    for platform, login in sorted(account_keys):
        if platform in ALLOWED_PLATFORMS:
            by_platform.setdefault(platform, []).append(int(login))
    if not by_platform:
        return " AND 0"
    predicates = [
        f"(m.platform = '{platform}' AND m.login IN ({','.join(str(login) for login in logins)}))"
        for platform, logins in sorted(by_platform.items())
    ]
    return " AND (" + " OR ".join(predicates) + ")"


def build_book_symbol_query(
    *, platforms: list[str], start: str, end: str, account_keys: set[tuple[str, int]] | None = None
) -> tuple[str, dict[str, Any]]:
    clean_platforms = [platform for platform in platforms if platform in ALLOWED_PLATFORMS] or sorted(ALLOWED_PLATFORMS)
    return f"""
    SELECT m.platform, m.login, m.symbol,
           count() AS trade_count, sum(m.volume) AS volume,
           sum(m.profit) AS market_pnl,
           avg(m.holding_seconds) AS avg_holding_seconds
    FROM risk.dwd_matched_trades AS m FINAL
    INNER JOIN {USER_SOURCE_SQL} AS u
      ON m.platform = u.platform AND m.login = u.login
    WHERE u.is_deleted = 0
      AND positionCaseInsensitive(u.`group`, 'test') = 0
      AND positionCaseInsensitive(u.`group`, 'demo') = 0
      AND m.platform IN {{platforms:Array(String)}}
      AND m.exit_time >= {{start:Date}}
      AND m.exit_time < {{end_exclusive:Date}}
      {_book_key_predicate(account_keys)}
    GROUP BY m.platform, m.login, m.symbol
    ORDER BY market_pnl DESC
    """, {"platforms": clean_platforms, "start": start, "end_exclusive": _date_end_exclusive(end)}
