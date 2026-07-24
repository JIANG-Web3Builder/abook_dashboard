"""Personal as-of newcomer screening, isolated from overview Abook KPIs."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from statistics import median
from typing import Any

from .book_analytics import NEUTRAL_BAND, PNL_BUCKETS
from .service import _leverage_filter_pass, _long_trades_ratio_pass, _profit_factor


AccountKey = tuple[str, int]


def _date_value(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _number(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    return float(value)


def _int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    return int(value)


def _key(item: dict[str, Any]) -> AccountKey:
    return str(item["platform"]), int(item["login"])


def _aggregate_through(
    days: list[dict[str, Any]],
    *,
    as_of: date | None,
) -> dict[str, Any]:
    trades = 0
    wins = 0
    losses = 0
    long_trades = 0
    short_trades = 0
    gross_wins = 0.0
    gross_losses = 0.0
    client_pnl = 0.0
    active_days = 0
    positive_days: list[float] = []
    for row in days:
        day = _date_value(row["trade_date"])
        if as_of is not None and day > as_of:
            continue
        day_trades = _int(row.get("matched_trades"))
        if day_trades <= 0:
            continue
        active_days += 1
        trades += day_trades
        wins += _int(row.get("winning_trades"))
        losses += _int(row.get("losing_trades"))
        long_trades += _int(row.get("long_trades"))
        short_trades += _int(row.get("short_trades"))
        gross_wins += _number(row.get("gross_wins"))
        gross_losses += _number(row.get("gross_losses"))
        day_pnl = _number(row.get("client_net_pnl"))
        client_pnl += day_pnl
        if day_pnl > 0:
            positive_days.append(day_pnl)
    positive_sum = sum(positive_days)
    max_positive = max(positive_days) if positive_days else 0.0
    average_win = (gross_wins / wins) if wins else 0.0
    average_loss = (abs(gross_losses) / losses) if losses else 0.0
    payoff = (average_win / average_loss) if average_loss else 0.0
    return {
        "trade_count": trades,
        "active_trade_days": active_days,
        "winning_trades": wins,
        "losing_trades": losses,
        "long_trades": long_trades,
        "short_trades": short_trades,
        "long_trades_ratio": (long_trades / (long_trades + short_trades)) if (long_trades + short_trades) else 0.0,
        "win_rate": (wins / trades) if trades else 0.0,
        "profit_factor": _profit_factor(gross_wins, gross_losses),
        "payoff_ratio": payoff,
        "client_net_pnl": round(client_pnl, 6),
        "top_positive_day_concentration": (max_positive / positive_sum) if positive_sum > 0 else 0.0,
        "gross_wins": gross_wins,
        "gross_losses": gross_losses,
    }


def _candidate_eval_days(
    days: list[dict[str, Any]],
    *,
    min_trades: int,
    max_active_days: int,
) -> list[date]:
    """Days where quality may be evaluated under the short-history track."""
    ordered = sorted(days, key=lambda row: _date_value(row["trade_date"]))
    cumulative_trades = 0
    active_days = 0
    candidates: list[date] = []
    for row in ordered:
        day_trades = _int(row.get("matched_trades"))
        if day_trades <= 0:
            continue
        active_days += 1
        cumulative_trades += day_trades
        day = _date_value(row["trade_date"])
        if active_days > max_active_days:
            break
        if cumulative_trades >= min_trades:
            candidates.append(day)
        elif active_days == max_active_days:
            candidates.append(day)
    return candidates


def _sum_pnl(
    days: list[dict[str, Any]],
    *,
    start_inclusive: date | None,
    end_inclusive: date,
    start_exclusive: date | None = None,
) -> float:
    total = 0.0
    for row in days:
        day = _date_value(row["trade_date"])
        if start_inclusive is not None and day < start_inclusive:
            continue
        if start_exclusive is not None and day <= start_exclusive:
            continue
        if day > end_inclusive:
            continue
        total += _number(row.get("client_net_pnl"))
    return round(total, 6)


def _quality_pass(
    metrics: dict[str, Any],
    *,
    rules: Any,
    leverage_selection: dict[str, Any],
) -> tuple[bool, list[str]]:
    flags: list[str] = []
    if metrics["trade_count"] < rules.min_trades:
        flags.append("insufficient_sample")
    if metrics.get("profit_factor") is not None and metrics["profit_factor"] <= rules.min_profit_factor:
        flags.append("profit_factor")
    if metrics["win_rate"] < rules.min_win_rate:
        flags.append("win_rate")
    if metrics["payoff_ratio"] < rules.min_payoff_ratio:
        flags.append("payoff_ratio")
    if not _long_trades_ratio_pass(metrics, rules.min_long_trades_ratio, rules.max_long_trades_ratio):
        flags.append("long_trades_ratio")
    if metrics["top_positive_day_concentration"] >= rules.max_top1_day_profit_contribution:
        flags.append("profit_concentration")
    if not _leverage_filter_pass(
        leverage_selection,
        rules.max_leverage_p95_ratio,
        rules.max_high_leverage_holding_seconds,
    ):
        flags.append("leverage_p95_ratio")
    return not flags, flags


def _leverage_selection(account: dict[str, Any]) -> dict[str, Any]:
    nested = dict(account.get("selection") or {})
    selection = dict(nested)
    if "risk_leverage_p95_ratio" not in selection:
        selection["risk_leverage_p95_ratio"] = account.get("risk_leverage_p95_ratio")
    if "risk_balance_status" not in selection:
        selection["risk_balance_status"] = account.get("risk_balance_status", "not_available")
    if selection.get("median_holding_seconds") is None:
        selection["median_holding_seconds"] = account.get("median_holding_seconds")
    return selection


def _result(
    account: dict[str, Any],
    *,
    pool: str,
    admitted: bool,
    as_of: date | None,
    metrics: dict[str, Any],
    flags: list[str],
    post_asof_pnl: float,
    validation_period_pnl: float,
    stats_end: date,
    min_trades: int,
    window_trade_count: int = 0,
) -> dict[str, Any]:
    platform, login = _key(account)
    return {
        "platform": platform,
        "login": login,
        "account_group": account.get("account_group", ""),
        "martingale_hard_block": bool(account.get("martingale_hard_block")),
        "martingale_risk_level": account.get("martingale_risk_level"),
        "pool": pool,
        "admitted": admitted,
        "admitted_on": as_of.isoformat() if admitted and as_of is not None else None,
        "as_of": as_of.isoformat() if as_of is not None else None,
        "active_trade_days": metrics.get("active_trade_days", 0),
        "window_trade_count": window_trade_count,
        "selection": metrics,
        "selection_flags": flags,
        "post_asof_pnl": post_asof_pnl,
        "validation_period_pnl": validation_period_pnl,
        "stats_end": stats_end.isoformat(),
        "min_trades_used": min_trades,
    }


def evaluate_newcomer_account(
    account: dict[str, Any],
    days: list[dict[str, Any]],
    *,
    rules: Any,
    max_active_days: int,
    validation_start: date,
    validation_end: date,
    min_trades_override: int | None = None,
    stats_end: date | None = None,
) -> dict[str, Any]:
    """Evaluate one non-Abook account for the newcomer tab.

    Accounts with zero trades in the analysis window are marked inactive and
    should be dropped from candidate pools (zombie / dormant accounts).
    """
    min_trades = int(min_trades_override if min_trades_override is not None else rules.min_trades)
    end = stats_end or validation_end
    validation_period_pnl = _sum_pnl(days, start_inclusive=validation_start, end_inclusive=end)
    total_trades = sum(_int(row.get("matched_trades")) for row in days)

    if total_trades <= 0:
        return _result(
            account,
            pool="inactive",
            admitted=False,
            as_of=None,
            metrics=_aggregate_through(days, as_of=None),
            flags=["no_history"],
            post_asof_pnl=0.0,
            validation_period_pnl=validation_period_pnl,
            stats_end=end,
            min_trades=min_trades,
            window_trade_count=0,
        )

    if bool(account.get("martingale_hard_block")):
        as_of_candidates = _candidate_eval_days(
            days, min_trades=min_trades, max_active_days=max_active_days
        )
        as_of = as_of_candidates[0] if as_of_candidates else None
        metrics = _aggregate_through(days, as_of=as_of)
        return _result(
            account,
            pool="blocked",
            admitted=False,
            as_of=as_of,
            metrics=metrics,
            flags=["martingale_hard_block"],
            post_asof_pnl=0.0,
            validation_period_pnl=validation_period_pnl,
            stats_end=end,
            min_trades=min_trades,
            window_trade_count=total_trades,
        )

    candidates = _candidate_eval_days(days, min_trades=min_trades, max_active_days=max_active_days)
    if not candidates:
        metrics = _aggregate_through(days, as_of=None)
        # Trades > 0 but still immature: keep in observe (short-history watch list).
        return _result(
            account,
            pool="observe",
            admitted=False,
            as_of=None,
            metrics=metrics,
            flags=["insufficient_sample"],
            post_asof_pnl=0.0,
            validation_period_pnl=validation_period_pnl,
            stats_end=end,
            min_trades=min_trades,
            window_trade_count=total_trades,
        )

    leverage_selection = _leverage_selection(account)
    last_metrics: dict[str, Any] | None = None
    last_flags: list[str] = []
    last_as_of: date | None = None
    for as_of in candidates:
        metrics = _aggregate_through(days, as_of=as_of)
        last_metrics = metrics
        last_as_of = as_of
        check_rules = rules
        if min_trades_override is not None:
            class _RulesProxy:
                def __getattr__(self, name: str) -> Any:
                    if name == "min_trades":
                        return min_trades
                    return getattr(rules, name)

            check_rules = _RulesProxy()
        passed, flags = _quality_pass(metrics, rules=check_rules, leverage_selection=leverage_selection)
        last_flags = flags
        if passed:
            post_pnl = _sum_pnl(days, start_inclusive=None, end_inclusive=end, start_exclusive=as_of)
            return _result(
                account,
                pool="admitted",
                admitted=True,
                as_of=as_of,
                metrics=metrics,
                flags=[],
                post_asof_pnl=post_pnl,
                validation_period_pnl=validation_period_pnl,
                stats_end=end,
                min_trades=min_trades,
                window_trade_count=total_trades,
            )

    metrics = last_metrics or _aggregate_through(days, as_of=last_as_of)
    at_cap = metrics.get("active_trade_days", 0) >= max_active_days
    # Quality failed before maturity day-60: still short-history observe if sample was the only issue
    # and they haven't hit the active-day cap yet? Prefer rejected for quality fails.
    if not at_cap and last_flags == ["insufficient_sample"]:
        return _result(
            account,
            pool="observe",
            admitted=False,
            as_of=last_as_of,
            metrics=metrics,
            flags=last_flags,
            post_asof_pnl=0.0,
            validation_period_pnl=validation_period_pnl,
            stats_end=end,
            min_trades=min_trades,
            window_trade_count=total_trades,
        )
    return _result(
        account,
        pool="left_track" if at_cap else "rejected",
        admitted=False,
        as_of=last_as_of,
        metrics=metrics,
        flags=last_flags or ["quality_failed"],
        post_asof_pnl=0.0,
        validation_period_pnl=validation_period_pnl,
        stats_end=end,
        min_trades=min_trades,
        window_trade_count=total_trades,
    )


def _concentration(values: list[float]) -> dict[str, dict[str, float]]:
    total = sum(abs(value) for value in values)
    ordered = sorted(values, key=abs, reverse=True)
    return {
        f"top_{count}": {
            "absolute_share": round(sum(abs(value) for value in ordered[:count]) / total, 6) if total else 0.0
        }
        for count in (5, 10, 20)
    }


def _cohort_summary(accounts: list[dict[str, Any]], pnl_field: str) -> dict[str, Any]:
    values = [_number(item.get(pnl_field)) for item in accounts]
    profitable = [value for value in values if value > NEUTRAL_BAND]
    losses = [value for value in values if value < -NEUTRAL_BAND]
    trades = sum(_int((item.get("selection") or {}).get("trade_count")) for item in accounts)
    wins = sum(_int((item.get("selection") or {}).get("winning_trades")) for item in accounts)
    losses_trades = sum(_int((item.get("selection") or {}).get("losing_trades")) for item in accounts)
    gross_wins = sum(_number((item.get("selection") or {}).get("gross_wins")) for item in accounts)
    gross_losses = sum(_number((item.get("selection") or {}).get("gross_losses")) for item in accounts)
    return {
        "accounts": len(accounts),
        "active_accounts": sum(1 for item in accounts if _int(item.get("window_trade_count")) > 0),
        "profitable_accounts": len(profitable),
        "loss_accounts": len(losses),
        "neutral_accounts": len(values) - len(profitable) - len(losses),
        "positive_pnl": round(sum(value for value in values if value > 0), 6),
        "negative_pnl": round(sum(value for value in values if value < 0), 6),
        "net_pnl": round(sum(values), 6),
        "average_pnl": round(sum(values) / len(values), 6) if values else 0.0,
        "median_pnl": round(float(median(values)), 6) if values else 0.0,
        "matched_trades": trades,
        "winning_trades": wins,
        "losing_trades": losses_trades,
        "win_rate": round(wins / trades, 6) if trades else 0.0,
        "profit_factor": round(gross_wins / abs(gross_losses), 6) if gross_losses else None,
        "profit_concentration": _concentration(values),
    }


def _pnl_distribution(accounts: list[dict[str, Any]], pnl_field: str) -> list[dict[str, Any]]:
    values = [_number(item.get(pnl_field)) for item in accounts]
    result = []
    for key, label, lower, upper in PNL_BUCKETS:
        selected = [
            value
            for value in values
            if (lower is None or value >= lower) and (upper is None or value < upper)
        ]
        result.append(
            {
                "key": key,
                "label": label,
                "lower": lower,
                "upper": upper,
                "accounts": len(selected),
                "account_rate": round(len(selected) / len(values), 6) if values else 0.0,
                "net_pnl": round(sum(selected), 6),
            }
        )
    return result


def _top_accounts(accounts: list[dict[str, Any]], pnl_field: str) -> dict[str, list[dict[str, Any]]]:
    rows = []
    for item in accounts:
        pnl = _number(item.get(pnl_field))
        rows.append(
            {
                "platform": item["platform"],
                "login": item["login"],
                "account_group": item.get("account_group", ""),
                "as_of": item.get("as_of"),
                "active_trade_days": item.get("active_trade_days", 0),
                "trade_count": (item.get("selection") or {}).get("trade_count", 0),
                "pnl": round(pnl, 6),
            }
        )
    return {
        "winners": sorted((row for row in rows if row["pnl"] > NEUTRAL_BAND), key=lambda row: row["pnl"], reverse=True)[:10],
        "losers": sorted((row for row in rows if row["pnl"] < -NEUTRAL_BAND), key=lambda row: row["pnl"])[:10],
    }


def _admitted_cumulative_pnl(
    admitted: list[dict[str, Any]],
    by_account: dict[AccountKey, list[dict[str, Any]]],
    *,
    end: date,
) -> list[dict[str, Any]]:
    """Cohort cumulative client_net_pnl using each account's personal as-of+1 start."""
    grouped: dict[str, dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "matched_trades": 0.0, "accounts": 0.0})
    account_days_hit: dict[str, set[AccountKey]] = defaultdict(set)
    for item in admitted:
        as_of = item.get("as_of")
        if not as_of:
            continue
        as_of_date = _date_value(as_of)
        key = _key(item)
        for row in by_account.get(key, []):
            day = _date_value(row["trade_date"])
            if day <= as_of_date or day > end:
                continue
            day_key = day.isoformat()
            grouped[day_key]["pnl"] += _number(row.get("client_net_pnl"))
            grouped[day_key]["matched_trades"] += _int(row.get("matched_trades"))
            account_days_hit[day_key].add(key)
    cumulative = 0.0
    result = []
    for day in sorted(grouped):
        item = grouped[day]
        cumulative += item["pnl"]
        result.append(
            {
                "date": day,
                "pnl": round(item["pnl"], 6),
                "cumulative_pnl": round(cumulative, 6),
                "matched_trades": int(item["matched_trades"]),
                "active_accounts": len(account_days_hit[day]),
            }
        )
    return result


