from __future__ import annotations

from collections import Counter, defaultdict
from calendar import monthrange
from datetime import date, datetime
from statistics import median
from typing import Any, Iterable

from .service import AnalysisContext


AccountKey = tuple[str, int]
NEUTRAL_BAND = 10.0
PNL_BUCKETS = (
    ("loss_over_1000", "亏损 > 1000", None, -1000.0),
    ("loss_100_1000", "亏损 100–1000", -1000.0, -100.0),
    ("loss_10_100", "亏损 10–100", -100.0, -10.0),
    ("neutral", "中性 -10–10", -10.0, 10.0),
    ("profit_10_100", "盈利 10–100", 10.0, 100.0),
    ("profit_100_1000", "盈利 100–1000", 100.0, 1000.0),
    ("profit_over_1000", "盈利 > 1000", 1000.0, None),
)
LEVERAGE_BUCKETS = (
    ("0–1x", None, 1.0),
    ("1–2x", 1.0, 2.0),
    ("2–5x", 2.0, 5.0),
    ("5–10x", 5.0, 10.0),
    ("10–20x", 10.0, 20.0),
    ("20–50x", 20.0, 50.0),
    ("50–100x", 50.0, 100.0),
    ("100–200x", 100.0, 200.0),
    (">200x", 200.0, None),
)


def _key(account: dict[str, Any]) -> AccountKey:
    return str(account["platform"]), int(account["login"])


def _row_key(row: dict[str, Any]) -> AccountKey:
    return str(row.get("platform")), int(row.get("login", 0))


def split_books(accounts: list[dict[str, Any]], abook_keys: set[AccountKey]) -> tuple[list[dict], list[dict]]:
    population_keys = {_key(account) for account in accounts}
    abook = [account for account in accounts if _key(account) in abook_keys]
    bbook = [account for account in accounts if _key(account) not in abook_keys]
    if {_key(account) for account in abook} & {_key(account) for account in bbook}:
        raise ValueError("Abook and Bbook account sets overlap")
    if {_key(account) for account in abook} | {_key(account) for account in bbook} != population_keys:
        raise ValueError("Abook and Bbook do not partition the supplied population")
    return abook, bbook


def _drawdown(values: Iterable[float]) -> float:
    running = 0.0
    peak = 0.0
    maximum = 0.0
    for value in values:
        running += float(value)
        peak = max(peak, running)
        maximum = max(maximum, peak - running)
    return round(maximum, 6)


def _concentration(values: list[float]) -> dict[str, dict[str, float]]:
    net_total = sum(values)
    absolute_total = sum(abs(value) for value in values)
    ordered = sorted(values, key=abs, reverse=True)
    return {
        f"top_{count}": {
            "net_share": round(sum(ordered[:count]) / net_total, 6) if net_total else 0.0,
            "absolute_share": round(sum(abs(value) for value in ordered[:count]) / absolute_total, 6) if absolute_total else 0.0,
        }
        for count in (5, 10, 20)
    }


def _percentile(values: Iterable[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values if value is not None)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return round(ordered[0], 6)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 6)


def _metric_value(metric: dict[str, Any], name: str, default: float = 0.0) -> float:
    value = metric.get(name, default)
    return float(value) if value is not None else default


def _leverage_histogram(values: Iterable[float]) -> dict[str, list[Any]]:
    numeric_values = [float(value) for value in values if value is not None]
    counts = []
    for _, lower, upper in LEVERAGE_BUCKETS:
        if lower is None:
            counts.append(sum(value <= upper for value in numeric_values))
        elif upper is None:
            counts.append(sum(value > lower for value in numeric_values))
        else:
            counts.append(sum(lower < value <= upper for value in numeric_values))
    return {"labels": [label for label, _, _ in LEVERAGE_BUCKETS], "counts": counts}


