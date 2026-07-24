from app.queries import (
    build_account_direction_summary_query,
    build_analysis_query,
    build_daily_pnl_query,
    build_direction_matched_facts_query,
)


def test_account_direction_summary_query_aggregates_matched_trades_by_phase_and_direction():
    query, params = build_account_direction_summary_query(
        platform="mt5",
        login=7,
        start="2026-05-01",
        end="2026-07-16",
        selection_start="2026-05-01",
        selection_end="2026-06-30",
        validation_start="2026-07-01",
        validation_end="2026-07-16",
    )

    assert "risk.dwd_matched_trades AS mt FINAL" in query
    assert "mt.direction IN ('Long', 'Short')" in query
    assert "countIf(mt.profit > 0) AS winning_trades" in query
    assert "sumIf(toFloat64(mt.profit), mt.profit > 0) AS gross_wins" in query
    assert "GROUP BY phase, direction" in query
    assert params["platform"] == "mt5"
    assert params["login"] == 7
    assert params["selection_end_exclusive"] == "2026-07-01"
    assert params["validation_end_exclusive"] == "2026-07-17"


def test_direction_query_returns_only_matched_profit_facts_with_direction_and_daily_buckets():
    query, params = build_direction_matched_facts_query(
        platforms=["mt5"], start="2026-05-01", end="2026-07-16", filters={}
    )

    assert "risk.dwd_matched_trades AS mt FINAL" in query
    assert "mt.direction IN ('Long', 'Short')" in query
    assert "fact_level" in query
    assert "'phase'" in query
    assert "toDate(mt.exit_time)" in query
    assert "sum(toFloat64(mt.profit)) AS side_pnl" in query
    assert "ods_mt5_deals" not in query
    assert "INNER JOIN users" not in query
    assert "period.phase, mt.direction, mt.symbol" in query
    assert "period.phase, mt.direction, toStartOfMonth(mt.exit_time), toDate(mt.exit_time), mt.symbol" not in query
    assert params["platforms"] == ["mt5"]


def test_direction_query_uses_clickhouse_compatible_single_pass_grouping_sets():
    query, _ = build_direction_matched_facts_query(
        platforms=["mt5"], start="2026-05-01", end="2026-07-16", filters={}
    )

    assert "GROUP BY GROUPING SETS" in query
    assert "FROM base" not in query


def test_direction_query_uses_independent_selection_and_validation_windows():
    _, params = build_direction_matched_facts_query(
        platforms=["mt5"], start="2026-05-01", end="2026-07-16", filters={},
        selection_start="2026-05-01", selection_end="2026-06-30",
        validation_start="2026-07-01", validation_end="2026-07-16",
    )

    assert params["selection_start"] == "2026-05-01"
    assert params["selection_end_exclusive"] == "2026-07-01"
    assert params["validation_start"] == "2026-07-01"
    assert params["validation_end_exclusive"] == "2026-07-17"


def test_analysis_query_has_final_and_delete_filter():
    query, params = build_analysis_query(
        platforms=["mt5", "hh_mt5"],
        start="2026-05-15",
        end="2026-07-13",
        lookback_months=3,
        filters={"groups": ["real\\FPlive"]},
    )

    assert "risk.ods_mt5_users FINAL" in query
    assert "risk.ods_mt4_users FINAL" in query
    assert "FROM risk.dwd_matched_trades AS mt FINAL" in query
    assert "is_deleted = 0" in query
    assert params["group_0"] == ["real\\FPlive"]
    assert params["lookback_months"] == 3


def test_analysis_query_only_adds_group_and_login_filters():
    query, params = build_analysis_query(
        platforms=["mt5"],
        start="2026-05-15",
        end="2026-07-13",
        lookback_months=3,
        filters={"groups": ["real\\FPlive"], "logins": [123]},
    )

    assert "`group`" in query
    assert "has({login_0:Array(UInt64)}, login)" in query
    assert "country" not in query
    assert "leverage >=" not in query
    assert "balance >=" not in query
    assert params["group_0"] == ["real\\FPlive"]


