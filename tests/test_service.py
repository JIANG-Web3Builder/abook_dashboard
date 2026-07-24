from datetime import datetime
from decimal import Decimal
import json

from app.service import build_analysis_payload


def test_analysis_payload_uses_latest_requested_months_and_builds_curve():
    rows = [
        {
            "platform": "mt5", "login": 1, "account_group": "real\\FPlive",
            "country": "Brazil", "leverage": 100, "registration": datetime(2026, 1, 1),
            "last_access": datetime(2026, 7, 1), "balance": Decimal("1000"),
            "equity_prev_day": Decimal("1100"), "agent": 0, "client_id": 7,
            "lead_source": "", "lead_campaign": "", "month_start": datetime(2026, 5, 1),
            "matched_trades": 2, "winning_trades": 1, "losing_trades": 1,
            "matched_volume": Decimal("3"), "matched_market_pnl": Decimal("60"),
            "deal_market_pnl": Decimal("60"), "costs": Decimal("-13"),
            "client_net_pnl": Decimal("47"), "funding_pnl": Decimal("0"),
            "turnover": Decimal("10000"), "avg_holding_seconds": Decimal("90"),
            "median_holding_seconds": Decimal("60"), "long_trades": 1,
            "short_trades": 1, "symbols_traded": 2, "gross_wins": Decimal("100"),
            "gross_losses": Decimal("-40"), "active_trade_days": 2, "daily_profit_sum": Decimal("47"),
        },
        {
            "platform": "mt5", "login": 1, "account_group": "real\\FPlive",
            "country": "Brazil", "leverage": 100, "registration": datetime(2026, 1, 1),
            "last_access": datetime(2026, 7, 1), "balance": Decimal("1000"),
            "equity_prev_day": Decimal("1100"), "agent": 0, "client_id": 7,
            "lead_source": "", "lead_campaign": "", "month_start": datetime(2026, 6, 1),
            "matched_trades": 1, "winning_trades": 1, "losing_trades": 0,
            "matched_volume": Decimal("2"), "matched_market_pnl": Decimal("20"),
            "deal_market_pnl": Decimal("20"), "costs": Decimal("-2"),
            "client_net_pnl": Decimal("18"), "funding_pnl": Decimal("0"),
            "turnover": Decimal("5000"), "avg_holding_seconds": Decimal("30"),
            "median_holding_seconds": Decimal("30"), "long_trades": 1,
            "short_trades": 0, "symbols_traded": 1, "gross_wins": Decimal("20"),
            "gross_losses": Decimal("0"), "active_trade_days": 1, "daily_profit_sum": Decimal("18"),
        },
    ]

    result = build_analysis_payload(rows, lookback_months=1)

    assert result["kpis"]["selected_accounts"] == 1
    assert result["kpis"]["client_net_pnl"] == 18.0
    assert result["kpis"]["theoretical_mirror_pnl"] == -20.0
    assert result["monthly_series"][-1]["month"] == "2026-06"
    assert result["accounts"][0]["trade_count"] == 1


def test_analysis_payload_keeps_only_accounts_above_strategy_thresholds():
    rows = [{
        "platform": "mt5", "login": 1, "account_group": "real", "month_start": datetime(2026, 7, 1),
        "matched_trades": 2, "winning_trades": 1, "losing_trades": 1, "matched_volume": Decimal("2"),
        "matched_market_pnl": Decimal("20"), "gross_wins": Decimal("30"), "gross_losses": Decimal("-10"),
        "client_net_pnl": Decimal("22"), "costs": Decimal("0"), "funding_pnl": Decimal("0"),
        "turnover": Decimal("100"), "avg_holding_seconds": Decimal("10"), "median_holding_seconds": Decimal("10"),
        "long_trades": 1, "short_trades": 1, "symbols_traded": 1, "active_trade_days": 2,
        "daily_profit_sum": Decimal("22"),
    }, {
        "platform": "mt5", "login": 2, "account_group": "real", "month_start": datetime(2026, 7, 1),
        "matched_trades": 2, "winning_trades": 1, "losing_trades": 1, "matched_volume": Decimal("2"),
        "matched_market_pnl": Decimal("2"), "gross_wins": Decimal("10"), "gross_losses": Decimal("-20"),
        "client_net_pnl": Decimal("20"), "costs": Decimal("0"), "funding_pnl": Decimal("0"),
        "turnover": Decimal("100"), "avg_holding_seconds": Decimal("10"), "median_holding_seconds": Decimal("10"),
        "long_trades": 1, "short_trades": 1, "symbols_traded": 1, "active_trade_days": 1,
        "daily_profit_sum": Decimal("20"),
    }]

    result = build_analysis_payload(rows, lookback_months=1, min_profit_factor=1)

    assert [account["login"] for account in result["accounts"]] == [1]
    assert result["accounts"][0]["profit_factor"] == 3.0
    assert result["accounts"][0]["average_daily_profit"] == 11.0


def test_no_loss_account_has_json_safe_profit_factor_and_passes_finite_threshold():
    rows = [{
        "platform": "mt5", "login": 3, "account_group": "real", "month_start": datetime(2026, 7, 1),
        "matched_trades": 1, "winning_trades": 1, "losing_trades": 0, "matched_volume": Decimal("1"),
        "matched_market_pnl": Decimal("25"), "gross_wins": Decimal("25"), "gross_losses": Decimal("0"),
        "client_net_pnl": Decimal("25"), "costs": Decimal("0"), "funding_pnl": Decimal("0"),
        "turnover": Decimal("100"), "avg_holding_seconds": Decimal("10"), "median_holding_seconds": Decimal("10"),
        "long_trades": 1, "short_trades": 0, "symbols_traded": 1, "active_trade_days": 1,
        "daily_profit_sum": Decimal("25"),
    }]

    result = build_analysis_payload(rows, lookback_months=1)

    assert result["accounts"][0]["profit_factor"] is None
    json.dumps(result, allow_nan=False)
