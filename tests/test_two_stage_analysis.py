from datetime import datetime
from decimal import Decimal
import json
import math

from app.service import _account_period_metrics, _long_trades_ratio_pass, build_selection_funnel, build_two_stage_payload


def row(login, month, *, group="real\\FPlive", trades=0, wins=0, losses=0,
        market=0, net=0, costs=0, gross_wins=0, gross_losses=0,
        active_days=0, daily_sum=0, volume=0, turnover=0,
        daily_positive_days=None, daily_negative_days=None, daily_flat_days=None,
        daily_positive_sum=None, daily_negative_sum=None, max_positive_day=None,
        daily_stddev=0, daily_abs_sum=None, source_min=None, source_max=None,
        long_trades=None, short_trades=None):
    daily_positive_days = active_days if daily_positive_days is None else daily_positive_days
    daily_negative_days = 0 if daily_negative_days is None else daily_negative_days
    daily_flat_days = 0 if daily_flat_days is None else daily_flat_days
    daily_positive_sum = max(daily_sum, 0) if daily_positive_sum is None else daily_positive_sum
    daily_negative_sum = min(daily_sum, 0) if daily_negative_sum is None else daily_negative_sum
    max_positive_day = daily_positive_sum if max_positive_day is None else max_positive_day
    if long_trades is None and short_trades is None:
        long_trades = trades // 2
        short_trades = trades - long_trades
    elif long_trades is None or short_trades is None:
        raise ValueError("long_trades and short_trades must be provided together")
    return {
        "platform": "mt5",
        "login": login,
        "account_group": group,
        "month_start": datetime.fromisoformat(f"2026-{month}-01"),
        "matched_trades": trades,
        "winning_trades": wins,
        "losing_trades": losses,
        "matched_volume": Decimal(str(volume)),
        "matched_market_pnl": Decimal(str(market)),
        "gross_wins": Decimal(str(gross_wins)),
        "gross_losses": Decimal(str(gross_losses)),
        "deal_market_pnl": Decimal(str(market)),
        "costs": Decimal(str(costs)),
        "client_net_pnl": Decimal(str(net)),
        "funding_pnl": Decimal("0"),
        "active_trade_days": active_days,
        "daily_profit_sum": Decimal(str(daily_sum)),
        "positive_profit_days": max(active_days, 0),
        "negative_profit_days": 0,
        "flat_profit_days": 0,
        "daily_active_days": active_days,
        "daily_positive_days": daily_positive_days,
        "daily_negative_days": daily_negative_days,
        "daily_flat_days": daily_flat_days,
        "daily_positive_sum": Decimal(str(daily_positive_sum)),
        "daily_negative_sum": Decimal(str(daily_negative_sum)),
        "max_positive_day": Decimal(str(max_positive_day)),
        "daily_stddev": Decimal(str(daily_stddev)),
        "daily_abs_sum": Decimal(str(abs(daily_abs_sum if daily_abs_sum is not None else daily_sum))),
        "source_matched_min": source_min,
        "source_matched_max": source_max,
        "source_deal_min": source_min,
        "source_deal_max": source_max,
        "turnover": Decimal(str(turnover)),
        "avg_holding_seconds": Decimal("60"),
        "median_holding_seconds": Decimal("60"),
        "long_trades": long_trades,
        "short_trades": short_trades,
        "symbols_traded": 1,
    }


def daily_row(login, day, net, market=None, trades=1):
    return {
        "platform": "mt5",
        "login": login,
        "trade_date": datetime.fromisoformat(day),
        "client_net_pnl": Decimal(str(net)),
        "market_pnl": Decimal(str(market if market is not None else net)),
        "matched_trades": trades,
    }


def test_two_stage_payload_indexes_martingale_snapshot_once_for_all_accounts():
    class CountingSnapshot:
        status = "ready"
        missing_platforms = ()

        def __init__(self):
            self.index_calls = 0

        def indexed_records(self):
            self.index_calls += 1
            return {}

    snapshot = CountingSnapshot()
    result = build_two_stage_payload(
        [row(1, "05"), row(2, "05")],
        martingale_snapshot=snapshot,
        selection_start="2026-05-01", selection_end="2026-05-31",
        validation_start="2026-07-01", validation_end="2026-07-02",
        min_trades=0, min_win_rate=0, min_profit_factor=0, min_payoff_ratio=0,
        max_top1_day_profit_contribution=2, min_direction_day_rate_lower_bound=0,
        min_stability_score=0,
    )

    assert result["selection"]["counts"]["unique_accounts"] == 2
    assert snapshot.index_calls == 1


def test_selection_funnel_does_not_compare_full_account_dicts_for_membership():
    class IdentityDict(dict):
        def __eq__(self, other):
            raise AssertionError("funnel must not compare full account dictionaries")

    accounts = [
        IdentityDict(selection={"trade_count": 10}, book="abook"),
        IdentityDict(selection={"trade_count": 0}, book="bbook"),
    ]

    funnel = build_selection_funnel(accounts, min_trades=1)

    assert funnel["stages"][1]["count"] == 1
    assert funnel["stages"][1]["drop_reasons"] == {"insufficient_sample": 1}


