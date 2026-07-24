from __future__ import annotations

from pathlib import Path
import json
import time
from typing import Optional

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .analysis_cache import (
    AnalysisSession,
    analysis_session_cache,
    direction_analytics_cache,
    newcomer_analytics_cache,
    request_signature,
)
from .martingale import build_martingale_filter, load_martingale_snapshot, snapshot_path
from .avg_profit import build_avg_profit_filter, snapshot_path as avg_profit_snapshot_path
from .book_analytics import build_book_analytics
from .direction_analytics import build_direction_analytics_payload
from .newcomer_analytics import build_newcomer_account_sensitivity, build_newcomer_analytics_payload
from .exports import render_abook_csv
from .models import (
    AnalysisRequest,
    BookAnalyticsRequest,
    DirectionAnalyticsRequest,
    FilterOptions,
    NewcomerAccountRequest,
    NewcomerAnalyticsRequest,
)
from .personal_candidates import load_news_candidates, load_personal_candidates
from .config import get_settings
from .repository import ClickHouseRepository, LocalWarehouseRepository, RepositoryConfigurationError
from .risk import build_local_risk_filter
from .risk import snapshot_path as risk_snapshot_path
from .snapshot_refresh import ensure_local_snapshots_for_request
from .warehouse import WarehouseCoverageError, WarehouseError, load_manifest
from .service import (
    build_account_detail_payload,
    build_analysis_payload,
    build_two_stage_payload,
    prepare_analysis_context,
)
from .account_detail import build_direction_summary


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="A-Book Backtest Dashboard", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def get_repository() -> ClickHouseRepository:
    try:
        settings = get_settings()
        if settings.data_source == "local":
            return LocalWarehouseRepository(settings)
        return ClickHouseRepository(settings)
    except WarehouseCoverageError as exc:
        raise _coverage_http_exception(exc) from exc
    except RepositoryConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _coverage_http_exception(exc: WarehouseCoverageError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "warehouse_coverage_missing",
            "message": "本地数据未覆盖当前分析范围，请先运行 scripts/refresh_local_data.py",
            "missing": exc.missing,
        },
    )


def _fetch_daily_rows(
    repository: ClickHouseRepository,
    request: AnalysisRequest,
    excluded_logins: set[tuple[str, int]] | None = None,
) -> list[dict]:
    method = getattr(repository, "fetch_daily_pnl", None)
    if not callable(method):
        return []
    return method(request, excluded_logins=excluded_logins)


def _compact_population_account(account: dict) -> dict:
    """Keep only fields the overview uses for its population summaries.

    The full account objects stay in the server-side analysis session for the
    lazy Book analytics endpoint.  Omitting diagnostic snapshot records from
    the initial response avoids serializing the same large objects twice.
    """
    response_fields = (
        "platform",
        "login",
        "account_group",
        "book",
        "selection_source",
        "selection",
        "validation",
        "monthly",
        "stability",
        "risk_leverage_p95_ratio",
        "martingale_blocked",
        "martingale_risk_level",
    )
    return {key: account[key] for key in response_fields if key in account}


def _store_analysis_session(
    request: AnalysisRequest,
    payload: dict,
    *,
    daily_rows: list[dict] | None,
    overview_daily_rows: list[dict] | None,
) -> dict:
    token = analysis_session_cache.put(
        AnalysisSession(
            signature=request_signature(request),
            payload=payload,
            daily_rows=None if daily_rows is None else list(daily_rows),
            overview_daily_rows=overview_daily_rows,
            created_at=time.monotonic(),
        )
    )
    payload["analysis_token"] = token
    response_payload = dict(payload)
    population_accounts = payload.get("population_accounts")
    if isinstance(population_accounts, list):
        response_payload["population_accounts"] = [
            _compact_population_account(account) for account in population_accounts
        ]
    return response_payload


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "abook-dashboard"}


