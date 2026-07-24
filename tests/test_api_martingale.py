import json
from datetime import datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app, get_repository
from app.martingale import load_martingale_snapshot
from app.personal_candidates import PersonalCandidateList


def _row(month: str, pnl: int) -> dict:
    return {
        "platform": "mt5", "login": 7, "account_group": "real",
        "month_start": datetime.fromisoformat(month), "matched_trades": 20,
        "winning_trades": 15, "losing_trades": 5, "matched_volume": Decimal("10"),
        "matched_market_pnl": Decimal(str(pnl)), "market_pnl": Decimal(str(pnl)),
        "gross_wins": Decimal("200"), "gross_losses": Decimal("-50"),
        "costs": Decimal("-10"), "client_net_pnl": Decimal(str(pnl)),
        "funding_pnl": Decimal("0"), "active_trade_days": 10,
        "daily_active_days": 10, "daily_positive_days": 8, "daily_negative_days": 2,
        "daily_flat_days": 0, "daily_positive_sum": Decimal("100"),
        "daily_negative_sum": Decimal("-10"), "daily_abs_sum": Decimal("110"),
        "daily_profit_sum": Decimal(str(pnl)), "max_positive_day": Decimal("10"),
        "min_negative_day": Decimal("-2"), "turnover": Decimal("1000"),
        "avg_holding_seconds": Decimal("60"), "median_holding_seconds": Decimal("60"),
        "long_trades": 15, "short_trades": 5, "symbols_traded": 2,
        "positive_profit_days": 8, "negative_profit_days": 2, "flat_profit_days": 0,
    }


def test_analysis_reports_martingale_status_and_blocks_personal_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr("app.main.ensure_local_snapshots_for_request", lambda *args, **kwargs: {"status": "ready", "refreshed": False})
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps({
        "selection_start": "2026-05-01", "selection_end": "2026-06-30",
        "platforms": ["mt5"], "window_type": "7D_SLIDING",
        "records": [{"platform": "mt5", "login": 7, "risk_level": "high",
                      "martingale_detection_status": "confirmed",
                      "confirmed_windows": 2, "confirmed_extreme_windows": 0,
                      "expanded_windows": 0,
                      "layer_hits": {"layer1": True, "layer2": True}}],
    }))
    monkeypatch.setenv("ABOOK_MARTINGALE_SNAPSHOT_PATH", str(snapshot_path))
    monkeypatch.setattr(
        "app.main.load_personal_candidates",
        lambda: PersonalCandidateList(tmp_path / "candidates.csv", "ready", frozenset({7})),
    )

    class FakeRepository:
        def fetch_analysis(self, request, excluded_logins=None):
            return [_row("2026-05-01", 120), _row("2026-06-01", 100), _row("2026-07-01", 80)]

        def fetch_daily_pnl(self, request, excluded_logins=None):
            return []

    app.dependency_overrides[get_repository] = lambda: FakeRepository()
    try:
        response = TestClient(app).post(
            "/api/abook/analysis",
            json={"platforms": ["mt5"], "personal_candidate_list": True},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    account = next(item for item in body["accounts"] if item["login"] == 7)
    assert account["selection_source"] == "martingale_blocked"
    assert account["book"] == "bbook"
    assert body["martingale"]["status"] == "ready"


def test_suspected_martingale_does_not_override_personal_abook_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr("app.main.ensure_local_snapshots_for_request", lambda *args, **kwargs: {"status": "ready", "refreshed": False})
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps({
        "selection_start": "2026-05-01", "selection_end": "2026-06-30",
        "platforms": ["mt5"], "window_type": "7D_SLIDING",
        "records": [{"platform": "mt5", "login": 7, "risk_level": "extreme",
                      "martingale_detection_status": "suspected",
                      "confirmed_windows": 1, "confirmed_extreme_windows": 1,
                      "expanded_windows": 1,
                      "layer_hits": {"layer1": True, "layer3": True}}],
    }))
    monkeypatch.setenv("ABOOK_MARTINGALE_SNAPSHOT_PATH", str(snapshot_path))
    monkeypatch.setattr(
        "app.main.load_personal_candidates",
        lambda: PersonalCandidateList(tmp_path / "candidates.csv", "ready", frozenset({7})),
    )

    class FakeRepository:
        def fetch_analysis(self, request, excluded_logins=None):
            return [_row("2026-05-01", 120), _row("2026-06-01", 100), _row("2026-07-01", 80)]

        def fetch_daily_pnl(self, request, excluded_logins=None):
            return []

    app.dependency_overrides[get_repository] = lambda: FakeRepository()
    try:
        response = TestClient(app).post(
            "/api/abook/analysis",
            json={"platforms": ["mt5"], "personal_candidate_list": True},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    account = next(item for item in response.json()["accounts"] if item["login"] == 7)
    assert account["martingale_detection_status"] == "suspected"
    assert account["martingale_blocked"] is False
    assert account["martingale_hard_block"] is False
    assert account["book"] == "abook"


def test_account_detail_includes_martingale_record(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps({
        "selection_start": "2026-05-01", "selection_end": "2026-06-30",
        "platforms": ["mt5"], "window_type": "7D_SLIDING",
        "records": [{"platform": "mt5", "login": 7, "risk_level": "extreme",
                      "martingale_detection_status": "confirmed",
                      "confirmed_windows": 2, "confirmed_extreme_windows": 2,
                      "expanded_windows": 0,
                      "layer_hits": {"layer1": True, "layer5": True}}],
    }))
    monkeypatch.setenv("ABOOK_MARTINGALE_SNAPSHOT_PATH", str(snapshot_path))

    class FakeRepository:
        def fetch_account_detail(self, platform, login, start, end):
            return [{"symbol": "EURUSD", "profit": Decimal("10"), "volume": Decimal("1"), "holding_seconds": 30}]

    app.dependency_overrides[get_repository] = lambda: FakeRepository()
    try:
        response = TestClient(app).get(
            "/api/abook/accounts/mt5/7?selection_start=2026-05-01&selection_end=2026-06-30"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["martingale"]["record"]["risk_level"] == "extreme"
