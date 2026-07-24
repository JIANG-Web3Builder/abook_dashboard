from datetime import datetime
from decimal import Decimal
import inspect

from fastapi.testclient import TestClient

from app.main import analysis, app, get_repository
from app.models import AnalysisRequest


client = TestClient(app)


def test_dashboard_shell_is_served():
    response = client.get("/")

    assert response.status_code == 200
    assert "Abook 筛选与 Book 分析" in response.text


def test_health_endpoint_reports_service_status():
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_analysis_rejects_unsupported_lookback_months():
    response = client.post("/api/abook/analysis", json={"lookback_months": 4})

    assert response.status_code == 422


def test_analysis_returns_dashboard_payload_from_repository_rows():
    class FakeRepository:
        def fetch_analysis(self, request, excluded_logins=None):
            return [{
                "platform": "mt5", "login": 1, "account_group": "real", "country": "Brazil",
                "leverage": 100, "registration": datetime(2026, 1, 1), "last_access": datetime(2026, 7, 1),
                "balance": Decimal("1000"), "equity_prev_day": Decimal("1000"), "agent": 0, "client_id": 1,
                "lead_source": "", "lead_campaign": "", "month_start": datetime(2026, 7, 1),
                "matched_trades": 1, "winning_trades": 1, "losing_trades": 0, "matched_volume": Decimal("1"),
                "matched_market_pnl": Decimal("10"), "deal_market_pnl": Decimal("10"), "gross_wins": Decimal("10"),
                "gross_losses": Decimal("-5"), "costs": Decimal("-1"), "client_net_pnl": Decimal("9"),
                "funding_pnl": Decimal("0"), "turnover": Decimal("100"), "avg_holding_seconds": Decimal("5"),
                "median_holding_seconds": Decimal("5"), "long_trades": 1, "short_trades": 0, "symbols_traded": 1,
                "active_trade_days": 1, "daily_profit_sum": Decimal("11"),
            }]

    app.dependency_overrides[get_repository] = lambda: FakeRepository()
    try:
        response = client.post("/api/abook/analysis", json={"lookback_months": 1})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["kpis"]["client_net_pnl"] == 9.0


