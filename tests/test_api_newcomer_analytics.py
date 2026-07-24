from fastapi.testclient import TestClient

from app.analysis_cache import newcomer_analytics_cache
from app.main import app, get_repository


def test_newcomer_analytics_endpoint_reuses_analysis_session_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr("app.main.ensure_local_snapshots_for_request", lambda *args, **kwargs: {"status": "ready", "refreshed": False})
    for name in (
        "ABOOK_RISK_SNAPSHOT_PATH",
        "ABOOK_MARTINGALE_SNAPSHOT_PATH",
        "ABOOK_AVG_PROFIT_SNAPSHOT_PATH",
    ):
        monkeypatch.setenv(name, str(tmp_path / f"{name}.json"))

    class SpyRepository:
        def __init__(self):
            self.analysis_calls = 0
            self.newcomer_calls = 0

        def fetch_analysis(self, request, excluded_logins=None):
            self.analysis_calls += 1
            return []

        def fetch_daily_pnl(self, request, excluded_logins=None):
            return []

        def fetch_newcomer_daily_facts(self, request, excluded_logins=None):
            self.newcomer_calls += 1
            return []

    repository = SpyRepository()
    newcomer_analytics_cache.clear()
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        overview = TestClient(app).post("/api/abook/analysis", json={"platforms": ["mt5"]})
        assert overview.status_code == 200
        token = overview.json()["analysis_token"]
        body = {
            "analysis": {"platforms": ["mt5"]},
            "analysis_token": token,
            "max_active_days": 60,
        }
        response = TestClient(app).post("/api/abook/newcomer-analytics", json=body)
        cached_response = TestClient(app).post("/api/abook/newcomer-analytics", json=body)
    finally:
        app.dependency_overrides.clear()
        newcomer_analytics_cache.clear()

    assert response.status_code == 200
    assert cached_response.status_code == 200
    assert response.json()["kpi"]["admitted_accounts"] == 0
    assert repository.analysis_calls == 1
    assert repository.newcomer_calls == 1
