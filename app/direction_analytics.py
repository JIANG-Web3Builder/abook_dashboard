from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from statistics import median
from typing import Any, Iterable

from .book_analytics import PNL_BUCKETS
from .service import (
    _account_period_metrics,
    _leverage_filter_pass,
    _long_trades_ratio_pass,
    _period_months,
)


AccountKey = tuple[str, int]
DIRECTIONS = {"Long": "long", "Short": "short"}
PHASES = ("selection", "validation")
SIDE_STATUSES = {"ok", "insufficient", "no_activity"}


def _key(item: dict[str, Any]) -> AccountKey:
    return str(item["platform"]), int(item["login"])


def _date_value(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _month_value(value: Any) -> str:
    return _date_value(value).strftime("%Y-%m")


def _number(value: Any, default: float = 0.0) -> float:
    return default if value is None else float(value)


def _int(value: Any, default: int = 0) -> int:
    return default if value is None else int(value)


def _empty_month(account: dict[str, Any], month: str, phase: str) -> dict[str, Any]:
    month_start = datetime.fromisoformat(f"{month}-01")
    return {
        "platform": account["platform"],
        "login": int(account["login"]),
        "account_group": account.get("account_group", ""),
        "phase": phase,
        "month_start": month_start,
        "matched_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "matched_volume": 0.0,
        "side_pnl": 0.0,
        "client_net_pnl": 0.0,
        "market_pnl": 0.0,
        "matched_market_pnl": 0.0,
        "gross_wins": 0.0,
        "gross_losses": 0.0,
        "costs": 0.0,
        "funding_pnl": 0.0,
        "avg_holding_seconds": 0.0,
        "median_holding_seconds": 0.0,
        "active_trade_days": 0,
        "daily_active_days": 0,
        "daily_positive_days": 0,
        "daily_negative_days": 0,
        "daily_flat_days": 0,
        "daily_positive_sum": 0.0,
        "daily_negative_sum": 0.0,
        "max_positive_day": 0.0,
        "min_negative_day": 0.0,
        "daily_pnl_sum_for_variance": 0.0,
        "daily_pnl_square_sum": 0.0,
        "daily_variance_count": 0,
        "daily_abs_sum": 0.0,
        "selection_median_holding_seconds": 0.0,
        "validation_median_holding_seconds": 0.0,
        "selection_symbols_traded": 0,
        "validation_symbols_traded": 0,
    }


def _monthly_rows(
    rows: list[dict[str, Any]],
    accounts: dict[AccountKey, dict[str, Any]],
    selection_months: list[str],
    validation_months: list[str],
) -> tuple[dict[tuple[AccountKey, str, str], list[dict[str, Any]]], dict[tuple[AccountKey, str, str], list[dict[str, Any]]], dict[tuple[AccountKey, str, str], set[str]]]:
    monthly_facts: dict[tuple[AccountKey, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    daily_facts: dict[tuple[AccountKey, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    symbols: dict[tuple[AccountKey, str, str], set[str]] = defaultdict(set)
    phase_stats: dict[tuple[AccountKey, str, str], dict[str, float]] = defaultdict(dict)
    for row in rows:
        key = (str(row["platform"]), int(row["login"]))
        if key not in accounts or row.get("direction") not in DIRECTIONS:
            continue
        side = DIRECTIONS[row["direction"]]
        phase = str(row.get("phase"))
        if phase not in PHASES:
            continue
        month = _month_value(row["month_start"])
        bucket = (key, side, phase)
        fact_level = row.get("fact_level")
        if fact_level == "symbol":
            symbol = str(row.get("symbol") or "")
            if symbol:
                symbols[bucket].add(symbol)
            continue
        if fact_level == "phase":
            phase_stats[bucket]["median_holding_seconds"] = _number(row.get("median_holding_seconds"))
            continue
        target = monthly_facts if fact_level == "month" else daily_facts if fact_level == "day" else None
        if target is None:
            continue
        day = str(_date_value(row.get("trade_date"))) if fact_level == "day" else month
        previous = target[bucket].get(day)
        if previous is None:
            target[bucket][day] = dict(row)
        else:
            for field in ("matched_trades", "winning_trades", "losing_trades"):
                previous[field] = _int(previous.get(field)) + _int(row.get(field))
            for field in ("side_pnl", "matched_volume", "gross_wins", "gross_losses"):
                previous[field] = _number(previous.get(field)) + _number(row.get(field))

    all_months = {
        "selection": selection_months,
        "validation": validation_months,
    }
    normalized: dict[tuple[AccountKey, str, str], list[dict[str, Any]]] = {}
    normalized_daily: dict[tuple[AccountKey, str, str], list[dict[str, Any]]] = {}
    for key in accounts:
        for side in DIRECTIONS.values():
            for phase in PHASES:
                bucket = (key, side, phase)
                month_rows = []
                for month in all_months[phase]:
                    fact = monthly_facts[bucket].get(month)
                    item = _empty_month(accounts[key], month, phase) if fact is None else dict(fact)
                    item["phase"] = phase
                    item["client_net_pnl"] = _number(item.get("side_pnl"))
                    item["market_pnl"] = item["client_net_pnl"]
                    item["matched_market_pnl"] = item["client_net_pnl"]
                    month_days = [
                        dict(day_fact)
                        for day, day_fact in daily_facts[bucket].items()
                        if _month_value(day_fact["month_start"]) == month
                    ]
                    day_values = [_number(day.get("side_pnl")) for day in month_days]
                    positive = [value for value in day_values if value > 0]
                    negative = [value for value in day_values if value < 0]
                    item["daily_active_days"] = len(day_values)
                    item["active_trade_days"] = len(day_values)
                    item["daily_positive_days"] = len(positive)
                    item["daily_negative_days"] = len(negative)
                    item["daily_flat_days"] = len(day_values) - len(positive) - len(negative)
                    item["daily_positive_sum"] = sum(positive)
                    item["daily_negative_sum"] = sum(negative)
                    item["max_positive_day"] = max(positive, default=0.0)
                    item["min_negative_day"] = min(negative, default=0.0)
                    item["daily_abs_sum"] = sum(abs(value) for value in day_values)
                    item["daily_pnl_sum_for_variance"] = sum(day_values)
                    item["daily_pnl_square_sum"] = sum(value * value for value in day_values)
                    item["daily_variance_count"] = len(day_values)
                    item[f"{phase}_median_holding_seconds"] = phase_stats[bucket].get(
                        "median_holding_seconds", _number(item.get("median_holding_seconds"))
                    )
                    item[f"{phase}_symbols_traded"] = len(symbols[bucket])
                    month_rows.append(item)
                normalized[bucket] = month_rows
                normalized_daily[bucket] = [
                    dict(day_fact) for day_fact in daily_facts[bucket].values()
                ]
    return normalized, normalized_daily, symbols


def _side_metric(month_rows: list[dict[str, Any]], account: dict[str, Any], phase: str) -> dict[str, Any]:
    metric = _account_period_metrics(month_rows, account, phase=phase)
    metric["side_pnl"] = metric["client_net_pnl"]
    metric["sample_status"] = (
        "no_activity" if metric["trade_count"] == 0 else "ok"
    )
    return metric


def _side_flags(
    metric: dict[str, Any],
    *,
    min_trades: int,
    min_win_rate: float,
    min_profit_factor: float,
    min_payoff_ratio: float,
    max_top1_day_profit_contribution: float,
    leverage_pass: bool,
    direction_balance_pass: bool,
    martingale_hard_block: bool,
) -> tuple[bool, list[str]]:
    flags: list[str] = []
    if metric["trade_count"] == 0:
        flags.append("no_side_activity")
    elif metric["trade_count"] < min_trades:
        flags.append("insufficient_side_sample")
    if metric.get("profit_factor") is not None and metric["profit_factor"] <= min_profit_factor:
        flags.append("profit_factor")
    if metric["win_rate"] < min_win_rate:
        flags.append("win_rate")
    if metric["payoff_ratio"] < min_payoff_ratio:
        flags.append("payoff_ratio")
    if metric["top_positive_day_concentration"] >= max_top1_day_profit_contribution:
        flags.append("profit_concentration")
    if not direction_balance_pass:
        flags.append("long_trades_ratio")
    if not leverage_pass:
        flags.append("leverage_p95_ratio")
    if martingale_hard_block:
        flags.append("martingale_hard_block")
    passed = not flags
    if metric["trade_count"] == 0 or metric["trade_count"] < min_trades:
        passed = False
    return passed, flags


def _sample_status(metric: dict[str, Any], min_trades: int) -> str:
    if metric["trade_count"] == 0:
        return "no_activity"
    if metric["trade_count"] < min_trades:
        return "insufficient"
    return "ok"


def _quadrant(long_pass: bool, short_pass: bool, long_status: str, short_status: str) -> str:
    if long_pass and short_pass:
        return "both_pass"
    if long_pass:
        return "long_only_pass"
    if short_pass:
        return "short_only_pass"
    if long_status != "ok" or short_status != "ok":
        return "insufficient_side"
    return "neither_pass"


def _style(account: dict[str, Any], metric: dict[str, Any]) -> str:
    if account.get("martingale_blocked") or account.get("martingale_risk_level"):
        return "martingale"
    holding = _number(metric.get("median_holding_seconds"))
    trades = _int(metric.get("trade_count"))
    if holding < 60:
        return "scalping"
    if trades >= 100:
        return "high_frequency"
    if holding > 86400:
        return "swing"
    return "normal"


def _concentration(values: list[float]) -> dict[str, dict[str, float]]:
    total = sum(abs(value) for value in values)
    ordered = sorted(values, key=abs, reverse=True)
    return {
        f"top_{count}": {"absolute_share": round(sum(abs(value) for value in ordered[:count]) / total, 6) if total else 0.0}
        for count in (5, 10, 20)
    }


def _phase_summary(accounts: list[dict[str, Any]], side: str, phase: str) -> dict[str, Any]:
    metrics = [account[side][phase] for account in accounts]
    values = [float(metric.get("side_pnl", 0) or 0) for metric in metrics]
    profitable = [value for value in values if value > 10]
    losses = [value for value in values if value < -10]
    trades = sum(_int(metric.get("trade_count")) for metric in metrics)
    wins = sum(_int(metric.get("winning_trades")) for metric in metrics)
    gross_wins = sum(_number(metric.get("gross_wins")) for metric in metrics)
    gross_losses = sum(_number(metric.get("gross_losses")) for metric in metrics)
    return {
        "accounts": len(metrics),
        "active_accounts": sum(1 for metric in metrics if _int(metric.get("trade_count")) > 0),
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
        "losing_trades": sum(_int(metric.get("losing_trades")) for metric in metrics),
        "win_rate": round(wins / trades, 6) if trades else 0.0,
        "profit_factor": round(gross_wins / abs(gross_losses), 6) if gross_losses else None,
        "max_drawdown": round(max((_number(metric.get("max_drawdown")) for metric in metrics), default=0.0), 6),
        "worst_daily_pnl": round(min((_number(metric.get("min_negative_day")) for metric in metrics), default=0.0), 6),
        "profit_concentration": _concentration(values),
    }


def _pnl_buckets(accounts: list[dict[str, Any]], side: str, phase: str) -> list[dict[str, Any]]:
    values = [float(account[side][phase].get("side_pnl", 0) or 0) for account in accounts]
    result = []
    for key, label, lower, upper in PNL_BUCKETS:
        count = sum(
            1 for value in values
            if (lower is None or value > lower) and (upper is None or value <= upper)
        )
        result.append({"key": key, "label": label, "accounts": count})
    return result


def _top_accounts(accounts: list[dict[str, Any]], side: str, phase: str) -> dict[str, list[dict[str, Any]]]:
    ordered = sorted(accounts, key=lambda item: float(item[side][phase].get("side_pnl", 0) or 0), reverse=True)
    def row(account: dict[str, Any]) -> dict[str, Any]:
        metric = account[side][phase]
        return {
            "platform": account["platform"],
            "login": account["login"],
            "pnl": metric.get("side_pnl", 0),
            "trade_count": metric.get("trade_count", 0),
            "style": _style(account, metric),
            "quadrant": account["quadrant"],
        }
    return {"winners": [row(item) for item in ordered[:10] if float(item[side][phase].get("side_pnl", 0) or 0) > 0],
            "losers": [row(item) for item in reversed(ordered[-10:]) if float(item[side][phase].get("side_pnl", 0) or 0) < 0]}


def _symbol_heatmap(rows: list[dict[str, Any]], accounts: list[dict[str, Any]], side: str, phase: str) -> list[dict[str, Any]]:
    keys = {_key(account) for account in accounts}
    direction = next(key for key, value in DIRECTIONS.items() if value == side)
    grouped: dict[str, dict[str, float]] = defaultdict(lambda: {"trade_count": 0, "volume": 0.0, "market_pnl": 0.0})
    for row in rows:
        if row.get("fact_level") != "symbol" or row.get("direction") != direction or row.get("phase") != phase:
            continue
        if (str(row["platform"]), int(row["login"])) not in keys:
            continue
        symbol = str(row.get("symbol") or "")
        if not symbol:
            continue
        grouped[symbol]["trade_count"] += _int(row.get("matched_trades"))
        grouped[symbol]["volume"] += _number(row.get("matched_volume"))
        grouped[symbol]["market_pnl"] += _number(row.get("side_pnl"))
    return [
        {"symbol": symbol, **values}
        for symbol, values in sorted(grouped.items(), key=lambda item: abs(item[1]["market_pnl"]), reverse=True)
    ][:50]


def _cumulative_pnl(
    daily_rows: dict[tuple[AccountKey, str, str], list[dict[str, Any]]],
    accounts: list[dict[str, Any]],
    side: str,
    phase: str,
) -> list[dict[str, Any]]:
    keys = {_key(account) for account in accounts}
    grouped: dict[str, dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "matched_trades": 0.0})
    for (key, row_side, row_phase), facts in daily_rows.items():
        if row_side != side or row_phase != phase or key not in keys:
            continue
        for row in facts:
            day = str(_date_value(row.get("trade_date")))
            grouped[day]["pnl"] += _number(row.get("side_pnl"))
            grouped[day]["matched_trades"] += _int(row.get("matched_trades"))
    cumulative = 0.0
    result = []
    for day in sorted(grouped):
        item = grouped[day]
        cumulative += item["pnl"]
        result.append({
            "date": day,
            "pnl": round(item["pnl"], 6),
            "cumulative_pnl": round(cumulative, 6),
            "matched_trades": int(item["matched_trades"]),
        })
    return result


def _cumulative_pnl_full_range(
    daily_rows: dict[tuple[AccountKey, str, str], list[dict[str, Any]]],
    accounts: list[dict[str, Any]],
    side: str,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    keys = {_key(account) for account in accounts}
    grouped: dict[str, dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "matched_trades": 0.0})
    for (key, row_side, row_phase), facts in daily_rows.items():
        if row_side != side or row_phase not in PHASES or key not in keys:
            continue
        for row in facts:
            day = str(_date_value(row.get("trade_date")))
            grouped[day]["pnl"] += _number(row.get("side_pnl"))
            grouped[day]["matched_trades"] += _int(row.get("matched_trades"))

    cumulative = 0.0
    result = []
    current = start_date
    while current <= end_date:
        day = current.isoformat()
        item = grouped.get(day, {"pnl": 0.0, "matched_trades": 0.0})
        cumulative += item["pnl"]
        result.append({
            "date": day,
            "pnl": round(item["pnl"], 6),
            "cumulative_pnl": round(cumulative, 6),
            "matched_trades": int(item["matched_trades"]),
        })
        current = current.fromordinal(current.toordinal() + 1)
    return result


_DIRECTION_METRIC_FIELDS = (
    "trade_count", "win_rate", "profit_factor", "payoff_ratio", "side_pnl", "sample_status",
)


def _compact_direction_metric(metric: dict[str, Any]) -> dict[str, Any]:
    return {field: metric.get(field) for field in _DIRECTION_METRIC_FIELDS}


def _compact_direction_account(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "platform": account["platform"],
        "login": account["login"],
        "account_group": account.get("account_group", ""),
        "book": account.get("book", "bbook"),
        "long": {
            "selection": _compact_direction_metric(account["long"]["selection"]),
            "validation": _compact_direction_metric(account["long"]["validation"]),
        },
        "short": {
            "selection": _compact_direction_metric(account["short"]["selection"]),
            "validation": _compact_direction_metric(account["short"]["validation"]),
        },
        "long_pass": account["long_pass"],
        "short_pass": account["short_pass"],
        "quadrant": account["quadrant"],
        "in_total_abook": account["in_total_abook"],
        "selection_flags_long": account["selection_flags_long"],
        "selection_flags_short": account["selection_flags_short"],
        "long_trade_share": account["long_trade_share"],
        "short_trade_share": account["short_trade_share"],
        "shared_gates": account["shared_gates"],
    }


def _has_material_direction_pnl(account: dict[str, Any]) -> bool:
    return any(
        abs(_number(account[side][phase].get("side_pnl"))) > 10
        for side in DIRECTIONS.values()
        for phase in PHASES
    )


def _set_payload(
    accounts: list[dict[str, Any]],
    side: str,
    rows: list[dict[str, Any]],
    daily_rows: dict[tuple[AccountKey, str, str], list[dict[str, Any]]],
    cumulative_start: date,
    cumulative_end: date,
) -> dict[str, Any]:
    return {
        "accounts": len(accounts),
        "phase_summary": {phase: _phase_summary(accounts, side, phase) for phase in PHASES},
        "pnl_distribution": {phase: _pnl_buckets(accounts, side, phase) for phase in PHASES},
        "cumulative_pnl": {
            **{phase: _cumulative_pnl(daily_rows, accounts, side, phase) for phase in PHASES},
            "full": _cumulative_pnl_full_range(
                daily_rows, accounts, side, cumulative_start, cumulative_end
            ),
        },
        "symbol_heatmap": {
            phase: _symbol_heatmap(rows, accounts, side, phase) for phase in PHASES
        },
        "top_accounts": {phase: _top_accounts(accounts, side, phase) for phase in PHASES},
    }


def build_direction_analytics_payload(
    rows: Iterable[dict[str, Any]],
    total_accounts: list[dict[str, Any]],
    request: Any,
) -> dict[str, Any]:
    """Build independent Long/Short screening and diagnostic sets."""
    materialized_rows = list(rows)
    account_map: dict[AccountKey, dict[str, Any]] = {_key(account): dict(account) for account in total_accounts}
    for row in materialized_rows:
        key = _key(row)
        if total_accounts and key not in account_map:
            continue
        account_map.setdefault(key, {
            "platform": key[0], "login": key[1], "account_group": row.get("account_group", ""),
            "book": "bbook", "selection": {}, "martingale_hard_block": False,
        })
    selection_months = _period_months(request.selection.start.isoformat(), request.selection.end.isoformat())
    validation_months = _period_months(request.validation.start.isoformat(), request.validation.end.isoformat())
    cumulative_start = request.selection.start
    cumulative_end = request.validation.end
    normalized, normalized_daily, _ = _monthly_rows(materialized_rows, account_map, selection_months, validation_months)
    rules = request.rules
    accounts: list[dict[str, Any]] = []
    total_abook_keys = {_key(account) for account in total_accounts if account.get("book") == "abook"}
    for key, base in sorted(account_map.items()):
        side_data: dict[str, Any] = {}
        total_selection = dict(base.get("selection") or {})
        leverage_pass = _leverage_filter_pass(
            total_selection,
            rules.max_leverage_p95_ratio,
            rules.max_high_leverage_holding_seconds,
        )
        martingale_hard_block = bool(base.get("martingale_hard_block"))
        for side in DIRECTIONS.values():
            phase_data: dict[str, Any] = {}
            for phase in PHASES:
                metric = _side_metric(normalized[(key, side, phase)], base, phase)
                metric["sample_status"] = _sample_status(metric, rules.min_trades)
                phase_data[phase] = metric
            side_data[side] = {
                "selection": phase_data["selection"],
                "validation": phase_data["validation"],
            }
        long_trades = int(side_data["long"]["selection"]["trade_count"])
        short_trades = int(side_data["short"]["selection"]["trade_count"])
        long_trade_share = round(long_trades / max(1, long_trades + short_trades), 6)
        short_trade_share = round(short_trades / max(1, long_trades + short_trades), 6)
        direction_balance_pass = _long_trades_ratio_pass(
            {"long_trades_ratio": long_trade_share},
            rules.min_long_trades_ratio,
            rules.max_long_trades_ratio,
        )
        for side in DIRECTIONS.values():
            passed, flags = _side_flags(
                side_data[side]["selection"],
                min_trades=rules.min_trades,
                min_win_rate=rules.min_win_rate,
                min_profit_factor=rules.min_profit_factor,
                min_payoff_ratio=rules.min_payoff_ratio,
                max_top1_day_profit_contribution=rules.max_top1_day_profit_contribution,
                leverage_pass=leverage_pass,
                direction_balance_pass=direction_balance_pass,
                martingale_hard_block=martingale_hard_block,
            )
            side_data[side]["pass"] = passed
            side_data[side]["selection_flags"] = flags
        long_status = side_data["long"]["selection"]["sample_status"]
        short_status = side_data["short"]["selection"]["sample_status"]
        long_pass = bool(side_data["long"]["pass"])
        short_pass = bool(side_data["short"]["pass"])
        account = {
            "platform": key[0],
            "login": key[1],
            "account_group": base.get("account_group", ""),
            "book": base.get("book") if base.get("book") in {"abook", "bbook"} else "bbook",
            "long": side_data["long"],
            "short": side_data["short"],
            "long_pass": long_pass,
            "short_pass": short_pass,
            "quadrant": _quadrant(long_pass, short_pass, long_status, short_status),
            "in_total_abook": key in total_abook_keys,
            "selection_flags_long": side_data["long"]["selection_flags"],
            "selection_flags_short": side_data["short"]["selection_flags"],
            "long_trade_share": long_trade_share,
            "short_trade_share": short_trade_share,
            "shared_gates": {
                "leverage_pass": leverage_pass,
                "direction_balance_pass": direction_balance_pass,
                "martingale_hard_block": martingale_hard_block,
            },
        }
        accounts.append(account)

    groups = {
        "all": accounts,
        "long_pass": [account for account in accounts if account["long_pass"]],
        "short_pass": [account for account in accounts if account["short_pass"]],
        "both_pass": [account for account in accounts if account["quadrant"] == "both_pass"],
        "long_only_pass": [account for account in accounts if account["quadrant"] == "long_only_pass"],
        "short_only_pass": [account for account in accounts if account["quadrant"] == "short_only_pass"],
        "neither_pass": [account for account in accounts if account["quadrant"] == "neither_pass"],
        "insufficient_side": [account for account in accounts if account["quadrant"] == "insufficient_side"],
    }
    group_payload = {
        name: {
            "long": _set_payload(group, "long", materialized_rows, normalized_daily, cumulative_start, cumulative_end),
            "short": _set_payload(group, "short", materialized_rows, normalized_daily, cumulative_start, cumulative_end),
        }
        for name, group in groups.items()
    }
    book_set_payload = {
        name: {
            book: {
                side: _set_payload(
                    [account for account in group if account.get("book") == book],
                    side,
                    materialized_rows,
                    normalized_daily,
                    cumulative_start,
                    cumulative_end,
                )
                for side in DIRECTIONS.values()
            }
            for book in ("abook", "bbook")
        }
        for name, group in groups.items()
    }
    book_counts = {
        book: {name: sum(1 for account in group if account.get("book") == book) for name, group in groups.items()}
        for book in ("abook", "bbook")
    }
    abook_but_one_sided = [account for account in accounts if account["in_total_abook"] and account["quadrant"] in {"long_only_pass", "short_only_pass"}]
    both_not_abook = [account for account in groups["both_pass"] if not account["in_total_abook"]]
    total_abook = [account for account in accounts if account["in_total_abook"]]
    total_abook_base = [base for key, base in account_map.items() if key in total_abook_keys]
    compact_accounts = [
        _compact_direction_account(account)
        for account in accounts
        if _has_material_direction_pnl(account) or account["long_pass"] or account["short_pass"]
    ]
    return {
        "pnl_basis": "matched.profit",
        "selection": {"start": request.selection.start.isoformat(), "end": request.selection.end.isoformat()},
        "validation": {"start": request.validation.start.isoformat(), "end": request.validation.end.isoformat()},
        "counts": {name: len(group) for name, group in groups.items()},
        "accounts": compact_accounts,
        "sets": group_payload,
        "book_sets": book_set_payload,
        "book_counts": book_counts,
        "comparison": {
            "total_abook_accounts": len(total_abook),
            "both_pass_accounts": len(groups["both_pass"]),
            "total_abook_and_both_pass": sum(1 for account in groups["both_pass"] if account["in_total_abook"]),
            "abook_but_one_sided": len(abook_but_one_sided),
            "both_pass_not_total_abook": len(both_not_abook),
            "total_abook_validation_matched_profit": round(
                sum(
                    float(account["long"]["validation"].get("side_pnl", 0) or 0)
                    + float(account["short"]["validation"].get("side_pnl", 0) or 0)
                    for account in total_abook
                ),
                6,
            ),
            "total_abook_validation_client_net_pnl": round(sum(float(account.get("validation", {}).get("client_net_pnl", 0) or 0) for account in total_abook_base), 6),
            "both_pass_validation_matched_profit": round(sum(float(account["long"]["validation"].get("side_pnl", 0) or 0) + float(account["short"]["validation"].get("side_pnl", 0) or 0) for account in groups["both_pass"]), 6),
        },
        "rules": {
            "min_trades": rules.min_trades,
            "min_win_rate": rules.min_win_rate,
            "min_profit_factor": rules.min_profit_factor,
            "min_payoff_ratio": rules.min_payoff_ratio,
            "min_long_trades_ratio": rules.min_long_trades_ratio,
            "max_long_trades_ratio": rules.max_long_trades_ratio,
            "max_top1_day_profit_contribution": rules.max_top1_day_profit_contribution,
            "max_leverage_p95_ratio": rules.max_leverage_p95_ratio,
            "max_high_leverage_holding_seconds": rules.max_high_leverage_holding_seconds,
            "candidate_overrides_included": False,
        },
    }
