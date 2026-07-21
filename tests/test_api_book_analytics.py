from fastapi.testclient import TestClient

from app.main import _compact_population_account, app, get_repository


def test_book_analytics_endpoint_returns_four_blocks_without_turnover_query(tmp_path, monkeypatch):
    monkeypatch.setenv("ABOOK_MARTINGALE_SNAPSHOT_PATH", str(tmp_path / "missing.json"))

    class FakeRepository:
        def fetch_analysis(self, request, excluded_logins=None):
            return []

        def fetch_daily_pnl(self, request, excluded_logins=None):
            return []

        def fetch_daily_turnover_rows(self, *args, **kwargs):
            raise AssertionError("Book analytics must not query turnover")

    app.dependency_overrides[get_repository] = lambda: FakeRepository()
    try:
        response = TestClient(app).post("/api/abook/book-analytics", json={"analysis": {"platforms": ["mt5"]}})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert set(response.json()) >= {"pnl_structure", "user_structure", "risk_exposure", "routing_quality"}
    assert "daily_turnover" not in response.json()["risk_exposure"]["abook"]


def test_population_account_response_keeps_ui_metrics_without_heavy_diagnostics():
    account = {
        "platform": "mt5",
        "login": 7,
        "account_group": "real",
        "book": "abook",
        "selection_source": "rule_filter",
        "selection": {"client_net_pnl": 10},
        "validation": {"client_net_pnl": 20},
        "monthly": [],
        "martingale_blocked": False,
        "martingale_risk_level": None,
        "risk_leverage_p95_ratio": 100,
        "martingale_record": {"large": "diagnostic"},
        "r4_record": {"large": "diagnostic"},
    }

    compact = _compact_population_account(account)

    assert compact["platform"] == "mt5"
    assert compact["selection"]["client_net_pnl"] == 10
    assert "martingale_record" not in compact
    assert "r4_record" not in compact


def test_book_analytics_reuses_overview_analysis_session(tmp_path, monkeypatch):
    for name in (
        "ABOOK_RISK_SNAPSHOT_PATH",
        "ABOOK_MARTINGALE_SNAPSHOT_PATH",
        "ABOOK_AVG_PROFIT_SNAPSHOT_PATH",
        "ABOOK_R4_SNAPSHOT_PATH",
    ):
        monkeypatch.setenv(name, str(tmp_path / f"{name}.json"))

    class SpyRepository:
        def __init__(self):
            self.analysis_calls = 0
            self.daily_calls = 0

        def fetch_analysis(self, request, excluded_logins=None):
            self.analysis_calls += 1
            return []

        def fetch_daily_pnl(self, request, excluded_logins=None):
            self.daily_calls += 1
            return []

    repository = SpyRepository()
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        overview = TestClient(app).post("/api/abook/analysis", json={"platforms": ["mt5"]})
        assert overview.status_code == 200
        token = overview.json()["analysis_token"]

        book = TestClient(app).post(
            "/api/abook/book-analytics",
            json={"analysis": {"platforms": ["mt5"]}, "analysis_token": token},
        )
    finally:
        app.dependency_overrides.clear()

    assert book.status_code == 200
    assert repository.analysis_calls == 1
    assert repository.daily_calls == 1


def test_analysis_defers_daily_pnl_query_until_book_analytics(tmp_path, monkeypatch):
    for name in (
        "ABOOK_RISK_SNAPSHOT_PATH",
        "ABOOK_MARTINGALE_SNAPSHOT_PATH",
        "ABOOK_AVG_PROFIT_SNAPSHOT_PATH",
        "ABOOK_R4_SNAPSHOT_PATH",
    ):
        monkeypatch.setenv(name, str(tmp_path / f"{name}.json"))

    class SpyRepository:
        def __init__(self):
            self.daily_calls = 0

        def fetch_analysis(self, request, excluded_logins=None):
            return []

        def fetch_daily_pnl(self, request, excluded_logins=None):
            self.daily_calls += 1
            return []

    repository = SpyRepository()
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        overview = TestClient(app).post("/api/abook/analysis", json={"platforms": ["mt5"]})
    finally:
        app.dependency_overrides.clear()

    assert overview.status_code == 200
    assert repository.daily_calls == 0


def test_book_analytics_skips_symbol_queries_when_symbols_are_not_requested(tmp_path, monkeypatch):
    for name in (
        "ABOOK_RISK_SNAPSHOT_PATH",
        "ABOOK_MARTINGALE_SNAPSHOT_PATH",
        "ABOOK_AVG_PROFIT_SNAPSHOT_PATH",
        "ABOOK_R4_SNAPSHOT_PATH",
    ):
        monkeypatch.setenv(name, str(tmp_path / f"{name}.json"))

    class SpyRepository:
        def fetch_analysis(self, request, excluded_logins=None):
            return []

        def fetch_daily_pnl(self, request, excluded_logins=None):
            return []

        def fetch_book_symbol_rows(self, *args, **kwargs):
            raise AssertionError("symbol queries must be skipped for non-user tabs")

    repository = SpyRepository()
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        overview = TestClient(app).post("/api/abook/analysis", json={"platforms": ["mt5"]})
        token = overview.json()["analysis_token"]
        book = TestClient(app).post(
            "/api/abook/book-analytics",
            json={
                "analysis": {"platforms": ["mt5"]},
                "analysis_token": token,
                "include_symbols": False,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert book.status_code == 200
