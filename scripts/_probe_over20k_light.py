#!/usr/bin/env python3
"""Light full-payload verification of candidate July P&L rule sets (>20k target)."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.avg_profit import build_avg_profit_filter
from app.config import load_env_file
from app.martingale import build_martingale_filter
from app.models import AnalysisPeriod, AnalysisRequest
from app.personal_candidates import load_news_candidates, load_personal_candidates
from app.r4 import build_r4_filter
from app.repository import ClickHouseRepository
from app.risk import build_local_risk_filter
from app.service import build_two_stage_payload


def main() -> int:
    load_env_file()
    request = AnalysisRequest(
        selection=AnalysisPeriod(start=date(2026, 5, 1), end=date(2026, 6, 30)),
        validation=AnalysisPeriod(start=date(2026, 7, 1), end=date(2026, 7, 16)),
    )
    print("loading rows...", flush=True)
    risk_filter = build_local_risk_filter(request)
    r4_snapshot = build_r4_filter(request)
    martingale_snapshot = build_martingale_filter(request)
    avg_profit_snapshot = build_avg_profit_filter(request)
    rows = martingale_snapshot.enrich_rows(
        risk_filter.snapshot.enrich_rows(ClickHouseRepository().fetch_analysis(request))
    )
    rows = avg_profit_snapshot.enrich_rows(r4_snapshot.enrich_rows(rows))
    personal = load_personal_candidates()
    news = load_news_candidates()
    personal_logins = set(personal.login_ids) if personal.status == "ready" else set()
    news_logins = set(news.login_ids) if news.status == "ready" else set()
    print(f"rows={len(rows)} personal={len(personal_logins)} news={len(news_logins)}", flush=True)

    combos = [
        (
            "current_pre_change_500_60",
            dict(
                min_trades=75,
                min_win_rate=0.5,
                min_profit_factor=1.25,
                min_payoff_ratio=0.4,
                max_top1_day_profit_contribution=0.3,
                max_leverage_p95_ratio=500.0,
                max_high_leverage_holding_seconds=60.0,
                enable_r4=False,
                r4_min_passing_weeks=1,
            ),
            ["extreme", "high", "medium", "low"],
            False,
            False,
        ),
        (
            "new_defaults_5000_300",
            dict(
                min_trades=75,
                min_win_rate=0.5,
                min_profit_factor=1.25,
                min_payoff_ratio=0.4,
                max_top1_day_profit_contribution=0.3,
                max_leverage_p95_ratio=5000.0,
                max_high_leverage_holding_seconds=300.0,
                enable_r4=False,
                r4_min_passing_weeks=1,
            ),
            ["extreme", "high", "medium", "low"],
            False,
            False,
        ),
    ]

    results = []
    for label, rules, mg, pers, news_flag in combos:
        print(f"verifying {label}...", flush=True)
        payload = build_two_stage_payload(
            rows,
            overview_rows=rows,
            selection_start="2026-05-01",
            selection_end="2026-06-30",
            validation_start="2026-07-01",
            validation_end="2026-07-16",
            martingale_snapshot=martingale_snapshot,
            excluded_martingale_levels=mg,
            personal_candidate_logins=personal_logins if pers else set(),
            news_candidate_logins=news_logins if news_flag else set(),
            personal_candidate_info={"enabled": pers, "status": "ready" if pers else "disabled"},
            news_candidate_info={"enabled": news_flag, "status": "ready" if news_flag else "disabled"},
            **rules,
        )
        abook = [a for a in payload["accounts"] if a["book"] == "abook"]
        pnl = round(sum(float(a["validation"]["client_net_pnl"]) for a in abook), 2)
        item = {
            "label": label,
            "july_abook_net_pnl": pnl,
            "abook_accounts": len(abook),
            "over_20k": pnl >= 20000,
            "rules": rules,
            "excluded_martingale_levels": mg,
        }
        results.append(item)
        print(f"  {label}: pnl={pnl:.2f} n={len(abook)} over20k={pnl >= 20000}", flush=True)

    path = ROOT / "docs" / "analysis" / "2026-07-21-july-pnl-over20k-light.json"
    print(json.dumps(results, indent=2), flush=True)
    print(f"(historical full probe saved at {path})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