def test_analysis_query_does_not_reintroduce_runtime_position_proxy():
    query, _ = build_analysis_query(
        platforms=["mt5"], start="2026-05-01", end="2026-07-13", lookback_months=3,
        filters={},
    )

    assert "max_trade_volume" not in query
    assert "max_top_day_concentration" not in query


def test_analysis_query_does_not_compute_turnover():
    query, _ = build_analysis_query(
        platforms=["mt5"], start="2026-05-01", end="2026-07-13", lookback_months=3,
        filters={},
    )

    assert "turnover" not in query.lower()


def test_martingale_snapshot_query_requires_repeated_confirmed_evidence():
    from pathlib import Path

    source = Path("scripts/build_martingale_snapshot.py").read_text()

    assert "confirmed_gate" in source
    assert "confirmed_extreme_candidate" in source
    assert "confirmed_extreme_windows" in source
    assert "expanded_windows" in source
    assert "averaging_down_profit <= 0" in source


def test_analysis_query_embeds_large_local_login_list_to_avoid_http_url_414():
    logins = list(range(3000))
    query, params = build_analysis_query(
        platforms=["mt5"], start="2026-05-01", end="2026-07-13", lookback_months=3,
        filters={"logins": logins},
    )

    assert "login IN (0,1,2" in query
    assert "login_0" not in params


def test_analysis_query_can_exclude_large_local_risk_login_set():
    query, _ = build_analysis_query(
        platforms=["mt5"], start="2026-05-01", end="2026-07-13", lookback_months=3,
        filters={}, excluded_logins={("mt5", login) for login in range(3000)},
    )

    assert "NOT ((platform = 'mt5' AND login IN (0,1,2" in query


def test_analysis_query_builds_full_months_and_excludes_test_accounts():
    query, params = build_analysis_query(
        platforms=["mt5"],
        start="2026-05-01",
        end="2026-07-13",
        lookback_months=3,
        filters={},
    )

    assert "positionCaseInsensitive(`group`, 'test') = 0" in query
    assert "positionCaseInsensitive(`group`, 'demo') = 0" in query
    assert "period_months AS" in query
    assert "positive_profit_days" in query
    assert "uniqExact(tuple(platform, login)) AS unique_account_count" in query
    assert "population_unique_accounts" in query
    assert "account_daily" in query
    assert "daily_positive_sum" in query
    assert "trade_source_coverage" in query
    assert "source_matched_min" in query
    assert params["start"] == "2026-05-01"


def test_analysis_query_always_excludes_test_accounts():
    query, _ = build_analysis_query(
        platforms=["mt5"], start="2026-05-01", end="2026-07-13", lookback_months=3,
        filters={},
    )

    assert "positionCaseInsensitive(`group`, 'test') = 0" in query
    assert "positionCaseInsensitive(`group`, 'demo') = 0" in query


def test_analysis_query_exposes_raw_and_active_deal_coverage():
    query, _ = build_analysis_query(
        platforms=["mt5"], start="2026-05-01", end="2026-07-13", lookback_months=3,
        filters={},
    )

    assert "source_deal_raw_min" in query
    assert "source_deal_raw_max" in query
    assert "source_deal_deleted_rows" in query


def test_analysis_query_uses_tuple_population_and_canonical_deals_pnl():
    query, _ = build_analysis_query(
        platforms=["mt5", "hh_mt5"], start="2026-05-01", end="2026-07-13", lookback_months=3,
        filters={},
    )

    assert "uniqExact(tuple(platform, login)) AS unique_account_count" in query
    assert "daily_pnl_square_sum" in query
    assert "daily_variance_count" in query
    assert "daily_gross_wins" in query
    assert "daily_gross_losses" in query
    assert query.count("INNER JOIN users") >= 2
    assert "coalesce(d.deal_market_pnl, 0) AS market_pnl" in query
    assert "positionCaseInsensitive(`group`, 'test') = 0" in query
    assert "positionCaseInsensitive(`group`, 'demo') = 0" in query


def test_account_detail_query_keeps_open_close_fields_for_exposure_reconstruction():
    from app.queries import build_account_detail_query

    query, _ = build_account_detail_query("mt5", 7, "2026-05-01", "2026-06-30")

    assert "entry_time" in query
    assert "exit_time" in query
    assert "entry_price" in query
    assert "volume" in query
    assert "entry_deal_id" in query
    assert "exit_deal_id" in query
    assert "turnover" in query


