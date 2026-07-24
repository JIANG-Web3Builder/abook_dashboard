import pytest
import importlib
import json

from app.config import Settings
from app.models import AnalysisRequest


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


def _local_settings(tmp_path):
    return Settings(
        clickhouse_host="",
        clickhouse_port=8123,
        clickhouse_database="risk",
        clickhouse_user="",
        clickhouse_password="",
        clickhouse_secure=False,
        warehouse_path=tmp_path / "warehouse",
        data_source="local",
    )


def _write_ready_snapshot(path, *, generation="g1", selection_start="2026-05-01", selection_end="2026-06-30", validation_start="2026-07-01", validation_end="2026-07-22"):
    path.write_text(json.dumps({
        "warehouse_generation": generation,
        "selection_start": selection_start,
        "selection_end": selection_end,
        "validation_start": validation_start,
        "validation_end": validation_end,
        "records": [],
    }))


def test_snapshot_status_marks_selection_window_stale_but_validation_change_ready(monkeypatch, tmp_path):
    snapshot_refresh = _load_snapshot_refresh()
    paths = {
        "risk": tmp_path / "risk.json",
        "avg_profit": tmp_path / "avg_profit.json",
        "martingale": tmp_path / "martingale.json",
    }
    monkeypatch.setattr(snapshot_refresh, "snapshot_paths", lambda: paths)
    settings = _local_settings(tmp_path)
    settings.warehouse_path.mkdir(parents=True)
    (settings.warehouse_path / "manifest.json").write_text(json.dumps({"schema_version": 1, "generation": "g1", "tables": {}}))
    for path in paths.values():
        _write_ready_snapshot(path)

    selection_changed = AnalysisRequest.model_validate({
        "selection": {"start": "2026-05-14", "end": "2026-06-30"},
        "validation": {"start": "2026-07-01", "end": "2026-07-22"},
    })
    validation_changed = AnalysisRequest.model_validate({
        "validation": {"start": "2026-07-02", "end": "2026-07-22"},
    })

    stale = snapshot_refresh.snapshot_status_for_request(selection_changed, settings=settings)
    ready = snapshot_refresh.snapshot_status_for_request(validation_changed, settings=settings)

    assert stale["status"] == "stale"
    assert stale["reason"] == "selection_window"
    assert ready["status"] == "ready"
    assert ready["reason"] is None


def test_ensure_local_snapshots_rebuilds_only_when_selection_or_generation_is_stale(monkeypatch, tmp_path):
    snapshot_refresh = _load_snapshot_refresh()
    paths = {
        "risk": tmp_path / "risk.json",
        "avg_profit": tmp_path / "avg_profit.json",
        "martingale": tmp_path / "martingale.json",
    }
    monkeypatch.setattr(snapshot_refresh, "snapshot_paths", lambda: paths)
    settings = _local_settings(tmp_path)
    settings.warehouse_path.mkdir(parents=True)
    (settings.warehouse_path / "manifest.json").write_text(json.dumps({"schema_version": 1, "generation": "g1", "tables": {}}))
    for path in paths.values():
        _write_ready_snapshot(path)

    calls = []

    def fake_refresh(start, end, platforms, **kwargs):
        calls.append((start, end, platforms, kwargs))
        for path in paths.values():
            _write_ready_snapshot(
                path,
                generation=kwargs["warehouse_generation"],
                selection_start=start,
                selection_end=end,
                validation_start=kwargs["validation_start"],
                validation_end=kwargs["validation_end"],
            )
        return {"status": "ready", "selection_start": start, "selection_end": end, "refreshed": True}

    monkeypatch.setattr(snapshot_refresh, "refresh_snapshots", fake_refresh)
    request = AnalysisRequest.model_validate({
        "selection": {"start": "2026-05-14", "end": "2026-06-30"},
        "validation": {"start": "2026-07-01", "end": "2026-07-22"},
    })

    result = snapshot_refresh.ensure_local_snapshots_for_request(request, settings=settings)

    assert result["status"] == "ready"
    assert result["refreshed"] is True
    assert calls == [("2026-05-14", "2026-06-30", ["mt4", "mt5", "hh_mt5"], {
        "validation_start": "2026-07-01",
        "validation_end": "2026-07-22",
        "warehouse_generation": "g1",
    })]

    calls.clear()
    validation_only = AnalysisRequest.model_validate({
        **request.model_dump(),
        "validation": {"start": "2026-07-02", "end": "2026-07-22"},
    })
    ready = snapshot_refresh.ensure_local_snapshots_for_request(validation_only, settings=settings)
    assert ready["refreshed"] is False
    assert calls == []