def test_analysis_returns_two_stage_payload_for_new_request():
    class FakeRepository:
        def fetch_analysis(self, request, excluded_logins=None):
            rows = []
            for month, net, market in (("2026-05-01", 120, 130), ("2026-06-01", 100, 110), ("2026-07-01", 70, 80)):
                rows.append({
                    "platform": "mt5", "login": 7, "account_group": "real\\FPlive", "month_start": datetime.fromisoformat(month),
                    "matched_trades": 20, "winning_trades": 15, "losing_trades": 5, "matched_volume": Decimal("1"),
                    "matched_market_pnl": Decimal(str(market)), "deal_market_pnl": Decimal(str(market)),
                    "gross_wins": Decimal("180"), "gross_losses": Decimal("-60"), "costs": Decimal("-10"),
                    "client_net_pnl": Decimal(str(net)), "funding_pnl": Decimal("0"), "active_trade_days": 10,
                    "daily_profit_sum": Decimal(str(net)), "positive_profit_days": 8, "negative_profit_days": 2,
                    "daily_positive_sum": Decimal("100"), "daily_negative_sum": Decimal("-10"),
                    "max_positive_day": Decimal("10"), "min_negative_day": Decimal("-2"),
                    "flat_profit_days": 0, "turnover": Decimal("100"), "avg_holding_seconds": Decimal("5"),
                        "median_holding_seconds": Decimal("5"), "long_trades": 10, "short_trades": 10, "symbols_traded": 1,
                })
            return rows

        def fetch_daily_pnl(self, request, excluded_logins=None):
            return [{
                "platform": "mt5", "login": 7,
                "trade_date": datetime(2026, 5, 15),
                "client_net_pnl": Decimal("12"),
                "market_pnl": Decimal("13"),
                "matched_trades": 2,
            }]

    app.dependency_overrides[get_repository] = lambda: FakeRepository()
    try:
        response = client.post(
            "/api/abook/analysis",
            json={"rules": {
                "min_trades": 2,
                "min_stability_score": 72,
                "max_top1_day_profit_contribution": 0.4,
            }},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["selection"]["counts"]["abook"] == 1
    assert body["coverage"]["validation_partial"] is True
    assert body["rules"]["min_stability_score"] == 72
    assert body["rules"]["max_top1_day_profit_contribution"] == 0.4
    assert "daily_book_series" in body


def test_dashboard_does_not_expose_refresh_endpoint():
    response = client.post(
        "/api/abook/refresh-snapshots",
        json={
            "selection": {"start": "2026-05-01", "end": "2026-06-30"},
            "validation": {"start": "2026-07-01", "end": "2026-07-23"},
            "platforms": ["mt5", "hh_mt5"],
        },
    )

    assert response.status_code == 404


def test_local_snapshot_refresh_endpoint_delegates_to_shared_ensure(monkeypatch):
    from app.config import Settings

    settings = Settings(
        clickhouse_host="",
        clickhouse_port=8123,
        clickhouse_database="risk",
        clickhouse_user="",
        clickhouse_password="",
        clickhouse_secure=False,
        data_source="local",
    )
    captured = {}

    def fake_ensure(request, *, settings=None):
        captured["request"] = request
        captured["settings"] = settings
        return {"status": "ready", "refreshed": True, "reason": "selection_window"}

    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    monkeypatch.setattr("app.main.ensure_local_snapshots_for_request", fake_ensure)

    response = client.post("/api/warehouse/snapshots/refresh", json={})

    assert response.status_code == 200
    assert response.json()["refreshed"] is True
    assert captured["request"].selection.start.isoformat() == "2026-05-01"
    assert captured["settings"] is settings


def test_warehouse_status_reports_snapshot_selection_window(tmp_path, monkeypatch):
    import json

    warehouse_path = tmp_path / "warehouse"
    warehouse_path.mkdir()
    (warehouse_path / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "generation": "g1",
        "updated_at": "2026-07-24T10:00:00+08:00",
        "tables": {},
    }))
    snapshot_paths = {
        "ABOOK_RISK_SNAPSHOT_PATH": tmp_path / "risk.json",
        "ABOOK_AVG_PROFIT_SNAPSHOT_PATH": tmp_path / "avg.json",
        "ABOOK_MARTINGALE_SNAPSHOT_PATH": tmp_path / "martingale.json",
    }
    for path in snapshot_paths.values():
        path.write_text(json.dumps({
            "warehouse_generation": "g1",
            "selection_start": "2026-05-14",
            "selection_end": "2026-06-30",
            "validation_start": "2026-07-01",
            "validation_end": "2026-07-22",
            "records": [],
        }))
    for key, path in snapshot_paths.items():
        monkeypatch.setenv(key, str(path))
    monkeypatch.setenv("ABOOK_WAREHOUSE_PATH", str(warehouse_path))
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        response = client.get("/api/warehouse/status")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["snapshots"]["risk"]["selection_start"] == "2026-05-14"
    assert body["snapshots"]["risk"]["validation_end"] == "2026-07-22"


def test_warehouse_status_is_read_only_and_reports_missing_local_data(tmp_path, monkeypatch):
    monkeypatch.setenv("ABOOK_WAREHOUSE_PATH", str(tmp_path / "warehouse"))
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        response = client.get("/api/warehouse/status")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    assert response.json()["source"] == "local"
    assert response.json()["status"] == "missing"


