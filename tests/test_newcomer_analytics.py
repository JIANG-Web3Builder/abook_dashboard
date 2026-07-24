from datetime import date
from types import SimpleNamespace

from app.newcomer_analytics import (
    build_newcomer_account_sensitivity,
    build_newcomer_analytics_payload,
    evaluate_newcomer_account,
)


def _rules(**overrides):
    base = dict(
        min_trades=75,
        min_win_rate=0.5,
        min_profit_factor=1.25,
        min_payoff_ratio=0.6,
        min_long_trades_ratio=0.3,
        max_long_trades_ratio=0.7,
        max_top1_day_profit_contribution=0.3,
        max_leverage_p95_ratio=2000,
        max_high_leverage_holding_seconds=60,
        excluded_martingale_levels=["extreme", "high", "medium", "low"],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _request(**rule_overrides):
    return SimpleNamespace(
        selection=SimpleNamespace(start=date(2026, 5, 1), end=date(2026, 6, 30)),
        validation=SimpleNamespace(start=date(2026, 7, 1), end=date(2026, 7, 22)),
        rules=_rules(**rule_overrides),
    )


def _account(login: int, *, book="bbook", martingale_hard_block=False, **extra):
    payload = {
        "platform": "mt5",
        "login": login,
        "account_group": "real\\std",
        "book": book,
        "martingale_hard_block": martingale_hard_block,
        "risk_balance_status": "not_available",
        "selection": {},
    }
    payload.update(extra)
    return payload


def _day(
    day: str,
    trades: int,
    *,
    login: int = 1,
    wins=None,
    losses=None,
    long=None,
    short=None,
    pnl=10.0,
    gross_wins=None,
    gross_losses=None,
):
    wins = trades if wins is None else wins
    losses = 0 if losses is None else losses
    long = trades if long is None else long
    short = 0 if short is None else short
    return {
        "platform": "mt5",
        "login": login,
        "trade_date": day,
        "matched_trades": trades,
        "winning_trades": wins,
        "losing_trades": losses,
        "long_trades": long,
        "short_trades": short,
        "client_net_pnl": pnl,
        "gross_wins": gross_wins if gross_wins is not None else 80.0,
        "gross_losses": gross_losses if gross_losses is not None else -20.0,
    }


def _quality_days(login: int, active_days: int = 15, trades_per_day: int = 5):
    days = []
    for index in range(active_days):
        days.append(
            _day(
                f"2026-05-{index + 1:02d}",
                trades_per_day,
                login=login,
                wins=4,
                losses=1,
                long=3,
                short=2,
                pnl=20.0,
                gross_wins=80.0,
                gross_losses=-20.0,
            )
        )
    return days


def test_zero_history_is_inactive_and_skipped_from_pools():
    result = evaluate_newcomer_account(
        _account(1),
        [],
        rules=_rules(),
        max_active_days=60,
        validation_start=date(2026, 7, 1),
        validation_end=date(2026, 7, 22),
    )
    assert result["pool"] == "inactive"
    assert result["admitted"] is False

    payload = build_newcomer_analytics_payload(
        [_account(1), _account(2, book="abook")],
        [],
        _request(),
        max_active_days=60,
    )
    assert payload["counts"]["skipped_inactive"] == 1
    assert payload["counts"]["skipped_abook"] == 1
    assert payload["counts"]["observe"] == 0
    assert payload["kpi"]["admitted_accounts"] == 0


def test_immature_traders_with_trades_go_to_observe():
    days = [_day("2026-07-02", 3, login=8, wins=2, losses=1, long=2, short=1, pnl=12.0)]
    result = evaluate_newcomer_account(
        _account(8),
        days,
        rules=_rules(min_trades=75),
        max_active_days=60,
        validation_start=date(2026, 7, 1),
        validation_end=date(2026, 7, 22),
    )
    assert result["pool"] == "observe"
    assert result["window_trade_count"] == 3
    assert "insufficient_sample" in result["selection_flags"]


def test_personal_as_of_admits_and_post_pnl_starts_next_day():
    days = _quality_days(9, active_days=15, trades_per_day=5)
    days.append(
        _day("2026-07-10", 3, login=9, wins=2, losses=1, long=2, short=1, pnl=50.0, gross_wins=60.0, gross_losses=-10.0)
    )
    days.append(
        _day("2026-07-11", 2, login=9, wins=1, losses=1, long=1, short=1, pnl=-20.0, gross_wins=5.0, gross_losses=-25.0)
    )

    result = evaluate_newcomer_account(
        _account(9),
        days,
        rules=_rules(),
        max_active_days=60,
        validation_start=date(2026, 7, 1),
        validation_end=date(2026, 7, 22),
    )
    assert result["admitted"] is True
    assert result["as_of"] == "2026-05-15"
    assert result["post_asof_pnl"] == 30.0


def test_payload_includes_summary_charts_and_skips_zombies():
    days = _quality_days(9, active_days=15, trades_per_day=5)
    days.append(_day("2026-07-10", 3, login=9, wins=2, losses=1, long=2, short=1, pnl=50.0))
    payload = build_newcomer_analytics_payload(
        [_account(9), _account(10), _account(11, book="abook")],
        days,
        _request(),
        max_active_days=60,
    )
    assert payload["counts"]["admitted"] == 1
    assert payload["counts"]["skipped_inactive"] == 1
    assert payload["counts"]["skipped_abook"] == 1
    assert payload["summary"]["post_asof"]["accounts"] == 1
    assert payload["pnl_distribution"]["post_asof"]
    assert payload["cumulative_pnl"]["post_asof"]
    assert payload["top_accounts"]["post_asof"]["winners"]


def test_martingale_hard_block_never_admits():
    result = evaluate_newcomer_account(
        _account(3, martingale_hard_block=True),
        _quality_days(3),
        rules=_rules(),
        max_active_days=60,
        validation_start=date(2026, 7, 1),
        validation_end=date(2026, 7, 22),
    )
    assert result["pool"] == "blocked"
    assert result["admitted"] is False
    assert "martingale_hard_block" in result["selection_flags"]


def test_single_user_min_trades_sensitivity_shifts_as_of():
    days = _quality_days(4, active_days=20, trades_per_day=5)
    days.append(
        _day("2026-07-08", 4, login=4, wins=2, losses=2, long=2, short=2, pnl=40.0, gross_wins=50.0, gross_losses=-10.0)
    )

    sensitivity = build_newcomer_account_sensitivity(
        _account(4),
        days,
        _request(),
        min_trades_values=[75, 100],
        max_active_days=60,
        stats_end=date(2026, 7, 22),
    )
    by_min = {point["min_trades_used"]: point for point in sensitivity["points"]}
    assert by_min[75]["as_of"] == "2026-05-15"
    assert by_min[100]["as_of"] == "2026-05-20"
    assert by_min[100]["post_asof_pnl"] <= by_min[75]["post_asof_pnl"]
