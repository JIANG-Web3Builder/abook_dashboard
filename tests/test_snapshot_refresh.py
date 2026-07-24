import pytest
import importlib


def _load_snapshot_refresh():
    try:
        return importlib.import_module("app.snapshot_refresh")
    except ImportError as exc:
        pytest.fail(f"snapshot refresh module is not implemented: {exc}")


def test_refresh_snapshots_builds_all_three_payloads_with_current_selection(monkeypatch, tmp_path):
    snapshot_refresh = _load_snapshot_refresh()
    calls = []
    monkeypatch.setattr(
        snapshot_refresh,
        "risk_build_snapshot",
        lambda start, end, platforms: calls.append(("risk", start, end, platforms)) or {"records": [{"login": 1}]},
    )
    monkeypatch.setattr(
        snapshot_refresh,
        "avg_profit_build_snapshot",
        lambda start, end, platforms: calls.append(("avg_profit", start, end, platforms)) or {"records": [{"login": 1}]},
    )
    monkeypatch.setattr(
        snapshot_refresh,
        "martingale_build_snapshot",
        lambda start, end, platforms: calls.append(("martingale", start, end, platforms)) or {"records": [{"login": 1}]},
    )
    paths = {
        "risk": tmp_path / "risk.json",
        "avg_profit": tmp_path / "avg_profit.json",
        "martingale": tmp_path / "martingale.json",
    }
    monkeypatch.setattr(snapshot_refresh, "snapshot_paths", lambda: paths)

    result = snapshot_refresh.refresh_snapshots("2026-05-01", "2026-06-30", ["mt4", "mt5", "hh_mt5"])

    assert result["status"] == "ready"
    assert {(name, start, end) for name, start, end, _ in calls} == {
        ("risk", "2026-05-01", "2026-06-30"),
        ("avg_profit", "2026-05-01", "2026-06-30"),
        ("martingale", "2026-05-01", "2026-06-30"),
    }
    assert all(path.exists() for path in paths.values())
    assert all(platforms == ["mt4", "mt5", "hh_mt5"] for _, _, _, platforms in calls)


def test_refresh_snapshots_keeps_existing_files_when_a_builder_fails(monkeypatch, tmp_path):
    snapshot_refresh = _load_snapshot_refresh()
    paths = {
        "risk": tmp_path / "risk.json",
        "avg_profit": tmp_path / "avg_profit.json",
        "martingale": tmp_path / "martingale.json",
    }
    for path in paths.values():
        path.write_text("old")
    monkeypatch.setattr(snapshot_refresh, "snapshot_paths", lambda: paths)
    monkeypatch.setattr(snapshot_refresh, "risk_build_snapshot", lambda *args: {"records": []})
    monkeypatch.setattr(
        snapshot_refresh,
        "avg_profit_build_snapshot",
        lambda *args: (_ for _ in ()).throw(RuntimeError("source unavailable")),
    )
    monkeypatch.setattr(snapshot_refresh, "martingale_build_snapshot", lambda *args: {"records": []})

    result = snapshot_refresh.refresh_snapshots("2026-05-01", "2026-06-30", ["mt5"])

    assert result["status"] == "error"
    assert "source unavailable" in result["error"]
    assert all(path.read_text() == "old" for path in paths.values())


def test_refresh_snapshots_stamps_validation_and_warehouse_generation(monkeypatch, tmp_path):
    snapshot_refresh = _load_snapshot_refresh()
    paths = {
        "risk": tmp_path / "risk.json",
        "avg_profit": tmp_path / "avg_profit.json",
        "martingale": tmp_path / "martingale.json",
    }
    monkeypatch.setattr(snapshot_refresh, "snapshot_paths", lambda: paths)
    for name in ("risk_build_snapshot", "avg_profit_build_snapshot", "martingale_build_snapshot"):
        monkeypatch.setattr(snapshot_refresh, name, lambda *args: {"records": []})

    result = snapshot_refresh.refresh_snapshots(
        "2026-05-01",
        "2026-06-30",
        ["mt5"],
        validation_start="2026-07-01",
        validation_end="2026-07-23",
        warehouse_generation="g1",
    )

    assert result["status"] == "ready"
    for path in paths.values():
        payload = __import__("json").loads(path.read_text())
        assert payload["validation_start"] == "2026-07-01"
        assert payload["validation_end"] == "2026-07-23"
        assert payload["warehouse_generation"] == "g1"