def test_account_detail_endpoint_forwards_requested_analysis_window():
    calls = []

    class FakeRepository:
        def fetch_account_detail(self, platform, login, start, end):
            calls.append((platform, login, start, end))
            return [
                {
                    "platform": platform,
                    "login": login,
                    "symbol": "XAUUSD",
                    "entry_time": "2026-01-01 00:00:00.000",
                    "exit_time": "2026-01-01 00:00:00.100",
                    "entry_price": 100.0,
                    "exit_price": 101.0,
                    "direction": "Long",
                    "volume": 1.0,
                    "profit": 1.0,
                },
                {
                    "platform": platform,
                    "login": login,
                    "symbol": "XAUUSD",
                    "entry_time": "2026-01-01 00:00:00.200",
                    "exit_time": "2026-01-01 00:00:00.300",
                    "entry_price": 100.0,
                    "exit_price": 99.0,
                    "direction": "Long",
                    "volume": 1.0,
                    "profit": -1.0,
                },
            ]

        def fetch_account_direction_summary(self, platform, login, start, end, selection_start, selection_end, validation_start, validation_end):
            calls.append(("direction", platform, login, start, end, selection_start, selection_end, validation_start, validation_end))
            return []

    app.dependency_overrides[get_repository] = lambda: FakeRepository()
    try:
        response = client.get(
            "/api/abook/accounts/mt5/7",
            params={
                "start": "2026-05-01",
                "end": "2026-07-16",
                "selection_start": "2026-05-01",
                "selection_end": "2026-06-30",
                "validation_start": "2026-07-01",
                "validation_end": "2026-07-16",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert calls == [
        ("mt5", 7, "2026-05-01", "2026-07-16"),
        ("direction", "mt5", 7, "2026-05-01", "2026-07-16", "2026-05-01", "2026-06-30", "2026-07-01", "2026-07-16"),
    ]
    assert response.json()["direction_summary"]["basis"] == "matched.profit"
    assert response.json()["markout"]["entry"]["curve"]


def test_account_detail_default_end_matches_dashboard_validation_default():
    from app.main import account_detail

    assert inspect.signature(account_detail).parameters["end"].default == "2026-07-22"


def test_analysis_routes_population_accounts_even_when_risk_sql_excludes_some(monkeypatch):
    full_population = [{"platform": "mt5", "login": 1}, {"platform": "mt5", "login": 2}]
    effective_population = [full_population[0]]
    captured = {}

    class Snapshot:
        status = "ready"

        def enrich_rows(self, rows):
            return list(rows)

        def summary(self, *args):
            return {"status": self.status}

    class RiskFilter:
        snapshot = Snapshot()
        status = "ready"
        allowed_logins = None
        excluded_logins = {('mt5', 2)}

        def apply(self, request):
            return request

        def is_empty_for(self, request):
            return False

        def summary(self):
            return {"status": self.status}

    class SnapshotFilter:
        status = "missing"

        def enrich_rows(self, rows):
            return list(rows)

        def summary(self, *args):
            return {"status": self.status}

    class FakeRepository:
        def fetch_analysis(self, request, excluded_logins=None):
            return effective_population if excluded_logins else full_population

        def fetch_daily_pnl(self, request, excluded_logins=None):
            return []

    class Candidates:
        status = "disabled"
        login_ids = frozenset()

        def summary(self, enabled=False):
            return {"enabled": enabled, "status": self.status}

    monkeypatch.setattr("app.main.build_local_risk_filter", lambda request: RiskFilter())
    monkeypatch.setattr("app.main.build_martingale_filter", lambda request: SnapshotFilter())
    monkeypatch.setattr("app.main.build_avg_profit_filter", lambda request: SnapshotFilter())
    monkeypatch.setattr("app.main.load_personal_candidates", lambda: Candidates())

    def fake_build(rows, **kwargs):
        captured["rows"] = rows
        return {}

    monkeypatch.setattr("app.main.build_two_stage_payload", fake_build)

    analysis(AnalysisRequest(), FakeRepository())

    assert {(row["platform"], row["login"]) for row in captured["rows"]} == {('mt5', 1), ('mt5', 2)}
