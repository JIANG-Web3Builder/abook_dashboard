from __future__ import annotations

from app.book_analytics import build_book_analytics, split_books
from app.service import prepare_analysis_context


def _account(login: int, book: str, selection_pnl: float, validation_pnl: float, monthly: list[dict] | None = None):
    return {
        "platform": "mt5", "login": login, "account_group": "real", "book": book,
        "selection": {
            "client_net_pnl": selection_pnl, "trade_count": 10, "active_trade_days": 2,
            "avg_holding_seconds": 60, "median_holding_seconds": 60, "profit_factor": 1.5,
            "winning_trades": 7, "losing_trades": 3, "gross_wins": 120, "gross_losses": -20,
            "costs": 5, "daily_active_days": 2, "daily_positive_days": 1,
            "daily_negative_days": 1, "daily_flat_days": 0,
        },
        "validation": {
            "client_net_pnl": validation_pnl, "trade_count": 5, "active_trade_days": 1,
            "avg_holding_seconds": 60, "median_holding_seconds": 60, "profit_factor": 1.5,
            "winning_trades": 3, "losing_trades": 2, "gross_wins": 60, "gross_losses": -30,
            "costs": 3, "daily_active_days": 1, "daily_positive_days": 1,
            "daily_negative_days": 0, "daily_flat_days": 0,
        },
        "monthly": monthly or [],
        "validation_status": "profitable" if validation_pnl > 0 else "loss",
        "martingale_blocked": False, "martingale_risk_level": None,
        "stability": {"score": 80, "tier": "core"},
    }


def _month(month: str, pnl: float, trades: int = 2) -> dict:
    return {
        "month": month, "phase": "selection" if month < "2026-07" else "validation",
        "client_net_pnl": pnl, "trade_count": trades, "active_trade_days": 1 if trades else 0,
        "daily_active_days": 1 if trades else 0, "daily_positive_days": 1 if pnl > 0 else 0,
        "daily_negative_days": 1 if pnl < 0 else 0, "daily_flat_days": 1 if pnl == 0 else 0,
        "winning_trades": 1 if pnl > 0 else 0, "losing_trades": 1 if pnl < 0 else 0,
        "gross_wins": max(pnl, 0), "gross_losses": min(pnl, 0), "costs": 1,
        "win_rate": 0.5 if trades else 0.0, "profit_factor": 2.0 if pnl > 0 else 0.0,
    }


def _context(daily_rows=None, validation_end="2026-07-13"):
    return prepare_analysis_context(
        [], daily_rows=daily_rows or [], overview_daily_rows=daily_rows or [],
        selection_start="2026-05-01", selection_end="2026-06-30",
        validation_start="2026-07-01", validation_end=validation_end,
    )


def test_bbook_is_population_minus_abook():
    accounts = [_account(1, "abook", 20, 20), _account(2, "bbook", -20, -20), _account(3, "bbook", 0, 0)]
    abook, bbook = split_books(accounts, {("mt5", 1)})

    assert {(row["platform"], row["login"]) for row in abook} == {("mt5", 1)}
    assert {(row["platform"], row["login"]) for row in bbook} == {("mt5", 2), ("mt5", 3)}


def test_book_analytics_reports_period_profitability_and_distribution():
    accounts = [
        _account(1, "abook", 200, 120, [_month("2026-05", 80), _month("2026-06", 120), _month("2026-07", 120)]),
        _account(2, "bbook", -200, -120, [_month("2026-05", -80), _month("2026-06", -120), _month("2026-07", -120)]),
        _account(3, "bbook", 0, 0, [_month("2026-05", 0, 0), _month("2026-06", 0, 0), _month("2026-07", 0, 0)]),
    ]

    result = build_book_analytics(_context(), accounts, {('mt5', 1)}, symbol_rows=[])
    abook_selection = result["pnl_structure"]["abook"]["selection"]
    bbook_selection = result["pnl_structure"]["bbook"]["selection"]

    assert abook_selection["profitable_accounts"] == 1
    assert abook_selection["loss_accounts"] == 0
    assert abook_selection["neutral_accounts"] == 0
    assert abook_selection["positive_pnl"] == 200.0
    assert abook_selection["negative_pnl"] == 0.0
    assert abook_selection["net_pnl"] == 200.0
    assert bbook_selection["profitable_accounts"] == 0
    assert bbook_selection["loss_accounts"] == 1
    assert bbook_selection["neutral_accounts"] == 1
    bbook_validation = result["pnl_structure"]["bbook"]["validation"]
    assert bbook_validation["positive_pnl"] == 0.0
    assert bbook_validation["negative_pnl"] == -120.0
    assert bbook_validation["net_pnl"] == -120.0
    assert bbook_selection["negative_pnl"] == -200.0
    assert {row["month"] for row in result["pnl_structure"]["abook"]["monthly"]} == {"2026-05", "2026-06", "2026-07", "selection_total"}
    selection_total = next(row for row in result["pnl_structure"]["abook"]["monthly"] if row["month"] == "selection_total")
    assert selection_total["net_pnl"] == 200.0
    assert sum(row["accounts"] for row in result["pnl_structure"]["bbook"]["distribution"]) == 2
    assert {row["period"] for row in result["pnl_structure"]["abook"]["distribution_by_period"]} == {"2026-05", "2026-06", "selection_total", "2026-07"}