def _phase_metrics(
    metrics: list[dict[str, Any]],
    phase: str,
    book: str,
    amount_metrics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    values = [_metric_value(metric, "client_net_pnl") for metric in metrics]
    amount_values = [
        _metric_value(metric, "client_net_pnl")
        for metric in (amount_metrics if amount_metrics is not None else metrics)
    ]
    profitable = [value for value in values if value > NEUTRAL_BAND]
    losses = [value for value in values if value < -NEUTRAL_BAND]
    neutral = [value for value in values if -NEUTRAL_BAND <= value <= NEUTRAL_BAND]
    trade_count = sum(int(_metric_value(metric, "trade_count")) for metric in metrics)
    winning_trades = sum(int(_metric_value(metric, "winning_trades")) for metric in metrics)
    gross_wins = sum(_metric_value(metric, "gross_wins") for metric in metrics)
    gross_losses = sum(_metric_value(metric, "gross_losses") for metric in metrics)
    positive_days = sum(int(_metric_value(metric, "daily_positive_days", _metric_value(metric, "positive_profit_days"))) for metric in metrics)
    negative_days = sum(int(_metric_value(metric, "daily_negative_days", _metric_value(metric, "negative_profit_days"))) for metric in metrics)
    flat_days = sum(int(_metric_value(metric, "daily_flat_days", _metric_value(metric, "flat_profit_days"))) for metric in metrics)
    net_pnl = round(sum(values), 6)
    # Account-level ``values`` drive account counts and net P&L. Amounts are
    # decomposed by the underlying month rows when available, so a May loss
    # is not hidden by a larger June profit from the same account.
    positive_pnl = round(sum(value for value in amount_values if value > 0), 6)
    negative_pnl = round(sum(value for value in amount_values if value < 0), 6)
    return {
        "phase": phase,
        "accounts": len(metrics),
        "active_accounts": sum(1 for metric in metrics if _metric_value(metric, "trade_count") > 0),
        "profitable_accounts": len(profitable),
        "loss_accounts": len(losses),
        "neutral_accounts": len(neutral),
        "profitable_rate": round(len(profitable) / len(metrics), 6) if metrics else 0.0,
        "loss_rate": round(len(losses) / len(metrics), 6) if metrics else 0.0,
        "neutral_rate": round(len(neutral) / len(metrics), 6) if metrics else 0.0,
        "total_client_net_pnl": net_pnl,
        "customer_net_pnl": net_pnl,
        "net_pnl": net_pnl,
        "positive_pnl": positive_pnl,
        "negative_pnl": negative_pnl,
        "bbook_company_profit": round(-net_pnl, 6),
        "company_profit_if_current_book": 0.0 if book == "abook" else round(-net_pnl, 6),
        "theoretical_company_increment_if_routed_abook": net_pnl if book == "abook" else 0.0,
        "average_pnl": round(net_pnl / len(metrics), 6) if metrics else 0.0,
        "median_pnl": round(float(median(values)), 6) if values else 0.0,
        "average_profitable_pnl": round(sum(profitable) / len(profitable), 6) if profitable else 0.0,
        "average_loss_pnl": round(sum(losses) / len(losses), 6) if losses else 0.0,
        "max_profit_pnl": round(max(values), 6) if values else 0.0,
        "max_loss_pnl": round(min(values), 6) if values else 0.0,
        "matched_trades": trade_count,
        "winning_trades": winning_trades,
        "losing_trades": sum(int(_metric_value(metric, "losing_trades")) for metric in metrics),
        "gross_wins": round(gross_wins, 6),
        "gross_losses": round(gross_losses, 6),
        "costs": round(sum(_metric_value(metric, "costs") for metric in metrics), 6),
        "win_rate": round(winning_trades / trade_count, 6) if trade_count else 0.0,
        "profit_factor": round(gross_wins / abs(gross_losses), 6) if gross_losses else None,
        "active_trade_days": sum(int(_metric_value(metric, "active_trade_days")) for metric in metrics),
        "daily_positive_days": positive_days,
        "daily_negative_days": negative_days,
        "daily_flat_days": flat_days,
        "positive_day_rate": round(positive_days / (positive_days + negative_days + flat_days), 6) if positive_days + negative_days + flat_days else 0.0,
        "negative_day_rate": round(negative_days / (positive_days + negative_days + flat_days), 6) if positive_days + negative_days + flat_days else 0.0,
        "pnl_distribution": values,
        "win_rate_distribution": [_metric_value(metric, "win_rate") for metric in metrics],
        "profit_factor_distribution": [_metric_value(metric, "profit_factor") for metric in metrics if metric.get("profit_factor") is not None],
        "profit_concentration": _concentration(values),
    }


def _style(account: dict[str, Any]) -> str:
    if account.get("martingale_blocked") or account.get("martingale_risk_level"):
        return "martingale"
    holding = float(account.get("selection", {}).get("median_holding_seconds", 0) or 0)
    trades = int(account.get("selection", {}).get("trade_count", 0) or 0)
    if holding < 60:
        return "scalping"
    if trades >= 100:
        return "high_frequency"
    if holding > 86400:
        return "swing"
    return "normal"


def _row_date(row: dict[str, Any]) -> date:
    value = row.get("trade_date")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _daily_series(context: AnalysisContext, accounts: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    keys = {_key(account) for account in accounts}
    grouped: dict[date, float] = defaultdict(float)
    for row in context.daily_rows:
        if _row_key(row) in keys:
            grouped[_row_date(row)] += float(row.get("client_net_pnl", 0) or 0)
    running = 0.0
    company_running = 0.0
    selection_start = date.fromisoformat(context.selection_start)
    selection_end = date.fromisoformat(context.selection_end)
    validation_start = date.fromisoformat(context.validation_start)
    validation_end = date.fromisoformat(context.validation_end)
    result = []
    for day in sorted(grouped):
        phase = "selection" if selection_start <= day <= selection_end else "validation" if validation_start <= day <= validation_end else "other"
        running += grouped[day]
        company_pnl = 0.0 if label == "abook" else -grouped[day]
        company_running += company_pnl
        result.append({
            "date": day.isoformat(), "phase": phase, "pnl": round(grouped[day], 6),
            "customer_pnl": round(grouped[day], 6), "cumulative_pnl": round(running, 6),
            "company_pnl": round(company_pnl, 6), "company_cumulative_pnl": round(company_running, 6), "book": label,
        })
    return result


def _bucket(values: Iterable[float], edges: list[float]) -> list[dict[str, Any]]:
    values = list(values)
    result = []
    for index, lower in enumerate(edges):
        upper = edges[index + 1] if index + 1 < len(edges) else None
        result.append({"lower": lower, "upper": upper, "accounts": sum(1 for value in values if value >= lower and (upper is None or value < upper))})
    return result


def _aggregate_symbol_rows(rows: Iterable[dict[str, Any]], keys: set[AccountKey] | None = None) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, float]] = defaultdict(lambda: {"trade_count": 0.0, "volume": 0.0, "market_pnl": 0.0})
    for row in rows:
        if keys is not None and _row_key(row) not in keys:
            continue
        item = grouped[str(row.get("symbol", "unknown"))]
        for field in item:
            item[field] += float(row.get(field, 0) or 0)
    return [{"symbol": symbol, **{field: round(value, 6) for field, value in metrics.items()}} for symbol, metrics in sorted(grouped.items(), key=lambda pair: abs(pair[1]["market_pnl"]), reverse=True)]


