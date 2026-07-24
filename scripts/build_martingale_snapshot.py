from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import clickhouse_connect

from app.config import get_settings, load_env_file
from app.query_client import LocalClientAdapter
from app.martingale import snapshot_path
from app.queries import ALLOWED_PLATFORMS, USER_SOURCE_SQL


RISK_LEVEL_ORDER = ("extreme", "high", "medium", "low")


def _risk_level_for_row(row: dict, detection_status: str) -> str:
    """Return severity without letting a broad outlier upgrade confirmed users."""
    if detection_status == "confirmed":
        strict_fields = (
            ("confirmed_extreme_tier_windows", "extreme"),
            ("confirmed_high_tier_windows", "high"),
            ("confirmed_medium_tier_windows", "medium"),
            ("confirmed_low_tier_windows", "low"),
        )
        for field, level in strict_fields:
            if int(row[field]):
                return level
        return "low" if int(row.get("confirmed_windows", 0)) else "low"
    for field, level in (
        ("extreme_windows", "extreme"),
        ("high_windows", "high"),
        ("medium_windows", "medium"),
        ("low_windows", "low"),
    ):
        if int(row[field]):
            return level
    return "low"


def _end_exclusive(value: str) -> str:
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()


def build_snapshot(
    selection_start: str,
    selection_end: str,
    platforms: list[str],
    window_type: str = "7D_SLIDING",
) -> dict:
    settings = get_settings()
    if settings.data_source == "remote" and not settings.configured:
        raise RuntimeError("CLICKHOUSE_PASSWORD is not configured")
    selected = sorted(set(platforms) & ALLOWED_PLATFORMS)
    if settings.data_source == "local":
        client = LocalClientAdapter(settings.warehouse_path)
    else:
        client = clickhouse_connect.get_client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            username=settings.clickhouse_user,
            password=settings.clickhouse_password,
            database=settings.clickhouse_database,
            secure=settings.clickhouse_secure,
        )
    # avg_volume_escalation is stored as a multiplier (median 1.0 across the
    # table), so martingale.md thresholds apply unchanged. Windows are rolling
    # 7-day windows. A broad candidate is retained for audit, but only repeated
    # confirmed windows can produce a confirmed user-level detection.
    query = """
    WITH users AS (
        SELECT platform, login
        FROM risk.ods_mt5_users FINAL
        WHERE is_deleted = 0
          AND has({platforms:Array(String)}, platform)
          AND positionCaseInsensitive(`group`, 'test') = 0
          AND positionCaseInsensitive(`group`, 'demo') = 0
        GROUP BY platform, login
    ), windows AS (
        SELECT
            w.platform AS platform,
            w.login AS login,
            w.window_end AS window_end,
            toFloat64(w.avg_volume_escalation) AS escalation,
            toFloat64(w.averaging_down_ratio) AS averaging_down_ratio,
            toFloat64(w.sequence_trade_ratio) AS sequence_trade_ratio,
            toFloat64(w.sequence_win_rate) AS sequence_win_rate,
            toFloat64(w.sequence_total_profit) AS sequence_total_profit,
            toFloat64(w.averaging_down_profit) AS averaging_down_profit,
            toFloat64(w.avg_gap_time) AS avg_gap_time,
            w.max_sequence_length AS max_sequence_length,
            (w.averaging_down_count >= 3 AND w.averaging_down_ratio >= 0.50 AND w.total_sequences >= 5) AS layer1,
            (w.avg_volume_escalation >= 1.20) AS layer2,
            (w.avg_sequence_length >= 3 AND w.sequence_trade_ratio >= 0.40 AND w.max_sequence_length >= 5) AS layer3,
            (w.averaging_down_ratio >= w.pyramiding_ratio * 2 AND w.pyramiding_ratio <= 0.25) AS layer4,
            (w.sequence_win_rate >= 0.70 AND w.sequence_total_profit < 0) AS layer5_strict,
            (w.sequence_win_rate >= 0.75 AND w.averaging_down_profit < 0
             AND w.avg_volume_escalation >= 1.5 AND w.max_sequence_length >= 7) AS layer5_alt
        FROM risk.dws_account_martingale_window AS w
        INNER JOIN users AS u ON w.platform = u.platform AND w.login = u.login
        WHERE w.window_type = {window_type:String}
          AND w.platform IN {platforms:Array(String)}
          AND w.window_end >= {start:DateTime}
          AND w.window_start < {end_exclusive:DateTime}
    ), scored AS (
        SELECT
            *,
            (layer1 AND layer3 AND layer4) AS confirmed_gate,
            (layer1 AND layer3 AND layer4
             AND escalation >= 1.5
             AND max_sequence_length >= 7
             AND sequence_total_profit < 0
             AND averaging_down_profit <= 0) AS confirmed_extreme_candidate,
            (layer1 AND (layer2 OR layer3 OR layer4 OR layer5_strict OR layer5_alt)) AS gate,
            multiIf(
                gate AND escalation >= 1.5 AND max_sequence_length >= 7 AND sequence_total_profit < 0, 'extreme',
                gate AND (
                    (escalation >= 1.3 AND max_sequence_length >= 5 AND averaging_down_ratio >= 0.60)
                    OR (escalation >= 1.2 AND layer5_strict)
                    OR layer5_alt
                ), 'high',
                gate AND escalation >= 1.2, 'medium',
                gate AND escalation >= 1.1, 'low',
                'none'
            ) AS tier,
            multiIf(
                confirmed_extreme_candidate, 'extreme',
                confirmed_gate AND (
                    (escalation >= 1.3 AND max_sequence_length >= 5 AND averaging_down_ratio >= 0.60
                     AND averaging_down_profit <= 0)
                    OR (escalation >= 1.2 AND layer5_strict)
                    OR layer5_alt
                ), 'high',
                confirmed_gate AND escalation >= 1.2, 'medium',
                confirmed_gate, 'low',
                'none'
            ) AS confirmed_tier
        FROM windows
    )
    SELECT
        platform,
        login,
        count() AS windows_total,
        countIf(layer1) AS layer1_windows,
        countIf(layer1 AND layer2) AS layer2_windows,
        countIf(layer1 AND layer2 AND layer3) AS layer3_windows,
        countIf(layer1 AND layer2 AND layer3 AND layer4) AS layer4_windows,
        countIf(layer1 AND layer2 AND layer3 AND layer4 AND layer5_strict) AS strict_windows,
        countIf(layer1 AND layer2 AND layer3 AND layer4 AND layer5_alt) AS alt_windows,
        countIf(confirmed_gate) AS confirmed_gate_windows,
        uniqExactIf(window_end, confirmed_gate) AS confirmed_windows,
        uniqExactIf(window_end, confirmed_extreme_candidate) AS confirmed_extreme_windows,
        uniqExactIf(window_end, gate AND NOT confirmed_gate) AS expanded_windows,
        uniqExactIf(window_end, confirmed_tier = 'extreme') AS confirmed_extreme_tier_windows,
        uniqExactIf(window_end, confirmed_tier = 'high') AS confirmed_high_tier_windows,
        uniqExactIf(window_end, confirmed_tier = 'medium') AS confirmed_medium_tier_windows,
        uniqExactIf(window_end, confirmed_tier = 'low') AS confirmed_low_tier_windows,
        countIf(gate) AS gate_windows,
        countIf(tier = 'extreme') AS extreme_windows,
        countIf(tier = 'high') AS high_windows,
        countIf(tier = 'medium') AS medium_windows,
        countIf(tier = 'low') AS low_windows,
        max(escalation) AS max_volume_escalation,
        max(max_sequence_length) AS max_sequence_length,
        max(averaging_down_ratio) AS max_averaging_down_ratio,
        max(sequence_trade_ratio) AS max_sequence_trade_ratio,
        max(sequence_win_rate) AS max_sequence_win_rate,
        min(sequence_total_profit) AS min_sequence_total_profit,
        sumIf(sequence_total_profit, tier != 'none') AS flagged_sequence_profit,
        minIf(avg_gap_time, gate) AS min_avg_gap_time,
        max(window_end) AS last_window_end
    FROM scored
    GROUP BY platform, login
    HAVING extreme_windows + high_windows + medium_windows + low_windows > 0
        OR confirmed_windows > 0
    ORDER BY platform, login
    """
    query = query.replace("FROM risk.ods_mt5_users FINAL", f"FROM {USER_SOURCE_SQL} AS user_source")
    result = client.query(query, parameters={
        "platforms": selected,
        "window_type": window_type,
        "start": selection_start,
        "end_exclusive": _end_exclusive(selection_end),
    })
    records = []
    for values in result.result_rows:
        row = dict(zip(result.column_names, values))
        confirmed_windows = int(row["confirmed_windows"])
        candidate_windows = sum(
            int(row[field])
            for field in ("extreme_windows", "high_windows", "medium_windows", "low_windows")
        )
        if confirmed_windows >= 2:
            detection_status = "confirmed"
        elif confirmed_windows or candidate_windows:
            detection_status = "suspected"
        else:
            detection_status = "none"
        risk_level = _risk_level_for_row(row, detection_status)
        detection_mode = "confirmed" if confirmed_windows > 0 else "expanded"
        records.append({
            "platform": str(row["platform"]),
            "login": int(row["login"]),
            "risk_level": risk_level,
            "detection_mode": detection_mode,
            "martingale_detection_status": detection_status,
            "windows_total": int(row["windows_total"]),
            "gate_windows": int(row["gate_windows"]),
            "confirmed_gate_windows": int(row["confirmed_gate_windows"]),
            "confirmed_windows": confirmed_windows,
            "confirmed_extreme_windows": int(row["confirmed_extreme_windows"]),
            "expanded_windows": int(row["expanded_windows"]),
            "extreme_windows": int(row["extreme_windows"]),
            "high_windows": int(row["high_windows"]),
            "medium_windows": int(row["medium_windows"]),
            "low_windows": int(row["low_windows"]),
            "layer1_windows": int(row["layer1_windows"]),
            "layer2_windows": int(row["layer2_windows"]),
            "layer3_windows": int(row["layer3_windows"]),
            "layer4_windows": int(row["layer4_windows"]),
            "strict_windows": int(row["strict_windows"]),
            "alt_windows": int(row["alt_windows"]),
            "layer_hits": {
                "layer1": int(row["layer1_windows"]) > 0,
                "layer2": int(row["layer2_windows"]) > 0,
                "layer3": int(row["layer3_windows"]) > 0,
                "layer4": int(row["layer4_windows"]) > 0,
                "layer5": int(row["strict_windows"]) > 0 or int(row["alt_windows"]) > 0,
                "layer5_strict": int(row["strict_windows"]) > 0,
                "layer5_alt": int(row["alt_windows"]) > 0,
            },
            "max_volume_escalation": float(row["max_volume_escalation"] or 0),
            "max_sequence_length": int(row["max_sequence_length"] or 0),
            "max_averaging_down_ratio": float(row["max_averaging_down_ratio"] or 0),
            "max_sequence_trade_ratio": float(row["max_sequence_trade_ratio"] or 0),
            "max_sequence_win_rate": float(row["max_sequence_win_rate"] or 0),
            "min_sequence_total_profit": float(row["min_sequence_total_profit"] or 0),
            "flagged_sequence_profit": float(row["flagged_sequence_profit"] or 0),
            "min_avg_gap_time": float(row["min_avg_gap_time"] or 0),
            "last_window_end": str(row["last_window_end"]),
        })
    level_counts = {level: sum(1 for record in records if record["risk_level"] == level) for level in RISK_LEVEL_ORDER}
    return {
        "selection_start": selection_start,
        "selection_end": selection_end,
        "platforms": selected,
        "window_type": window_type,
        "escalation_unit": "multiplier",
        "level_counts": level_counts,
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the local martingale user snapshot")
    parser.add_argument("--selection-start", default="2026-05-01")
    parser.add_argument("--selection-end", default="2026-06-30")
    parser.add_argument("--window-type", default="7D_SLIDING")
    parser.add_argument("--platform", action="append", dest="platforms")
    args = parser.parse_args()
    load_env_file()
    platforms = args.platforms or sorted(ALLOWED_PLATFORMS)
    output = snapshot_path()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    payload = build_snapshot(args.selection_start, args.selection_end, platforms, args.window_type)
    temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
    temporary.replace(output)
    print(json.dumps({
        "path": str(output),
        "records": len(payload["records"]),
        "level_counts": payload["level_counts"],
        "test_demo_excluded_by_sql": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
