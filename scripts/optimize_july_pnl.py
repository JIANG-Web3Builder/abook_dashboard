#!/usr/bin/env python3
"""Grid-search Abook routing rules to maximize July (validation) Abook net P&L.

Loads ClickHouse + local snapshots once, then evaluates rule combinations in memory.
Under Abook company P&L = 0 and Bbook company P&L = -client_net_pnl, maximizing
Abook validation client net P&L is equivalent to maximizing company theoretical P&L.
"""

from __future__ import annotations

import itertools
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.avg_profit import build_avg_profit_filter
from app.config import load_env_file
from app.martingale import build_martingale_filter
from app.models import AnalysisPeriod, AnalysisRequest, AnalysisRules
from app.repository import ClickHouseRepository
from app.risk import build_local_risk_filter
from app.service import (
    _leverage_filter_pass,
    _long_trades_ratio_pass,
    build_two_stage_payload,
)


@dataclass(frozen=True)
class RuleCombo:
    min_trades: int
    min_win_rate: float
    min_profit_factor: float
    min_payoff_ratio: float
    min_long_trades_ratio: float
    max_long_trades_ratio: float
    max_top1_day_profit_contribution: float
    max_leverage_p95_ratio: float
    max_high_leverage_holding_seconds: float


@dataclass
class Candidate:
    selection: dict[str, Any]
    validation_pnl: float
    martingale_blocked: bool


def _load_rows(request: AnalysisRequest) -> tuple[list[dict[str, Any]], Any]:
    risk_filter = build_local_risk_filter(request)
    martingale_snapshot = build_martingale_filter(request)
    avg_profit_snapshot = build_avg_profit_filter(request)
    repository = ClickHouseRepository()
    overview_rows = repository.fetch_analysis(request)
    rows = martingale_snapshot.enrich_rows(risk_filter.snapshot.enrich_rows(overview_rows))
    rows = avg_profit_snapshot.enrich_rows(rows)
    return rows, martingale_snapshot


def _build_candidates(
    rows: list[dict[str, Any]],
    request: AnalysisRequest,
    martingale_snapshot: Any,
) -> list[Candidate]:
    # Wide-open routing gates so each combo can re-score in memory.
    payload = build_two_stage_payload(
        rows,
        overview_rows=rows,
        selection_start=request.selection.start.isoformat(),
        selection_end=request.selection.end.isoformat(),
        validation_start=request.validation.start.isoformat(),
        validation_end=request.validation.end.isoformat(),
        min_trades=0,
        min_win_rate=0.0,
        min_profit_factor=0.0,
        min_payoff_ratio=0.0,
        min_long_trades_ratio=0.0,
        max_long_trades_ratio=1.0,
        max_top1_day_profit_contribution=1.0,
        max_leverage_p95_ratio=1e12,
        max_high_leverage_holding_seconds=0.0,
        martingale_snapshot=martingale_snapshot,
        excluded_martingale_levels=["extreme", "high", "medium", "low"],
    )
    return [
        Candidate(
            selection=account["selection"],
            validation_pnl=float(account["validation"]["client_net_pnl"]),
            martingale_blocked=bool(account.get("martingale_blocked")),
        )
        for account in payload["accounts"]
    ]


def _score_combo(candidates: list[Candidate], combo: RuleCombo) -> dict[str, Any]:
    abook_pnl = 0.0
    abook_n = 0
    positive = 0
    negative = 0
    for item in candidates:
        if item.martingale_blocked:
            continue
        selection = item.selection
        if not (
            selection["trade_count"] >= combo.min_trades
            and (selection["profit_factor"] is None or selection["profit_factor"] > combo.min_profit_factor)
            and selection["win_rate"] >= combo.min_win_rate
            and selection["payoff_ratio"] >= combo.min_payoff_ratio
            and _long_trades_ratio_pass(
                selection, combo.min_long_trades_ratio, combo.max_long_trades_ratio
            )
            and selection["top_positive_day_concentration"] < combo.max_top1_day_profit_contribution
            and _leverage_filter_pass(
                selection,
                combo.max_leverage_p95_ratio,
                combo.max_high_leverage_holding_seconds,
            )
        ):
            continue
        pnl = item.validation_pnl
        abook_pnl += pnl
        abook_n += 1
        if pnl > 10:
            positive += 1
        elif pnl < -10:
            negative += 1
    return {
        "july_abook_net_pnl": round(abook_pnl, 2),
        "abook_accounts": abook_n,
        "abook_positive": positive,
        "abook_negative": negative,
        "rules": asdict(combo),
    }