@app.get("/api/warehouse/status")
def warehouse_status() -> dict:
    settings = get_settings()
    if settings.data_source == "remote":
        return {"source": "remote", "status": "remote"}
    manifest = load_manifest(settings.warehouse_path / "manifest.json")
    generation = manifest.get("generation")
    snapshots = {}
    for name, path in {
        "risk": risk_snapshot_path(),
        "avg_profit": avg_profit_snapshot_path(),
        "martingale": snapshot_path(),
    }.items():
        if not path.exists():
            snapshots[name] = {"status": "missing", "path": str(path)}
            continue
        try:
            payload = json.loads(path.read_text())
            snapshot_generation = payload.get("warehouse_generation")
            status = "stale" if generation and snapshot_generation != generation else "ready"
            snapshots[name] = {
                "status": status,
                "path": str(path),
                "warehouse_generation": snapshot_generation,
                "platforms": payload.get("platforms"),
                "selection_start": payload.get("selection_start"),
                "selection_end": payload.get("selection_end"),
                "validation_start": payload.get("validation_start"),
                "validation_end": payload.get("validation_end"),
            }
        except (OSError, ValueError):
            snapshots[name] = {"status": "invalid", "path": str(path)}
    return {
        "source": "local",
        "status": "ready" if generation else "missing",
        "warehouse_path": str(settings.warehouse_path),
        "generation": generation,
        "updated_at": manifest.get("updated_at"),
        "data_start": manifest.get("data_start"),
        "data_end": manifest.get("data_end"),
        "platforms": manifest.get("platforms", []),
        "tables": manifest.get("tables", {}),
        "snapshots": snapshots,
    }


@app.post("/api/warehouse/snapshots/refresh")
def refresh_local_snapshots(request: AnalysisRequest) -> dict:
    settings = get_settings()
    if settings.data_source != "local":
        raise HTTPException(
            status_code=409,
            detail="当前数据源为远端，快照重建仅支持本地 Warehouse",
        )
    try:
        result = ensure_local_snapshots_for_request(request, settings=settings)
    except WarehouseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if result.get("status") == "error":
        raise HTTPException(
            status_code=502,
            detail={
                "code": "snapshot_refresh_failed",
                "message": str(result.get("error") or "本地快照重建失败"),
            },
        )
    return result


@app.get("/api/abook/filters", response_model=FilterOptions)
def filter_options(repository: ClickHouseRepository = Depends(get_repository)) -> dict[str, list[str]]:
    try:
        return repository.fetch_filter_options()
    except WarehouseCoverageError as exc:
        raise _coverage_http_exception(exc) from exc
    except RepositoryConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="ClickHouse filter query failed") from exc


