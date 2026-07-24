from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from app.avg_profit import load_avg_profit_snapshot
from app.models import AnalysisRules
from app.service import build_two_stage_payload


def test_avg_profit_snapshot_builder_uses_custom_daily_rows(monkeypatch):
    from scripts import build_avg_profit_snapshot as builder
    from app.config import Settings

    class Result:
        def __init__(self, columns, rows):
            self.column_names = columns
            self.result_rows = rows

    class FakeClient:
        def __init__(self):
            self.queries = []

        def query(self, query, parameters=None):
            self.queries.append(query)
            if query.startswith("DESCRIBE TABLE"):
                return Result([], [
                    ("platform", "String"), ("login", "UInt64"), ("window_type", "String"),
                    ("trade_date", "Date"), ("window_end", "DateTime"),
                    ("daily_profit", "Decimal(24, 8)"), ("daily_trades", "UInt32"),
                ])
            return Result(
                ["platform", "login", "avg_profit", "profit_observations", "total_profit", "source_min", "source_max"],
                [("mt5", 1, Decimal("12.5"), 2, Decimal("25"), "2026-05-15", "2026-05-16")],
            )

    fake_client = FakeClient()
    monkeypatch.setattr(
        builder,
        "get_settings",
        lambda: Settings("host", 8123, "risk", "user", "password", False, data_source="remote"),
    )
    monkeypatch.setattr(builder.clickhouse_connect, "get_client", lambda **kwargs: fake_client)

    payload = builder.build_snapshot("2026-05-01", "2026-06-30", ["mt5"])

    assert payload["source_table"] == "risk.dws_account_daily_window"
    assert payload["records"][0]["avg_profit"] == 12.5
    query = fake_client.queries[1]
    assert "window_type" in query
    assert "'CUSTOM'" in query
    assert "argMax" in query
    assert "7D_SLIDING" not in query
    assert "holding_buckets" not in query


def test_avg_profit_snapshot_enriches_rows_and_applies_strict_threshold(tmp_path):
    path = tmp_path / "avg_profit.json"
    path.write_text(json.dumps({
        "selection_start": "2026-05-01",
        "selection_end": "2026-06-30",
        "platforms": ["mt5"],
        "records": [
            {"platform": "mt5", "login": 1, "avg_profit": 120.0},
            {"platform": "mt5", "login": 2, "avg_profit": 80.0},
        ],
    }))
    snapshot = load_avg_profit_snapshot(path, "2026-05-01", "2026-06-30", ["mt5"])
    rows = snapshot.enrich_rows([{"platform": "mt5", "login": 1}, {"platform": "mt5", "login": 3}])

    assert snapshot.status == "ready"
    assert rows[0]["avg_profit"] == 120.0
    assert rows[1]["avg_profit"] is None
    assert snapshot.allows(("mt5", 1), 100)
    assert not snapshot.allows(("mt5", 2), 100)


def test_avg_profit_threshold_does_not_gate_abook_routing():
    def row(login: int, avg_profit: float, month: str) -> dict:
        return {
            "platform": "mt5", "login": login, "account_group": "real", "phase": "selection",
            "month_start": datetime.fromisoformat(f"2026-{month}-01"), "matched_trades": 20, "winning_trades": 15,
            "losing_trades": 5, "matched_volume": Decimal("10"), "matched_market_pnl": Decimal("100"),
            "market_pnl": Decimal("100"), "gross_wins": Decimal("200"), "gross_losses": Decimal("-50"),
            "costs": Decimal("0"), "client_net_pnl": Decimal("100"), "funding_pnl": Decimal("0"),
            "active_trade_days": 10, "daily_active_days": 10, "daily_positive_days": 8,
            "daily_negative_days": 2, "daily_flat_days": 0, "daily_positive_sum": Decimal("100"),
            "daily_negative_sum": Decimal("-10"), "max_positive_day": Decimal("20"),
            "min_negative_day": Decimal("-2"), "daily_pnl_sum_for_variance": Decimal("100"),
            "daily_pnl_square_sum": Decimal("1000"), "daily_variance_count": 10, "daily_abs_sum": Decimal("110"),
            "turnover": Decimal("0"), "avg_holding_seconds": Decimal("60"), "median_holding_seconds": Decimal("60"),
            "long_trades": 10, "short_trades": 10, "symbols_traded": 1, "avg_profit": avg_profit,
        }

    payload = build_two_stage_payload(
        [row(1, 120, "05"), row(1, 120, "06"), row(2, 80, "05"), row(2, 80, "06")],
        selection_start="2026-05-01", selection_end="2026-06-30",
        validation_start="2026-07-01", validation_end="2026-07-13",
        min_trades=0, min_profit_factor=0, min_payoff_ratio=0,
        min_positive_month_rate=0, max_top1_day_profit_contribution=2,
        min_direction_day_rate_lower_bound=0, min_stability_score=0,
        avg_profit_snapshot_status="ready",
    )

    by_login = {account["login"]: account for account in payload["accounts"]}
    assert by_login[1]["book"] == "abook"
    assert by_login[2]["book"] == "abook"
    assert "min_avg_profit" not in payload["rules"]["removed_selection_rules"]