def test_daily_pnl_query_aggregates_utc_deals_and_reuses_account_boundaries():
    query, params = build_daily_pnl_query(
        platforms=["mt5"],
        start="2026-05-01",
        end="2026-07-13",
        filters={"groups": ["real\\FPlive"], "logins": [123]},
    )

    assert "risk.ods_mt5_users FINAL" in query
    assert "risk.ods_mt4_users FINAL" in query
    assert "FROM risk.ods_mt5_deals AS d FINAL" in query
    assert "toDate(d.time) AS trade_date" in query
    assert "action IN (0, 1)" in query
    assert "toFloat64(sumIf(profit + storage + commission + fee, action IN (0, 1))) AS client_net_pnl" in query
    assert "is_deleted = 0" in query
    assert "positionCaseInsensitive" in query
    assert "'test') = 0" in query
    assert "'demo') = 0" in query
    assert "has({login_0:Array(UInt64)}, login)" in query
    assert params["start"] == "2026-05-01"
    assert params["end_exclusive"] == "2026-07-14"


def test_analysis_query_preserves_exact_selection_and_validation_phase_grain():
    query, params = build_analysis_query(
        platforms=["mt5"], start="2026-06-01", end="2026-06-30",
        lookback_months=1, filters={},
        selection_start="2026-06-01", selection_end="2026-06-15",
        validation_start="2026-06-16", validation_end="2026-06-30",
    )

    assert "periods AS" in query
    assert "period_months AS" in query
    assert "period.phase AS phase" in query
    assert "k.phase = m.phase" in query
    assert params["selection_start"] == "2026-06-01"
    assert params["validation_start"] == "2026-06-16"


def test_daily_pnl_query_uses_clickhouse_identifier_quoting_without_backslashes():
    query, _ = build_daily_pnl_query(
        platforms=["mt5"],
        start="2026-05-01",
        end="2026-07-13",
        filters={"groups": ["real\\FPlive"]},
    )

    assert "positionCaseInsensitive(`group`, 'test') = 0" in query
    assert "positionCaseInsensitive(\\`group\\`, 'test') = 0" not in query


def test_daily_pnl_query_embeds_large_excluded_login_sets_without_http_array_params():
    query, params = build_daily_pnl_query(
        platforms=["mt5"],
        start="2026-05-01",
        end="2026-07-13",
        filters={},
        excluded_logins={("mt5", login) for login in range(3000)},
    )

    assert "NOT ((platform = 'mt5' AND login IN (0,1,2" in query
    assert "excluded_logins" not in params


def test_account_detail_query_always_filters_demo_and_test_accounts():
    from app.queries import build_account_detail_query

    query, _ = build_account_detail_query("mt5", 123, "2026-05-01", "2026-07-13")

    assert "risk.ods_mt5_users FINAL" in query
    assert "risk.ods_mt4_users FINAL" in query
    assert "positionCaseInsensitive(u.`group`, 'test') = 0" in query
    assert "positionCaseInsensitive(u.`group`, 'demo') = 0" in query


def test_analysis_and_daily_queries_include_validation_end_2026_07_16():
    analysis_query, analysis_params = build_analysis_query(
        platforms=["mt5"],
        start="2026-05-01",
        end="2026-07-16",
        lookback_months=3,
        filters={},
        selection_start="2026-05-01",
        selection_end="2026-06-30",
        validation_start="2026-07-01",
        validation_end="2026-07-16",
    )
    daily_query, daily_params = build_daily_pnl_query(
        platforms=["mt5"],
        start="2026-05-01",
        end="2026-07-16",
        filters={},
    )

    assert analysis_params["end_exclusive"] == "2026-07-17"
    assert analysis_params["validation_end_exclusive"] == "2026-07-17"
    assert daily_params["end_exclusive"] == "2026-07-17"
    assert "{validation_end_exclusive:Date}" in analysis_query
    assert "{end_exclusive:Date}" in daily_query
