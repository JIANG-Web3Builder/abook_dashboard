from datetime import datetime

from app.direction_analytics import _monthly_rows, _side_metric, build_direction_analytics_payload
from app.models import AnalysisRequest


def _fact(
    login: int,
    direction: str,
    phase: str,
    fact_level: str,
    *,
    month: str = "2026-05-01",
    day: str = "2026-05-03",
    trades: int = 0,
    wins: int = 0,
    losses: int = 0,
    pnl: float = 0.0,
    volume: float = 0.0,
    gross_wins: float = 0.0,
    gross_losses: float = 0.0,
    holding: float = 60.0,
    symbol: str = "EURUSD",
):
    return {
        "platform": "mt5",
        "login": login,
        "account_group": "real",
        "direction": direction,
        "phase": phase,
        "fact_level": fact_level,
        "month_start": datetime.fromisoformat(month),
        "trade_date": datetime.fromisoformat(day),
        "symbol": symbol,
        "matched_trades": trades,
        "winning_trades": wins,
        "losing_trades": losses,
        "side_pnl": pnl,
        "matched_volume": volume,
        "gross_wins": gross_wins,
        "gross_losses": gross_losses,
        "avg_holding_seconds": holding,
        "median_holding_seconds": holding,
        "active_trade_days": 1 if trades else 0,
    }


def _account(login: int, *, book: str = "bbook", **extra):
    return {
        "platform": "mt5",
        "login": login,
        "account_group": "real",
        "book": book,
        "risk_balance_status": "not_available",
        "risk_leverage_p95_ratio": None,
        "martingale_hard_block": False,
        "selection": {"median_holding_seconds": 60, "risk_balance_status": "not_available"},
        **extra,
    }


def test_direction_payload_classifies_independent_sides_without_candidate_overrides():
    request = AnalysisRequest(
        platforms=["mt5"],
        rules={"min_trades": 2, "min_win_rate": 0.5, "min_profit_factor": 1.25, "min_payoff_ratio": 0.4, "max_top1_day_profit_contribution": 0.8},
    )
    rows = []
    for direction in ("Long", "Short"):
        rows.extend([
            _fact(1, direction, "selection", "month", trades=2, wins=1, losses=1, pnl=20, volume=2, gross_wins=30, gross_losses=-10),
            _fact(1, direction, "selection", "day", trades=1, wins=1, pnl=15, volume=1, gross_wins=15),
            _fact(1, direction, "selection", "day", day="2026-05-04", trades=1, losses=1, pnl=5, volume=1, gross_losses=-10),
        ])
    total_accounts = [
        _account(
            1,
            book="abook",
            selection_source="personal_candidate_list",
            abook_rules_pass=False,
            is_personal_candidate=True,
            is_news_candidate=True,
        )
    ]

    payload = build_direction_analytics_payload(rows, total_accounts, request)

    account = payload["accounts"][0]
    assert account["long_pass"] is True
    assert account["short_pass"] is True
    assert account["quadrant"] == "both_pass"
    assert account["in_total_abook"] is True
    assert payload["counts"]["both_pass"] == 1
    assert payload["accounts"][0]["long"]["selection"]["side_pnl"] == 20.0


def test_direction_payload_separates_insufficient_side_from_quality_failure():
    request = AnalysisRequest(platforms=["mt5"], rules={"min_trades": 3})
    rows = [
        _fact(2, "Long", "selection", "month", trades=3, wins=2, losses=1, pnl=11, gross_wins=20, gross_losses=-10),
        _fact(2, "Long", "selection", "day", trades=3, wins=2, losses=1, pnl=11, gross_wins=20, gross_losses=-10),
        _fact(2, "Short", "selection", "month", trades=1, wins=1, pnl=2, gross_wins=2),
        _fact(2, "Short", "selection", "day", trades=1, wins=1, pnl=2, gross_wins=2),
    ]

    payload = build_direction_analytics_payload(rows, [_account(2)], request)

    account = payload["accounts"][0]
    assert account["long"]["selection"]["sample_status"] == "ok"
    assert account["short"]["selection"]["sample_status"] == "insufficient"
    assert account["quadrant"] == "insufficient_side"
    assert payload["counts"]["insufficient_side"] == 1


def test_direction_payload_uses_shared_martingale_gate_without_recomputing_it_by_side():
    request = AnalysisRequest(platforms=["mt5"], rules={"min_trades": 1})
    rows = [
        _fact(3, "Long", "selection", "month", trades=1, wins=1, pnl=11, gross_wins=11),
        _fact(3, "Long", "selection", "day", trades=1, wins=1, pnl=11, gross_wins=11),
        _fact(3, "Short", "selection", "month", trades=1, wins=1, pnl=11, gross_wins=11),
        _fact(3, "Short", "selection", "day", trades=1, wins=1, pnl=11, gross_wins=11),
    ]

    payload = build_direction_analytics_payload(rows, [_account(3, martingale_hard_block=True)], request)

    account = payload["accounts"][0]
    assert account["long_pass"] is False
    assert account["short_pass"] is False
    assert "martingale_hard_block" in account["selection_flags_long"]
    assert account["quadrant"] == "neither_pass"


