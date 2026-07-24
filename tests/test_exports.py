from app.exports import render_abook_csv
from app.service import build_misjudge_summary, build_selection_funnel


def _account(login: int, book: str, selection_pnl: float, validation_pnl: float, source: str = "rule_filter"):
    return {
        "platform": "mt5", "login": login, "account_group": "real",
        "book": book, "selection_source": source, "selection_client_net_pnl": selection_pnl,
        "validation_client_net_pnl": validation_pnl, "validation_status": "loss" if validation_pnl < 0 else "profitable",
        "validation": {"client_net_pnl": validation_pnl, "trade_count": 2, "active_trade_days": 1},
        "selection": {"client_net_pnl": selection_pnl, "trade_count": 2, "active_trade_days": 1, "long_trades_ratio": 0.5},
        "selection_months_positive": selection_pnl > 0,
        "stability": {"tier": "core", "score": 85},
        "selection_flags": [], "martingale_blocked": False,
        "martingale_status": "ready", "martingale_risk_level": None,
    }


def test_misjudge_cost_is_sum_of_validation_losses_only():
    summary = build_misjudge_summary([
        _account(1, "abook", 50, -40),
        _account(2, "abook", 40, 30),
        _account(3, "bbook", -40, 25),
    ])

    assert summary["abook_loss_total"] == 40.0
    assert [row["login"] for row in summary["abook_losses"]] == [1]
    assert [row["login"] for row in summary["bbook_profitable"]] == [3]


def test_bbook_profitable_accounts_are_called_bbook_misjudges():
    account = _account(4, "bbook", 0, 100)

    summary = build_misjudge_summary([account])

    assert [row["login"] for row in summary["bbook_profitable"]] == [4]
    assert summary["bbook_company_loss_total"] == -100.0


def test_funnel_reports_ordered_stages_and_drop_reasons():
    accounts = [_account(1, "abook", 50, 10), _account(2, "bbook", 0, 0)]
    accounts[1]["selection"]["trade_count"] = 0
    funnel = build_selection_funnel(accounts, eligible_accounts=2)

    assert [stage["name"] for stage in funnel["stages"]] == [
        "eligible", "sample_qualified", "direction_balance_passed", "leverage_passed", "non_martingale", "abook",
    ]
    assert funnel["stages"][1]["count"] == 1
    assert funnel["stages"][1]["drop_reasons"]["insufficient_sample"] == 1


def test_export_contains_only_final_abook_and_utf8_bom():
    csv_text = render_abook_csv([_account(7, "abook", 50, 20)])

    assert csv_text.startswith("\ufeff")
    assert "platform,login" in csv_text
    assert "mt5,7" in csv_text

    bbook_csv = render_abook_csv([_account(8, "bbook", -20, 20)])
    assert "mt5,8" not in bbook_csv