def test_long_trades_ratio_uses_long_plus_short_and_inclusive_bounds():
    rows = [
        row(10, "05", trades=10, wins=5, losses=5, long_trades=5, short_trades=5, market=100, net=100, gross_wins=100, active_days=5, daily_sum=100),
        row(11, "05", trades=10, wins=6, losses=4, long_trades=6, short_trades=4, market=100, net=100, gross_wins=100, active_days=5, daily_sum=100),
        row(12, "05", trades=10, wins=7, losses=3, long_trades=7, short_trades=3, market=100, net=100, gross_wins=100, active_days=5, daily_sum=100),
    ]
    result = build_two_stage_payload(
        rows,
        selection_start="2026-05-01", selection_end="2026-05-31",
        validation_start="2026-07-01", validation_end="2026-07-02",
        min_trades=0, min_win_rate=0, min_profit_factor=0, min_payoff_ratio=0,
        min_long_trades_ratio=0.4, max_long_trades_ratio=0.6,
        max_top1_day_profit_contribution=2, min_direction_day_rate_lower_bound=0,
        min_stability_score=0,
    )

    accounts = {account["login"]: account for account in result["population_accounts"]}
    assert accounts[10]["selection"]["long_trades_ratio"] == 0.5
    assert accounts[11]["selection"]["long_trades_ratio"] == 0.6
    assert accounts[12]["selection"]["long_trades_ratio"] == 0.7
    assert accounts[10]["book"] == "abook"
    assert accounts[11]["book"] == "abook"
    assert accounts[12]["book"] == "bbook"
    assert "long_trades_ratio" in accounts[12]["selection_flags"]
    assert result["funnel"]["stages"][2]["name"] == "direction_balance_passed"
    assert result["funnel"]["stages"][2]["drop_reasons"] == {"long_trades_ratio": 1}


def test_long_trades_ratio_denominator_is_not_matched_trade_count():
    item = row(13, "05", trades=10, wins=7, losses=0, long_trades=7, short_trades=0, market=100, net=100, gross_wins=100, active_days=5, daily_sum=100)
    item["long_trades"] = 7
    item["short_trades"] = 0
    metrics = _account_period_metrics([item], {"platform": "mt5", "login": 13, "account_group": "real"}, phase="selection")

    assert metrics["long_trades_ratio"] == 1.0


def test_long_trades_ratio_helper_includes_configured_boundaries():
    assert _long_trades_ratio_pass({"long_trades_ratio": 0.4}, 0.4, 0.6)
    assert _long_trades_ratio_pass({"long_trades_ratio": 0.6}, 0.4, 0.6)
    assert not _long_trades_ratio_pass({"long_trades_ratio": 0.39}, 0.4, 0.6)
    assert not _long_trades_ratio_pass({"long_trades_ratio": 0.61}, 0.4, 0.6)


def test_two_stage_payload_builds_daily_book_series_and_merges_observation_into_bbook():
    rows = [
        row(1, "05", trades=20, wins=15, losses=5, market=100, net=80,
            gross_wins=100, gross_losses=0, active_days=10, daily_sum=80),
        row(1, "06", trades=20, wins=15, losses=5, market=100, net=80,
            gross_wins=100, gross_losses=0, active_days=10, daily_sum=80),
        row(2, "05", trades=20, wins=4, losses=16, market=-100, net=-80,
            gross_wins=20, gross_losses=-100, active_days=10, daily_sum=-80),
        row(2, "06", trades=20, wins=4, losses=16, market=-100, net=-80,
            gross_wins=20, gross_losses=-100, active_days=10, daily_sum=-80),
        row(3, "05", trades=1, wins=1, losses=0, market=5, net=5,
            active_days=1, daily_sum=5),
    ]
    daily = [
        daily_row(1, "2026-05-15", 30), daily_row(1, "2026-05-16", -10),
        daily_row(2, "2026-05-15", -25), daily_row(2, "2026-05-16", -5),
        daily_row(3, "2026-05-15", 7),
    ]
    result = build_two_stage_payload(
        rows, daily_rows=daily, overview_daily_rows=daily,
        selection_start="2026-05-01", selection_end="2026-06-30",
        validation_start="2026-07-01", validation_end="2026-07-02",
        min_trades=0, min_profit_factor=1, min_payoff_ratio=0,
        min_positive_month_rate=0,
        max_top1_day_profit_contribution=2,
        min_direction_day_rate_lower_bound=0, min_stability_score=0,
    )

    series = {item["date"]: item for item in result["daily_book_series"]}
    assert series["2026-05-15"]["company_net_pnl"] == -12.0
    assert series["2026-05-15"]["abook"] == {"net_pnl": 30.0, "profitable_pnl": 30.0, "loss_pnl": 0.0}
    assert series["2026-05-15"]["bbook"] == {"net_pnl": -18.0, "profitable_pnl": 7.0, "loss_pnl": -25.0}
    assert series["2026-05-16"]["bbook"]["net_pnl"] == -5.0
    assert series["2026-05-16"]["abook"]["loss_pnl"] == -10.0
    assert series["2026-06-01"]["company_net_pnl"] == 0.0
    assert result["book_performance"]["selection"]["bbook"]["accounts"] == 2


def test_daily_company_line_uses_population_rows_when_effective_rows_are_filtered():
    rows = [
        row(1, "05", trades=20, wins=15, losses=5, market=100, net=80,
            gross_wins=100, gross_losses=0, active_days=10, daily_sum=80),
    ]
    effective_daily = [daily_row(1, "2026-05-15", 30)]
    overview_daily = effective_daily + [daily_row(9, "2026-05-15", -50)]

    result = build_two_stage_payload(
        rows, daily_rows=effective_daily, overview_daily_rows=overview_daily,
        selection_start="2026-05-01", selection_end="2026-05-31",
        validation_start="2026-07-01", validation_end="2026-07-02",
        min_trades=0, min_profit_factor=1, min_payoff_ratio=0,
        min_positive_month_rate=0,
        max_top1_day_profit_contribution=2,
        min_direction_day_rate_lower_bound=0, min_stability_score=0,
    )

    assert result["daily_book_series"][14]["company_net_pnl"] == 20.0


