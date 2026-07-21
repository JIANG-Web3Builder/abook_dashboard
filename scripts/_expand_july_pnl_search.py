#!/usr/bin/env python3
"""Expanded July Abook P&L search targeting >20k. Does not change defaults."""

from __future__ import annotations

import itertools
import json
import random
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.avg_profit import build_avg_profit_filter
from app.config import load_env_file
from app.martingale import build_martingale_filter
from app.models import AnalysisPeriod, AnalysisRequest, AnalysisRules
from app.personal_candidates import load_news_candidates, load_personal_candidates
from app.r4 import build_r4_filter
from app.repository import ClickHouseRepository
from app.risk import build_local_risk_filter
from app.service import _leverage_filter_pass, build_two_stage_payload


@dataclass(frozen=True)
class RuleCombo:
    min_trades: int
    min_win_rate: float
    min_profit_factor: float
    min_payoff_ratio: float
    max_top1_day_profit_contribution: float
    max_leverage_p95_ratio: float
    max_high_leverage_holding_seconds: float
    enable_r4: bool
    r4_min_passing_weeks: int
    excluded_martingale_levels: tuple[str, ...]
    personal_candidate_list: bool
    news_candidate_list: bool


@dataclass
class Candidate:
    login: int
    selection: dict[str, Any]
    validation_pnl: float
    enrichment_r4_pass: bool
    r4_passing_weeks: int
    martingale_level: str | None
    martingale_confirmed: bool


def _load_rows(request: AnalysisRequest) -> tuple[list[dict[str, Any]], Any]:
    risk_filter = build_local_risk_filter(request)
    r4_snapshot = build_r4_filter(request)
    martingale_snapshot = build_martingale_filter(request)
    avg_profit_snapshot = build_avg_profit_filter(request)
    repository = ClickHouseRepository()
    overview_rows = repository.fetch_analysis(request)
    rows = martingale_snapshot.enrich_rows(risk_filter.snapshot.enrich_rows(overview_rows))
    rows = r4_snapshot.enrich_rows(rows)
    rows = avg_profit_snapshot.enrich_rows(rows)
    return rows, martingale_snapshot


def _build_candidates(
    rows: list[dict[str, Any]],
    request: AnalysisRequest,
    martingale_snapshot: Any,
) -> list[Candidate]:
    enrichment: dict[tuple[str, int], tuple[bool, int, str | None, bool]] = {}
    for row in rows:
        key = (str(row["platform"]), int(row["login"]))
        if key not in enrichment:
            enrichment[key] = (
                bool(row.get("r4_pass", False)),
                int(row.get("r4_passing_weeks", 0) or 0),
                row.get("martingale_level"),
                bool(row.get("martingale_confirmed", False)),
            )

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
        max_top1_day_profit_contribution=1.0,
        max_leverage_p95_ratio=1e12,
        max_high_leverage_holding_seconds=0.0,
        martingale_snapshot=martingale_snapshot,
        excluded_martingale_levels=[],
        enable_r4=False,
        r4_min_passing_weeks=1,
    )
    candidates: list[Candidate] = []
    for account in payload["accounts"]:
        key = (str(account["platform"]), int(account["login"]))
        r4_pass, r4_weeks, level, confirmed = enrichment.get(key, (False, 0, None, False))
        candidates.append(
            Candidate(
                login=int(account["login"]),
                selection=account["selection"],
                validation_pnl=float(account["validation"]["client_net_pnl"]),
                enrichment_r4_pass=r4_pass,
                r4_passing_weeks=r4_weeks,
                martingale_level=str(level) if level is not None else None,
                martingale_confirmed=confirmed,
            )
        )
    return candidates


def _score_combo(
    candidates: list[Candidate],
    combo: RuleCombo,
    personal_logins: set[int],
    news_logins: set[int],
) -> dict[str, Any]:
    excluded = set(combo.excluded_martingale_levels)
    abook_pnl = 0.0
    abook_n = 0
    positive = 0
    negative = 0
    for item in candidates:
        martingale_blocked = bool(
            item.martingale_confirmed
            and item.martingale_level is not None
            and item.martingale_level in excluded
        )
        if martingale_blocked:
            continue
        selection = item.selection
        normal_pass = (
            selection["trade_count"] >= combo.min_trades
            and (selection["profit_factor"] is None or selection["profit_factor"] > combo.min_profit_factor)
            and selection["win_rate"] >= combo.min_win_rate
            and selection["payoff_ratio"] >= combo.min_payoff_ratio
            and selection["top_positive_day_concentration"] < combo.max_top1_day_profit_contribution
            and _leverage_filter_pass(
                selection,
                combo.max_leverage_p95_ratio,
                combo.max_high_leverage_holding_seconds,
            )
        )
        r4_pass = bool(
            combo.enable_r4
            and item.enrichment_r4_pass
            and item.r4_passing_weeks >= combo.r4_min_passing_weeks
            and _leverage_filter_pass(
                selection,
                combo.max_leverage_p95_ratio,
                combo.max_high_leverage_holding_seconds,
            )
        )
        on_list = (combo.personal_candidate_list and item.login in personal_logins) or (
            combo.news_candidate_list and item.login in news_logins
        )
        if not (normal_pass or r4_pass or on_list):
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