def test_phase_loss_amount_keeps_losses_from_profitable_months_inside_a_positive_period():
    accounts = [
        _account(1, "abook", 100, 0, [_month("2026-05", -50), _month("2026-06", 150), _month("2026-07", 0)]),
    ]

    selection = build_book_analytics(_context(), accounts, {('mt5', 1)}, symbol_rows=[])["pnl_structure"]["abook"]["selection"]

    assert selection["net_pnl"] == 100.0
    assert selection["positive_pnl"] == 150.0
    assert selection["negative_pnl"] == -50.0


def test_book_analytics_reports_style_top_accounts_and_risk_without_turnover():
    accounts = [
        _account(1, "abook", 200, 120, [_month("2026-05", 80), _month("2026-06", 120), _month("2026-07", 120)]),
        _account(2, "bbook", -200, -120, [_month("2026-05", -80), _month("2026-06", -120), _month("2026-07", -120)]),
    ]
    daily_rows = [
        {"platform": "mt5", "login": 1, "trade_date": "2026-05-03", "client_net_pnl": 80, "matched_trades": 2},
        {"platform": "mt5", "login": 1, "trade_date": "2026-07-02", "client_net_pnl": -30, "matched_trades": 2},
        {"platform": "mt5", "login": 2, "trade_date": "2026-05-03", "client_net_pnl": -80, "matched_trades": 2},
        {"platform": "mt5", "login": 2, "trade_date": "2026-07-02", "client_net_pnl": 30, "matched_trades": 2},
    ]
    result = build_book_analytics(_context(daily_rows), accounts, {('mt5', 1)}, symbol_rows=[])

    abook = result["pnl_structure"]["abook"]
    assert abook["style_breakdown"]
    assert abook["top_accounts"]["winners"][0]["login"] == 1
    assert abook["top_accounts"]["losers"] == []
    assert result["risk_exposure"]["abook"]["leverage_p95"] >= 0
    assert "daily_turnover" not in result["risk_exposure"]["abook"]
    assert "hedge_cost_bps" not in result["risk_exposure"]["abook"]
    assert result["routing_quality"]["company_profit_comparison"]


def test_book_analytics_exposes_leverage_histogram_buckets():
    accounts = []
    leverage_values = [0.5, 7.0, 30.0, 150.0, 2.0, 15.0, 75.0, 250.0]
    abook_keys = set()
    for index, leverage in enumerate(leverage_values, start=10):
        account = _account(index, "abook" if index < 14 else "bbook", 20, 20)
        account["risk_leverage_p95_ratio"] = leverage
        accounts.append(account)
        if index < 14:
            abook_keys.add(("mt5", index))

    result = build_book_analytics(_context(), accounts, abook_keys, symbol_rows=[])
    histogram = result["risk_exposure"]["abook"]["leverage_histogram"]

    assert histogram["labels"] == ["0–1x", "1–2x", "2–5x", "5–10x", "10–20x", "20–50x", "50–100x", "100–200x", ">200x"]
    assert histogram["counts"] == [1, 0, 0, 1, 0, 1, 0, 1, 0]


def test_book_analytics_separates_customer_pnl_from_company_profit_sign():
    accounts = [_account(1, "abook", 20, 20), _account(2, "bbook", -20, -20)]
    result = build_book_analytics(_context(), accounts, {('mt5', 1)}, symbol_rows=[])
    abook_validation = result["pnl_structure"]["abook"]["validation"]
    bbook_validation = result["pnl_structure"]["bbook"]["validation"]

    assert abook_validation["customer_net_pnl"] == 20.0
    assert abook_validation["company_profit_if_current_book"] == 0.0
    assert bbook_validation["customer_net_pnl"] == -20.0
    assert bbook_validation["company_profit_if_current_book"] == 20.0


def test_book_analytics_marks_any_validation_window_before_month_end_partial():
    accounts = [_account(1, "abook", 20, 20)]
    result = build_book_analytics(
        _context(validation_end="2026-07-16"), accounts, {('mt5', 1)}, symbol_rows=[]
    )

    assert result["routing_quality"]["validation_partial"] is True