def test_two_stage_analysis_separates_selection_and_validation_and_keeps_inactive_users():
    rows = [
        row(1, "05", trades=20, wins=15, losses=5, market=120, net=110,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=110),
        row(1, "06", trades=20, wins=14, losses=6, market=100, net=90,
            gross_wins=160, gross_losses=-60, active_days=8, daily_sum=90),
        # Login 1 has no July row in the source; service must materialize it as inactive.
        row(2, "05", trades=20, wins=4, losses=16, market=-120, net=-110,
            gross_wins=40, gross_losses=-160, active_days=10, daily_sum=-110),
        row(2, "06", trades=20, wins=5, losses=15, market=-100, net=-90,
            gross_wins=50, gross_losses=-150, active_days=8, daily_sum=-90),
        row(2, "07", trades=2, wins=1, losses=1, market=-20, net=-20,
            gross_wins=5, gross_losses=-25, active_days=1, daily_sum=-20),
        row(3, "05", trades=2, wins=1, losses=1, market=5, net=4,
            gross_wins=10, gross_losses=-5, active_days=1, daily_sum=4),
        row(3, "06", trades=2, wins=1, losses=1, market=5, net=4,
            gross_wins=10, gross_losses=-5, active_days=1, daily_sum=4),
        row(4, "05", group="real\\TEST_USD", trades=20, wins=15, losses=5,
            market=120, net=100, gross_wins=180, gross_losses=-60,
            active_days=10, daily_sum=100),
        row(4, "06", group="real\\TEST_USD", trades=20, wins=15, losses=5,
            market=120, net=100, gross_wins=180, gross_losses=-60,
            active_days=10, daily_sum=100),
    ]

    result = build_two_stage_payload(
        rows,
        selection_start="2026-05-01",
        selection_end="2026-06-30",
        validation_start="2026-07-01",
        validation_end="2026-07-13",
        min_trades=20,
        min_profit_factor=1,
        min_positive_month_rate=0,
        max_top1_day_profit_contribution=1,
        min_direction_day_rate_lower_bound=0,
        min_stability_score=0,
    )

    assert result["selection"]["counts"] == {
        "eligible_accounts": 3,
        "unique_accounts": 3,
        "account_rows": 3,
        "abook": 1,
        "bbook": 2,
        "martingale_bbook": 0,
        "personal_abook": 0,
        "personal_bbook_martingale": 0,
    }
    accounts = {account["login"]: account for account in result["accounts"]}
    assert accounts[1]["book"] == "abook"
    assert accounts[1]["validation_status"] == "inactive"
    assert accounts[2]["book"] == "bbook"
    assert accounts[2]["transition_status"] == "continued_loss"
    assert 4 not in accounts
    assert result["validation"]["groups"]["abook"]["active_accounts"] == 0


def test_two_stage_analysis_reports_transition_precision_lift_and_abook_delta():
    rows = [
        row(1, "05", trades=20, wins=15, losses=5, market=120, net=110,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=110),
        row(1, "06", trades=20, wins=14, losses=6, market=100, net=90,
            gross_wins=160, gross_losses=-60, active_days=8, daily_sum=90),
        row(1, "07", trades=10, wins=8, losses=2, market=80, net=70,
            gross_wins=100, gross_losses=-20, active_days=5, daily_sum=70),
        row(2, "05", trades=20, wins=4, losses=16, market=-120, net=-110,
            gross_wins=40, gross_losses=-160, active_days=10, daily_sum=-110),
        row(2, "06", trades=20, wins=5, losses=15, market=-100, net=-90,
            gross_wins=50, gross_losses=-150, active_days=8, daily_sum=-90),
        row(2, "07", trades=10, wins=2, losses=8, market=-70, net=-60,
            gross_wins=20, gross_losses=-90, active_days=5, daily_sum=-60),
        row(3, "05", trades=20, wins=10, losses=10, market=20, net=5,
            gross_wins=50, gross_losses=-30, active_days=5, daily_sum=5),
        row(3, "06", trades=20, wins=10, losses=10, market=20, net=5,
            gross_wins=50, gross_losses=-30, active_days=5, daily_sum=5),
        row(3, "07", trades=10, wins=6, losses=4, market=40, net=30,
            gross_wins=60, gross_losses=-20, active_days=5, daily_sum=30),
    ]

    result = build_two_stage_payload(
        rows,
        selection_start="2026-05-01",
        selection_end="2026-06-30",
        validation_start="2026-07-01",
        validation_end="2026-07-13",
        min_trades=20,
        min_profit_factor=1,
        min_positive_month_rate=0,
        max_top1_day_profit_contribution=1,
        min_direction_day_rate_lower_bound=0,
        min_stability_score=0,
    )

    assert result["transitions"]["abook"]["continued_profitable"] == 2
    assert result["transitions"]["bbook"]["continued_loss"] == 1
    assert result["validation"]["groups"]["abook"]["precision"] == 1.0
    assert result["validation"]["groups"]["bbook"]["precision"] == 1.0
    assert result["profit_impact"]["abook"]["selection_client_net_pnl"] == 210.0
    assert result["profit_impact"]["abook"]["incremental_change"] == 210.0
    assert result["profit_impact"]["bbook"]["incremental_change"] == -200.0
    json.dumps(result, allow_nan=False)


def test_two_stage_payload_preserves_zero_pnl_population_for_strict_abook_precision():
    rows = [
        row(101, "05", trades=2, wins=2, losses=0, market=20, net=20,
            gross_wins=20, active_days=1, daily_sum=20),
        row(101, "07", trades=1, wins=1, losses=0, market=5, net=5,
            gross_wins=5, active_days=1, daily_sum=5),
        row(102, "05"),
    ]

    result = build_two_stage_payload(
        rows,
        selection_start="2026-05-01", selection_end="2026-06-30",
        validation_start="2026-07-01", validation_end="2026-07-13",
        min_trades=0, min_win_rate=0, min_profit_factor=0, min_payoff_ratio=0,
        max_top1_day_profit_contribution=2, min_direction_day_rate_lower_bound=0,
        min_stability_score=0, personal_candidate_logins={102},
    )

    population_abook = [account for account in result["population_accounts"] if account["book"] == "abook"]
    display_abook = [account for account in result["accounts"] if account["book"] == "abook"]
    assert len(population_abook) == 2
    assert len(display_abook) == 1
    assert result["validation"]["groups"]["abook"]["strict_positive_accounts"] == 1
    assert result["validation"]["groups"]["abook"]["strict_positive_account_rate"] == 0.5


