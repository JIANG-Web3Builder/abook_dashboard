from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    clickhouse_host: str
    clickhouse_port: int
    clickhouse_database: str
    clickhouse_user: str
    clickhouse_password: str
    clickhouse_secure: bool
    clickhouse_compress: bool = True
    clickhouse_use_server_time_zone_for_dates: bool = True
    data_source: str = "local"
    warehouse_path: Path = field(
        default_factory=lambda: Path(__file__).resolve().parent.parent / "data" / "warehouse"
    )
    warehouse_tail_days: int = 7
    warehouse_lookback_months: int = 3

    def __post_init__(self) -> None:
        if self.data_source not in {"local", "remote"}:
            raise ValueError("ABOOK_DATA_SOURCE must be local or remote")
        if self.warehouse_tail_days < 1:
            raise ValueError("ABOOK_WAREHOUSE_TAIL_DAYS must be at least 1")
        if self.warehouse_lookback_months < 0:
            raise ValueError("ABOOK_WAREHOUSE_LOOKBACK_MONTHS must be non-negative")

    @property
    def configured(self) -> bool:
        return bool(self.clickhouse_host and self.clickhouse_user and self.clickhouse_password)


def load_env_file(path: Path | None = None) -> None:
    """Load simple KEY=VALUE entries without overriding process environment."""
    env_path = path or Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            os.environ.setdefault(key, value)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        clickhouse_host=os.getenv(
            "CLICKHOUSE_HOST",
            "data-collect-alb-110459182.ap-southeast-2.elb.amazonaws.com",
        ),
        clickhouse_port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        clickhouse_database=os.getenv("CLICKHOUSE_DATABASE", "risk"),
        clickhouse_user=os.getenv("CLICKHOUSE_USER", "default"),
        clickhouse_password=os.getenv("CLICKHOUSE_PASSWORD", ""),
        clickhouse_secure=os.getenv("CLICKHOUSE_SECURE", "0").lower() in {"1", "true", "yes"},
        clickhouse_compress=os.getenv("CLICKHOUSE_COMPRESS", "1").lower() in {"1", "true", "yes"},
        clickhouse_use_server_time_zone_for_dates=os.getenv(
            "CLICKHOUSE_USE_SERVER_TIME_ZONE_FOR_DATES", "1"
        ).lower() in {"1", "true", "yes"},
        data_source=os.getenv("ABOOK_DATA_SOURCE", "local").lower(),
        warehouse_path=Path(
            os.getenv(
                "ABOOK_WAREHOUSE_PATH",
                str(Path(__file__).resolve().parent.parent / "data" / "warehouse"),
            )
        ),
        warehouse_tail_days=int(os.getenv("ABOOK_WAREHOUSE_TAIL_DAYS", "7")),
        warehouse_lookback_months=int(os.getenv("ABOOK_WAREHOUSE_LOOKBACK_MONTHS", "3")),
    )