@app.post("/api/abook/analysis")
def analysis(request: AnalysisRequest, repository: ClickHouseRepository = Depends(get_repository)) -> dict:
    try:
        snapshot_refresh = ensure_local_snapshots_for_request(request)
        if snapshot_refresh.get("status") == "error":
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "snapshot_refresh_failed",
                    "message": str(snapshot_refresh.get("error") or "本地快照重建失败"),
                },
            )
        personal_candidates = load_personal_candidates()
        news_candidates = load_news_candidates()
        avg_profit_snapshot = build_avg_profit_filter(request)
        personal_candidates_enabled = request.personal_candidate_list and personal_candidates.status == "ready"
        news_candidates_enabled = request.news_candidate_list and news_candidates.status == "ready"
        personal_candidate_logins = personal_candidates.login_ids if personal_candidates_enabled else frozenset()
        news_candidate_logins = news_candidates.login_ids if news_candidates_enabled else frozenset()
        risk_filter = build_local_risk_filter(request)
        martingale_snapshot = build_martingale_filter(request)
        effective_request = risk_filter.apply(request)
        overview_rows = None
        overview_daily_rows = None
        if personal_candidates_enabled or news_candidates_enabled or risk_filter.allowed_logins is not None or risk_filter.excluded_logins:
            # Company-profit overview must remain population-level. Do not let
            # the Abook leverage rule remove users from the monthly baseline.
            overview_rows = repository.fetch_analysis(request)
        if personal_candidates_enabled or news_candidates_enabled:
            # Candidate lists are explicit Abook overrides. Keep the
            # normal platform/group/login/test-demo query boundaries, but do
            # not let the local leverage snapshot remove them.
            rows = overview_rows or []
        elif risk_filter.is_empty_for(request):
            rows = []
        else:
            excluded_logins = risk_filter.excluded_logins
            rows = repository.fetch_analysis(effective_request, excluded_logins=excluded_logins) if excluded_logins else repository.fetch_analysis(effective_request)
        rows = martingale_snapshot.enrich_rows(risk_filter.snapshot.enrich_rows(rows))
        rows = avg_profit_snapshot.enrich_rows(rows)
        if overview_rows is not None:
            overview_rows = martingale_snapshot.enrich_rows(risk_filter.snapshot.enrich_rows(overview_rows))
            overview_rows = avg_profit_snapshot.enrich_rows(overview_rows)
        # Risk SQL exclusions are an optimization for the Abook candidate query.
        # Final routing and company P&L must still classify the full population:
        # excluded high-leverage users belong in Bbook, not outside the report.
        routing_rows = overview_rows if overview_rows is not None else rows
        if request.lookback_months is not None:
            payload = build_analysis_payload(
                rows,
                request.lookback_months,
                min_profit_factor=request.min_profit_factor if request.min_profit_factor is not None else request.rules.min_profit_factor,
            )
            payload["risk_management"] = risk_filter.summary()
            payload["martingale"] = martingale_snapshot.summary(request.rules.excluded_martingale_levels)
            payload["avg_profit"] = avg_profit_snapshot.summary()
            payload["snapshot_refresh"] = snapshot_refresh
            return _store_analysis_session(
                request,
                payload,
                daily_rows=[],
                overview_daily_rows=overview_daily_rows,
            )
        # Daily account rows are only needed by the lazy Book analytics tabs.
        # Defer this query so the overview can render as soon as the account
        # aggregates are available.
        daily_rows = None
        rules = request.rules
        payload = build_two_stage_payload(
            routing_rows,
            overview_rows=overview_rows,
            daily_rows=daily_rows,
            overview_daily_rows=overview_daily_rows,
            selection_start=request.selection.start.isoformat(),
            selection_end=request.selection.end.isoformat(),
            validation_start=request.validation.start.isoformat(),
            validation_end=request.validation.end.isoformat(),
            min_trades=rules.min_trades,
            min_win_rate=rules.min_win_rate,
            min_profit_factor=rules.min_profit_factor,
            min_payoff_ratio=rules.min_payoff_ratio,
            min_selection_monthly_consistency=rules.min_selection_monthly_consistency,
            min_positive_month_rate=rules.min_positive_month_rate,
            max_top1_day_profit_contribution=rules.max_top1_day_profit_contribution,
            min_long_trades_ratio=rules.min_long_trades_ratio,
            max_long_trades_ratio=rules.max_long_trades_ratio,
            max_daily_profit_month_contribution=rules.max_daily_profit_month_contribution,
            max_leverage_p95_ratio=rules.max_leverage_p95_ratio,
            max_high_leverage_holding_seconds=rules.max_high_leverage_holding_seconds,
            risk_snapshot_status=risk_filter.status,
            min_direction_day_rate_lower_bound=rules.min_direction_day_rate_lower_bound,
            min_stability_score=rules.min_stability_score,
            high_confidence_trades=rules.high_confidence_trades,
            high_confidence_days=rules.high_confidence_days,
            personal_candidate_logins=set(personal_candidate_logins),
            news_candidate_logins=set(news_candidate_logins),
            personal_candidate_info=(
                personal_candidates.summary(enabled=request.personal_candidate_list)
            ),
            news_candidate_info=(
                news_candidates.summary(enabled=request.news_candidate_list)
            ),
            martingale_snapshot=martingale_snapshot,
            excluded_martingale_levels=request.rules.excluded_martingale_levels,
            avg_profit_snapshot_status=avg_profit_snapshot.status,
        )
        payload["risk_management"] = risk_filter.summary()
        payload["martingale"] = martingale_snapshot.summary(request.rules.excluded_martingale_levels)
        payload["avg_profit"] = avg_profit_snapshot.summary()
        payload["snapshot_refresh"] = snapshot_refresh
        return _store_analysis_session(
            request,
            payload,
            daily_rows=daily_rows,
            overview_daily_rows=overview_daily_rows,
        )
    except WarehouseCoverageError as exc:
        raise _coverage_http_exception(exc) from exc
    except HTTPException:
        raise
    except WarehouseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RepositoryConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="ClickHouse analysis query failed") from exc


@app.get("/api/abook/accounts/{platform}/{login}")
def account_detail(
    platform: str,
    login: int,
    start: str = "2026-05-01",
    end: str = "2026-07-22",
    selection_start: str = "2026-05-01",
    selection_end: str = "2026-06-30",
    validation_start: str = "2026-07-01",
    validation_end: str = "2026-07-22",
    repository: ClickHouseRepository = Depends(get_repository),
) -> dict:
    try:
        rows = repository.fetch_account_detail(platform, login, start, end)
        payload = build_account_detail_payload(rows)
        fetch_direction_summary = getattr(repository, "fetch_account_direction_summary", None)
        direction_rows = fetch_direction_summary(
            platform,
            login,
            start,
            end,
            selection_start,
            selection_end,
            validation_start,
            validation_end,
        ) if callable(fetch_direction_summary) else []
        payload["direction_summary"] = build_direction_summary(direction_rows)
        snapshot = load_martingale_snapshot(snapshot_path(), selection_start, selection_end, [platform])
        payload["martingale"] = {
            **snapshot.summary(),
            "record": snapshot.indexed_records().get((platform, int(login))) if snapshot.status == "ready" else None,
        }
        return payload
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except WarehouseCoverageError as exc:
        raise _coverage_http_exception(exc) from exc
    except RepositoryConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="ClickHouse account query failed") from exc