def test_two_stage_analysis_requires_win_rate_monthly_consistency_and_risk_concentration():
    strong = [
        row(21, "05", trades=20, wins=14, losses=6, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100,
            volume=100),
        row(21, "06", trades=20, wins=13, losses=7, market=80, net=60,
            gross_wins=150, gross_losses=-50, active_days=10, daily_sum=60,
            volume=100),
    ]
    weak_month = [
        row(22, "05", trades=20, wins=14, losses=6, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100,
            volume=100),
        row(22, "06", trades=20, wins=13, losses=7, market=20, net=20,
            gross_wins=150, gross_losses=-130, active_days=10, daily_sum=20,
            volume=100),
    ]
    oversized = [
        row(23, "05", trades=20, wins=14, losses=6, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100,
            volume=100, max_positive_day=150),
        row(23, "06", trades=20, wins=13, losses=7, market=80, net=60,
            gross_wins=150, gross_losses=-50, active_days=10, daily_sum=60,
            volume=100, max_positive_day=150),
    ]
    result = build_two_stage_payload(
        strong + weak_month + oversized,
        selection_start="2026-05-01", selection_end="2026-06-30",
        validation_start="2026-07-01", validation_end="2026-07-13",
        min_trades=20, min_profit_factor=1,
        min_win_rate=0.6,
        min_selection_monthly_consistency=0.5,
        min_positive_month_rate=0, max_top1_day_profit_contribution=0.75,
        min_direction_day_rate_lower_bound=0, min_stability_score=0,
    )

    accounts = {account["login"]: account for account in result["accounts"]}
    assert accounts[21]["book"] == "abook"
    assert accounts[22]["book"] == "bbook"
    assert accounts[23]["book"] == "bbook"
    assert accounts[21]["selection"]["monthly_consistency_ratio"] == 0.6


def test_two_stage_analysis_requires_strict_top1_contribution_and_skips_leverage_for_zero_balance():
    diffuse = [
        row(31, "05", trades=20, wins=15, losses=5, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100,
            daily_positive_sum=100, max_positive_day=19),
        row(31, "06", trades=20, wins=14, losses=6, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100,
            daily_positive_sum=100, max_positive_day=19),
    ]
    exactly_twenty = [
        row(32, "05", trades=20, wins=15, losses=5, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100,
            daily_positive_sum=100, max_positive_day=40),
        row(32, "06", trades=20, wins=14, losses=6, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100,
            daily_positive_sum=100, max_positive_day=40),
    ]
    high_leverage = [
        row(33, "05", trades=20, wins=15, losses=5, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100,
            daily_positive_sum=100, max_positive_day=19),
        row(33, "06", trades=20, wins=14, losses=6, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100,
            daily_positive_sum=100, max_positive_day=19),
    ]
    zero_balance = [
        row(34, "05", trades=20, wins=15, losses=5, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100,
            daily_positive_sum=100, max_positive_day=19),
        row(34, "06", trades=20, wins=14, losses=6, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100,
            daily_positive_sum=100, max_positive_day=19),
    ]
    for item in high_leverage:
        item.update(risk_balance_prev_month=100, risk_balance_status="positive", risk_leverage_p95_ratio=8)
    for item in zero_balance:
        item.update(risk_balance_prev_month=0, risk_balance_status="unknown_nonpositive_balance", risk_leverage_p95_ratio=None)

    result = build_two_stage_payload(
        diffuse + exactly_twenty + high_leverage + zero_balance,
        selection_start="2026-05-01", selection_end="2026-06-30",
        validation_start="2026-07-01", validation_end="2026-07-13",
        min_trades=20, min_profit_factor=1,
        min_win_rate=0.5,
        min_selection_monthly_consistency=0.5,
        max_top1_day_profit_contribution=0.2, max_peak_leverage_ratio=5,
        max_high_leverage_holding_seconds=0,
        min_positive_month_rate=0, min_direction_day_rate_lower_bound=0,
        min_stability_score=0,
    )

    accounts = {account["login"]: account for account in result["accounts"]}
    assert accounts[31]["book"] == "abook"
    assert accounts[32]["book"] == "bbook"
    assert accounts[33]["book"] == "bbook"
    assert accounts[34]["book"] == "abook"


def test_abook_selection_uses_trade_quality_but_not_active_days_or_removed_quality_gates():
    rows = [
            row(101, "05", trades=1, wins=1, losses=0, long_trades=1, short_trades=1, market=10, net=10,
                gross_wins=10, gross_losses=0, active_days=1, daily_sum=10),
            row(101, "06", trades=1, wins=1, losses=0, long_trades=1, short_trades=1, market=12, net=12,
                gross_wins=12, gross_losses=0, active_days=1, daily_sum=12),
    ]

    result = build_two_stage_payload(
        rows,
        selection_start="2026-05-01",
        selection_end="2026-06-30",
        validation_start="2026-07-01",
        validation_end="2026-07-02",
        min_trades=2,
        min_win_rate=0,
        min_profit_factor=1,
        min_payoff_ratio=0,
        min_positive_month_rate=1,
        min_selection_monthly_consistency=1,
        max_top1_day_profit_contribution=2,
        max_daily_profit_month_contribution=0.01,
        min_direction_day_rate_lower_bound=1,
        min_stability_score=100,
    )

    account = result["accounts"][0]
    assert account["book"] == "abook"
    assert account["selection_months_positive"] is True


