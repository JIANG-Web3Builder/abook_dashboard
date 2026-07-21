from __future__ import annotations

import bisect
from collections import defaultdict
from datetime import datetime
import math
from typing import Any, Iterable


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _positive_contribution(values: Iterable[float], top_n: int = 1) -> float | None:
    positive = sorted((value for value in values if value > 0), reverse=True)
    total = sum(positive)
    return round(sum(positive[:top_n]) / total, 6) if total else None


def _date_key(row: dict[str, Any]) -> str:
    value = row.get("exit_time") or row.get("entry_time")
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value or "")[:10]


def _hour_key(row: dict[str, Any]) -> str:
    value = row.get("exit_time") or row.get("entry_time")
    if isinstance(value, datetime):
        return f"{value.hour:02d}"
    text = str(value or " ")
    return text[11:13] if len(text) >= 13 else "unknown"


def _sum_rows(rows: Iterable[dict[str, Any]]) -> float:
    return round(sum(_number(row.get("profit")) for row in rows), 6)


def _remove_group_profit(rows: list[dict[str, Any]], group_key: Any, top_n: int = 1) -> float:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = group_key(row) if callable(group_key) else str(row.get(group_key) or "unknown")
        grouped[str(key)].append(row)
    ranked = sorted(
        ((key, sum(_number(row.get("profit")) for row in items if _number(row.get("profit")) > 0)) for key, items in grouped.items()),
        key=lambda item: item[1],
        reverse=True,
    )
    removed = {key for key, profit in ranked[:top_n] if profit > 0}
    return round(_sum_rows(row for row in rows if str(group_key(row) if callable(group_key) else row.get(group_key) or "unknown") not in removed), 6)


def build_account_detail_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    materialized = list(rows)
    if not materialized:
        return {"metrics": {}, "concentration": {}, "de_extreme": {}}
    raw_net_profit = _sum_rows(materialized)
    positive_rows = [row for row in materialized if _number(row.get("profit")) > 0]
    day_profits: dict[str, float] = defaultdict(float)
    order_profits = [_number(row.get("profit")) for row in materialized]
    symbol_profits: dict[str, float] = defaultdict(float)
    hour_profits: dict[str, float] = defaultdict(float)
    event_profits: dict[str, float] = defaultdict(float)
    for row in positive_rows:
        profit = _number(row.get("profit"))
        day_profits[_date_key(row)] += profit
        symbol_profits[str(row.get("symbol") or "unknown")] += profit
        hour_profits[_hour_key(row)] += profit
        if row.get("event_window"):
            event_profits[str(row["event_window"])] += profit

    positive_total = sum(value for value in order_profits if value > 0)
    best_event_values = list(event_profits.values())
    max_position_row = max(materialized, key=lambda row: abs(_number(row.get("entry_price")) * _number(row.get("volume"))), default=None)
    max_position_profit = _number(max_position_row.get("profit")) if max_position_row else 0.0
    metrics = {
        "raw_net_profit": raw_net_profit,
        "positive_profit_total": round(positive_total, 6),
        "negative_profit_total": round(sum(value for value in order_profits if value < 0), 6),
    }
    concentration = {}
    for key, values, top_n in (
        ("max_profit_day_contribution", day_profits.values(), 1),
        ("top3_profit_day_contribution", day_profits.values(), 3),
        ("max_profit_order_contribution", order_profits, 1),
        ("top3_profit_order_contribution", order_profits, 3),
        ("best_symbol_contribution", symbol_profits.values(), 1),
        ("best_time_bucket_contribution", hour_profits.values(), 1),
        ("best_event_contribution", best_event_values, 1),
    ):
        value = _positive_contribution(values, top_n)
        if value is not None:
            concentration[key] = value

    de_extreme = {}
    if day_profits:
        de_extreme["without_max_profit_day"] = _remove_group_profit(materialized, _date_key, 1)
        de_extreme["without_top3_profit_days"] = _remove_group_profit(materialized, _date_key, 3)
    positive_orders = sorted((value for value in order_profits if value > 0), reverse=True)
    if positive_orders:
        de_extreme["without_max_profit_order"] = round(raw_net_profit - positive_orders[0], 6)
        de_extreme["without_top3_profit_orders"] = round(raw_net_profit - sum(positive_orders[:3]), 6)
        de_extreme["without_top5_profit_orders"] = round(raw_net_profit - sum(positive_orders[:5]), 6)
    if symbol_profits:
        de_extreme["without_best_symbol"] = _remove_group_profit(materialized, "symbol", 1)
    if hour_profits:
        de_extreme["without_best_time_bucket"] = _remove_group_profit(materialized, _hour_key, 1)
    if event_profits:
        de_extreme["without_best_event"] = _remove_group_profit(materialized, "event_window", 1)
    if max_position_row:
        de_extreme["without_max_position_order"] = round(raw_net_profit - max_position_profit, 6)
    return {"metrics": metrics, "concentration": concentration, "de_extreme": de_extreme}