@app.post("/api/abook/book-analytics")
def book_analytics(
    request: BookAnalyticsRequest,
    repository: ClickHouseRepository = Depends(get_repository),
) -> dict:
    try:
        cached_session = analysis_session_cache.get(
            request.analysis_token,
            request_signature(request.analysis),
        )
        if cached_session is not None:
            analysis_payload = cached_session.payload
            daily_rows = cached_session.daily_rows
            overview_daily_rows = cached_session.overview_daily_rows
            if daily_rows is None:
                daily_rows = _fetch_daily_rows(repository, request.analysis)
                overview_daily_rows = overview_daily_rows if overview_daily_rows is not None else daily_rows
        else:
            analysis_payload = analysis(request.analysis, repository)
            cached_session = analysis_session_cache.get(
                analysis_payload.get("analysis_token"),
                request_signature(request.analysis),
            )
            if cached_session is not None:
                daily_rows = cached_session.daily_rows
                overview_daily_rows = cached_session.overview_daily_rows
                if daily_rows is None:
                    daily_rows = _fetch_daily_rows(repository, request.analysis)
                    overview_daily_rows = overview_daily_rows if overview_daily_rows is not None else daily_rows
            else:
                daily_rows = _fetch_daily_rows(repository, request.analysis)
                overview_daily_rows = None
        population_accounts = analysis_payload.get("population_accounts")
        accounts = population_accounts if population_accounts is not None else analysis_payload.get("accounts", [])
        abook_keys = {
            (str(account["platform"]), int(account["login"]))
            for account in accounts
            if account.get("book") == "abook"
        }
        if request.abook_accounts and population_accounts is None:
            abook_keys = {(item.platform, int(item.login)) for item in request.abook_accounts}
        symbol_method = getattr(repository, "fetch_book_symbol_rows", None)
        if request.include_symbols and callable(symbol_method):
            symbol_rows = {
                "population": symbol_method(request.analysis),
                "abook": symbol_method(request.analysis, account_keys=abook_keys),
            }
        else:
            symbol_rows = {"population": [], "abook": []}
        context = prepare_analysis_context(
            [],
            daily_rows=daily_rows,
            overview_daily_rows=overview_daily_rows if overview_daily_rows is not None else daily_rows,
            selection_start=request.analysis.selection.start.isoformat(),
            selection_end=request.analysis.selection.end.isoformat(),
            validation_start=request.analysis.validation.start.isoformat(),
            validation_end=request.analysis.validation.end.isoformat(),
        )
        result = build_book_analytics(context, accounts, abook_keys, symbol_rows)
        result["coverage"] = analysis_payload.get("coverage", {})
        result["book_counts"] = {
            "population": len(accounts),
            "abook": sum(1 for account in accounts if (str(account["platform"]), int(account["login"])) in abook_keys),
            "bbook": sum(1 for account in accounts if (str(account["platform"]), int(account["login"])) not in abook_keys),
        }
        return result
    except WarehouseCoverageError as exc:
        raise _coverage_http_exception(exc) from exc
    except RepositoryConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="ClickHouse book analytics query failed") from exc


@app.post("/api/abook/direction-analytics")
def direction_analytics(
    request: DirectionAnalyticsRequest,
    repository: ClickHouseRepository = Depends(get_repository),
) -> dict:
    try:
        signature = request_signature(request.analysis)
        cached_result = direction_analytics_cache.get(signature)
        if cached_result is not None:
            return cached_result
        cached_session = analysis_session_cache.get(
            request.analysis_token,
            signature,
        )
        if cached_session is None:
            analysis_payload = analysis(request.analysis, repository)
            cached_session = analysis_session_cache.get(
                analysis_payload.get("analysis_token"),
                signature,
            )
            if cached_session is not None:
                analysis_payload = cached_session.payload
        else:
            analysis_payload = cached_session.payload
        rows = repository.fetch_direction_matched_facts(request.analysis)
        result = build_direction_analytics_payload(
            rows,
            analysis_payload.get("population_accounts") or analysis_payload.get("accounts", []),
            request.analysis,
        )
        direction_analytics_cache.put(signature, result)
        return result
    except WarehouseCoverageError as exc:
        raise _coverage_http_exception(exc) from exc
    except RepositoryConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="ClickHouse direction analytics query failed") from exc