def test_abook_selection_does_not_require_positive_selection_months():
    rows = [
            row(102, "05", trades=1, wins=1, losses=0, long_trades=1, short_trades=1, market=10, net=10,
                gross_wins=10, gross_losses=0, active_days=1, daily_sum=10),
            row(102, "06", trades=1, wins=1, losses=0, long_trades=1, short_trades=1, market=-5, net=-5,
                gross_wins=10, gross_losses=0, active_days=1, daily_sum=-5),
    ]

    result = build_two_stage_payload(
        rows,
        selection_start="2026-05-01",
        selection_end="2026-06-30",
        validation_start="2026-07-01",
        validation_end="2026-07-02",
        min_trades=2,
        min_win_rate=0,
        min_profit_factor=1,
        min_payoff_ratio=0,
        min_positive_month_rate=0,
        min_selection_monthly_consistency=0,
        max_top1_day_profit_contribution=2,
        max_daily_profit_month_contribution=1,
        min_direction_day_rate_lower_bound=0,
        min_stability_score=0,
    )

    account = result["accounts"][0]
    assert account["selection"]["client_net_pnl"] > 0
    assert account["selection_months_positive"] is False
    assert account["book"] == "abook"
    assert "selection_monthly_positive" in result["rules"]["removed_selection_rules"]
    assert "selection_period_client_net_pnl" in result["rules"]["removed_selection_rules"]


def test_july_new_user_is_tagged_but_not_routed_by_new_user_status():
    rows = [
        row(104, "05", trades=0, wins=0, losses=0, market=0, net=0),
        row(104, "06", trades=0, wins=0, losses=0, market=0, net=0),
        row(104, "07", trades=30, wins=25, losses=5, market=300, net=300,
            gross_wins=350, gross_losses=-50, active_days=10, daily_sum=300),
    ]

    result = build_two_stage_payload(
        rows,
        selection_start="2026-05-01",
        selection_end="2026-06-30",
        validation_start="2026-07-01",
        validation_end="2026-07-16",
        min_trades=2,
        min_win_rate=0,
        min_profit_factor=0,
        min_payoff_ratio=0,
        max_top1_day_profit_contribution=2,
    )

    account = result["accounts"][0]
    assert account["july_new_user"] is True
    assert account["book"] == "bbook"
    assert account["selection_source"] == "abook_rules_failed"


def test_two_stage_analysis_allows_short_holding_high_leverage_exception_only():
    short_hold = [
        row(35, "05", trades=20, wins=15, losses=5, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100),
        row(35, "06", trades=20, wins=14, losses=6, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100),
    ]
    long_hold = [
        row(36, "05", trades=20, wins=15, losses=5, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100),
        row(36, "06", trades=20, wins=14, losses=6, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100),
    ]
    for item in short_hold:
        item.update(risk_balance_prev_month=100, risk_balance_status="positive",
                    risk_leverage_p95_ratio=250, selection_median_holding_seconds=60)
    for item in long_hold:
        item.update(risk_balance_prev_month=100, risk_balance_status="positive",
                    risk_leverage_p95_ratio=250, selection_median_holding_seconds=600)

    result = build_two_stage_payload(
        short_hold + long_hold,
        selection_start="2026-05-01", selection_end="2026-06-30",
        validation_start="2026-07-01", validation_end="2026-07-13",
        min_trades=20, min_profit_factor=1,
        min_win_rate=0.5, min_payoff_ratio=0.8,
        min_selection_monthly_consistency=0, min_positive_month_rate=0,
        max_top1_day_profit_contribution=1, max_peak_leverage_ratio=200,
        max_high_leverage_holding_seconds=300,
        min_direction_day_rate_lower_bound=0, min_stability_score=0,
    )

    accounts = {account["login"]: account for account in result["accounts"]}
    assert accounts[35]["book"] == "abook"
    assert accounts[36]["book"] == "bbook"


def test_two_stage_analysis_keeps_test_accounts_excluded():
    rows = [row(99, "05", group="real\\TEST", trades=20, wins=15, losses=5, market=120, net=110,
                gross_wins=180, gross_losses=-60, active_days=10, daily_sum=110)]
    rows.append(row(99, "06", group="real\\TEST", trades=20, wins=15, losses=5, market=120, net=110,
                    gross_wins=180, gross_losses=-60, active_days=10, daily_sum=110))

    result = build_two_stage_payload(
        rows,
        selection_start="2026-05-01", selection_end="2026-06-30",
        validation_start="2026-07-01", validation_end="2026-07-13",
        min_positive_month_rate=0,
        max_top1_day_profit_contribution=1,
        min_direction_day_rate_lower_bound=0,
        min_stability_score=0,
    )

    assert result["selection"]["counts"]["eligible_accounts"] == 0


def test_two_stage_analysis_excludes_demo_accounts_by_default():
    rows = [row(100, "05", group="demo\\HHdemo\\forexhh-USD", trades=20, wins=15, losses=5,
                market=120, net=110, gross_wins=180, gross_losses=-60, active_days=10, daily_sum=110)]

    result = build_two_stage_payload(
        rows,
        selection_start="2026-05-01", selection_end="2026-06-30",
        validation_start="2026-07-01", validation_end="2026-07-13",
        min_positive_month_rate=0,
        max_top1_day_profit_contribution=1,
        min_direction_day_rate_lower_bound=0,
        min_stability_score=0,
    )

    assert result["selection"]["counts"]["eligible_accounts"] == 0


def test_two_stage_analysis_reports_raw_and_active_deal_coverage_separately():
    item = row(101, "05", source_min="2026-05-15", source_max="2026-07-14")
    item["source_deal_raw_min"] = "2023-08-08"
    item["source_deal_raw_max"] = "2026-07-14"
    item["source_deal_deleted_rows"] = 123

    result = build_two_stage_payload(
        [item],
        selection_start="2026-05-01", selection_end="2026-06-30",
        validation_start="2026-07-01", validation_end="2026-07-13",
    )

    assert result["coverage"]["source_min"] == "2026-05-15"
    assert result["coverage"]["source_deal_raw_min"] == "2023-08-08"
    assert result["coverage"]["source_deal_deleted_rows"] == 123
    assert result["coverage"]["historical_deals_before_active"] is True


