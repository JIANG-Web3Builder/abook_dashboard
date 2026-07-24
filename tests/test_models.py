import pytest
from pydantic import ValidationError

from app.models import AnalysisFilters, AnalysisRequest


def test_account_filters_are_limited_to_user_selection_fields():
    filters = AnalysisFilters(groups=["real\\FPlive"], logins=[123, 456])

    assert filters.model_dump() == {"groups": ["real\\FPlive"], "logins": [123, 456]}


@pytest.mark.parametrize("field", ["countries", "min_leverage", "min_balance"])
def test_account_filters_reject_non_selection_dimensions(field):
    with pytest.raises(ValidationError):
        AnalysisFilters(**{field: ["Brazil"] if field == "countries" else 1})


def test_default_strategy_filters_match_july_tuned_profile_without_monthly_pnl_gate():
    request = AnalysisRequest()

    assert request.rules.min_trades == 75
    assert request.rules.min_profit_factor == 1.25
    assert request.rules.min_payoff_ratio == 0.6
    assert request.selection.start.isoformat() == "2026-05-01"
    assert request.validation.end.isoformat() == "2026-07-22"
    assert request.rules.min_positive_month_rate == 0.5  # legacy field retained but ignored by routing
    assert request.rules.max_top1_day_profit_contribution == 0.3
    assert request.rules.max_daily_profit_month_contribution == 0.6
    assert request.rules.max_leverage_p95_ratio == 2000.0
    assert request.rules.max_peak_leverage_ratio == 2000.0
    assert request.rules.max_high_leverage_holding_seconds == 60.0
    assert request.rules.min_direction_day_rate_lower_bound == 0.55
    assert request.rules.min_stability_score == 70
    assert request.rules.min_win_rate == 0.5
    assert request.rules.min_long_trades_ratio == 0.3
    assert request.rules.max_long_trades_ratio == 0.7
    assert request.rules.min_selection_monthly_consistency == 0.0
    assert request.personal_candidate_list is False
    assert request.news_candidate_list is False


@pytest.mark.parametrize("field", ["min_active_days", "min_avg_profit", "require_selection_monthly_positive"])
def test_removed_routing_parameters_are_rejected(field):
    with pytest.raises(ValidationError):
        AnalysisRequest(rules={field: 1})


def test_legacy_peak_leverage_field_still_overrides_new_default_when_sent_alone():
    request = AnalysisRequest(rules={"max_peak_leverage_ratio": 125})

    assert request.rules.max_leverage_p95_ratio == 125.0


def test_default_platforms_include_mt4():
    assert AnalysisRequest().platforms == ["mt4", "mt5", "hh_mt5"]


def test_analysis_request_accepts_separate_selection_and_validation_rules():
    request = AnalysisRequest(
        selection={"start": "2026-05-01", "end": "2026-06-30"},
        validation={"start": "2026-07-01", "end": "2026-07-31"},
        rules={"min_trades": 30},
    )

    assert request.rules.min_trades == 30


def test_long_trades_ratio_range_is_configurable_and_ordered():
    request = AnalysisRequest(rules={"min_long_trades_ratio": 0.35, "max_long_trades_ratio": 0.65})

    assert request.rules.min_long_trades_ratio == 0.35
    assert request.rules.max_long_trades_ratio == 0.65

    with pytest.raises(ValidationError):
        AnalysisRequest(rules={"min_long_trades_ratio": 0.7, "max_long_trades_ratio": 0.6})


@pytest.mark.parametrize("payload", [
    {"rules": {"neutral_band_usd": 15}},
    {"exclude_test_accounts": False},
])
def test_non_configurable_legacy_inputs_are_rejected(payload):
    with pytest.raises(ValidationError):
        AnalysisRequest(**payload)


def test_old_position_management_rule_is_rejected():
    with pytest.raises(ValidationError):
        AnalysisRequest(rules={"max_top_day_concentration": 0.5})


def test_analysis_request_accepts_personal_candidate_list_switch():
    request = AnalysisRequest(personal_candidate_list=True)

    assert request.personal_candidate_list is True


def test_analysis_request_accepts_news_candidate_list_switch():
    request = AnalysisRequest(news_candidate_list=True)

    assert request.news_candidate_list is True