def _resolve_analysis_session(analysis_request: AnalysisRequest, analysis_token: str | None, repository: ClickHouseRepository):
    signature = request_signature(analysis_request)
    cached_session = analysis_session_cache.get(analysis_token, signature)
    if cached_session is not None:
        return signature, cached_session.payload
    analysis_payload = analysis(analysis_request, repository)
    cached_session = analysis_session_cache.get(analysis_payload.get("analysis_token"), signature)
    if cached_session is not None:
        return signature, cached_session.payload
    return signature, analysis_payload


@app.post("/api/abook/newcomer-analytics")
def newcomer_analytics(
    request: NewcomerAnalyticsRequest,
    repository: ClickHouseRepository = Depends(get_repository),
) -> dict:
    try:
        cache_signature = request_signature(request)
        cached_result = newcomer_analytics_cache.get(cache_signature)
        if cached_result is not None:
            return cached_result
        _, analysis_payload = _resolve_analysis_session(
            request.analysis,
            request.analysis_token,
            repository,
        )
        rows = repository.fetch_newcomer_daily_facts(request.analysis)
        result = build_newcomer_analytics_payload(
            analysis_payload.get("population_accounts") or analysis_payload.get("accounts", []),
            rows,
            request.analysis,
            max_active_days=request.max_active_days,
        )
        newcomer_analytics_cache.put(cache_signature, result)
        return result
    except WarehouseCoverageError as exc:
        raise _coverage_http_exception(exc) from exc
    except RepositoryConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="ClickHouse newcomer analytics query failed") from exc


@app.post("/api/abook/newcomer-account")
def newcomer_account(
    request: NewcomerAccountRequest,
    repository: ClickHouseRepository = Depends(get_repository),
) -> dict:
    try:
        _, analysis_payload = _resolve_analysis_session(
            request.analysis,
            request.analysis_token,
            repository,
        )
        population = analysis_payload.get("population_accounts") or analysis_payload.get("accounts", [])
        account = next(
            (
                item
                for item in population
                if str(item.get("platform")) == request.platform and int(item.get("login")) == request.login
            ),
            None,
        )
        if account is None:
            raise HTTPException(status_code=404, detail="account not found in analysis population")
        if account.get("book") == "abook":
            raise HTTPException(status_code=400, detail="overview Abook accounts are excluded from newcomer tab")
        filtered = request.analysis.model_copy(deep=True)
        filtered.filters.logins = [request.login]
        filtered.platforms = [request.platform]
        account_days = [
            row
            for row in repository.fetch_newcomer_daily_facts(filtered)
            if str(row.get("platform")) == request.platform and int(row.get("login")) == request.login
        ]
        min_trades_values = list(request.min_trades_values)
        if request.min_trades is not None and request.min_trades not in min_trades_values:
            min_trades_values = sorted({*min_trades_values, request.min_trades})
        return build_newcomer_account_sensitivity(
            account,
            account_days,
            request.analysis,
            min_trades_values=min_trades_values,
            max_active_days=request.max_active_days,
            stats_end=request.stats_end,
        )
    except HTTPException:
        raise
    except WarehouseCoverageError as exc:
        raise _coverage_http_exception(exc) from exc
    except RepositoryConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="ClickHouse newcomer account query failed") from exc


def _export_response(request: AnalysisRequest, repository: ClickHouseRepository) -> StreamingResponse:
    payload = analysis(request, repository)
    content = render_abook_csv(payload.get("accounts", []))
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=abook_accounts.csv"},
    )


@app.get("/api/abook/export")
def export(
    request: Optional[AnalysisRequest] = Body(default=None),
    repository: ClickHouseRepository = Depends(get_repository),
) -> StreamingResponse:
    return _export_response(request or AnalysisRequest(), repository)


@app.post("/api/abook/export")
def export_post(
    request: AnalysisRequest,
    repository: ClickHouseRepository = Depends(get_repository),
) -> StreamingResponse:
    return _export_response(request, repository)


if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")
