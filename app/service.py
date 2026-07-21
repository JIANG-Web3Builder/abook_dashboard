from __future__ import annotations

from collections import defaultdict
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
import math
from typing import Any, Iterable, Optional

from .metrics import calculate_drawdown
from .account_detail import build_account_detail_metrics, summarize_markout_rows
from .r4 import R4_MIN_PRIMARY_TRADES, R4_MIN_TRADES, R4_PRIMARY_TRADE_PCT, R4_WIN_RATE


ZERO = Decimal("0")
NEUTRAL_BAND_USD = Decimal("10")


@dataclass
class AnalysisContext:
    rows: list[dict[str, Any]]
    daily_rows: list[dict[str, Any]]
    overview_rows: list[dict[str, Any]]
    overview_daily_rows: list[dict[str, Any]]
    selection_start: str
    selection_end: str
    validation_start: str
    validation_end: str


def prepare_analysis_context(
    rows: Iterable[dict[str, Any]],
    *,
    daily_rows: Iterable[dict[str, Any]] | None = None,
    overview_rows: Iterable[dict[str, Any]] | None = None,
    overview_daily_rows: Iterable[dict[str, Any]] | None = None,
    selection_start: str,
    selection_end: str,
    validation_start: str,
    validation_end: str,
) -> AnalysisContext:
    materialized = list(rows)
    return AnalysisContext(
        rows=materialized,
        daily_rows=list(daily_rows or []),
        overview_rows=list(overview_rows) if overview_rows is not None else list(materialized),
        overview_daily_rows=list(overview_daily_rows or []),
        selection_start=selection_start,
        selection_end=selection_end,
        validation_start=validation_start,
        validation_end=validation_end,
    )


def _safe_ratio(numerator: Decimal, denominator: Decimal) -> float:
    return _ratio(numerator, denominator)