def _window_cumulative_pnl(
    accounts: list[dict[str, Any]],
    by_account: dict[AccountKey, list[dict[str, Any]]],
    *,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    keys = {_key(item) for item in accounts}
    grouped: dict[str, dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "matched_trades": 0.0})
    for key in keys:
        for row in by_account.get(key, []):
            day = _date_value(row["trade_date"])
            if day < start or day > end:
                continue
            day_key = day.isoformat()
            grouped[day_key]["pnl"] += _number(row.get("client_net_pnl"))
            grouped[day_key]["matched_trades"] += _int(row.get("matched_trades"))
    cumulative = 0.0
    result = []
    for day in sorted(grouped):
        item = grouped[day]
        cumulative += item["pnl"]
        result.append(
            {
                "date": day,
                "pnl": round(item["pnl"], 6),
                "cumulative_pnl": round(cumulative, 6),
                "matched_trades": int(item["matched_trades"]),
            }
        )
    return result


def build_newcomer_analytics_payload(
    population_accounts: list[dict[str, Any]],
    daily_facts: list[dict[str, Any]],
    request: Any,
    *,
    max_active_days: int = 60,
) -> dict[str, Any]:
    """Build newcomer tab payload from overview population + local daily facts."""
    rules = request.rules
    selection_start = request.selection.start
    selection_end = request.selection.end
    validation_start = request.validation.start
    validation_end = request.validation.end
    by_account: dict[AccountKey, list[dict[str, Any]]] = defaultdict(list)
    for row in daily_facts:
        by_account[_key(row)].append(row)

    observe: list[dict[str, Any]] = []
    admitted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    left_track = 0
    blocked = 0
    skipped_abook = 0
    skipped_inactive = 0

    for account in population_accounts:
        if account.get("book") == "abook":
            skipped_abook += 1
            continue
        key = _key(account)
        result = evaluate_newcomer_account(
            account,
            by_account.get(key, []),
            rules=rules,
            max_active_days=max_active_days,
            validation_start=validation_start,
            validation_end=validation_end,
        )
        pool = result["pool"]
        if pool == "inactive":
            skipped_inactive += 1
            continue
        if pool == "observe":
            observe.append(result)
        elif pool == "admitted":
            admitted.append(result)
        elif pool == "rejected":
            rejected.append(result)
        elif pool == "left_track":
            left_track += 1
            rejected.append(result)
        elif pool == "blocked":
            blocked += 1
            rejected.append(result)

    admitted.sort(key=lambda item: item["post_asof_pnl"], reverse=True)
    observe.sort(
        key=lambda item: (
            _int((item.get("selection") or {}).get("trade_count")),
            item["validation_period_pnl"],
        ),
        reverse=True,
    )
    rejected.sort(key=lambda item: item.get("selection", {}).get("trade_count", 0), reverse=True)

    admitted_pnl = round(sum(item["post_asof_pnl"] for item in admitted), 2)
    positive = sum(1 for item in admitted if item["post_asof_pnl"] > NEUTRAL_BAND)
    negative = sum(1 for item in admitted if item["post_asof_pnl"] < -NEUTRAL_BAND)
    rejected_only = sum(1 for item in rejected if item["pool"] == "rejected")

    post_asof_summary = _cohort_summary(admitted, "post_asof_pnl")
    validation_summary = _cohort_summary(admitted, "validation_period_pnl")
    observe_summary = _cohort_summary(observe, "validation_period_pnl")

    return {
        "pnl_basis": "deals.client_net_pnl",
        "selection": {
            "start": selection_start.isoformat(),
            "end": selection_end.isoformat(),
        },
        "validation": {
            "start": validation_start.isoformat(),
            "end": validation_end.isoformat(),
        },
        "counts": {
            "observe": len(observe),
            "admitted": len(admitted),
            "rejected": rejected_only,
            "left_track": left_track,
            "martingale_blocked": blocked,
            "skipped_abook": skipped_abook,
            "skipped_inactive": skipped_inactive,
            "active_candidates": len(admitted) + len(observe) + len(rejected),
        },
        "kpi": {
            "admitted_accounts": len(admitted),
            "post_asof_net_pnl": admitted_pnl,
            "admitted_positive": positive,
            "admitted_negative": negative,
            "observe_accounts": len(observe),
            "skipped_inactive": skipped_inactive,
            "average_post_asof_pnl": post_asof_summary["average_pnl"],
            "median_post_asof_pnl": post_asof_summary["median_pnl"],
        },
        "summary": {
            "post_asof": post_asof_summary,
            "validation": validation_summary,
            "observe_validation": observe_summary,
        },
        "pnl_distribution": {
            "post_asof": _pnl_distribution(admitted, "post_asof_pnl"),
            "validation": _pnl_distribution(admitted, "validation_period_pnl"),
            "observe_validation": _pnl_distribution(observe, "validation_period_pnl"),
        },
        "cumulative_pnl": {
            "post_asof": _admitted_cumulative_pnl(admitted, by_account, end=validation_end),
            "full_window": _window_cumulative_pnl(
                admitted,
                by_account,
                start=min(selection_start, validation_start),
                end=validation_end,
            ),
        },
        "top_accounts": {
            "post_asof": _top_accounts(admitted, "post_asof_pnl"),
            "validation": _top_accounts(admitted, "validation_period_pnl"),
            "observe_validation": _top_accounts(observe, "validation_period_pnl"),
        },
        "admitted": admitted,
        "observe": observe[:500],
        "rejected": rejected[:500],
        "rules": {
            "max_active_days": max_active_days,
            "require_window_trades": True,
            "min_trades": rules.min_trades,
            "min_win_rate": rules.min_win_rate,
            "min_profit_factor": rules.min_profit_factor,
            "min_payoff_ratio": rules.min_payoff_ratio,
            "min_long_trades_ratio": rules.min_long_trades_ratio,
            "max_long_trades_ratio": rules.max_long_trades_ratio,
            "max_top1_day_profit_contribution": rules.max_top1_day_profit_contribution,
            "max_leverage_p95_ratio": rules.max_leverage_p95_ratio,
            "max_high_leverage_holding_seconds": rules.max_high_leverage_holding_seconds,
            "excluded_martingale_levels": list(rules.excluded_martingale_levels),
        },
    }


def build_newcomer_account_sensitivity(
    account: dict[str, Any],
    days: list[dict[str, Any]],
    request: Any,
    *,
    min_trades_values: list[int],
    max_active_days: int = 60,
    stats_end: date | None = None,
) -> dict[str, Any]:
    """Recompute one account across several min_trades cutoffs."""
    points = []
    for min_trades in min_trades_values:
        points.append(
            evaluate_newcomer_account(
                account,
                days,
                rules=request.rules,
                max_active_days=max_active_days,
                validation_start=request.validation.start,
                validation_end=request.validation.end,
                min_trades_override=min_trades,
                stats_end=stats_end,
            )
        )
    return {
        "platform": account.get("platform"),
        "login": account.get("login"),
        "stats_end": (stats_end or request.validation.end).isoformat(),
        "points": points,
    }