def _symbol_heatmap(symbol_rows: Any, book: str, keys: set[AccountKey]) -> list[dict[str, Any]]:
    if isinstance(symbol_rows, dict):
        population = _aggregate_symbol_rows(symbol_rows.get("population", []))
        selected = _aggregate_symbol_rows(symbol_rows.get("abook", []))
        if book == "abook":
            return selected
        selected_by_symbol = {row["symbol"]: row for row in selected}
        return [{"symbol": row["symbol"], **{field: round(float(row.get(field, 0)) - float(selected_by_symbol.get(row["symbol"], {}).get(field, 0)), 6) for field in ("trade_count", "volume", "market_pnl")}} for row in population]
    return _aggregate_symbol_rows(symbol_rows or [], keys)


def _metric_for_period(account: dict[str, Any], phase: str, month: str | None = None) -> dict[str, Any]:
    if month is None:
        return dict(account.get(phase, {}))
    for item in account.get("monthly", []):
        if item.get("month") == month:
            return dict(item)
    return {"month": month, "phase": phase, "client_net_pnl": 0.0, "trade_count": 0}


def _phase_amount_metrics(accounts: list[dict[str, Any]], phase: str) -> list[dict[str, Any]]:
    """Return monthly P&L rows for gross phase amount presentation."""
    result: list[dict[str, Any]] = []
    for account in accounts:
        monthly = [row for row in account.get("monthly", []) if row.get("phase") == phase]
        result.extend(monthly or [dict(account.get(phase, {}))])
    return result