def test_direction_payload_retains_population_accounts_with_no_activity():
    request = AnalysisRequest(platforms=["mt5"], rules={"min_trades": 1})

    payload = build_direction_analytics_payload([], [_account(5, book="abook")], request)

    assert payload["accounts"] == []
    assert payload["book_sets"]["all"]["abook"]["long"]["accounts"] == 1
    assert payload["book_sets"]["all"]["abook"]["long"]["phase_summary"]["selection"]["active_accounts"] == 0


def test_direction_payload_ignores_matched_rows_outside_population_accounts():
    request = AnalysisRequest(platforms=["mt5"], rules={"min_trades": 1})
    rows = [_fact(99, "Long", "selection", "day", trades=1, wins=1, pnl=25, gross_wins=25)]

    payload = build_direction_analytics_payload(rows, [_account(7)], request)

    assert payload["accounts"] == []
    assert payload["book_sets"]["all"]["bbook"]["long"]["phase_summary"]["selection"]["matched_trades"] == 0


def test_direction_payload_user_list_keeps_only_accounts_with_material_pnl():
    request = AnalysisRequest(platforms=["mt5"], rules={"min_trades": 1})
    rows = [
        _fact(8, "Long", "selection", "month", trades=1, wins=1, pnl=5, gross_wins=5),
        _fact(8, "Long", "selection", "day", trades=1, wins=1, pnl=5, gross_wins=5),
        _fact(9, "Long", "selection", "month", trades=1, wins=1, pnl=11, gross_wins=11),
        _fact(9, "Long", "selection", "day", trades=1, wins=1, pnl=11, gross_wins=11),
    ]

    payload = build_direction_analytics_payload(rows, [_account(8), _account(9)], request)

    assert [account["login"] for account in payload["accounts"]] == [9]
    assert payload["book_sets"]["all"]["bbook"]["long"]["accounts"] == 2


def test_direction_payload_applies_shared_long_trades_ratio_gate():
    request = AnalysisRequest(
        platforms=["mt5"],
        rules={
            "min_trades": 1,
            "min_win_rate": 0,
            "min_profit_factor": 0,
            "min_payoff_ratio": 0,
            "min_long_trades_ratio": 0.3,
            "max_long_trades_ratio": 0.7,
            "max_top1_day_profit_contribution": 1.0,
        },
    )
    balanced = []
    skewed = []
    for direction in ("Long", "Short"):
        balanced.extend([
            _fact(20, direction, "selection", "month", trades=2, wins=2, pnl=20, gross_wins=20),
            _fact(20, direction, "selection", "day", trades=1, wins=1, pnl=10, gross_wins=10),
            _fact(20, direction, "selection", "day", day="2026-05-04", trades=1, wins=1, pnl=10, gross_wins=10),
        ])
    skewed.extend([
        _fact(21, "Long", "selection", "month", trades=4, wins=4, pnl=40, gross_wins=40),
        _fact(21, "Long", "selection", "day", trades=2, wins=2, pnl=20, gross_wins=20),
        _fact(21, "Long", "selection", "day", day="2026-05-04", trades=2, wins=2, pnl=20, gross_wins=20),
        _fact(21, "Short", "selection", "month", trades=1, wins=1, pnl=10, gross_wins=10),
        _fact(21, "Short", "selection", "day", trades=1, wins=1, pnl=10, gross_wins=10),
    ])

    payload = build_direction_analytics_payload(
        balanced + skewed,
        [_account(20), _account(21)],
        request,
    )
    accounts = {account["login"]: account for account in payload["accounts"]}

    assert accounts[20]["long_pass"] is True
    assert accounts[20]["short_pass"] is True
    assert accounts[20]["shared_gates"]["direction_balance_pass"] is True
    assert accounts[21]["long_pass"] is False
    assert accounts[21]["short_pass"] is False
    assert accounts[21]["shared_gates"]["direction_balance_pass"] is False
    assert "long_trades_ratio" in accounts[21]["selection_flags_long"]
    assert payload["rules"]["min_long_trades_ratio"] == 0.3
    assert payload["rules"]["max_long_trades_ratio"] == 0.7
    assert payload["rules"]["max_leverage_p95_ratio"] == 2000.0
    assert payload["rules"]["max_high_leverage_holding_seconds"] == 60.0