def _grid() -> Iterable[RuleCombo]:
    trades = [40, 50, 60, 75, 100, 120]
    win_rates = [0.45, 0.50, 0.55, 0.60]
    profit_factors = [1.0, 1.1, 1.25, 1.4, 1.5]
    payoff_ratios = [0.4, 0.5, 0.6, 0.8]
    long_bands = [
        (0.40, 0.60),
        (0.35, 0.65),
        (0.30, 0.70),
        (0.45, 0.55),
        (0.00, 1.00),
    ]
    top1 = [0.20, 0.25, 0.30, 0.40, 0.50]
    leverage = [500, 1000, 2000, 5000]
    hold_seconds = [60.0, 300.0]
    for values in itertools.product(
        trades, win_rates, profit_factors, payoff_ratios, long_bands, top1, leverage, hold_seconds
    ):
        t, wr, pf, po, (long_min, long_max), t1, lev, hold = values
        yield RuleCombo(
            min_trades=t,
            min_win_rate=wr,
            min_profit_factor=pf,
            min_payoff_ratio=po,
            min_long_trades_ratio=long_min,
            max_long_trades_ratio=long_max,
            max_top1_day_profit_contribution=t1,
            max_leverage_p95_ratio=float(lev),
            max_high_leverage_holding_seconds=hold,
        )


def main() -> int:
    load_env_file()
    request = AnalysisRequest(
        selection=AnalysisPeriod(start=date(2026, 5, 1), end=date(2026, 6, 30)),
        validation=AnalysisPeriod(start=date(2026, 7, 1), end=date(2026, 7, 22)),
        platforms=["mt4", "mt5", "hh_mt5"],
        rules=AnalysisRules(),
        personal_candidate_list=False,
        news_candidate_list=False,
    )
    print("Loading ClickHouse population + snapshots...", flush=True)
    rows, martingale_snapshot = _load_rows(request)
    print(f"Loaded {len(rows)} rows; building candidates...", flush=True)
    candidates = _build_candidates(rows, request, martingale_snapshot)
    print(f"Candidates: {len(candidates)}; sweeping rule grid...", flush=True)

    best: dict[str, Any] | None = None
    qualifying: list[dict[str, Any]] = []
    evaluated = 0
    for combo in _grid():
        result = _score_combo(candidates, combo)
        evaluated += 1
        if result["july_abook_net_pnl"] >= 10000:
            qualifying.append(result)
        if best is None or result["july_abook_net_pnl"] > best["july_abook_net_pnl"]:
            best = result
            print(
                f"  new best {best['july_abook_net_pnl']:.2f} "
                f"(n={best['abook_accounts']}) @ {best['rules']}",
                flush=True,
            )
        if evaluated % 5000 == 0:
            print(f"  ... evaluated {evaluated}", flush=True)

    qualifying.sort(
        key=lambda item: (
            item["july_abook_net_pnl"],
            -item["abook_accounts"],
            item["rules"]["min_trades"],
            item["rules"]["min_profit_factor"],
        ),
        reverse=True,
    )
    chosen = qualifying[0] if qualifying else best
    out: dict[str, Any] = {
        "evaluated": evaluated,
        "best": best,
        "chosen": chosen,
        "qualifying_count": len(qualifying),
        "top10": (qualifying[:10] if qualifying else ([best] if best else [])),
        "window": {
            "selection": "2026-05-01..2026-06-30",
            "validation": "2026-07-01..2026-07-22",
        },
    }

    if chosen is not None:
        rules_kwargs = {
            **AnalysisRules().model_dump(),
            **chosen["rules"],
            "excluded_martingale_levels": ["extreme", "high", "medium", "low"],
        }
        rules = AnalysisRules(**rules_kwargs)
        verified = build_two_stage_payload(
            rows,
            overview_rows=rows,
            selection_start=request.selection.start.isoformat(),
            selection_end=request.selection.end.isoformat(),
            validation_start=request.validation.start.isoformat(),
            validation_end=request.validation.end.isoformat(),
            min_trades=rules.min_trades,
            min_win_rate=rules.min_win_rate,
            min_profit_factor=rules.min_profit_factor,
            min_payoff_ratio=rules.min_payoff_ratio,
            min_long_trades_ratio=rules.min_long_trades_ratio,
            max_long_trades_ratio=rules.max_long_trades_ratio,
            max_top1_day_profit_contribution=rules.max_top1_day_profit_contribution,
            max_leverage_p95_ratio=rules.max_leverage_p95_ratio,
            max_high_leverage_holding_seconds=rules.max_high_leverage_holding_seconds,
            martingale_snapshot=martingale_snapshot,
            excluded_martingale_levels=rules.excluded_martingale_levels,
        )
        abook = [a for a in verified["accounts"] if a["book"] == "abook"]
        verified_pnl = round(sum(float(a["validation"]["client_net_pnl"]) for a in abook), 2)
        print(f"Verified full path: abook={len(abook)} july_net_pnl={verified_pnl}", flush=True)
        out["verified"] = {
            "abook_accounts": len(abook),
            "july_abook_net_pnl": verified_pnl,
            "rules": chosen["rules"],
        }
        if abs(verified_pnl - chosen["july_abook_net_pnl"]) > 1.0:
            print(
                f"WARNING: score {chosen['july_abook_net_pnl']} vs verified {verified_pnl}",
                flush=True,
            )

    out_path = ROOT / "docs" / "analysis" / "2026-07-23-july-pnl-parameter-sweep.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"evaluated": evaluated, "chosen": chosen, "best": best}, indent=2, ensure_ascii=False))
    print(f"Wrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