def test_two_stage_analysis_exposes_monthly_company_profit_and_july_book_split():
    rows = [
        row(1, "05", trades=20, wins=15, losses=5, market=120, net=110,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=110),
        row(1, "06", trades=20, wins=14, losses=6, market=100, net=90,
            gross_wins=160, gross_losses=-60, active_days=8, daily_sum=90),
        row(1, "07", trades=10, wins=8, losses=2, market=80, net=70,
            gross_wins=100, gross_losses=-20, active_days=5, daily_sum=70),
        row(2, "05", trades=20, wins=4, losses=16, market=-120, net=-110,
            gross_wins=40, gross_losses=-160, active_days=10, daily_sum=-110),
        row(2, "06", trades=20, wins=5, losses=15, market=-100, net=-90,
            gross_wins=50, gross_losses=-150, active_days=8, daily_sum=-90),
        row(2, "07", trades=10, wins=2, losses=8, market=-70, net=-60,
            gross_wins=20, gross_losses=-90, active_days=5, daily_sum=-60),
    ]
    result = build_two_stage_payload(
        rows,
        selection_start="2026-05-01", selection_end="2026-06-30",
        validation_start="2026-07-01", validation_end="2026-07-13",
        min_trades=2,
        min_win_rate=0.5,
        min_profit_factor=1,
        min_payoff_ratio=0.8,
        min_positive_month_rate=0,
        max_top1_day_profit_contribution=1,
        min_direction_day_rate_lower_bound=0,
        min_stability_score=0,
    )

    monthly = {item["month"]: item for item in result["profit_overview"]["monthly"]}
    assert monthly["2026-05"]["company_bbook_profit"] == 0.0
    assert monthly["2026-06"]["company_bbook_profit"] == 0.0
    assert result["profit_overview"]["july_book_split"]["abook"]["user_net_pnl"] == 70.0
    assert result["profit_overview"]["july_book_split"]["bbook"]["company_profit"] == 60.0


def test_profit_overview_uses_stable_unfiltered_population_rows():
    selected_rows = [
        row(201, "05", trades=20, wins=15, losses=5, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100),
        row(201, "06", trades=20, wins=14, losses=6, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100),
    ]
    overview_rows = selected_rows + [
        row(202, "05", trades=20, wins=5, losses=15, market=-60, net=-50,
            gross_wins=50, gross_losses=-100, active_days=10, daily_sum=-50),
        row(202, "06", trades=20, wins=6, losses=14, market=-60, net=-50,
            gross_wins=60, gross_losses=-100, active_days=10, daily_sum=-50),
    ]

    result = build_two_stage_payload(
        selected_rows,
        overview_rows=overview_rows,
        selection_start="2026-05-01", selection_end="2026-06-30",
        validation_start="2026-07-01", validation_end="2026-07-13",
        min_trades=20, min_profit_factor=1,
        min_positive_month_rate=0,
        max_top1_day_profit_contribution=1,
        min_direction_day_rate_lower_bound=0, min_stability_score=0,
    )

    monthly = {item["month"]: item for item in result["profit_overview"]["monthly"]}
    assert monthly["2026-05"]["user_net_pnl"] == 50.0
    assert monthly["2026-06"]["user_net_pnl"] == 50.0
    assert monthly["2026-05"]["active_accounts"] == 2
    assert result["selection"]["counts"]["eligible_accounts"] == 2


def test_two_stage_analysis_uses_source_unique_account_count_and_hides_zero_pnl_accounts():
    zero = row(9, "05")
    zero["population_unique_accounts"] = 1
    zero["population_account_rows"] = 1
    active = row(9, "06", trades=1, wins=1, losses=0, market=5, net=5,
                 gross_wins=5, gross_losses=0, active_days=1, daily_sum=5)
    active["population_unique_accounts"] = 1
    active["population_account_rows"] = 1
    result = build_two_stage_payload(
        [zero, active],
        selection_start="2026-05-01", selection_end="2026-06-30",
        validation_start="2026-07-01", validation_end="2026-07-13",
    )

    assert result["selection"]["counts"]["unique_accounts"] == 1
    assert [account["login"] for account in result["accounts"]] == [9]


def test_two_stage_analysis_scores_stability_and_penalizes_one_day_concentration():
    rows = [
        row(10, "05", trades=40, wins=26, losses=14, market=150, net=130,
            gross_wins=240, gross_losses=-90, active_days=12, daily_sum=130,
            daily_positive_days=8, daily_negative_days=4, daily_positive_sum=120,
            daily_negative_sum=-30, max_positive_day=150, daily_stddev=32,
            daily_abs_sum=150, source_min="2026-05-15", source_max="2026-07-14"),
        row(10, "06", trades=40, wins=26, losses=14, market=150, net=130,
            gross_wins=240, gross_losses=-90, active_days=12, daily_sum=130,
            daily_positive_days=8, daily_negative_days=4, daily_positive_sum=120,
            daily_negative_sum=-30, max_positive_day=150, daily_stddev=32,
            daily_abs_sum=150, source_min="2026-05-15", source_max="2026-07-14"),
        row(11, "05", trades=40, wins=26, losses=14, market=150, net=130,
            gross_wins=240, gross_losses=-90, active_days=12, daily_sum=130,
            daily_positive_days=8, daily_negative_days=4, daily_positive_sum=120,
            daily_negative_sum=-30, max_positive_day=30, daily_stddev=12,
            daily_abs_sum=150, source_min="2026-05-15", source_max="2026-07-14"),
        row(11, "06", trades=40, wins=26, losses=14, market=150, net=130,
            gross_wins=240, gross_losses=-90, active_days=12, daily_sum=130,
            daily_positive_days=8, daily_negative_days=4, daily_positive_sum=120,
            daily_negative_sum=-30, max_positive_day=30, daily_stddev=12,
            daily_abs_sum=150, source_min="2026-05-15", source_max="2026-07-14"),
    ]

    result = build_two_stage_payload(
        rows,
        selection_start="2026-05-01", selection_end="2026-06-30",
        validation_start="2026-07-01", validation_end="2026-07-13",
        min_trades=20,
    )

    accounts = {account["login"]: account for account in result["accounts"]}
    concentrated = accounts[10]
    stable = accounts[11]
    assert concentrated["stability"]["top_positive_day_concentration"] > 0.5
    assert "profit_concentration" in concentrated["selection_flags"]
    assert stable["stability"]["score"] > concentrated["stability"]["score"]
    assert stable["confidence_tier"] in {"medium", "high"}
    assert result["selection"]["stability_overview"]["bbook"] == 1
    assert result["coverage"]["source_min"] == "2026-05-15"
    assert "2026-05" in result["coverage"]["partial_months"]