def _neighbors(base: RuleCombo) -> list[RuleCombo]:
    """Coordinate neighborhood around a seed combo."""
    dims = {
        "min_trades": [40, 50, 60, 65, 70, 75, 80, 85, 90, 100, 120, 150],
        "min_win_rate": [0.35, 0.40, 0.45, 0.48, 0.50, 0.52, 0.55, 0.58, 0.60, 0.65],
        "min_profit_factor": [0.9, 1.0, 1.1, 1.15, 1.2, 1.25, 1.3, 1.35, 1.4, 1.5, 1.6, 1.8],
        "min_payoff_ratio": [0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.7, 0.8, 1.0],
        "max_top1_day_profit_contribution": [0.15, 0.18, 0.2, 0.22, 0.25, 0.28, 0.3, 0.32, 0.35, 0.4, 0.5, 0.6, 0.8],
        "max_leverage_p95_ratio": [200.0, 500.0, 1000.0, 2000.0, 3000.0, 5000.0, 8000.0, 10000.0, 20000.0, 1e9],
        "max_high_leverage_holding_seconds": [0.0, 30.0, 60.0, 120.0, 180.0, 300.0, 600.0, 1800.0, 3600.0, 86400.0],
    }
    out: list[RuleCombo] = []
    data = asdict(base)
    for key, values in dims.items():
        for value in values:
            if data[key] == value:
                continue
            mutated = dict(data)
            mutated[key] = value
            out.append(RuleCombo(**mutated))
    # R4 toggles
    for enable_r4, weeks in [(False, 1), (True, 1), (True, 2)]:
        if base.enable_r4 == enable_r4 and base.r4_min_passing_weeks == weeks:
            continue
        mutated = dict(data)
        mutated["enable_r4"] = enable_r4
        mutated["r4_min_passing_weeks"] = weeks
        out.append(RuleCombo(**mutated))
    return out


def _random_combos(rng: random.Random, n: int) -> list[RuleCombo]:
    trades = [20, 30, 40, 50, 60, 70, 75, 80, 90, 100, 120, 150, 200]
    win_rates = [0.3, 0.35, 0.4, 0.45, 0.48, 0.5, 0.52, 0.55, 0.6, 0.65]
    profit_factors = [0.8, 1.0, 1.1, 1.15, 1.2, 1.25, 1.3, 1.4, 1.5, 1.8, 2.0]
    payoff_ratios = [0.1, 0.2, 0.3, 0.35, 0.4, 0.5, 0.6, 0.8, 1.0]
    top1 = [0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6, 0.8, 1.0]
    leverage = [100.0, 500.0, 1000.0, 2000.0, 5000.0, 8000.0, 10000.0, 50000.0, 1e9]
    hold = [0.0, 60.0, 180.0, 300.0, 600.0, 3600.0, 86400.0]
    r4_opts = [(False, 1), (True, 1), (True, 2)]
    mg_opts = [
        ("extreme", "high", "medium", "low"),
        ("extreme", "high", "medium"),
        ("extreme", "high"),
        ("extreme",),
        (),
    ]
    list_opts = [(False, False), (True, False), (False, True), (True, True)]
    out: list[RuleCombo] = []
    for _ in range(n):
        enable_r4, weeks = rng.choice(r4_opts)
        pers, news = rng.choice(list_opts)
        out.append(
            RuleCombo(
                min_trades=rng.choice(trades),
                min_win_rate=rng.choice(win_rates),
                min_profit_factor=rng.choice(profit_factors),
                min_payoff_ratio=rng.choice(payoff_ratios),
                max_top1_day_profit_contribution=rng.choice(top1),
                max_leverage_p95_ratio=rng.choice(leverage),
                max_high_leverage_holding_seconds=rng.choice(hold),
                enable_r4=enable_r4,
                r4_min_passing_weeks=weeks,
                excluded_martingale_levels=rng.choice(mg_opts),
                personal_candidate_list=pers,
                news_candidate_list=news,
            )
        )
    return out


