from fastapi.testclient import TestClient

from app.main import app, get_repository


def test_export_endpoint_returns_utf8_csv_header(tmp_path, monkeypatch):
    monkeypatch.setattr("app.main.ensure_local_snapshots_for_request", lambda *args, **kwargs: {"status": "ready", "refreshed": False})
    monkeypatch.setenv("ABOOK_MARTINGALE_SNAPSHOT_PATH", str(tmp_path / "missing.json"))

    class FakeRepository:
        def fetch_analysis(self, request, excluded_logins=None):
            return []

        def fetch_daily_pnl(self, request, excluded_logins=None):
            return []

    app.dependency_overrides[get_repository] = lambda: FakeRepository()
    try:
        response = TestClient(app).get("/api/abook/export")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.text.startswith("\ufeffplatform,login")