def test_two_stage_analysis_only_deploys_stable_candidates_and_uses_active_precision():
    rows = [
        row(21, "05", trades=60, wins=42, losses=18, market=560, net=500,
            gross_wins=900, gross_losses=-250, active_days=40, daily_sum=500,
            daily_positive_days=34, daily_negative_days=6, daily_positive_sum=550,
            daily_negative_sum=-50, max_positive_day=80, daily_stddev=18,
            daily_abs_sum=600),
        row(21, "06", trades=60, wins=42, losses=18, market=560, net=500,
            gross_wins=900, gross_losses=-250, active_days=40, daily_sum=500,
            daily_positive_days=34, daily_negative_days=6, daily_positive_sum=550,
            daily_negative_sum=-50, max_positive_day=80, daily_stddev=18,
            daily_abs_sum=600),
        row(21, "07", trades=30, wins=22, losses=8, market=250, net=150,
            gross_wins=400, gross_losses=-120, active_days=20, daily_sum=150,
            daily_positive_days=12, daily_negative_days=8, daily_positive_sum=190,
            daily_negative_sum=-40, max_positive_day=30, daily_stddev=15,
            daily_abs_sum=230),
        row(22, "05", trades=60, wins=42, losses=18, market=560, net=500,
            gross_wins=900, gross_losses=-250, active_days=40, daily_sum=600,
            daily_positive_days=25, daily_negative_days=15, daily_positive_sum=560,
            daily_negative_sum=-60, max_positive_day=200, daily_stddev=40,
            daily_abs_sum=620),
        row(22, "06", trades=60, wins=42, losses=18, market=560, net=-100,
            gross_wins=900, gross_losses=-250, active_days=40, daily_sum=300,
            daily_positive_days=15, daily_negative_days=25, daily_positive_sum=100,
            daily_negative_sum=-200, max_positive_day=80, daily_stddev=40,
            daily_abs_sum=300),
        row(22, "07", trades=30, wins=8, losses=22, market=-200, net=-150,
            gross_wins=120, gross_losses=-400, active_days=20, daily_sum=-150,
            daily_positive_days=6, daily_negative_days=14, daily_positive_sum=40,
            daily_negative_sum=-190, max_positive_day=20, daily_stddev=20,
            daily_abs_sum=230),
        row(23, "05", trades=60, wins=18, losses=42, market=-560, net=-500,
            gross_wins=250, gross_losses=-900, active_days=40, daily_sum=-500,
            daily_positive_days=6, daily_negative_days=34, daily_positive_sum=50,
            daily_negative_sum=-550, max_positive_day=20, daily_stddev=18,
            daily_abs_sum=600),
        row(23, "06", trades=60, wins=18, losses=42, market=-560, net=-500,
            gross_wins=250, gross_losses=-900, active_days=40, daily_sum=-500,
            daily_positive_days=6, daily_negative_days=34, daily_positive_sum=50,
            daily_negative_sum=-550, max_positive_day=20, daily_stddev=18,
            daily_abs_sum=600),
        row(23, "07", trades=30, wins=8, losses=22, market=-200, net=-150,
            gross_wins=120, gross_losses=-400, active_days=20, daily_sum=-150,
            daily_positive_days=6, daily_negative_days=14, daily_positive_sum=40,
            daily_negative_sum=-190, max_positive_day=20, daily_stddev=20,
            daily_abs_sum=230),
    ]

    result = build_two_stage_payload(
        rows,
        selection_start="2026-05-01", selection_end="2026-06-30",
        validation_start="2026-07-01", validation_end="2026-07-13",
        min_selection_monthly_consistency=0.5,
    )

    assert result["selection"]["counts"]["abook"] == 1
    assert result["selection"]["counts"]["bbook"] == 2
    accounts = {account["login"]: account for account in result["accounts"]}
    assert "monthly_consistency" in accounts[22]["selection_flags"]
    assert result["validation"]["groups"]["abook"]["precision"] == 1.0
    assert result["validation"]["groups"]["abook"]["active_positive_account_rate"] == 1.0
    assert result["validation"]["groups"]["bbook"]["active_negative_account_rate"] == 1.0


def test_two_stage_analysis_uses_exact_cross_period_statistics():
    first = row(500, "05", trades=2, wins=2, losses=0, market=4, net=4, active_days=2, daily_sum=4)
    second = row(500, "06", trades=1, wins=1, losses=0, market=5, net=5, active_days=1, daily_sum=5)
    for item in (first, second):
        item["market_pnl"] = item["deal_market_pnl"]
        item["selection_median_holding_seconds"] = Decimal("42") if item is first else Decimal("90")
        item["selection_symbols_traded"] = 3
    first["daily_pnl_sum_for_variance"] = Decimal("4")
    first["daily_pnl_square_sum"] = Decimal("10")
    first["daily_variance_count"] = 2
    second["daily_pnl_sum_for_variance"] = Decimal("5")
    second["daily_pnl_square_sum"] = Decimal("25")
    second["daily_variance_count"] = 1
    first["median_holding_seconds"] = Decimal("10")
    second["median_holding_seconds"] = Decimal("99")
    first["symbols_traded"] = 1
    second["symbols_traded"] = 2

    metrics = _account_period_metrics(
        [first, second],
        {"platform": "mt5", "login": 500, "account_group": "real"},
        phase="selection",
    )

    assert math.isclose(metrics["daily_stddev"], math.sqrt(8 / 3), rel_tol=1e-9)
    assert metrics["median_holding_seconds"] == 42.0
    assert metrics["symbols_traded"] == 3