def _pnl_distribution(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values = [_metric_value(metric, "client_net_pnl") for metric in metrics]
    result = []
    for key, label, lower, upper in PNL_BUCKETS:
        selected = [value for value in values if (lower is None or value >= lower) and (upper is None or value < upper)]
        result.append({"key": key, "label": label, "lower": lower, "upper": upper, "accounts": len(selected), "account_rate": round(len(selected) / len(values), 6) if values else 0.0, "net_pnl": round(sum(selected), 6)})
    return result


def _top_accounts(accounts: list[dict[str, Any]], phase: str, month: str | None = None) -> dict[str, list[dict[str, Any]]]:
    metrics = [_metric_for_period(account, phase, month) for account in accounts]
    positive_total = sum(max(_metric_value(metric, "client_net_pnl"), 0) for metric in metrics)
    negative_total = sum(abs(min(_metric_value(metric, "client_net_pnl"), 0)) for metric in metrics)
    rows = []
    for account, metric in zip(accounts, metrics):
        pnl = _metric_value(metric, "client_net_pnl")
        rows.append({"platform": str(account["platform"]), "login": int(account["login"]), "style": _style(account), "phase": phase, "month": month, "pnl": round(pnl, 6), "share": round(pnl / positive_total, 6) if pnl > 0 and positive_total else round(abs(pnl) / negative_total, 6) if pnl < 0 and negative_total else 0.0})
    return {
        "winners": sorted((row for row in rows if row["pnl"] > NEUTRAL_BAND), key=lambda row: row["pnl"], reverse=True)[:10],
        "losers": sorted((row for row in rows if row["pnl"] < -NEUTRAL_BAND), key=lambda row: row["pnl"])[:10],
    }


def _period_labels(context: AnalysisContext) -> tuple[list[str], list[str]]:
    def months(start: str, end: str) -> list[str]:
        current = date.fromisoformat(start).replace(day=1)
        last = date.fromisoformat(end).replace(day=1)
        result = []
        while current <= last:
            result.append(current.strftime("%Y-%m"))
            current = current.replace(year=current.year + (1 if current.month == 12 else 0), month=1 if current.month == 12 else current.month + 1)
        return result
    return months(context.selection_start, context.selection_end), months(context.validation_start, context.validation_end)


def _style_breakdown(accounts: list[dict[str, Any]], selection_months: list[str], validation_months: list[str]) -> list[dict[str, Any]]:
    periods = [(month, "selection", month) for month in selection_months] + [("selection_total", "selection", None)] + [(month, "validation", month) for month in validation_months]
    rows = []
    for label, phase, month in periods:
        for style in sorted({_style(account) for account in accounts}):
            style_accounts = [account for account in accounts if _style(account) == style]
            summary = _phase_metrics([_metric_for_period(account, phase, month) for account in style_accounts], phase, "abook")
            rows.append({"period": label, "phase": phase, "style": style, **{key: summary[key] for key in ("accounts", "active_accounts", "profitable_accounts", "loss_accounts", "neutral_accounts", "positive_pnl", "negative_pnl", "net_pnl", "average_pnl", "win_rate", "profit_factor")}})
    return rows


def _risk_summary(accounts: list[dict[str, Any]], pnl_structure: dict[str, Any]) -> dict[str, Any]:
    leverage = [float(account["risk_leverage_p95_ratio"]) for account in accounts if account.get("risk_leverage_p95_ratio") is not None]
    threshold = _percentile(leverage, 0.95)
    extreme = [value for value in leverage if float(value) >= threshold] if threshold else []
    validation_values = [float(account.get("validation", {}).get("client_net_pnl", 0) or 0) for account in accounts]
    negative_total = sum(abs(value) for value in validation_values if value < 0)
    loss_tail = sum(sorted((abs(value) for value in validation_values if value < -NEUTRAL_BAND), reverse=True)[:5])
    return {
        "leverage_distribution": leverage, "leverage_p50": _percentile(leverage, 0.50), "leverage_p75": _percentile(leverage, 0.75),
        "leverage_p95": threshold, "leverage_max": round(max(leverage), 6) if leverage else 0.0,
        "leverage_histogram": _leverage_histogram(leverage),
        "extreme_leverage_threshold": threshold, "extreme_leverage_accounts": len(extreme), "extreme_leverage_rate": round(len(extreme) / len(leverage), 6) if leverage else 0.0,
        "selection_max_drawdown": pnl_structure["max_drawdown_by_phase"]["selection"], "validation_max_drawdown": pnl_structure["max_drawdown_by_phase"]["validation"],
        "selection_worst_daily_customer_pnl": pnl_structure["worst_daily_customer_pnl"]["selection"], "validation_worst_daily_customer_pnl": pnl_structure["worst_daily_customer_pnl"]["validation"],
        "validation_loss_tail_5_share": round(loss_tail / negative_total, 6) if negative_total else 0.0,
    }


def _daily_hit_curves(context: AnalysisContext, accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    books = {_key(account): account.get("book", "bbook") for account in accounts}
    grouped: dict[date, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(lambda: {"active": 0, "hits": 0, "net_pnl": 0.0}))
    for row in context.daily_rows:
        book = books.get(_row_key(row))
        if not book:
            continue
        value = float(row.get("client_net_pnl", 0) or 0)
        item = grouped[_row_date(row)][book]
        if int(row.get("matched_trades", 0) or 0) > 0:
            item["active"] += 1
            item["hits"] += 1 if (value > 0 if book == "abook" else value < 0) else 0
        item["net_pnl"] += value
    cumulative = 0
    result = []
    for day in sorted(grouped):
        abook = grouped[day].get("abook", {"active": 0, "hits": 0, "net_pnl": 0.0})
        bbook = grouped[day].get("bbook", {"active": 0, "hits": 0, "net_pnl": 0.0})
        difference = abook["hits"] - bbook["hits"]
        cumulative += difference
        result.append({"date": day.isoformat(), "abook_hit_rate": round(abook["hits"] / abook["active"], 6) if abook["active"] else 0.0, "bbook_hit_rate": round(bbook["hits"] / bbook["active"], 6) if bbook["active"] else 0.0, "abook_net_pnl": round(abook["net_pnl"], 6), "bbook_net_pnl": round(bbook["net_pnl"], 6), "hit_difference": difference, "cumulative_hit_difference": cumulative})
    return result


def _company_profit_comparison(context: AnalysisContext, accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    account_books = {_key(account): account.get("book", "bbook") for account in accounts}
    grouped: dict[date, dict[str, float]] = defaultdict(lambda: {"baseline_user_net_pnl": 0.0, "bbook_user_net_pnl": 0.0})
    for row in context.overview_daily_rows or context.daily_rows:
        day = _row_date(row)
        value = float(row.get("client_net_pnl", 0) or 0)
        grouped[day]["baseline_user_net_pnl"] += value
        if account_books.get(_row_key(row)) == "bbook":
            grouped[day]["bbook_user_net_pnl"] += value
    return [{"date": day.isoformat(), "baseline_company_profit": round(-item["baseline_user_net_pnl"], 6), "after_routing_company_profit": round(-item["bbook_user_net_pnl"], 6), "incremental_change": round(item["baseline_user_net_pnl"] - item["bbook_user_net_pnl"], 6), "company_profit_definition": "Bbook 公司 P&L = -Bbook 客户 P&L"} for day, item in sorted(grouped.items())]


def build_book_analytics(
    context: AnalysisContext,
    accounts: list[dict[str, Any]],
    abook_keys: set[AccountKey],
    symbol_rows: Iterable[dict[str, Any]] | dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    abook, bbook = split_books(accounts, abook_keys)
    books = {"abook": abook, "bbook": bbook}
    selection_months, validation_months = _period_labels(context)
    pnl_structure: dict[str, Any] = {}
    user_structure: dict[str, Any] = {}
    risk_exposure: dict[str, Any] = {}
    for book, items in books.items():
        daily_series = _daily_series(context, items, book)
        phase_metrics = {
            phase: _phase_metrics(
                [_metric_for_period(account, phase) for account in items],
                phase,
                book,
                amount_metrics=_phase_amount_metrics(items, phase),
            )
            for phase in ("selection", "validation")
        }
        monthly = []
        periods = [(month, "selection", month) for month in selection_months] + [("selection_total", "selection", None)] + [(month, "validation", month) for month in validation_months]
        distribution_by_period = []
        for label, phase, month in periods:
            metrics = [_metric_for_period(account, phase, month) for account in items]
            summary = _phase_metrics(
                metrics,
                phase,
                book,
                amount_metrics=_phase_amount_metrics(items, phase) if month is None else None,
            )
            monthly.append({"month": label, "phase": phase, **{key: value for key, value in summary.items() if key != "phase"}})
            distribution_by_period.extend({"period": label, **row} for row in _pnl_distribution(metrics))
        phase_daily = {phase: [row for row in daily_series if row["phase"] == phase] for phase in ("selection", "validation")}
        max_drawdown_by_phase = {phase: _drawdown(row["pnl"] for row in phase_daily[phase]) for phase in phase_daily}
        worst_daily = {phase: round(min((row["pnl"] for row in phase_daily[phase]), default=0.0), 6) for phase in phase_daily}
        validation_top = _top_accounts(items, "validation")
        pnl_structure[book] = {
            "selection": phase_metrics["selection"], "validation": phase_metrics["validation"], "phase_summary": phase_metrics, "monthly": monthly,
            "distribution": _pnl_distribution([_metric_for_period(account, "validation") for account in items]),
            "distribution_by_period": distribution_by_period,
            "style_breakdown": _style_breakdown(items, selection_months, validation_months),
            "top_accounts": {**validation_top, "by_period": {"selection": _top_accounts(items, "selection"), "validation": validation_top}},
            "daily_series": daily_series, "max_drawdown": max_drawdown_by_phase["validation"], "max_drawdown_by_phase": max_drawdown_by_phase,
            "worst_daily_customer_pnl": worst_daily, "profit_concentration": phase_metrics["validation"]["profit_concentration"],
        }
        style_counts = Counter(_style(account) for account in items)
        user_structure[book] = {
            "styles": [{"style": style, "accounts": count, "validation_client_net_pnl": round(sum(_metric_value(_metric_for_period(account, "validation"), "client_net_pnl") for account in items if _style(account) == style), 6)} for style, count in sorted(style_counts.items())],
            "holding_seconds": [float(account.get("selection", {}).get("median_holding_seconds", 0) or 0) for account in items],
            "holding_duration_bins": _bucket((float(account.get("selection", {}).get("median_holding_seconds", 0) or 0) for account in items), [0, 60, 300, 3600, 86400]),
            "trade_counts": [int(account.get("selection", {}).get("trade_count", 0) or 0) for account in items],
            "trade_volume_bins": _bucket((float(account.get("selection", {}).get("total_volume", 0) or 0) for account in items), [0, 1, 10, 100, 1000]),
            "symbol_heatmap": _symbol_heatmap(symbol_rows, book, {_key(account) for account in items}),
        }
        risk_exposure[book] = _risk_summary(items, pnl_structure[book])
    transitions = Counter((account.get("book", "bbook"), account.get("validation_status", "no_trade")) for account in accounts)
    transition_matrix: dict[str, dict[str, int]] = defaultdict(dict)
    for (book, status), count in transitions.items():
        transition_matrix[book][status] = count
    return {
        "pnl_structure": pnl_structure, "user_structure": user_structure, "risk_exposure": risk_exposure,
        "routing_quality": {
            "transitions": [{"book": book, "validation_status": status, "accounts": count} for (book, status), count in sorted(transitions.items())],
            "transition_matrix": dict(transition_matrix), "daily_hit_curves": _daily_hit_curves(context, accounts),
            "company_profit_comparison": _company_profit_comparison(context, accounts),
            "validation_partial": (
                date.fromisoformat(context.validation_end).day
                < monthrange(
                    date.fromisoformat(context.validation_end).year,
                    date.fromisoformat(context.validation_end).month,
                )[1]
            ),
            "sample_warning": sum(1 for account in abook if account.get("validation", {}).get("trade_count", 0) > 0) < 30,
        },
    }