def _seed_combos() -> list[RuleCombo]:
    """Known-good + intentional extremes."""
    seeds = [
        RuleCombo(75, 0.5, 1.25, 0.4, 0.3, 5000.0, 300.0, False, 1, ("extreme", "high", "medium", "low"), False, False),
        RuleCombo(75, 0.5, 1.25, 0.4, 0.3, 500.0, 60.0, False, 1, ("extreme", "high", "medium", "low"), False, False),
        RuleCombo(75, 0.5, 1.25, 0.5, 0.3, 5000.0, 300.0, False, 1, ("extreme", "high", "medium", "low"), False, False),
        RuleCombo(60, 0.5, 1.2, 0.4, 0.35, 10000.0, 600.0, False, 1, ("extreme", "high", "medium", "low"), False, False),
        RuleCombo(100, 0.55, 1.4, 0.5, 0.25, 5000.0, 300.0, False, 1, ("extreme", "high", "medium", "low"), False, False),
        RuleCombo(40, 0.45, 1.1, 0.3, 0.4, 1e9, 86400.0, False, 1, ("extreme", "high", "medium", "low"), False, False),
        RuleCombo(75, 0.5, 1.25, 0.4, 0.3, 5000.0, 300.0, False, 1, ("extreme", "high"), False, False),
        RuleCombo(75, 0.5, 1.25, 0.4, 0.3, 5000.0, 300.0, False, 1, ("extreme",), False, False),
        RuleCombo(75, 0.5, 1.25, 0.4, 0.3, 5000.0, 300.0, False, 1, (), False, False),
        RuleCombo(75, 0.5, 1.25, 0.4, 0.3, 5000.0, 300.0, False, 1, ("extreme", "high", "medium", "low"), True, True),
        RuleCombo(75, 0.5, 1.25, 0.4, 0.3, 5000.0, 300.0, True, 1, ("extreme", "high", "medium", "low"), False, False),
        RuleCombo(75, 0.5, 1.25, 0.4, 0.3, 5000.0, 300.0, True, 2, ("extreme", "high", "medium", "low"), False, False),
    ]
    # Dense local grid around previous best (routing knobs only).
    for t, wr, pf, po, t1, lev, hold in itertools.product(
        [65, 70, 75, 80, 85],
        [0.48, 0.5, 0.52],
        [1.2, 1.25, 1.3],
        [0.35, 0.4, 0.45, 0.5],
        [0.28, 0.3, 0.32, 0.35],
        [3000.0, 5000.0, 8000.0, 10000.0],
        [180.0, 300.0, 600.0],
    ):
        seeds.append(
            RuleCombo(
                t, wr, pf, po, t1, lev, hold, False, 1,
                ("extreme", "high", "medium", "low"), False, False,
            )
        )
    # Martingale + list variants on previous best.
    for mg, (pers, news), lev, hold in itertools.product(
        [
            ("extreme", "high", "medium", "low"),
            ("extreme", "high", "medium"),
            ("extreme", "high"),
            ("extreme",),
            (),
        ],
        [(False, False), (True, False), (False, True), (True, True)],
        [5000.0, 10000.0, 1e9],
        [300.0, 600.0, 86400.0],
    ):
        seeds.append(
            RuleCombo(
                75, 0.5, 1.25, 0.4, 0.3, lev, hold, False, 1, mg, pers, news,
            )
        )
    return seeds