def test_direction_payload_keeps_all_independently_passing_accounts_for_list_counts():
    request = AnalysisRequest(
        platforms=["mt5"],
        rules={
            "min_trades": 1,
            "min_win_rate": 0,
            "min_profit_factor": 0,
            "min_payoff_ratio": 0,
            "min_long_trades_ratio": 0.0,
            "max_long_trades_ratio": 1.0,
            "max_top1_day_profit_contribution": 1.0,
        },
    )
    rows = [
        _fact(10, "Long", "selection", "month", trades=3, wins=2, losses=1, pnl=1, gross_wins=2, gross_losses=-1),
        _fact(10, "Long", "selection", "day", trades=1, wins=1, pnl=1, gross_wins=1),
        _fact(10, "Long", "selection", "day", day="2026-05-04", trades=1, wins=1, pnl=1, gross_wins=1),
        _fact(10, "Long", "selection", "day", day="2026-05-05", trades=1, losses=1, pnl=-1, gross_losses=-1),
        _fact(11, "Short", "selection", "month", trades=3, wins=2, losses=1, pnl=1, gross_wins=2, gross_losses=-1),
        _fact(11, "Short", "selection", "day", trades=1, wins=1, pnl=1, gross_wins=1),
        _fact(11, "Short", "selection", "day", day="2026-05-04", trades=1, wins=1, pnl=1, gross_wins=1),
        _fact(11, "Short", "selection", "day", day="2026-05-05", trades=1, losses=1, pnl=-1, gross_losses=-1),
    ]

    payload = build_direction_analytics_payload(rows, [_account(10), _account(11)], request)

    assert payload["book_counts"]["bbook"]["long_pass"] == 1
    assert payload["book_counts"]["bbook"]["short_pass"] == 1
    assert {account["login"] for account in payload["accounts"]} == {10, 11}


def test_direction_phase_median_uses_phase_fact_instead_of_first_month():
    request = AnalysisRequest(platforms=["mt5"], rules={"min_trades": 1})
    rows = [
        _fact(6, "Long", "selection", "month", trades=1, wins=1, pnl=11, gross_wins=11, holding=10),
        _fact(6, "Long", "selection", "month", month="2026-06-01", trades=1, wins=1, pnl=11, gross_wins=11, holding=100),
        _fact(6, "Long", "selection", "phase", trades=2, wins=2, pnl=22, gross_wins=22, holding=55),
    ]

    account = _account(6)
    normalized, _, _ = _monthly_rows(rows, {("mt5", 6): account}, ["2026-05", "2026-06"], ["2026-07"])
    metric = _side_metric(normalized[(("mt5", 6), "long", "selection")], account, "selection")

    assert metric["median_holding_seconds"] == 55.0


def test_direction_comparison_keeps_total_abook_deals_pnl_separate_from_matched_profit():
    request = AnalysisRequest(platforms=["mt5"], rules={"min_trades": 1})
    total = _account(4, book="abook", validation={"client_net_pnl": 123.0})
    payload = build_direction_analytics_payload([], [total], request)

    assert payload["comparison"]["total_abook_validation_matched_profit"] == 0.0
    assert payload["comparison"]["total_abook_validation_client_net_pnl"] == 123.0
    assert payload["comparison"]["both_pass_validation_matched_profit"] == 0.0


def test_direction_payload_exposes_book_side_and_phase_specific_user_structure():
    request = AnalysisRequest(
        platforms=["mt5"],
        selection={"start": "2026-05-15", "end": "2026-06-30"},
        validation={"start": "2026-07-01", "end": "2026-07-16"},
        rules={"min_trades": 1},
    )
    rows = [
        _fact(7, "Long", "selection", "month", trades=2, wins=2, pnl=25, volume=2, holding=30),
        _fact(7, "Long", "selection", "day", trades=2, wins=2, pnl=25, volume=2, holding=30),
        _fact(7, "Long", "selection", "phase", trades=2, wins=2, pnl=25, volume=2, holding=30),
        _fact(7, "Long", "validation", "month", month="2026-07-01", day="2026-07-03", trades=1, losses=1, pnl=-40, volume=4, holding=7200),
        _fact(7, "Long", "validation", "day", month="2026-07-01", day="2026-07-03", trades=1, losses=1, pnl=-40, volume=4, holding=7200),
        _fact(7, "Long", "validation", "phase", month="2026-07-01", day="2026-07-03", trades=1, losses=1, pnl=-40, volume=4, holding=7200),
    ]
    payload = build_direction_analytics_payload(
        rows,
        [_account(7, book="abook")],
        request,
    )

    assert payload["accounts"][0]["book"] == "abook"
    long_set = payload["book_sets"]["all"]["abook"]["long"]
    assert set(payload["book_sets"]["all"]) == {"abook", "bbook"}
    assert set(long_set["phase_summary"]) == {"selection", "validation"}
    assert long_set["phase_summary"]["selection"]["net_pnl"] == 25.0
    assert long_set["phase_summary"]["validation"]["net_pnl"] == -40.0
    assert "cumulative_pnl" in long_set
    assert long_set["cumulative_pnl"]["selection"][-1]["cumulative_pnl"] == 25.0
    assert long_set["cumulative_pnl"]["validation"][-1]["cumulative_pnl"] == -40.0
    assert long_set["cumulative_pnl"]["full"][0]["date"] == "2026-05-15"
    assert long_set["cumulative_pnl"]["full"][-1]["date"] == "2026-07-16"
    assert "style_breakdown" not in long_set
    assert "holding_duration_bins" not in long_set
    assert "trade_volume_bins" not in long_set
    assert "risk_balance_status" not in payload["accounts"][0]["long"]["selection"]
    assert set(payload["accounts"][0]["long"]["selection"]) == {
        "trade_count", "win_rate", "profit_factor", "payoff_ratio", "side_pnl", "sample_status",
    }