def test_two_stage_analysis_exposes_book_performance_and_finite_payload():
    profitable = row(501, "05", trades=20, wins=15, losses=5, market=120, net=100,
                     gross_wins=150, gross_losses=-50, active_days=10, daily_sum=100)
    profitable["market_pnl"] = profitable["deal_market_pnl"]
    profitable["daily_stddev"] = 10
    profitable["daily_pnl_sum_for_variance"] = Decimal("100")
    profitable["daily_pnl_square_sum"] = Decimal("1000")
    profitable["daily_variance_count"] = 10
    profitable_2 = dict(profitable, month_start=datetime(2026, 6, 1))
    losing = row(502, "05", trades=20, wins=5, losses=15, market=-120, net=-100,
                 gross_wins=50, gross_losses=-150, active_days=10, daily_sum=-100)
    losing["market_pnl"] = losing["deal_market_pnl"]
    losing["daily_pnl_sum_for_variance"] = Decimal("-100")
    losing["daily_pnl_square_sum"] = Decimal("1000")
    losing["daily_variance_count"] = 10
    losing_2 = dict(losing, month_start=datetime(2026, 6, 1))
    zero_variance = row(503, "05")
    zero_variance["daily_stddev"] = float("nan")
    zero_variance["daily_active_days"] = 0
    zero_variance["active_trade_days"] = 0
    zero_variance_2 = dict(zero_variance, month_start=datetime(2026, 6, 1))

    result = build_two_stage_payload(
        [profitable, profitable_2, losing, losing_2, zero_variance, zero_variance_2],
        selection_start="2026-05-01", selection_end="2026-06-30",
        validation_start="2026-07-01", validation_end="2026-07-13",
        min_trades=20, min_profit_factor=1,
        min_positive_month_rate=0,
        max_top1_day_profit_contribution=1, min_direction_day_rate_lower_bound=0,
        min_stability_score=0,
    )

    assert result["book_performance"]["selection"]["abook"]["net_pnl"] == 200.0
    assert result["book_performance"]["selection"]["abook"]["gross_profit"] == 300.0
    assert result["book_performance"]["selection"]["abook"]["gross_loss"] == -100.0
    assert result["book_performance"]["selection"]["bbook"]["net_pnl"] == -200.0
    assert result["book_performance"]["selection"]["abook"]["theoretical_increment"] == 200.0
    assert result["book_performance"]["selection"]["bbook"]["theoretical_increment"] == -200.0
    json.dumps(result, allow_nan=False)


def test_personal_candidate_list_forces_union_without_duplicate_account_impact():
    rows = [
        row(601, "05", trades=20, wins=15, losses=5, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100),
        row(601, "06", trades=20, wins=15, losses=5, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100),
        row(601, "07", trades=10, wins=8, losses=2, market=40, net=30,
            gross_wins=60, gross_losses=-20, active_days=5, daily_sum=30),
        row(602, "05", trades=20, wins=5, losses=15, market=-120, net=-100,
            gross_wins=50, gross_losses=-160, active_days=10, daily_sum=-100),
        row(602, "06", trades=20, wins=5, losses=15, market=-120, net=-100,
            gross_wins=50, gross_losses=-160, active_days=10, daily_sum=-100),
        row(602, "07", trades=10, wins=8, losses=2, market=25, net=20,
            gross_wins=50, gross_losses=-15, active_days=5, daily_sum=20),
    ]

    result = build_two_stage_payload(
        rows,
        personal_candidate_logins={601, 602},
        personal_candidate_info={"enabled": True, "status": "ready", "unique_logins": 2},
        selection_start="2026-05-01", selection_end="2026-06-30",
        validation_start="2026-07-01", validation_end="2026-07-13",
        min_trades=20, min_profit_factor=1,
        min_positive_month_rate=0,
        max_top1_day_profit_contribution=1,
        min_direction_day_rate_lower_bound=0, min_stability_score=0,
    )

    assert result["selection"]["counts"]["abook"] == 2
    assert result["selection"]["counts"]["personal_abook"] == 2
    assert result["personal_candidate_list"]["added_accounts"] == 1
    assert result["personal_candidate_list"]["overlap_accounts"] == 1
    assert result["profit_impact"]["abook"]["validation_incremental_change"] == 50.0
    assert len([account for account in result["accounts"] if account["login"] == 601]) == 1


def test_news_candidate_list_joins_existing_abook_rules_without_duplicate_account():
    rows = [
        row(701, "05", trades=20, wins=15, losses=5, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100),
        row(701, "06", trades=20, wins=15, losses=5, market=120, net=100,
            gross_wins=180, gross_losses=-60, active_days=10, daily_sum=100),
        row(702, "05", trades=2, wins=1, losses=1, market=20, net=10,
            gross_wins=30, gross_losses=-20, active_days=1, daily_sum=10),
        row(702, "06", trades=2, wins=1, losses=1, market=20, net=10,
            gross_wins=30, gross_losses=-20, active_days=1, daily_sum=10),
    ]

    result = build_two_stage_payload(
        rows,
        news_candidate_logins={701, 702},
        news_candidate_info={"enabled": True, "status": "ready", "unique_logins": 2},
        selection_start="2026-05-01", selection_end="2026-06-30",
        validation_start="2026-07-01", validation_end="2026-07-13",
        min_trades=20, min_profit_factor=1,
        min_positive_month_rate=0,
        max_top1_day_profit_contribution=1,
        min_direction_day_rate_lower_bound=0, min_stability_score=0,
    )

    assert result["selection"]["counts"]["abook"] == 2
    assert result["selection"]["counts"]["news_abook"] == 2
    assert result["news_candidate_list"]["added_accounts"] == 1
    assert result["news_candidate_list"]["overlap_accounts"] == 1
    assert result["news_candidate_list"]["matched_accounts"] == 2
    assert len([account for account in result["accounts"] if account["login"] == 701]) == 1
    assert len([account for account in result["accounts"] if account["login"] == 702]) == 1