def main() -> int:
    load_env_file()
    request = AnalysisRequest(
        selection=AnalysisPeriod(start=date(2026, 5, 1), end=date(2026, 6, 30)),
        validation=AnalysisPeriod(start=date(2026, 7, 1), end=date(2026, 7, 16)),
        platforms=["mt4", "mt5", "hh_mt5"],
        rules=AnalysisRules(),
        personal_candidate_list=False,
        news_candidate_list=False,
    )
    personal = load_personal_candidates()
    news = load_news_candidates()
    personal_logins = set(personal.login_ids) if personal.status == "ready" else set()
    news_logins = set(news.login_ids) if news.status == "ready" else set()
    print(
        f"Candidate lists: personal={len(personal_logins)} news={len(news_logins)}",
        flush=True,
    )
    print("Loading ClickHouse population + snapshots...", flush=True)
    rows, martingale_snapshot = _load_rows(request)
    print(f"Loaded {len(rows)} rows; building candidates...", flush=True)
    candidates = _build_candidates(rows, request, martingale_snapshot)
    print(f"Candidates: {len(candidates)}; searching...", flush=True)

    rng = random.Random(42)
    queue: list[RuleCombo] = []
    queue.extend(_seed_combos())
    queue.extend(_random_combos(rng, 25000))

    best: dict[str, Any] | None = None
    over_20k: list[dict[str, Any]] = []
    evaluated = 0
    seen: set[str] = set()
    # Hill-climb: whenever we improve, expand neighborhood.
    improve_rounds = 0

    def consider(combo: RuleCombo) -> None:
        nonlocal best, evaluated, improve_rounds
        key = json.dumps(asdict(combo), sort_keys=True)
        if key in seen:
            return
        seen.add(key)
        result = _score_combo(candidates, combo, personal_logins, news_logins)
        evaluated += 1
        if result["july_abook_net_pnl"] >= 20000:
            over_20k.append(result)
        if best is None or result["july_abook_net_pnl"] > best["july_abook_net_pnl"] + 1e-9:
            best = result
            improve_rounds += 1
            print(
                f"  new best {best['july_abook_net_pnl']:.2f} "
                f"(n={best['abook_accounts']}) @ {best['rules']}",
                flush=True,
            )
            # Expand local search around new best (cap rounds).
            if improve_rounds <= 40:
                for neighbor in _neighbors(combo):
                    queue.append(neighbor)
        if evaluated % 5000 == 0:
            print(
                f"  ... evaluated {evaluated}; unique={len(seen)}; "
                f"best={best['july_abook_net_pnl'] if best else None}; queue={len(queue)}",
                flush=True,
            )

    i = 0
    while i < len(queue):
        consider(queue[i])
        i += 1
        # Safety cap
        if evaluated >= 80000:
            print("Hit evaluation cap 80000", flush=True)
            break

    over_20k.sort(key=lambda item: item["july_abook_net_pnl"], reverse=True)
    out: dict[str, Any] = {
        "evaluated": evaluated,
        "best": best,
        "over_20k_count": len(over_20k),
        "over_20k_top10": over_20k[:10],
        "target": 20000,
    }

    if best is not None:
        rules_data = dict(best["rules"])
        mg = list(rules_data.pop("excluded_martingale_levels"))
        pers = bool(rules_data.pop("personal_candidate_list"))
        news_flag = bool(rules_data.pop("news_candidate_list"))
        verified = build_two_stage_payload(
            rows,
            overview_rows=rows,
            selection_start=request.selection.start.isoformat(),
            selection_end=request.selection.end.isoformat(),
            validation_start=request.validation.start.isoformat(),
            validation_end=request.validation.end.isoformat(),
            min_trades=rules_data["min_trades"],
            min_win_rate=rules_data["min_win_rate"],
            min_profit_factor=rules_data["min_profit_factor"],
            min_payoff_ratio=rules_data["min_payoff_ratio"],
            max_top1_day_profit_contribution=rules_data["max_top1_day_profit_contribution"],
            max_leverage_p95_ratio=rules_data["max_leverage_p95_ratio"],
            max_high_leverage_holding_seconds=rules_data["max_high_leverage_holding_seconds"],
            martingale_snapshot=martingale_snapshot,
            excluded_martingale_levels=mg,
            enable_r4=rules_data["enable_r4"],
            r4_min_passing_weeks=rules_data["r4_min_passing_weeks"],
            personal_candidate_logins=personal_logins if pers else set(),
            news_candidate_logins=news_logins if news_flag else set(),
            personal_candidate_info={"enabled": pers, "status": "ready" if pers else "disabled"},
            news_candidate_info={"enabled": news_flag, "status": "ready" if news_flag else "disabled"},
        )
        abook = [a for a in verified["accounts"] if a["book"] == "abook"]
        verified_pnl = round(sum(float(a["validation"]["client_net_pnl"]) for a in abook), 2)
        print(f"Verified: abook={len(abook)} july_net_pnl={verified_pnl}", flush=True)
        out["verified"] = {
            "abook_accounts": len(abook),
            "july_abook_net_pnl": verified_pnl,
            "rules": best["rules"],
        }

    out_path = ROOT / "docs" / "analysis" / "2026-07-21-july-pnl-expand-search.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "evaluated": evaluated,
                "best_pnl": best["july_abook_net_pnl"] if best else None,
                "over_20k_count": len(over_20k),
            },
            indent=2,
        ),
        flush=True,
    )
    print(f"Wrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