def _median(values: list[Decimal]) -> Decimal:
    if not values:
        return ZERO
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _percentile(values: Iterable[Decimal], quantile: float) -> Decimal:
    """Linear-interpolated percentile for small local snapshot vectors."""
    ordered = sorted(_finite_decimal(value) for value in values)
    if not ordered:
        return ZERO
    if quantile <= 0:
        return ordered[0]
    if quantile >= 1:
        return ordered[-1]
    position = Decimal(str(quantile)) * Decimal(len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - Decimal(lower)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _final_book(*, martingale_detected: bool, personal_candidate: bool, abook_rules_pass: bool) -> str:
    """Return the only two allowed business destinations."""
    if martingale_detected:
        return "bbook"
    if personal_candidate or abook_rules_pass:
        return "abook"
    return "bbook"


def _decimal(value: Any) -> Decimal:
    if value is None:
        return ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _finite_decimal(value: Any) -> Decimal:
    decimal = _decimal(value)
    return decimal if decimal.is_finite() else ZERO


def _float(value: Any) -> float:
    return float(_decimal(value))


def _optional_float(value: Any) -> Optional[float]:
    return None if value is None else _float(value)


def _canonical_market_pnl(row: dict[str, Any]) -> Decimal:
    """Use Deals P&L as the single market-P&L source; keep matched as fallback for old fixtures."""
    if row.get("market_pnl") is not None:
        return _finite_decimal(row.get("market_pnl"))
    return _finite_decimal(row.get("matched_market_pnl"))


def _month(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return f"{value.year:04d}-{value.month:02d}"
    return str(value)[:7]


def _sum(rows: Iterable[dict[str, Any]], field: str) -> Decimal:
    return sum((_decimal(row.get(field)) for row in rows), ZERO)


def _ratio(numerator: Decimal, denominator: Decimal) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _stddev(values: list[Decimal]) -> Decimal:
    if len(values) < 2:
        return ZERO
    mean = sum(values, ZERO) / Decimal(len(values))
    variance = sum(((value - mean) ** 2 for value in values), ZERO) / Decimal(len(values))
    return Decimal(str(math.sqrt(max(0.0, float(variance)))))


def _max_field(rows: Iterable[dict[str, Any]], field: str) -> Decimal:
    values = [_decimal(row.get(field)) for row in rows]
    return max(values) if values else ZERO


def _min_field(rows: Iterable[dict[str, Any]], field: str) -> Decimal:
    values = [_decimal(row.get(field)) for row in rows]
    return min(values) if values else ZERO


def _profit_factor(gross_wins: Decimal, gross_losses: Decimal) -> Optional[float]:
    if gross_losses == ZERO:
        # JSON has no representation for Infinity. Null means there were no
        # losing trades; such an account still passes any finite PF threshold.
        return None if gross_wins > ZERO else 0.0
    return _ratio(gross_wins, abs(gross_losses))


def _monthly_consistency_ratio(best_month_pnl: Any, worst_month_pnl: Any) -> float:
    """Measure the weaker directional month relative to the stronger month."""
    best = _finite_decimal(best_month_pnl)
    worst = _finite_decimal(worst_month_pnl)
    if best > ZERO and worst > ZERO:
        return _safe_ratio(worst, best)
    if best < ZERO and worst < ZERO:
        return _safe_ratio(abs(best), abs(worst))
    return 0.0


def _aggregate_account(rows: list[dict[str, Any]]) -> dict[str, Any]:
    first = rows[0]
    trade_count = sum(int(row.get("matched_trades", 0) or 0) for row in rows)
    winning_trades = sum(int(row.get("winning_trades", 0) or 0) for row in rows)
    losing_trades = sum(int(row.get("losing_trades", 0) or 0) for row in rows)
    weighted_hold = sum(
        _decimal(row.get("avg_holding_seconds")) * int(row.get("matched_trades", 0) or 0)
        for row in rows
    )
    gross_wins = _sum(rows, "gross_wins")
    gross_losses = _sum(rows, "gross_losses")
    active_trade_days = sum(int(row.get("active_trade_days", 0) or 0) for row in rows)
    daily_active_days = sum(
        int(row.get("daily_active_days", row.get("active_trade_days", 0)) or 0)
        for row in rows
    )
    daily_positive_days = sum(int(row.get("daily_positive_days", row.get("positive_profit_days", 0)) or 0) for row in rows)
    daily_negative_days = sum(int(row.get("daily_negative_days", row.get("negative_profit_days", 0)) or 0) for row in rows)
    daily_flat_days = sum(int(row.get("daily_flat_days", row.get("flat_profit_days", 0)) or 0) for row in rows)
    daily_positive_sum = _sum(rows, "daily_positive_sum")
    daily_negative_sum = _sum(rows, "daily_negative_sum")
    daily_abs_sum = _sum(rows, "daily_abs_sum")
    total_volume = _sum(rows, "matched_volume")
    variance_sum = _sum(rows, "daily_pnl_sum_for_variance")
    square_sum = _sum(rows, "daily_pnl_square_sum")
    variance_count = sum(int(row.get("daily_variance_count", 0) or 0) for row in rows)
    if variance_count > 0:
        mean = variance_sum / Decimal(variance_count)
        variance = max(ZERO, square_sum / Decimal(variance_count) - mean * mean)
        daily_stddev = Decimal(str(math.sqrt(max(0.0, float(variance)))))
    else:
        legacy_weighted_stddev = sum(
            (
                _finite_decimal(row.get("daily_stddev"))
                * Decimal(int(row.get("daily_active_days", row.get("active_trade_days", 0)) or 0))
                for row in rows
            ),
            ZERO,
        )
        daily_stddev = _ratio(legacy_weighted_stddev, Decimal(daily_active_days))
    daily_profit_sum = _sum(rows, "daily_profit_sum")
    cumulative = []
    running = ZERO
    for row in sorted(rows, key=lambda item: _month(item["month_start"])):
        running += _decimal(row.get("client_net_pnl"))
        cumulative.append(running)
    return {
        "platform": first["platform"],
        "login": int(first["login"]),
        "account_group": first.get("account_group", ""),
        "country": first.get("country", ""),
        "leverage": int(first.get("leverage", 0) or 0),
        "registration": _json_value(first.get("registration")),
        "last_access": _json_value(first.get("last_access")),
        "balance": _float(first.get("balance")),
        "equity_prev_day": _float(first.get("equity_prev_day")),
        "risk_balance_prev_month": _optional_float(first.get("risk_balance_prev_month")),
        "risk_average_open_degree": _optional_float(first.get("risk_average_open_degree")),
        "risk_peak_leverage_ratio": _optional_float(first.get("risk_peak_leverage_ratio")),
        "risk_leverage_p95_ratio": _optional_float(
            _percentile(
                [row.get("risk_leverage_p95_ratio") for row in rows if row.get("risk_leverage_p95_ratio") is not None],
                0.95,
            )
        ),
        "risk_balance_status": first.get("risk_balance_status", "not_available"),
        "agent": int(first.get("agent", 0) or 0),
        "client_id": str(first.get("client_id", "")),
        "trade_count": trade_count,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "total_volume": _float(total_volume),
        "client_net_pnl": _float(_sum(rows, "client_net_pnl")),
        "market_pnl": _float(sum((_canonical_market_pnl(row) for row in rows), ZERO)),
        "theoretical_mirror_pnl": _float(-sum((_canonical_market_pnl(row) for row in rows), ZERO)),
        "gross_wins": _float(gross_wins),
        "gross_losses": _float(gross_losses),
        "costs": _float(_sum(rows, "costs")),
        "funding_pnl": _float(_sum(rows, "funding_pnl")),
        "win_rate": _ratio(Decimal(winning_trades), Decimal(trade_count)),
        "profit_factor": _profit_factor(gross_wins, gross_losses),
        "active_trade_days": active_trade_days,
        "daily_active_days": daily_active_days,
        "daily_positive_days": daily_positive_days,
        "daily_negative_days": daily_negative_days,
        "daily_flat_days": daily_flat_days,
        "daily_positive_sum": _float(daily_positive_sum),
        "daily_negative_sum": _float(daily_negative_sum),
        "daily_abs_sum": _float(daily_abs_sum),
        "max_positive_day": _float(_max_field(rows, "max_positive_day")),
        "min_negative_day": _float(_min_field(rows, "min_negative_day")),
        "daily_stddev": float(daily_stddev),
        "daily_profit_sum": _float(daily_profit_sum),
        "average_daily_profit": _float(_ratio(daily_profit_sum, Decimal(active_trade_days))),
        "avg_holding_seconds": _ratio(weighted_hold, Decimal(trade_count)),
        "median_holding_seconds": _float(_median([_finite_decimal(row.get("median_holding_seconds")) for row in rows])),
        "long_trades": sum(int(row.get("long_trades", 0) or 0) for row in rows),
        "short_trades": sum(int(row.get("short_trades", 0) or 0) for row in rows),
        "symbols_traded": max(int(row.get("symbols_traded", 0) or 0) for row in rows),
        "max_drawdown": _float(calculate_drawdown(cumulative)),
        "months": [_month(row["month_start"]) for row in sorted(rows, key=lambda item: _month(item["month_start"]))],
    }


def build_analysis_payload(
    rows: Iterable[dict[str, Any]],
    lookback_months: int,
    min_profit_factor: float = 1.0,
    min_avg_daily_profit: float = 10.0,
) -> dict[str, Any]:
    """Convert account-month query rows into dashboard-ready JSON."""
    materialized = [
        row for row in rows
        if "test" not in str(row.get("account_group", "")).lower()
        and "demo" not in str(row.get("account_group", "")).lower()
    ]
    months = sorted({_month(row["month_start"]) for row in materialized})
    selected_months = set(months[-lookback_months:])
    account_rows: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in materialized:
        if _month(row["month_start"]) in selected_months:
            account_rows[(row["platform"], int(row["login"]))].append(row)
    accounts = [_aggregate_account(items) for items in account_rows.values()]
    accounts = [
        account for account in accounts
        if (account["profit_factor"] is None or account["profit_factor"] > min_profit_factor)
    ]
    accounts.sort(key=lambda item: (item["client_net_pnl"], item["platform"], item["login"]), reverse=True)
    eligible_keys = {(account["platform"], account["login"]) for account in accounts}

    monthly: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in materialized:
        if _month(row["month_start"]) in selected_months and (row["platform"], int(row["login"])) in eligible_keys:
            monthly[_month(row["month_start"])].append(row)
    monthly_series = []
    cumulative_client = ZERO
    cumulative_mirror = ZERO
    for month in sorted(monthly):
        month_rows = monthly[month]
        trades = sum(int(row.get("matched_trades", 0) or 0) for row in month_rows)
        wins = sum(int(row.get("winning_trades", 0) or 0) for row in month_rows)
        client_net = _sum(month_rows, "client_net_pnl")
        market = sum((_canonical_market_pnl(row) for row in month_rows), ZERO)
        cumulative_client += client_net
        cumulative_mirror -= market
        monthly_series.append({
            "month": month,
            "active_accounts": len({(row["platform"], int(row["login"])) for row in month_rows}),
            "matched_trades": trades,
            "winning_trades": wins,
            "losing_trades": sum(int(row.get("losing_trades", 0) or 0) for row in month_rows),
            "total_volume": _float(_sum(month_rows, "matched_volume")),
            "client_net_pnl": _float(client_net),
            "market_pnl": _float(market),
            "theoretical_mirror_pnl": _float(-market),
            "costs": _float(_sum(month_rows, "costs")),
            "funding_pnl": _float(_sum(month_rows, "funding_pnl")),
            "cumulative_client_net_pnl": _float(cumulative_client),
            "cumulative_theoretical_mirror_pnl": _float(cumulative_mirror),
        })
    client_curve = [Decimal(str(item["cumulative_client_net_pnl"])) for item in monthly_series]
    kpis = {
        "selected_accounts": len(accounts),
        "active_accounts": sum(1 for account in accounts if account["trade_count"] > 0),
        "matched_trades": sum(account["trade_count"] for account in accounts),
        "total_volume": sum(account["total_volume"] for account in accounts),
        "client_net_pnl": sum(account["client_net_pnl"] for account in accounts),
        "market_pnl": sum(account["market_pnl"] for account in accounts),
        "theoretical_mirror_pnl": sum(account["theoretical_mirror_pnl"] for account in accounts),
        "costs": sum(account["costs"] for account in accounts),
        "funding_pnl": sum(account["funding_pnl"] for account in accounts),
        "win_rate": _ratio(
            Decimal(sum(account["winning_trades"] for account in accounts)),
            Decimal(sum(account["trade_count"] for account in accounts)),
        ),
        "avg_holding_seconds": _ratio(
            sum(Decimal(str(account["avg_holding_seconds"])) * account["trade_count"] for account in accounts),
            Decimal(sum(account["trade_count"] for account in accounts)),
        ),
        "max_drawdown": _float(calculate_drawdown(client_curve)),
    }
    return {
        "coverage": {"months": months[-lookback_months:], "source": "UTC"},
        "lookback_months": lookback_months,
        "strategy_filters": {
            "profit_factor_gt": min_profit_factor,
            "average_daily_profit_gt": None,
            "average_daily_profit_definition": "not used for routing",
        },
        "kpis": kpis,
        "monthly_series": monthly_series,
        "accounts": accounts,
    }


def build_account_detail_payload(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    materialized = list(rows)
    symbols: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in materialized:
        symbols[str(row.get("symbol", ""))].append(row)
    symbol_rows = []
    for symbol, items in symbols.items():
        wins = sum(1 for row in items if _decimal(row.get("profit")) > 0)
        symbol_rows.append({
            "symbol": symbol,
            "trade_count": len(items),
            "volume": _float(_sum(items, "volume")),
            "profit": _float(_sum(items, "profit")),
            "win_rate": _ratio(Decimal(wins), Decimal(len(items))),
            "avg_holding_seconds": _ratio(_sum(items, "holding_seconds"), Decimal(len(items))),
        })
    symbol_rows.sort(key=lambda item: item["profit"], reverse=True)
    return {
        "symbols": symbol_rows,
        **build_account_detail_metrics(materialized),
        "markout": summarize_markout_rows(materialized),
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return value


def _json_row(row: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            result[key] = _float(value)
        else:
            result[key] = _json_value(value)
    return result


def _empty_period_row(account: dict[str, Any], month: str) -> dict[str, Any]:
    """Create a zero-activity month so validation keeps inactive accounts visible."""
    return {
        "platform": account["platform"],
        "login": account["login"],
        "account_group": account.get("account_group", ""),
        "month_start": datetime.fromisoformat(f"{month}-01"),
        "matched_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "matched_volume": ZERO,
        "matched_market_pnl": ZERO,
        "market_pnl": ZERO,
        "gross_wins": ZERO,
        "gross_losses": ZERO,
        "deal_market_pnl": ZERO,
        "costs": ZERO,
        "client_net_pnl": ZERO,
        "funding_pnl": ZERO,
        "active_trade_days": 0,
        "daily_profit_sum": ZERO,
        "positive_profit_days": 0,
        "negative_profit_days": 0,
        "flat_profit_days": 0,
        "daily_active_days": 0,
        "daily_positive_days": 0,
        "daily_negative_days": 0,
        "daily_flat_days": 0,
        "daily_positive_sum": ZERO,
        "daily_negative_sum": ZERO,
        "max_positive_day": ZERO,
        "min_negative_day": ZERO,
        "daily_stddev": ZERO,
        "daily_pnl_sum_for_variance": ZERO,
        "daily_pnl_square_sum": ZERO,
        "daily_variance_count": 0,
        "daily_abs_sum": ZERO,
        "avg_holding_seconds": ZERO,
        "median_holding_seconds": ZERO,
        "long_trades": 0,
        "short_trades": 0,
        "symbols_traded": 0,
        "selection_median_holding_seconds": ZERO,
        "validation_median_holding_seconds": ZERO,
        "selection_symbols_traded": 0,
        "validation_symbols_traded": 0,
    }


def _period_months(start: str, end: str) -> list[str]:
    start_date = date.fromisoformat(start).replace(day=1)
    end_date = date.fromisoformat(end).replace(day=1)
    result = []
    current = start_date
    while current <= end_date:
        result.append(f"{current.year:04d}-{current.month:02d}")
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return result


def _rows_for_phase(rows: list[dict[str, Any]], phase: str, months: list[str]) -> list[dict[str, Any]]:
    """Select exact phase rows when the query provides them; retain legacy fallback."""
    tagged = [row for row in rows if row.get("phase") in {"selection", "validation"}]
    if tagged:
        return [row for row in tagged if row.get("phase") == phase]
    return [row for row in rows if _month(row["month_start"]) in months]


def _account_period_metrics(
    rows: list[dict[str, Any]], account: dict[str, Any], phase: str | None = None
) -> dict[str, Any]:
    if not rows:
        rows = [_empty_period_row(account, "1970-01")]
    metrics = _aggregate_account(rows)
    avg_profit_values = [row.get("avg_profit") for row in rows if row.get("avg_profit") is not None]
    metrics["avg_profit"] = float(avg_profit_values[0]) if avg_profit_values else None
    metrics["avg_profit_status"] = next(
        (str(row.get("avg_profit_status")) for row in rows if row.get("avg_profit_status")),
        "not_loaded",
    )
    if phase in {"selection", "validation"}:
        median_field = f"{phase}_median_holding_seconds"
        symbols_field = f"{phase}_symbols_traded"
        phase_medians = [_finite_decimal(row.get(median_field)) for row in rows if row.get(median_field) is not None]
        phase_symbols = [int(row.get(symbols_field, 0) or 0) for row in rows]
        # period_matched_stats is an account-level exact median repeated on
        # each monthly row. Use that value once; taking max(month medians)
        # overstates the phase median and can incorrectly reject short holds.
        metrics["median_holding_seconds"] = _float(phase_medians[0] if phase_medians else ZERO)
        metrics["symbols_traded"] = max(phase_symbols, default=0)
    metrics["active_months"] = sum(
        1 for row in rows if int(row.get("matched_trades", 0) or 0) > 0
    )
    positive_days = sum(int(row.get("positive_profit_days", 0) or 0) for row in rows)
    negative_days = sum(int(row.get("negative_profit_days", 0) or 0) for row in rows)
    flat_days = sum(int(row.get("flat_profit_days", 0) or 0) for row in rows)
    metrics["positive_profit_days"] = positive_days
    metrics["negative_profit_days"] = negative_days
    metrics["flat_profit_days"] = flat_days
    metrics["profitable_day_rate"] = _safe_ratio(
        Decimal(positive_days), Decimal(positive_days + negative_days + flat_days)
    )
    month_values = [Decimal(str(row.get("client_net_pnl", 0) or 0)) for row in rows]
    metrics["month_count"] = len(rows)
    metrics["positive_months"] = sum(1 for value in month_values if value > ZERO)
    metrics["negative_months"] = sum(1 for value in month_values if value < ZERO)
    metrics["positive_month_rate"] = _safe_ratio(
        Decimal(metrics["positive_months"]), Decimal(len(rows))
    )
    metrics["negative_month_rate"] = _safe_ratio(
        Decimal(metrics["negative_months"]), Decimal(len(rows))
    )
    daily_days = int(metrics.get("daily_active_days", metrics["active_trade_days"]) or 0)
    daily_positive = int(metrics.get("daily_positive_days", positive_days) or 0)
    daily_negative = int(metrics.get("daily_negative_days", negative_days) or 0)
    metrics["positive_day_rate"] = _safe_ratio(Decimal(daily_positive), Decimal(daily_days))
    metrics["negative_day_rate"] = _safe_ratio(Decimal(daily_negative), Decimal(daily_days))
    metrics["positive_day_rate_ci95"] = _wilson_interval(daily_positive, daily_days)
    metrics["negative_day_rate_ci95"] = _wilson_interval(daily_negative, daily_days)
    positive_sum = _decimal(metrics.get("daily_positive_sum"))
    negative_sum = _decimal(metrics.get("daily_negative_sum"))
    metrics["top_positive_day_concentration"] = _safe_ratio(
        _decimal(metrics.get("max_positive_day")), positive_sum
    )
    metrics["top_negative_day_concentration"] = _safe_ratio(
        abs(_decimal(metrics.get("min_negative_day"))), abs(negative_sum)
    )
    monthly_contributions = []
    for row in rows:
        month_profit = _decimal(row.get("client_net_pnl"))
        max_positive_day = _decimal(row.get("max_positive_day"))
        if month_profit > ZERO and max_positive_day > ZERO:
            monthly_contributions.append(_safe_ratio(max_positive_day, month_profit))
    metrics["max_daily_profit_month_contribution"] = max(monthly_contributions, default=0.0)
    metrics["monthly_pnl_stddev"] = _float(_stddev(month_values))
    metrics["best_month_pnl"] = _float(max(month_values) if month_values else ZERO)
    metrics["worst_month_pnl"] = _float(min(month_values) if month_values else ZERO)
    metrics["monthly_consistency_ratio"] = _monthly_consistency_ratio(
        metrics["best_month_pnl"], metrics["worst_month_pnl"]
    )
    metrics["expectancy_per_trade"] = _safe_ratio(
        _decimal(metrics.get("client_net_pnl")), Decimal(metrics["trade_count"])
    )
    metrics["average_win"] = _safe_ratio(
        _decimal(metrics.get("gross_wins")), Decimal(metrics["winning_trades"])
    )
    metrics["average_loss"] = _safe_ratio(
        abs(_decimal(metrics.get("gross_losses"))), Decimal(metrics["losing_trades"])
    )
    metrics["payoff_ratio"] = _safe_ratio(
        _decimal(str(metrics["average_win"])), _decimal(str(metrics["average_loss"]))
    )
    metrics["return_drawdown_ratio"] = _safe_ratio(
        _decimal(metrics.get("client_net_pnl")), Decimal(str(metrics["max_drawdown"]))
    )
    return metrics


def _wilson_interval(successes: int, total: int) -> list[float]:
    if total <= 0:
        return [0.0, 0.0]
    proportion = successes / total
    z = 1.959963984540054
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * ((proportion * (1 - proportion) / total + z * z / (4 * total * total)) ** 0.5) / denominator
    return [max(0.0, centre - margin), min(1.0, centre + margin)]


def _date_text(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _stability_assessment(
    selection: dict[str, Any],
    *,
    min_trades: int,
    min_active_days: int,
    min_win_rate: float,
    min_profit_factor: float,
    min_payoff_ratio: float,
    min_positive_month_rate: float,
    min_selection_monthly_consistency: float,
    max_top1_day_profit_contribution: float,
    max_daily_profit_month_contribution: float,
    max_leverage_p95_ratio: float,
    max_high_leverage_holding_seconds: float,
    min_direction_day_rate_lower_bound: float,
    min_stability_score: float,
    high_confidence_trades: int,
    high_confidence_days: int,
) -> dict[str, Any]:
    is_positive = selection["client_net_pnl"] > 0
    is_negative = selection["client_net_pnl"] < 0
    direction = "positive" if is_positive else "negative" if is_negative else "neutral"
    direction_month_rate = selection["positive_month_rate"] if is_positive else selection["negative_month_rate"]
    direction_day_rate = selection["positive_day_rate"] if is_positive else selection["negative_day_rate"]
    direction_ci = selection["positive_day_rate_ci95"] if is_positive else selection["negative_day_rate_ci95"]
    concentration = selection["top_positive_day_concentration"] if is_positive else selection["top_negative_day_concentration"]
    confidence_tier = "low"
    if (
        selection["trade_count"] >= high_confidence_trades
        and selection["active_months"] >= 2
    ):
        confidence_tier = "high"
    elif (
        selection["trade_count"] >= min_trades
        and selection["active_months"] >= 2
    ):
        confidence_tier = "medium"

    flags: list[str] = []
    if selection["active_months"] < 2:
        flags.append("insufficient_months")
    if direction in {"positive", "negative"} and direction_month_rate < min_positive_month_rate:
        flags.append("monthly_inconsistency")
    if direction in {"positive", "negative"} and direction_ci[0] < min_direction_day_rate_lower_bound:
        flags.append("daily_confidence")
    if direction == "positive" and selection["win_rate"] < min_win_rate:
        flags.append("win_rate")
    if direction == "negative" and selection["win_rate"] > 1 - min_win_rate:
        flags.append("win_rate")
    if direction == "positive" and selection["payoff_ratio"] < min_payoff_ratio:
        flags.append("payoff_ratio")
    if direction == "negative" and selection["payoff_ratio"] > (1 / min_payoff_ratio if min_payoff_ratio else float("inf")):
        flags.append("payoff_ratio")
    if direction in {"positive", "negative"} and selection["monthly_consistency_ratio"] < min_selection_monthly_consistency:
        flags.append("monthly_consistency")
    if direction in {"positive", "negative"} and concentration >= max_top1_day_profit_contribution:
        flags.append("profit_concentration" if is_positive else "loss_concentration")
    if direction == "positive" and selection["max_daily_profit_month_contribution"] > max_daily_profit_month_contribution:
        flags.append("daily_profit_month_concentration")
    if (
        selection.get("risk_balance_status") == "positive"
        and not _leverage_filter_pass(
            selection,
            max_leverage_p95_ratio,
            max_high_leverage_holding_seconds,
        )
    ):
        flags.append("leverage_p95_ratio")
    if confidence_tier == "low":
        flags.append("insufficient_sample")
    if direction in {"positive", "negative"} and selection["max_drawdown"] > 0:
        return_drawdown = abs(selection["client_net_pnl"]) / selection["max_drawdown"]
        if return_drawdown < 1:
            flags.append("drawdown_efficiency")
    else:
        return_drawdown = 0.0

    score = 0.0
    if direction in {"positive", "negative"}:
        drawdown_component = min(1.0, return_drawdown) if selection["max_drawdown"] > 0 else 1.0
        score = (
            (20.0 if selection["trade_count"] >= min_trades else 0.0)
            + 25.0 * min(1.0, direction_month_rate)
            + 25.0 * min(1.0, direction_day_rate)
            + 15.0 * max(0.0, 1.0 - min(1.0, concentration))
            + 15.0 * drawdown_component
        )
    score = round(max(0.0, min(100.0, score)), 2)
    hard_flags = {
        "insufficient_months", "monthly_inconsistency", "daily_confidence",
        "profit_concentration", "loss_concentration", "daily_profit_month_concentration",
        "leverage_p95_ratio", "insufficient_sample", "drawdown_efficiency",
        "win_rate", "payoff_ratio", "monthly_consistency",
    }
    tier = "unqualified"
    if direction in {"positive", "negative"}:
        tier = "core" if score >= min_stability_score and not (set(flags) & hard_flags) else "watch"
    if not flags and direction in {"positive", "negative"}:
        flags.append("stable_direction")
    return {
        "score": score,
        "tier": tier,
        "direction": direction,
        "confidence_tier": confidence_tier,
        "flags": flags,
        "direction_month_rate": direction_month_rate,
        "direction_day_rate": direction_day_rate,
        "direction_day_rate_ci95": direction_ci,
        "top_positive_day_concentration": selection["top_positive_day_concentration"],
        "top_negative_day_concentration": selection["top_negative_day_concentration"],
        "return_drawdown_ratio": selection["return_drawdown_ratio"],
    }


def _leverage_filter_pass(
    selection: dict[str, Any],
    max_leverage_p95_ratio: float,
    max_high_leverage_holding_seconds: float | None = None,
) -> bool:
    """Apply leverage only with positive balance; allow short-hold exceptions."""
    if selection.get("risk_balance_status") != "positive":
        return True
    leverage_p95 = selection.get("risk_leverage_p95_ratio")
    if leverage_p95 is None:
        return True
    if leverage_p95 <= max_leverage_p95_ratio:
        return True
    holding = selection.get("median_holding_seconds")
    return (
        max_high_leverage_holding_seconds is not None
        and max_high_leverage_holding_seconds > 0
        and holding is not None
        and holding <= max_high_leverage_holding_seconds
    )


def _correlation(pairs: list[tuple[Decimal, Decimal]]) -> float:
    if len(pairs) < 2:
        return 0.0
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    x_mean = sum(xs, ZERO) / Decimal(len(xs))
    y_mean = sum(ys, ZERO) / Decimal(len(ys))
    numerator = sum(((x - x_mean) * (y - y_mean) for x, y in pairs), ZERO)
    x_var = sum(((x - x_mean) ** 2 for x in xs), ZERO)
    y_var = sum(((y - y_mean) ** 2 for y in ys), ZERO)
    denominator = Decimal(str(math.sqrt(max(0.0, float(x_var * y_var)))))
    return _ratio(numerator, denominator)


def classify_accounts(
    context: AnalysisContext,
    rules: Any,
    personal_candidate_logins: set[int] | None = None,
    martingale_snapshot: Any | None = None,
    avg_profit_snapshot_status: str = "not_loaded",
    news_candidate_logins: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Classify a materialized context without issuing another data query."""
    payload = build_two_stage_payload(
        context.rows,
        overview_rows=context.overview_rows,
        daily_rows=context.daily_rows,
        overview_daily_rows=context.overview_daily_rows,
        selection_start=context.selection_start,
        selection_end=context.selection_end,
        validation_start=context.validation_start,
        validation_end=context.validation_end,
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
        min_direction_day_rate_lower_bound=rules.min_direction_day_rate_lower_bound,
        min_stability_score=rules.min_stability_score,
        high_confidence_trades=rules.high_confidence_trades,
        high_confidence_days=rules.high_confidence_days,
        personal_candidate_logins=personal_candidate_logins,
        news_candidate_logins=news_candidate_logins,
        martingale_snapshot=martingale_snapshot,
        excluded_martingale_levels=rules.excluded_martingale_levels,
        avg_profit_snapshot_status=avg_profit_snapshot_status,
        require_selection_monthly_positive=rules.require_selection_monthly_positive,
        enable_r4=rules.enable_r4,
        r4_min_passing_weeks=rules.r4_min_passing_weeks,
    )
    return payload["accounts"]


def _group_summary(accounts: list[dict[str, Any]], target: Optional[str] = None) -> dict[str, Any]:
    total = len(accounts)
    active = [account for account in accounts if account["validation"]["trade_count"] > 0]
    validation_values = [Decimal(str(account["validation"]["client_net_pnl"])) for account in accounts]
    positive = sum(1 for value in validation_values if value > NEUTRAL_BAND_USD)
    negative = sum(1 for value in validation_values if value < -NEUTRAL_BAND_USD)
    strict_positive = sum(1 for value in validation_values if value > ZERO)
    total_trades = sum(account["validation"]["trade_count"] for account in accounts)
    total_wins = sum(account["validation"]["winning_trades"] for account in accounts)
    total_days = sum(account["validation"]["active_trade_days"] for account in accounts)
    total_positive_days = sum(account["validation"]["positive_profit_days"] for account in accounts)
    gross_wins = sum((Decimal(str(account["validation"]["gross_wins"])) for account in accounts), ZERO)
    gross_losses = sum((Decimal(str(account["validation"]["gross_losses"])) for account in accounts), ZERO)
    client_net_pnl = sum(validation_values, ZERO)
    market_pnl = sum((Decimal(str(account["validation"]["market_pnl"])) for account in accounts), ZERO)
    costs = sum((Decimal(str(account["validation"]["costs"])) for account in accounts), ZERO)
    active_days = sum(account["validation"]["active_trade_days"] for account in accounts)
    target_successes = 0
    if target == "positive":
        target_successes = positive
    elif target == "negative":
        target_successes = negative
    target_total = len(active) if target else 0
    control_success_rate = 0.0
    return {
        "accounts": total,
        "active_accounts": len(active),
        "active_rate": _safe_ratio(Decimal(len(active)), Decimal(total)),
        "client_net_pnl": float(client_net_pnl),
        "customer_net_pnl": float(client_net_pnl),
        "bbook_company_profit": float(-client_net_pnl),
        "market_pnl": float(market_pnl),
        "theoretical_mirror_pnl": float(-market_pnl),
        "costs": float(costs),
        "average_net_pnl": float(_safe_ratio(client_net_pnl, Decimal(total))),
        "median_net_pnl": float(_median(validation_values)),
        "positive_accounts": positive,
        "negative_accounts": negative,
        "positive_account_rate": _safe_ratio(Decimal(positive), Decimal(total)),
        "negative_account_rate": _safe_ratio(Decimal(negative), Decimal(total)),
        "strict_positive_accounts": strict_positive,
        "strict_positive_account_rate": _safe_ratio(Decimal(strict_positive), Decimal(total)),
        "active_positive_account_rate": _safe_ratio(Decimal(positive), Decimal(len(active))),
        "active_negative_account_rate": _safe_ratio(Decimal(negative), Decimal(len(active))),
        "matched_trades": total_trades,
        "win_rate": _safe_ratio(Decimal(total_wins), Decimal(total_trades)),
        "active_trade_days": active_days,
        "average_daily_profit": _safe_ratio(
            client_net_pnl,
            Decimal(active_days),
        ),
        "profitable_day_rate": _safe_ratio(
            Decimal(total_positive_days),
            Decimal(sum(
                account["validation"]["positive_profit_days"]
                + account["validation"]["negative_profit_days"]
                + account["validation"]["flat_profit_days"]
                for account in accounts
            )),
        ),
        "profit_factor": _profit_factor(gross_wins, gross_losses),
        "total_volume": sum(account["validation"]["total_volume"] for account in accounts),
        # Account ordering is not a time series. The group drawdown is filled
        # from account-day rows by build_two_stage_payload below.
        "max_drawdown": 0.0,
        "target_successes": target_successes,
        "target_total": target_total,
        "precision": _safe_ratio(Decimal(target_successes), Decimal(target_total)) if target else 0.0,
        "precision_ci95": _wilson_interval(target_successes, target_total) if target else [0.0, 0.0],
        "recall": 0.0,
        "lift": 0.0,
        "sample_warning": len(active) < 30,
    }


def _cumulative_series(values: Iterable[Decimal]) -> list[Decimal]:
    running = ZERO
    result = []
    for value in values:
        running += _finite_decimal(value)
        result.append(running)
    return result


def _daily_group_drawdown(
    accounts: list[dict[str, Any]],
    daily_rows: Iterable[dict[str, Any]],
    validation_start: str,
    validation_end: str,
) -> Decimal:
    keys = {(str(account["platform"]), int(account["login"])) for account in accounts}
    start = date.fromisoformat(validation_start)
    end = date.fromisoformat(validation_end)
    by_day: defaultdict[date, Decimal] = defaultdict(lambda: ZERO)
    for row in daily_rows:
        key = (str(row.get("platform")), int(row.get("login", 0)))
        if key not in keys:
            continue
        value = row.get("trade_date")
        row_day = value.date() if isinstance(value, datetime) else value
        if isinstance(row_day, str):
            row_day = date.fromisoformat(row_day[:10])
        if row_day is not None and start <= row_day <= end:
            by_day[row_day] += _finite_decimal(row.get("client_net_pnl"))
    return calculate_drawdown(_cumulative_series(by_day[day] for day in sorted(by_day)))


_BOOK_REASON_LABELS = {
    "insufficient_months": "样本月份不足",
    "monthly_inconsistency": "月度表现不一致",
    "daily_confidence": "日度稳定性不足",
    "win_rate": "胜率未达标",
    "payoff_ratio": "盈亏比未达标",
    "monthly_consistency": "月度一致性未达标",
    "profit_concentration": "盈利集中度过高",
    "loss_concentration": "亏损集中度异常",
    "daily_profit_month_concentration": "盈利月份集中度过高",
    "leverage_p95_ratio": "杠杆 P95 超限",
    "insufficient_sample": "交易样本不足",
    "drawdown_efficiency": "收益/回撤效率不足",
}


def _bbook_reason_tags(account: dict[str, Any]) -> list[str]:
    """Return readable, deterministic reasons for a Bbook routing decision."""
    tags: list[str] = []
    source = str(account.get("selection_source") or "")
    detection_status = str(account.get("martingale_detection_status") or "none")
    risk_level = str(account.get("martingale_risk_level") or "").strip()
    snapshot_status = str(account.get("martingale_status") or "")

    if source == "martingale_snapshot_unavailable" or snapshot_status in {"missing", "invalid", "stale"}:
        tags.append("马丁快照不可用，安全阻断")
    elif account.get("martingale_hard_block") or (
        account.get("martingale_blocked") and detection_status == "confirmed"
    ):
        tags.append(f"确认马丁{f' · {risk_level}' if risk_level else ''}")
    elif account.get("martingale_blocked"):
        tags.append(f"马丁阻断{f' · {risk_level}' if risk_level else ''}")

    if account.get("abook_rules_pass") is False or source == "abook_rules_failed":
        tags.append("Abook规则未通过")

    for flag in account.get("selection_flags", []) or []:
        label = _BOOK_REASON_LABELS.get(str(flag))
        if label and label not in tags:
            tags.append(label)

    if not tags:
        tags.append("Bbook（未通过 Abook 路由）")
    return tags


def build_misjudge_summary(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    abook_losses = []
    bbook_profitable = []
    for account in accounts:
        validation_pnl = float(account.get("validation", {}).get("client_net_pnl", account.get("validation_client_net_pnl", 0.0)))
        june_pnl = next(
            (
                float(month.get("client_net_pnl", 0.0) or 0.0)
                for month in account.get("monthly", [])
                if str(month.get("month")) == "2026-06"
            ),
            0.0,
        )
        may_pnl = next(
            (
                float(month.get("client_net_pnl", 0.0) or 0.0)
                for month in account.get("monthly", [])
                if str(month.get("month")) == "2026-05"
            ),
            0.0,
        )
        item = {
            "platform": account["platform"],
            "login": int(account["login"]),
            "account_group": account.get("account_group", ""),
            "selection_client_net_pnl": float(account.get("selection", {}).get("client_net_pnl", account.get("selection_client_net_pnl", 0.0))),
            "may_client_net_pnl": may_pnl,
            "june_client_net_pnl": june_pnl,
            "validation_client_net_pnl": validation_pnl,
            "selection_source": account.get("selection_source", ""),
            "selection_flags": list(account.get("selection_flags", [])),
            "martingale_risk_level": account.get("martingale_risk_level"),
            "bbook_reason_tags": list(account.get("bbook_reason_tags") or _bbook_reason_tags(account)),
        }
        if account.get("book") == "abook" and validation_pnl < 0:
            abook_losses.append({
                **item,
                "loss_amount": round(-validation_pnl, 6),
                "company_increment_if_routed": round(validation_pnl, 6),
            })
        elif account.get("book") == "bbook" and validation_pnl > 0:
            bbook_profitable.append({
                **item,
                "profit_amount": round(validation_pnl, 6),
                "company_loss_if_left_bbook": round(-validation_pnl, 6),
            })
    abook_losses.sort(key=lambda row: row["loss_amount"], reverse=True)
    bbook_profitable.sort(key=lambda row: row["profit_amount"], reverse=True)
    return {
        "abook_losses": abook_losses,
        "abook_loss_total": round(sum(row["loss_amount"] for row in abook_losses), 6),
        "bbook_profitable": bbook_profitable,
        "bbook_profitable_total": round(sum(row["profit_amount"] for row in bbook_profitable), 6),
        "bbook_company_loss_total": round(-sum(row["profit_amount"] for row in bbook_profitable), 6),
    }


def build_selection_funnel(
    accounts: list[dict[str, Any]],
    eligible_accounts: int | None = None,
    *,
    min_trades: int | None = None,
    min_active_days: int | None = None,
) -> dict[str, Any]:
    all_accounts = list(accounts)
    has_explicit_sample_rule = min_trades is not None
    required_trades = int(min_trades or 0)
    stages = [
        ("eligible", lambda account: True, "not_in_query_population"),
        (
            "sample_qualified",
            (
                lambda account: (
                    account.get("selection", {}).get("trade_count", 0) >= required_trades
                )
                if has_explicit_sample_rule
                else (
                    account.get("selection", {}).get("trade_count", 0) > 0
                )
            ),
            "insufficient_sample",
        ),
        ("leverage_passed", lambda account: "leverage_p95_ratio" not in account.get("selection_flags", []), "leverage_p95_ratio"),
        ("non_martingale", lambda account: not account.get("martingale_blocked", False), "martingale_blocked"),
        ("abook", lambda account: account.get("book") == "abook", "abook_rules_failed"),
    ]
    prior = all_accounts
    output = []
    for index, (name, predicate, reason) in enumerate(stages):
        if index:
            members = [account for account in prior if predicate(account)]
            dropped_count = len(prior) - len(members)
            dropped = {reason: dropped_count} if dropped_count else {}
        else:
            members = all_accounts
            dropped = {}
        output.append({"name": name, "count": len(members) if index else (eligible_accounts if eligible_accounts is not None else len(members)), "drop_reasons": dropped})
        prior = members
    return {"stages": output, "definition": "Each stage is evaluated against the accounts surviving the previous stage."}


def _status(value: float, trade_count: int) -> str:
    if trade_count <= 0:
        return "inactive"
    if value > float(NEUTRAL_BAND_USD):
        return "profitable"
    if value < -float(NEUTRAL_BAND_USD):
        return "loss"
    return "neutral"


def _transition(book: str, validation_status: str) -> str:
    if book == "abook":
        return {
            "profitable": "continued_profitable",
            "loss": "started_loss",
            "neutral": "neutral",
            "inactive": "inactive",
        }[validation_status]
    if book == "bbook":
        return {
            "loss": "continued_loss",
            "profitable": "turned_profit",
            "neutral": "neutral",
            "inactive": "inactive",
        }[validation_status]
    return validation_status


def _phase_summary(accounts: list[dict[str, Any]], phase: str) -> dict[str, Any]:
    phase_accounts = [account for account in accounts]
    values = [Decimal(str(account[phase]["client_net_pnl"])) for account in phase_accounts]
    total = len(phase_accounts)
    active = sum(1 for account in phase_accounts if account[phase]["trade_count"] > 0)
    trades = sum(account[phase]["trade_count"] for account in phase_accounts)
    wins = sum(account[phase]["winning_trades"] for account in phase_accounts)
    gross_wins = sum((Decimal(str(account[phase]["gross_wins"])) for account in phase_accounts), ZERO)
    gross_losses = sum((Decimal(str(account[phase]["gross_losses"])) for account in phase_accounts), ZERO)
    return {
        "accounts": total,
        "active_accounts": active,
        "active_rate": _safe_ratio(Decimal(active), Decimal(total)),
        "client_net_pnl": float(sum(values, ZERO)),
        "market_pnl": float(sum((Decimal(str(account[phase]["market_pnl"])) for account in phase_accounts), ZERO)),
        "theoretical_mirror_pnl": float(-sum((Decimal(str(account[phase]["market_pnl"])) for account in phase_accounts), ZERO)),
        "costs": float(sum((Decimal(str(account[phase]["costs"])) for account in phase_accounts), ZERO)),
        "matched_trades": trades,
        "win_rate": _safe_ratio(Decimal(wins), Decimal(trades)),
        "profit_factor": _profit_factor(gross_wins, gross_losses),
    }


def _book_split_summary(accounts: list[dict[str, Any]], *, abook: bool) -> dict[str, Any]:
    user_net = sum((Decimal(str(account["validation"]["client_net_pnl"])) for account in accounts), ZERO)
    market = sum((Decimal(str(account["validation"]["market_pnl"])) for account in accounts), ZERO)
    active = sum(1 for account in accounts if account["validation"]["trade_count"] > 0)
    return {
        "accounts": len(accounts),
        "active_accounts": active,
        "user_net_pnl": float(user_net),
        "user_market_pnl": float(market),
        "theoretical_bbook_profit": float(-user_net),
        "company_profit": 0.0 if abook else float(-user_net),
        "assumption": "abook_profit_not_available" if abook else "book_counterparty_profit",
    }


def _book_performance(accounts: list[dict[str, Any]], phase: str, book: str) -> dict[str, Any]:
    values = [_finite_decimal(account[phase].get("client_net_pnl")) for account in accounts]
    neutral = NEUTRAL_BAND_USD
    profitable = [value for value in values if value > neutral]
    losses = [value for value in values if value < -neutral]
    neutral_values = [value for value in values if -neutral <= value <= neutral]
    net_pnl = sum(values, ZERO)
    current_bbook_profit = -net_pnl
    assumed_abook_profit = ZERO
    company_profit_if_current_book = ZERO if book == "abook" else current_bbook_profit
    return {
        "accounts": len(values),
        "profitable_accounts": len(profitable),
        "loss_accounts": len(losses),
        "neutral_accounts": len(neutral_values),
        "profitable_account_rate": _safe_ratio(Decimal(len(profitable)), Decimal(len(values))),
        "loss_account_rate": _safe_ratio(Decimal(len(losses)), Decimal(len(values))),
        "gross_profit": float(sum((_finite_decimal(account[phase].get("gross_wins")) for account in accounts), ZERO)),
        "gross_loss": float(sum((_finite_decimal(account[phase].get("gross_losses")) for account in accounts), ZERO)),
        "net_pnl": float(net_pnl),
        "customer_net_pnl": float(net_pnl),
        "current_bbook_profit": float(current_bbook_profit),
        "assumed_abook_profit": float(assumed_abook_profit),
        "theoretical_increment": float(assumed_abook_profit - current_bbook_profit),
        "company_profit_if_current_book": float(company_profit_if_current_book),
        "company_profit_if_bbook": float(current_bbook_profit),
        "theoretical_company_increment_if_routed_abook": float(net_pnl),
    }


def _daily_book_series(
    daily_rows: Iterable[dict[str, Any]],
    overview_daily_rows: Iterable[dict[str, Any]] | None,
    accounts: list[dict[str, Any]],
    selection_start: str,
    selection_end: str,
    validation_start: str,
    validation_end: str,
) -> list[dict[str, Any]]:
    """Aggregate account-day P&L into Abook and display Bbook series."""
    selection_start_date = date.fromisoformat(selection_start)
    selection_end_date = date.fromisoformat(selection_end)
    validation_start_date = date.fromisoformat(validation_start)
    validation_end_date = date.fromisoformat(validation_end)
    start_date = min(selection_start_date, validation_start_date)
    end_date = max(selection_end_date, validation_end_date)

    def row_date(row: dict[str, Any]) -> date:
        value = row.get("trade_date")
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value)[:10])

    account_books = {
        (str(account["platform"]), int(account["login"])):
            account["book"]
        for account in accounts
    }
    effective_rows = list(daily_rows or [])
    population_rows = list(overview_daily_rows) if overview_daily_rows is not None else effective_rows
    company_by_day: defaultdict[date, Decimal] = defaultdict(lambda: ZERO)
    for row in population_rows:
        day = row_date(row)
        if start_date <= day <= end_date:
            company_by_day[day] += _finite_decimal(row.get("client_net_pnl"))

    books_by_day: defaultdict[date, dict[str, dict[str, Decimal]]] = defaultdict(
        lambda: {
            "abook": {"net_pnl": ZERO, "profitable_pnl": ZERO, "loss_pnl": ZERO},
            "bbook": {"net_pnl": ZERO, "profitable_pnl": ZERO, "loss_pnl": ZERO},
        }
    )
    for row in effective_rows:
        day = row_date(row)
        book_name = account_books.get((str(row.get("platform")), int(row.get("login", 0))))
        if book_name is None or not start_date <= day <= end_date:
            continue
        value = _finite_decimal(row.get("client_net_pnl"))
        book = books_by_day[day][book_name]
        book["net_pnl"] += value
        if value > ZERO:
            book["profitable_pnl"] += value
        elif value < ZERO:
            book["loss_pnl"] += value

    series = []
    current = start_date
    while current <= end_date:
        if selection_start_date <= current <= selection_end_date:
            phase = "selection"
        elif validation_start_date <= current <= validation_end_date:
            phase = "validation"
        else:
            current += timedelta(days=1)
            continue
        books = books_by_day[current]
        series.append({
            "date": current.isoformat(),
            "phase": phase,
            "company_net_pnl": _float(-company_by_day[current]),
            "abook": {key: _float(value) for key, value in books["abook"].items()},
            "bbook": {key: _float(value) for key, value in books["bbook"].items()},
        })
        current += timedelta(days=1)
    return series


def build_two_stage_payload(
    rows: Iterable[dict[str, Any]],
    *,
    overview_rows: Iterable[dict[str, Any]] | None = None,
    daily_rows: Iterable[dict[str, Any]] | None = None,
    overview_daily_rows: Iterable[dict[str, Any]] | None = None,
    selection_start: str,
    selection_end: str,
    validation_start: str,
    validation_end: str,
    min_trades: int = 75,
    min_active_days: int = 0,
    min_win_rate: float = 0.5,
    min_profit_factor: float = 1.25,
    min_payoff_ratio: float = 0.4,
    min_avg_daily_profit: float = 0.0,
    min_avg_profit: float = 0.0,
    min_selection_monthly_consistency: float = 0.0,
    min_positive_month_rate: float = 0.5,
    max_top1_day_profit_contribution: float = 0.3,
    max_daily_profit_month_contribution: float = 1.0,
    max_leverage_p95_ratio: float = 5000.0,
    max_peak_leverage_ratio: float | None = None,
    max_high_leverage_holding_seconds: float = 300.0,
    risk_snapshot_status: str = "not_loaded",
    min_direction_day_rate_lower_bound: float = 0.55,
    min_stability_score: float = 70.0,
    high_confidence_trades: int = 100,
    high_confidence_days: int = 30,
    personal_candidate_logins: set[int] | None = None,
    personal_candidate_info: dict[str, Any] | None = None,
    news_candidate_logins: set[int] | None = None,
    news_candidate_info: dict[str, Any] | None = None,
    martingale_snapshot: Any | None = None,
    excluded_martingale_levels: Iterable[str] = ("extreme", "high", "medium", "low"),
    avg_profit_snapshot_status: str = "not_loaded",
    require_selection_monthly_positive: bool = False,
    enable_r4: bool = False,
    r4_min_passing_weeks: int = 1,
) -> dict[str, Any]:
    """Build final Abook/Bbook routing and an independent validation-period readout."""
    if max_peak_leverage_ratio is not None and max_leverage_p95_ratio == 5000.0:
        max_leverage_p95_ratio = max_peak_leverage_ratio
    materialized = list(rows)
    # This is a hard safety boundary. The query already applies the same
    # predicates, and the service repeats them for injected/test rows.
    materialized = [
        row for row in materialized
        if "test" not in str(row.get("account_group", "")).lower()
        and "demo" not in str(row.get("account_group", "")).lower()
    ]
    personal_logins = {int(login) for login in (personal_candidate_logins or set())}
    personal_info = dict(personal_candidate_info or {"enabled": False, "status": "disabled"})
    news_logins = {int(login) for login in (news_candidate_logins or set())}
    news_info = dict(news_candidate_info or {"enabled": False, "status": "disabled"})
    overview_materialized = list(overview_rows) if overview_rows is not None else list(materialized)
    overview_materialized = [
        row for row in overview_materialized
        if "test" not in str(row.get("account_group", "")).lower()
        and "demo" not in str(row.get("account_group", "")).lower()
    ]
    excluded_martingale_levels = tuple(excluded_martingale_levels)
    excluded_martingale_level_set = set(excluded_martingale_levels)
    martingale_indexed = (
        martingale_snapshot.indexed_records()
        if martingale_snapshot is not None
        and getattr(martingale_snapshot, "status", "") in {"ready", "partial"}
        else {}
    )
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in materialized:
        grouped[(row["platform"], int(row["login"]))].append(row)

    selection_months = _period_months(selection_start, selection_end)
    validation_months = _period_months(validation_start, validation_end)
    all_months = selection_months + [month for month in validation_months if month not in selection_months]
    accounts = []
    for key, account_rows in grouped.items():
        first = account_rows[0]
        identity = {
            "platform": first["platform"],
            "login": int(first["login"]),
            "account_group": first.get("account_group", ""),
        }
        selection_rows = _rows_for_phase(account_rows, "selection", selection_months)
        validation_rows = _rows_for_phase(account_rows, "validation", validation_months)
        selection_rows = selection_rows or [_empty_period_row(identity, month) for month in selection_months]
        validation_rows = validation_rows or [_empty_period_row(identity, month) for month in validation_months]
        selection = _account_period_metrics(selection_rows, identity, phase="selection")
        validation = _account_period_metrics(validation_rows, identity, phase="validation")
        monthly_metrics = []
        for month in all_months:
            phase = "selection" if month in selection_months else "validation"
            month_rows = [
                row for row in account_rows
                if _month(row["month_start"]) == month
                and (row.get("phase") not in {"selection", "validation"} or row.get("phase") == phase)
            ]
            month_rows = month_rows or [_empty_period_row(identity, month)]
            monthly_metrics.append({
                "month": month,
                "phase": phase,
                **_account_period_metrics(month_rows, identity, phase=phase),
            })
        selection_month_pnls = [
            _decimal(item["client_net_pnl"])
            for item in monthly_metrics
            if item["phase"] == "selection"
        ]
        selection_months_positive = bool(selection_month_pnls) and all(
            value > ZERO for value in selection_month_pnls
        )
        qualifies_sample = (
            selection["trade_count"] >= min_trades
        )
        normal_abook_rules_pass = qualifies_sample and (
            selection["profit_factor"] is None or selection["profit_factor"] > min_profit_factor
        ) and selection["win_rate"] >= min_win_rate \
            and selection["payoff_ratio"] >= min_payoff_ratio \
            and selection["top_positive_day_concentration"] < max_top1_day_profit_contribution \
            and _leverage_filter_pass(
                selection, max_leverage_p95_ratio, max_high_leverage_holding_seconds
            )
        stability = _stability_assessment(
            selection,
            min_trades=min_trades,
            min_active_days=min_active_days,
            min_win_rate=min_win_rate,
            min_profit_factor=min_profit_factor,
            min_payoff_ratio=min_payoff_ratio,
            min_positive_month_rate=min_positive_month_rate,
            min_selection_monthly_consistency=min_selection_monthly_consistency,
            max_top1_day_profit_contribution=max_top1_day_profit_contribution,
            max_daily_profit_month_contribution=max_daily_profit_month_contribution,
            max_leverage_p95_ratio=max_leverage_p95_ratio,
            max_high_leverage_holding_seconds=max_high_leverage_holding_seconds,
            min_direction_day_rate_lower_bound=min_direction_day_rate_lower_bound,
            min_stability_score=min_stability_score,
            high_confidence_trades=high_confidence_trades,
            high_confidence_days=high_confidence_days,
        )
        # Day-distribution and stability metrics remain diagnostic only. They no
        # longer gate Abook routing under the revised policy.
        r4_pass = bool(
            enable_r4
            and first.get("r4_pass", False)
            and int(first.get("r4_passing_weeks", 0) or 0) >= r4_min_passing_weeks
            and _leverage_filter_pass(selection, max_leverage_p95_ratio, max_high_leverage_holding_seconds)
        )
        abook_rules_pass = bool(normal_abook_rules_pass or r4_pass)
        is_personal_candidate = int(identity["login"]) in personal_logins
        is_news_candidate = int(identity["login"]) in news_logins
        martingale_record = None
        if martingale_snapshot is not None and getattr(martingale_snapshot, "status", "") in {"ready", "partial"}:
            martingale_record = martingale_indexed.get(
                (str(identity["platform"]), int(identity["login"]))
            )
        martingale_level = martingale_record.get("risk_level") if martingale_record else None
        martingale_detection_status = (
            martingale_record.get("martingale_detection_status", "none")
            if martingale_record else "none"
        )
        martingale_status = getattr(martingale_snapshot, "status", "") if martingale_snapshot is not None else ""
        missing_platforms = set(getattr(martingale_snapshot, "missing_platforms", ())) if martingale_snapshot is not None else set()
        snapshot_unavailable = martingale_status in {"missing", "invalid", "stale"} or (
            martingale_status == "partial" and str(identity["platform"]) in missing_platforms
        )
        martingale_hard_block = (
            martingale_detection_status == "confirmed"
            and martingale_level in excluded_martingale_level_set
        )
        martingale_blocked = snapshot_unavailable or martingale_hard_block
        book = _final_book(
            martingale_detected=martingale_blocked,
            personal_candidate=is_personal_candidate or is_news_candidate,
            abook_rules_pass=abook_rules_pass,
        )
        if snapshot_unavailable:
            selection_source = "martingale_snapshot_unavailable"
        elif martingale_blocked:
            selection_source = "martingale_blocked"
        elif r4_pass and is_personal_candidate and is_news_candidate:
            selection_source = "r4_and_personal_and_news_list"
        elif r4_pass and is_personal_candidate:
            selection_source = "r4_and_personal_list"
        elif r4_pass and is_news_candidate:
            selection_source = "r4_and_news_list"
        elif r4_pass:
            selection_source = "r4"
        elif is_personal_candidate and is_news_candidate:
            selection_source = (
                "rule_and_personal_and_news_list"
                if abook_rules_pass
                else "personal_and_news_candidate_list"
            )
        elif is_personal_candidate:
            selection_source = (
                "rule_and_personal_list"
                if abook_rules_pass
                else "personal_candidate_list"
            )
        elif is_news_candidate:
            selection_source = "rule_and_news_list" if abook_rules_pass else "news_candidate_list"
        elif abook_rules_pass:
            selection_source = "rule_filter"
        else:
            selection_source = "abook_rules_failed"
        routing_reason = (
            "martingale_snapshot_unavailable" if snapshot_unavailable
            else "martingale" if martingale_blocked
            else "r4" if r4_pass
            else "personal_list" if is_personal_candidate
            else "abook_rules" if abook_rules_pass
            else "abook_rules_failed"
        )
        validation_status = _status(
            validation["client_net_pnl"], validation["trade_count"]
        )
        has_phase_rows = any(row.get("phase") in {"selection", "validation"} for row in account_rows)
        has_nonzero_pnl = any(
            abs(_decimal(row.get("client_net_pnl"))) > ZERO
            for row in account_rows
            if (has_phase_rows and row.get("phase") in {"selection", "validation"})
            or (not has_phase_rows and _month(row["month_start"]) in all_months)
        )
        account = {
            **identity,
            "book": book,
            "deployable": True,
            "is_personal_candidate": is_personal_candidate,
            "is_news_candidate": is_news_candidate,
            "selection_source": selection_source,
            "routing_reason": routing_reason,
            "abook_rules_pass": abook_rules_pass,
            "r4_pass": r4_pass,
            "r4_passing_weeks": int(first.get("r4_passing_weeks", 0) or 0),
            "r4_record": dict(first.get("r4_record") or {}),
            "july_new_user": selection["trade_count"] == 0 and validation["trade_count"] > 0,
            "martingale_blocked": martingale_blocked,
            "martingale_hard_block": martingale_hard_block,
            "martingale_status": getattr(martingale_snapshot, "status", "not_loaded") if martingale_snapshot is not None else "not_loaded",
            "martingale_risk_level": martingale_level,
            "martingale_detection_status": martingale_detection_status,
            "confirmed_windows": int(martingale_record.get("confirmed_windows", 0) or 0) if martingale_record else 0,
            "confirmed_extreme_windows": int(martingale_record.get("confirmed_extreme_windows", 0) or 0) if martingale_record else 0,
            "expanded_windows": int(martingale_record.get("expanded_windows", 0) or 0) if martingale_record else 0,
            "martingale_layer_hits": dict(martingale_record.get("layer_hits", {})) if martingale_record else {},
            "martingale_record": dict(martingale_record) if martingale_record else None,
            "validation_status": validation_status,
            "transition_status": _transition(book, validation_status),
            "selection": selection,
            "validation": validation,
            "monthly": monthly_metrics,
            "avg_profit": selection.get("avg_profit"),
            "avg_profit_status": selection.get("avg_profit_status", avg_profit_snapshot_status),
            "stability": {
                "score": stability["score"],
                "tier": stability["tier"],
                "top_positive_day_concentration": selection["top_positive_day_concentration"],
                "top_negative_day_concentration": selection["top_negative_day_concentration"],
                "monthly_pnl_stddev": selection["monthly_pnl_stddev"],
                "monthly_consistency_ratio": selection["monthly_consistency_ratio"],
                "positive_day_rate": selection["positive_day_rate"],
                "negative_day_rate": selection["negative_day_rate"],
                "positive_day_rate_ci95": selection["positive_day_rate_ci95"],
                "negative_day_rate_ci95": selection["negative_day_rate_ci95"],
                "expectancy_per_trade": selection["expectancy_per_trade"],
                "payoff_ratio": selection["payoff_ratio"],
                "return_drawdown_ratio": selection["return_drawdown_ratio"],
            },
            "confidence_tier": stability["confidence_tier"],
            "selection_flags": stability["flags"],
            "selection_direction": stability["direction"],
            "selection_months_positive": selection_months_positive,
            "selection_client_net_pnl": selection["client_net_pnl"],
            "validation_client_net_pnl": validation["client_net_pnl"],
            "risk_balance_prev_month": selection["risk_balance_prev_month"],
            "risk_average_open_degree": selection["risk_average_open_degree"],
            "risk_peak_leverage_ratio": selection["risk_peak_leverage_ratio"],
            "risk_leverage_p95_ratio": selection["risk_leverage_p95_ratio"],
            "risk_balance_status": selection["risk_balance_status"],
            "client_net_pnl": selection["client_net_pnl"],
            "market_pnl": selection["market_pnl"],
            "theoretical_mirror_pnl": -selection["market_pnl"],
            "selection_trade_count": selection["trade_count"],
            "validation_trade_count": validation["trade_count"],
            "average_daily_profit": selection["average_daily_profit"],
            "max_daily_profit_month_contribution": selection["max_daily_profit_month_contribution"],
            "has_nonzero_pnl": has_nonzero_pnl,
        }
        account["bbook_reason_tags"] = _bbook_reason_tags(account) if book == "bbook" else []
        accounts.append(account)

    accounts.sort(key=lambda account: (account["selection_client_net_pnl"], account["platform"], account["login"]), reverse=True)
    by_book = {
        book: [account for account in accounts if account["book"] == book]
        for book in ("abook", "bbook")
    }
    daily_book_series = _daily_book_series(
        daily_rows or [],
        overview_daily_rows,
        accounts,
        selection_start,
        selection_end,
        validation_start,
        validation_end,
    )
    population_counts = [
        int(row["population_unique_accounts"])
        for row in overview_materialized
        if row.get("population_unique_accounts") is not None
    ]
    unique_accounts = max(population_counts) if population_counts else len({
        (row["platform"], int(row["login"])) for row in overview_materialized
    })
    selection_counts = {
        "eligible_accounts": unique_accounts,
        "unique_accounts": unique_accounts,
        "account_rows": len(accounts),
        "abook": len(by_book["abook"]),
        "bbook": len(by_book["bbook"]),
        "martingale_bbook": sum(1 for account in by_book["bbook"] if account["martingale_blocked"]),
        "personal_abook": sum(1 for account in by_book["abook"] if account["is_personal_candidate"]),
        "personal_bbook_martingale": sum(1 for account in by_book["bbook"] if account["is_personal_candidate"] and account["martingale_blocked"]),
    }
    if news_info.get("enabled"):
        selection_counts.update({
            "news_abook": sum(1 for account in by_book["abook"] if account["is_news_candidate"]),
            "news_bbook_martingale": sum(1 for account in by_book["bbook"] if account["is_news_candidate"] and account["martingale_blocked"]),
        })
    personal_matched = [account for account in accounts if account["is_personal_candidate"]]
    personal_info.update({
        "matched_accounts": len(personal_matched),
        "added_accounts": sum(1 for account in personal_matched if account["book"] == "abook" and not account["abook_rules_pass"] and not account["is_news_candidate"]),
        "overlap_accounts": sum(1 for account in personal_matched if account["book"] == "abook" and (account["abook_rules_pass"] or account["is_news_candidate"])),
    })
    news_matched = [account for account in accounts if account["is_news_candidate"]]
    news_info.update({
        "matched_accounts": len(news_matched),
        "added_accounts": sum(1 for account in news_matched if account["book"] == "abook" and not account["abook_rules_pass"] and not account["is_personal_candidate"]),
        "overlap_accounts": sum(1 for account in news_matched if account["book"] == "abook" and (account["abook_rules_pass"] or account["is_personal_candidate"])),
    })
    stability_overview = {
        "abook": len(by_book["abook"]),
        "bbook": len(by_book["bbook"]),
        "abook_core": sum(1 for account in by_book["abook"] if account["stability"]["tier"] == "core"),
        "bbook_martingale": sum(1 for account in by_book["bbook"] if account["martingale_blocked"]),
        "low_confidence": sum(1 for account in accounts if account["confidence_tier"] == "low"),
    }
    stability_overview["abook_core_or_watch"] = stability_overview["abook_core"]
    validation_groups = {
        book: _group_summary(book_accounts, "positive" if book == "abook" else "negative")
        for book, book_accounts in by_book.items()
    }
    active_validation = [account for account in accounts if account["validation"]["trade_count"] > 0]
    for book in ("abook", "bbook"):
        target = "positive" if book == "abook" else "negative"
        target_count = sum(
            1 for account in active_validation
            if (account["validation_status"] == "profitable" if target == "positive" else account["validation_status"] == "loss")
        )
        summary = validation_groups[book]
        summary["recall"] = _safe_ratio(Decimal(summary["target_successes"]), Decimal(target_count))
        control_accounts = [account for account in by_book["bbook" if book == "abook" else "abook"] if account["validation"]["trade_count"] > 0]
        control_successes = sum(
            1 for account in control_accounts
            if account["validation_status"] == ("profitable" if target == "positive" else "loss")
        )
        control_rate = _safe_ratio(Decimal(control_successes), Decimal(len(control_accounts)))
        summary["lift"] = _safe_ratio(Decimal(summary["precision"]), Decimal(str(control_rate))) if control_rate else 0.0

    for book, summary in validation_groups.items():
        summary["max_drawdown"] = float(_daily_group_drawdown(
            by_book[book], daily_rows or [], validation_start, validation_end,
        ))

    validation_pairs = [
        (
            Decimal(str(account["selection"]["client_net_pnl"])),
            Decimal(str(account["validation"]["client_net_pnl"])),
        )
        for account in active_validation
    ]
    direction_agreements = []
    for account in active_validation:
        selection_value = account["selection"]["client_net_pnl"]
        validation_value = account["validation_status"]
        if selection_value > float(NEUTRAL_BAND_USD):
            direction_agreements.append(validation_value == "profitable")
        elif selection_value < -float(NEUTRAL_BAND_USD):
            direction_agreements.append(validation_value == "loss")
    validation_diagnostics = {
        "direction_agreement_rate": _safe_ratio(
            Decimal(sum(1 for value in direction_agreements if value)),
            Decimal(len(direction_agreements)),
        ),
        "direction_agreement_accounts": len(direction_agreements),
        "selection_validation_pnl_correlation": _correlation(validation_pairs),
        "sample_warning": len(active_validation) < 30,
    }

    transitions = defaultdict(lambda: defaultdict(int))
    for account in accounts:
        transitions[account["book"]][account["transition_status"]] += 1

    profit_impact = {}
    for book in ("abook", "bbook"):
        book_accounts = by_book[book]
        selection_market = sum((Decimal(str(account["selection"]["market_pnl"])) for account in book_accounts), ZERO)
        selection_net = sum((Decimal(str(account["selection"]["client_net_pnl"])) for account in book_accounts), ZERO)
        validation_market = sum((Decimal(str(account["validation"]["market_pnl"])) for account in book_accounts), ZERO)
        validation_net = sum((Decimal(str(account["validation"]["client_net_pnl"])) for account in book_accounts), ZERO)
        profit_impact[book] = {
            "selection_market_pnl": float(selection_market),
            "selection_client_net_pnl": float(selection_net),
            "current_bbook_market_pnl": float(-selection_market),
            "current_bbook_net_pnl": float(-selection_net),
            "after_abook_assumed_pnl": 0.0,
            "incremental_change": float(selection_net),
            "selection_customer_net_pnl": float(selection_net),
            "selection_bbook_company_profit": float(-selection_net),
            "selection_company_increment_if_routed_abook": float(selection_net),
            "validation_market_pnl": float(validation_market),
            "validation_client_net_pnl": float(validation_net),
            "validation_incremental_change": float(validation_net),
            "validation_customer_net_pnl": float(validation_net),
            "validation_bbook_company_profit": float(-validation_net),
            "validation_company_increment_if_routed_abook": float(validation_net),
            "is_theoretical": True,
        }

    monthly_series = []
    for month in all_months:
        phase = "selection" if month in selection_months else "validation"
        for book, book_accounts in by_book.items():
            month_rows = []
            for account in book_accounts:
                source_rows = grouped[(account["platform"], account["login"])]
                matching = [
                    row for row in source_rows
                    if _month(row["month_start"]) == month
                    and (row.get("phase") not in {"selection", "validation"} or row.get("phase") == phase)
                ]
                month_rows.extend(matching or [_empty_period_row(account, month)])
            month_metrics = _account_period_metrics(month_rows, book_accounts[0] if book_accounts else {"platform": "", "login": 0, "account_group": ""}, phase=phase)
            monthly_series.append({
                "month": month,
                "phase": phase,
                "book": book,
                "active_accounts": sum(1 for account in book_accounts if any(
                    _month(row["month_start"]) == month and int(row.get("matched_trades", 0) or 0) > 0
                    for row in grouped[(account["platform"], account["login"])]
                )),
                "client_net_pnl": month_metrics["client_net_pnl"],
                "market_pnl": month_metrics["market_pnl"],
                "theoretical_mirror_pnl": -month_metrics["market_pnl"],
                "matched_trades": month_metrics["trade_count"],
            })

    monthly_overview = []
    for month in all_months:
        month_rows = [
            row for row in overview_materialized
            if _month(row["month_start"]) == month
            and (row.get("phase") not in {"selection", "validation"} or row.get("phase") == ("selection" if month in selection_months else "validation"))
        ]
        active_logins = {
            (row["platform"], int(row["login"]))
            for row in month_rows
            if int(row.get("matched_trades", 0) or 0) > 0
        }
        client_net_pnl = _sum(month_rows, "client_net_pnl")
        market_pnl = sum((_canonical_market_pnl(row) for row in month_rows), ZERO)
        monthly_overview.append({
            "month": month,
            "phase": "selection" if month in selection_months else "validation",
            "active_accounts": len(active_logins),
            "user_net_pnl": _float(client_net_pnl),
            "user_market_pnl": _float(market_pnl),
            "company_bbook_profit": _float(-client_net_pnl),
            "company_market_profit": _float(-market_pnl),
            "matched_trades": sum(int(row.get("matched_trades", 0) or 0) for row in month_rows),
        })

    profit_overview = {
        "monthly": monthly_overview,
        "july_book_split": {
            "abook": _book_split_summary(by_book["abook"], abook=True),
            "bbook": _book_split_summary(by_book["bbook"], abook=False),
        },
        "company_profit_definition": "book company profit = - user net trading P&L; Abook assumed company profit = 0",
        "pnl_basis": {
            "client_net_pnl": "UTC ods_mt5_deals: sum(profit + storage + commission + fee) where is_deleted = 0 and action IN (0, 1)",
            "market_pnl": "UTC ods_mt5_deals: sum(profit) where is_deleted = 0 and action IN (0, 1)",
            "funding_pnl": "action IN (2, 3) is kept separately and is not included in client_net_pnl",
            "company_pnl": "Bbook company P&L = - client_net_pnl; Abook company P&L is theoretical 0 without external hedge execution data",
            "matched_trades": "dwd_matched_trades is used for matched trade counts and cross-check fields, not the canonical client_net_pnl",
        },
    }
    book_performance = {
        "selection": {
            "abook": _book_performance(by_book["abook"], "selection", "abook"),
            "bbook": _book_performance(by_book["bbook"], "selection", "bbook"),
        },
        "validation": {
            "abook": _book_performance(by_book["abook"], "validation", "abook"),
            "bbook": _book_performance(by_book["bbook"], "validation", "bbook"),
        },
        "definition": "Abook shows customer P&L and routing impact separately; Bbook company P&L equals negative customer P&L.",
    }

    validation_end_date = date.fromisoformat(validation_end)
    matched_min_values = [_date_text(row.get("source_matched_min")) for row in materialized]
    matched_max_values = [_date_text(row.get("source_matched_max")) for row in materialized]
    raw_deal_min_values = [_date_text(row.get("source_deal_raw_min")) for row in materialized]
    raw_deal_max_values = [_date_text(row.get("source_deal_raw_max")) for row in materialized]
    deal_min_values = [_date_text(row.get("source_deal_min")) for row in materialized]
    deal_max_values = [_date_text(row.get("source_deal_max")) for row in materialized]
    matched_min_values = [value for value in matched_min_values if value]
    matched_max_values = [value for value in matched_max_values if value]
    raw_deal_min_values = [value for value in raw_deal_min_values if value]
    raw_deal_max_values = [value for value in raw_deal_max_values if value]
    deal_min_values = [value for value in deal_min_values if value]
    deal_max_values = [value for value in deal_max_values if value]
    raw_deal_min = min(raw_deal_min_values) if raw_deal_min_values else None
    raw_deal_max = max(raw_deal_max_values) if raw_deal_max_values else None
    source_min = min(matched_min_values + deal_min_values) if matched_min_values + deal_min_values else None
    source_max = max(matched_max_values + deal_max_values) if matched_max_values + deal_max_values else None
    deleted_rows_values = [int(row.get("source_deal_deleted_rows", 0) or 0) for row in materialized]
    deleted_rows = max(deleted_rows_values) if deleted_rows_values else 0
    partial_months = set()
    if source_min and date.fromisoformat(source_min).day > 1:
        partial_months.add(source_min[:7])
    if source_max:
        source_max_date = date.fromisoformat(source_max)
        if source_max_date.day < monthrange(source_max_date.year, source_max_date.month)[1]:
            partial_months.add(source_max[:7])
    if validation_end_date.day < monthrange(validation_end_date.year, validation_end_date.month)[1]:
        partial_months.add(validation_end[:7])
    misjudge_summary = build_misjudge_summary(accounts)
    funnel = build_selection_funnel(
        accounts,
        unique_accounts,
        min_trades=min_trades,
        min_active_days=min_active_days,
    )
    without_personal_count = sum(
        1 for account in accounts
        if account.get("abook_rules_pass")
        and not account.get("martingale_blocked", False)
    )
    with_list_validation_increment = sum(
        float(account.get("validation", {}).get("client_net_pnl", 0.0))
        for account in by_book["abook"]
    )
    without_list_accounts = [
        account for account in accounts
        if account.get("abook_rules_pass")
        and not account.get("martingale_blocked", False)
    ]
    without_list_validation_increment = sum(
        float(account.get("validation", {}).get("client_net_pnl", 0.0))
        for account in without_list_accounts
    )
    personal_candidate_impact = {
        "enabled": bool(personal_info.get("enabled")),
        "with_list_abook_accounts": len(by_book["abook"]),
        "without_list_abook_accounts": without_personal_count,
        "abook_account_delta": len(by_book["abook"]) - without_personal_count,
        "with_list_validation_increment": round(with_list_validation_increment, 6),
        "without_list_validation_increment": round(without_list_validation_increment, 6),
        "validation_increment_delta": round(with_list_validation_increment - without_list_validation_increment, 6),
        "definition": "Without-list metrics remove personal overrides from the same in-memory population while preserving the martingale hard block.",
    }
    return {
        "coverage": {
            "selection_start": selection_start,
            "selection_end": selection_end,
            "validation_start": validation_start,
            "validation_end": validation_end,
            "validation_partial": validation_end_date.day < monthrange(validation_end_date.year, validation_end_date.month)[1],
            "source_min": source_min,
            "source_max": source_max,
            "source_matched_min": min(matched_min_values) if matched_min_values else None,
            "source_matched_max": max(matched_max_values) if matched_max_values else None,
            "source_deal_min": min(deal_min_values) if deal_min_values else None,
            "source_deal_max": max(deal_max_values) if deal_max_values else None,
            "source_deal_raw_min": raw_deal_min,
            "source_deal_raw_max": raw_deal_max,
            "source_deal_deleted_rows": deleted_rows,
            "historical_deals_before_active": bool(raw_deal_min and source_min and raw_deal_min < source_min),
            "partial_months": sorted(partial_months),
            "source": "UTC",
        },
        "rules": {
            "min_trades": min_trades,
            "enable_r4": enable_r4,
            "r4_min_passing_weeks": r4_min_passing_weeks,
            "r4_thresholds": {
                "primary_trade_pct": R4_PRIMARY_TRADE_PCT,
                "win_rate": R4_WIN_RATE,
                "total_trades": R4_MIN_TRADES,
                "primary_trades": R4_MIN_PRIMARY_TRADES,
                "soft_alignment": 1,
            },
            "selection_months_positive_required": False,
            "min_win_rate": min_win_rate,
            "min_profit_factor": min_profit_factor,
            "min_payoff_ratio": min_payoff_ratio,
            "max_top1_day_profit_contribution": max_top1_day_profit_contribution,
            "avg_profit_snapshot_status": avg_profit_snapshot_status,
            "max_leverage_p95_ratio": max_leverage_p95_ratio,
            "max_high_leverage_holding_seconds": max_high_leverage_holding_seconds,
            "risk_snapshot_status": risk_snapshot_status,
            "min_stability_score": min_stability_score,
            "stability_score_mode": "diagnostic_only",
            "excluded_martingale_levels": list(excluded_martingale_levels),
            "removed_selection_rules": [
                "min_active_days",
                "min_avg_daily_profit",
                "min_positive_month_rate",
                "min_selection_monthly_consistency",
                "min_direction_day_rate_lower_bound",
                "max_daily_profit_month_contribution",
                "min_stability_score",
                "min_avg_profit",
                "selection_monthly_positive",
                "selection_period_client_net_pnl",
            ],
            "test_accounts_excluded": True,
            "excluded_account_group_tokens": ["test", "demo"],
        },
        "selection": {
            "counts": selection_counts,
            "stability_overview": stability_overview,
            "groups": {
                book: _phase_summary(book_accounts, "selection")
                for book, book_accounts in by_book.items()
            },
        },
        "validation": {"groups": validation_groups, "diagnostics": validation_diagnostics},
        "transitions": {book: dict(statuses) for book, statuses in transitions.items()},
        "profit_impact": profit_impact,
        "personal_candidate_list": personal_info,
        "news_candidate_list": news_info,
        "profit_overview": profit_overview,
        "book_performance": book_performance,
        "daily_book_series": daily_book_series,
        "monthly_series": monthly_series,
        "misjudge": misjudge_summary,
        "funnel": funnel,
        "personal_candidate_impact": personal_candidate_impact,
        "population_accounts": accounts,
        "accounts": [account for account in accounts if account["has_nonzero_pnl"]],
    }