_MARKOUT_OFFSETS_MS = (-10000, -5000, -1000, -500, -100, -50, -10, 0, 10, 50, 100, 250, 500, 1000, 2000, 5000, 10000)


def _timestamp_ms(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


def _direction_sign(row: dict[str, Any]) -> int:
    action = row.get("action")
    if action not in (None, ""):
        return 1 if str(action).strip().lower() in {"0", "buy", "long"} else -1
    return 1 if str(row.get("direction") or "").strip().lower() in {"buy", "long", "0"} else -1


def _matched_trade_tape(rows: list[dict[str, Any]]) -> dict[str, tuple[list[int], list[float]]]:
    prints_by_symbol: dict[str, list[tuple[int, int, float]]] = defaultdict(list)
    for index, row in enumerate(rows):
        symbol = str(row.get("symbol") or "")
        entry_time = _timestamp_ms(row.get("entry_time"))
        exit_time = _timestamp_ms(row.get("exit_time"))
        entry_price = _number(row.get("entry_price"))
        exit_price = _number(row.get("exit_price"))
        if entry_time is not None and entry_price > 0:
            prints_by_symbol[symbol].append((entry_time, index, entry_price))
        if exit_time is not None and exit_price > 0:
            prints_by_symbol[symbol].append((exit_time, index, exit_price))
    return {
        symbol: (
            [item[0] for item in sorted(prints, key=lambda item: (item[0], item[1]))],
            [item[2] for item in sorted(prints, key=lambda item: (item[0], item[1]))],
        )
        for symbol, prints in prints_by_symbol.items()
    }


def _price_at_or_before(times: list[int], prices: list[float], target_ms: int) -> float | None:
    index = bisect.bisect_right(times, target_ms) - 1
    return prices[index] if index >= 0 else None


def _markout_value(row: dict[str, Any], kind: str, reference_price: float | None) -> float | None:
    if reference_price is None:
        return None
    price = _number(row.get("entry_price" if kind == "entry" else "exit_price"))
    if price <= 0:
        return None
    direction = _direction_sign(row) * (1 if kind == "entry" else -1)
    value = direction * (reference_price - price) / price * 10000
    return value if math.isfinite(value) else None


def _markout_weight(row: dict[str, Any]) -> float:
    """Return the trade's nominal turnover weight, with a local fallback."""
    turnover = abs(_number(row.get("turnover")))
    if turnover > 0 and math.isfinite(turnover):
        return turnover
    return abs(_number(row.get("entry_price")) * _number(row.get("volume")))


def summarize_markout_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Calculate turnover-weighted matched-trade markout without tick data."""
    result: dict[str, Any] = {}
    materialized = [row for row in rows if row.get("entry_time") or row.get("exit_time")]
    tapes = _matched_trade_tape(materialized)
    if not materialized or not tapes:
        return result
    for kind in ("entry", "exit"):
        curve = []
        for offset_ms in _MARKOUT_OFFSETS_MS:
            weighted_sum = 0.0
            weight_total = 0.0
            sample_count = 0
            for row in materialized:
                event_time = _timestamp_ms(row.get("entry_time" if kind == "entry" else "exit_time"))
                if event_time is None:
                    continue
                tape = tapes.get(str(row.get("symbol") or ""))
                if tape is None:
                    continue
                reference = _price_at_or_before(tape[0], tape[1], event_time + offset_ms)
                value = _markout_value(row, kind, reference)
                weight = _markout_weight(row)
                if value is None or weight <= 0 or not math.isfinite(weight):
                    continue
                weighted_sum += value * weight
                weight_total += weight
                sample_count += 1
            mean_bps = weighted_sum / weight_total if weight_total else None
            curve.append({
                "offset_ms": offset_ms,
                "mean_bps": round(mean_bps, 6) if mean_bps is not None else None,
                "sample_count": sample_count,
                "nominal_turnover": round(weight_total, 6),
            })

        primary = next((point for point in curve if point["offset_ms"] == 1000), None)
        five_second = next((point for point in curve if point["offset_ms"] == 5000), None)
        sample_count = primary["sample_count"] if primary else max(point["sample_count"] for point in curve)
        result[kind] = {
            "primary_horizon_ms": 1000,
            "primary_bps": primary["mean_bps"] if primary else None,
            "mean_5s_bps": five_second["mean_bps"] if five_second else None,
            "curve": curve,
            "sample_count": sample_count,
            "weighting": "abs(turnover) weighted; entry_price × volume fallback",
        }
    return result
