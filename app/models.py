from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AnalysisFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: List[str] = Field(default_factory=list)
    logins: List[int] = Field(default_factory=list)


class AnalysisPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date

    @model_validator(mode="after")
    def validate_range(self) -> "AnalysisPeriod":
        if self.end < self.start:
            raise ValueError("period end must be on or after start")
        return self


class AnalysisRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_trades: int = Field(default=75, ge=0)
    # Retained for request compatibility; active trade days no longer gate Abook routing.
    min_active_days: int = Field(default=0, ge=0)
    min_win_rate: float = Field(default=0.5, ge=0, le=1)
    min_profit_factor: float = Field(default=1.25, ge=0)
    min_payoff_ratio: float = Field(default=0.6, ge=0)
    min_long_trades_ratio: float = Field(default=0.3, ge=0, le=1)
    max_long_trades_ratio: float = Field(default=0.7, ge=0, le=1)
    min_avg_daily_profit: float = Field(default=0.0, ge=0)
    min_avg_profit: float = Field(default=0.0, ge=0)
    min_selection_monthly_consistency: float = Field(default=0.0, ge=0, le=1)
    min_positive_month_rate: float = Field(default=0.5, ge=0, le=1)
    max_top1_day_profit_contribution: float = Field(default=0.3, gt=0, le=1)
    max_daily_profit_month_contribution: float = Field(default=0.6, gt=0, le=1)
    max_leverage_p95_ratio: float = Field(default=2000.0, gt=0)
    # Kept for old clients; service decisions use max_leverage_p95_ratio.
    max_peak_leverage_ratio: float = Field(default=2000.0, gt=0)
    max_high_leverage_holding_seconds: float = Field(default=60.0, ge=0)
    min_direction_day_rate_lower_bound: float = Field(default=0.55, ge=0, le=1)
    min_stability_score: float = Field(default=70.0, ge=0, le=100)
    high_confidence_trades: int = Field(default=100, ge=0)
    high_confidence_days: int = Field(default=30, ge=0)
    excluded_martingale_levels: List[Literal["extreme", "high", "medium", "low"]] = Field(
        default_factory=lambda: ["extreme", "high", "medium", "low"]
    )
    # Retained for old clients; monthly P&L is diagnostic/validation only and
    # can no longer gate Abook routing.
    require_selection_monthly_positive: bool = False
    @model_validator(mode="after")
    def sync_legacy_leverage_rule(self) -> "AnalysisRules":
        # Older clients only send max_peak_leverage_ratio. Treat that value as
        # the p95 threshold during the migration, without using peak leverage
        # in the calculation itself.
        if (
            "max_peak_leverage_ratio" in self.model_fields_set
            and "max_leverage_p95_ratio" not in self.model_fields_set
        ):
            self.max_leverage_p95_ratio = self.max_peak_leverage_ratio
        if self.min_long_trades_ratio > self.max_long_trades_ratio:
            raise ValueError("min_long_trades_ratio must be less than or equal to max_long_trades_ratio")
        return self


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection: AnalysisPeriod = Field(
        default_factory=lambda: AnalysisPeriod(start=date(2026, 5, 1), end=date(2026, 6, 30))
    )
    validation: AnalysisPeriod = Field(
        default_factory=lambda: AnalysisPeriod(start=date(2026, 7, 1), end=date(2026, 7, 22))
    )
    platforms: List[str] = Field(default_factory=lambda: ["mt4", "mt5", "hh_mt5"])
    filters: AnalysisFilters = Field(default_factory=AnalysisFilters)
    rules: AnalysisRules = Field(default_factory=AnalysisRules)
    personal_candidate_list: bool = False
    news_candidate_list: bool = False

    # Backward-compatible fields for old callers. New callers should use the
    # explicit selection/validation/rules structure.
    start: Optional[date] = None
    end: Optional[date] = None
    lookback_months: Optional[Literal[1, 2, 3]] = None
    min_profit_factor: Optional[float] = Field(default=None, ge=0)
    min_avg_daily_profit: Optional[float] = None

    @field_validator("platforms")
    @classmethod
    def validate_platforms(cls, value: List[str]) -> List[str]:
        allowed = {"mt4", "mt5", "hh_mt5"}
        invalid = set(value) - allowed
        if invalid:
            raise ValueError(f"unsupported platform: {sorted(invalid)}")
        return value or sorted(allowed)

    @model_validator(mode="after")
    def validate_range(self) -> "AnalysisRequest":
        if self.min_profit_factor is not None:
            self.rules.min_profit_factor = self.min_profit_factor
        if self.min_avg_daily_profit is not None:
            self.rules.min_avg_daily_profit = self.min_avg_daily_profit
        if self.selection.end >= self.validation.start:
            raise ValueError("selection must end before validation starts")
        return self


class FilterOptions(BaseModel):
    platforms: List[str]
    groups: List[str]


class AccountKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: str
    login: int = Field(ge=0)


class BookAnalyticsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis: AnalysisRequest
    abook_accounts: List[AccountKey] = Field(default_factory=list)
    analysis_token: Optional[str] = None
    include_symbols: bool = True


class DirectionAnalyticsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis: AnalysisRequest
    analysis_token: Optional[str] = None
