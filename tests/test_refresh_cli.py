from datetime import date

import pytest

from scripts.refresh_local_data import parse_args


def test_refresh_cli_requires_all_four_period_dates():
    with pytest.raises(SystemExit):
        parse_args(["--selection-start", "2026-05-01"])


def test_refresh_cli_accepts_repeated_platforms_and_dry_run():
    args = parse_args([
        "--selection-start", "2026-05-01",
        "--selection-end", "2026-06-30",
        "--validation-start", "2026-07-01",
        "--validation-end", "2026-07-23",
        "--platform", "mt5",
        "--platform", "hh_mt5",
        "--dry-run",
    ])

    assert args.selection_start == date(2026, 5, 1)
    assert args.platforms == ["hh_mt5", "mt5"]
    assert args.dry_run is True
