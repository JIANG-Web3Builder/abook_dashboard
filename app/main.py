from __future__ import annotations

from pathlib import Path
import time
from typing import Optional

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .analysis_cache import AnalysisSession, analysis_session_cache, request_signature
from .martingale import build_martingale_filter, load_martingale_snapshot, snapshot_path
from .avg_profit import build_avg_profit_filter
from .book_analytics import build_book_analytics
from .exports import render_abook_csv
from .models import AnalysisRequest, BookAnalyticsRequest, FilterOptions, SnapshotRefreshRequest
from .personal_candidates import load_news_candidates, load_personal_candidates
from .repository import ClickHouseRepository, RepositoryConfigurationError
from .snapshot_refresh import refresh_snapshots
from .risk import build_local_risk_filter
from .r4 import build_r4_filter
from .service import (
    build_account_detail_payload,
    build_analysis_payload,
    build_two_stage_payload,
    prepare_analysis_context,
)


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
        return ClickHouseRepository()
    except RepositoryConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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


@app.post("/api/abook/refresh-snapshots")
def refresh_snapshot_files(request: SnapshotRefreshRequest) -> dict:
    result = refresh_snapshots(
        request.selection.start.isoformat(),
        request.selection.end.isoformat(),
        request.platforms,
    )
    if result.get("status") != "ready":
        raise HTTPException(
            status_code=502,
            detail=str(result.get("error", "snapshot refresh failed")),
        )
    return result


@app.get("/api/abook/filters", response_model=FilterOptions)
def filter_options(repository: ClickHouseRepository = Depends(get_repository)) -> dict[str, list[str]]:
    try:
        return repository.fetch_filter_options()
    except RepositoryConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="ClickHouse filter query failed") from exc


@app.post("/api/abook/analysis")
def analysis(request: AnalysisRequest, repository: ClickHouseRepository = Depends(get_repository)) -> dict:
    try:
        personal_candidates = load_personal_candidates()
        news_candidates = load_news_candidates()
        avg_profit_snapshot = build_avg_profit_filter(request)
        personal_candidates_enabled = request.personal_candidate_list and personal_candidates.status == "ready"
        news_candidates_enabled = request.news_candidate_list and news_candidates.status == "ready"
        personal_candidate_logins = personal_candidates.login_ids if personal_candidates_enabled else frozenset()
        news_candidate_logins = news_candidates.login_ids if news_candidates_enabled else frozenset()
        risk_filter = build_local_risk_filter(request)
        r4_snapshot = build_r4_filter(request)
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
        rows = r4_snapshot.enrich_rows(rows)
        rows = avg_profit_snapshot.enrich_rows(rows)
        if overview_rows is not None:
            overview_rows = martingale_snapshot.enrich_rows(risk_filter.snapshot.enrich_rows(overview_rows))
            overview_rows = r4_snapshot.enrich_rows(overview_rows)
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
                min_avg_daily_profit=request.min_avg_daily_profit if request.min_avg_daily_profit is not None else request.rules.min_avg_daily_profit,
            )
            payload["risk_management"] = risk_filter.summary()
            payload["martingale"] = martingale_snapshot.summary(request.rules.excluded_martingale_levels)
            payload["avg_profit"] = avg_profit_snapshot.summary()
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
            min_active_days=rules.min_active_days,
            min_win_rate=rules.min_win_rate,
            min_profit_factor=rules.min_profit_factor,
            min_payoff_ratio=rules.min_payoff_ratio,
            min_avg_daily_profit=rules.min_avg_daily_profit,
            min_avg_profit=rules.min_avg_profit,
            min_selection_monthly_consistency=rules.min_selection_monthly_consistency,
            min_positive_month_rate=rules.min_positive_month_rate,
            max_top1_day_profit_contribution=rules.max_top1_day_profit_contribution,
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
            require_selection_monthly_positive=rules.require_selection_monthly_positive,
            enable_r4=rules.enable_r4,
            r4_min_passing_weeks=rules.r4_min_passing_weeks,
        )
        payload["risk_management"] = risk_filter.summary()
        payload["r4"] = {
            **r4_snapshot.summary(),
            "routed_users": sum(1 for account in payload.get("accounts", []) if account.get("r4_pass")),
        }
        payload["martingale"] = martingale_snapshot.summary(request.rules.excluded_martingale_levels)
        payload["avg_profit"] = avg_profit_snapshot.summary()
        return _store_analysis_session(
            request,
            payload,
            daily_rows=daily_rows,
            overview_daily_rows=overview_daily_rows,
        )
    except RepositoryConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="ClickHouse analysis query failed") from exc


@app.get("/api/abook/accounts/{platform}/{login}")
def account_detail(
    platform: str,
    login: int,
    start: str = "2026-05-01",
    end: str = "2026-07-16",
    selection_start: str = "2026-05-01",
    selection_end: str = "2026-06-30",
    repository: ClickHouseRepository = Depends(get_repository),
) -> dict:
    try:
        rows = repository.fetch_account_detail(platform, login, start, end)
        payload = build_account_detail_payload(rows)
        snapshot = load_martingale_snapshot(snapshot_path(), selection_start, selection_end, [platform])
        payload["martingale"] = {
            **snapshot.summary(),
            "record": snapshot.indexed_records().get((platform, int(login))) if snapshot.status == "ready" else None,
        }
        return payload
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    except RepositoryConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="ClickHouse book analytics query failed") from exc


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
