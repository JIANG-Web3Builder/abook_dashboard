from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def _source(name: str) -> str:
    return (FRONTEND / "src" / name).read_text()


def test_vite_project_and_build_output_are_present():
    package = (FRONTEND / "package.json").read_text()
    index = (ROOT / "static" / "index.html").read_text()
    assert '"build": "vite build --outDir ../static"' in package
    assert '"vue"' in package
    assert '"echarts"' in package
    assert 'src="/assets/app.js"' in index
    assert 'href="/assets/styles.css"' in index
    assert (ROOT / "static" / "app.js").exists()
    assert (ROOT / "static" / "styles.css").exists()


def test_built_frontend_does_not_send_removed_rule_fields():
    bundle = (ROOT / "static" / "app.js").read_text()
    for removed_rule in [
        "min_active_months",
        "payoff_link_factor",
        "min_return_drawdown_ratio",
        "require_selection_net_positive",
    ]:
        assert removed_rule not in bundle


def test_frontend_contains_filter_controls_and_all_phase_six_tabs():
    app = _source("App.vue")
    sidebar = (FRONTEND / "src" / "components" / "FilterSidebar.vue").read_text()
    assert "总览" in app
    assert "盈亏结构" not in app
    assert "用户结构" in app
    assert "风险敞口" in app
    assert "分流质量" in app
    assert "参数寻优" not in app
    assert "个人候选名单" in sidebar
    assert "马丁" in sidebar
    for rule in [
        "min_trades", "min_payoff_ratio", "max_top1_day_profit_contribution",
        "max_leverage_p95_ratio", "excluded_martingale_levels", "enable_r4",
        "r4_min_passing_weeks",
    ]:
        assert rule in sidebar
    assert "min_active_days" not in sidebar
    assert "min_avg_daily_profit" not in sidebar
    assert "max_daily_profit_month_contribution" not in sidebar
    assert "min_avg_profit" not in sidebar
    assert "7月新用户" in (FRONTEND / "src/components/AccountsTable.vue").read_text()
    assert "confirmed_users" in sidebar
    assert "confirmed_level_counts" in sidebar
    assert "勾选 = 阻断该等级" in sidebar
    assert "当前平台快照不完整" in sidebar
    assert "suspected_users" in sidebar
    assert "疑似马丁" in sidebar


def test_frontend_default_rules_match_backend_july_tuned_profile():
    app = _source("App.vue")
    assert "min_trades: 75" in app
    assert "min_win_rate: 0.5" in app
    assert "min_profit_factor: 1.25" in app
    assert "min_payoff_ratio: 0.4" in app
    assert "max_top1_day_profit_contribution: 0.3" in app
    assert "max_leverage_p95_ratio: 5000" in app
    assert "max_high_leverage_holding_seconds: 300" in app
    assert "platforms: ['mt4', 'mt5', 'hh_mt5']" in app
    assert "enable_r4: false" in app


def test_frontend_uses_new_analysis_actions_and_book_lazy_load():
    api = _source("api.ts")
    app = _source("App.vue")
    assert "/api/abook/analysis" in api
    assert "/api/abook/sweep" not in api
    assert "/api/abook/export" in api
    assert "/api/abook/book-analytics" in api
    assert "loadBook" in app
    assert "AbookAnalysis" in app
    assert "MisjudgeAnalysis" not in app
    assert "SelectionFunnel" in app
    assert "AccountsTable" in app
    assert "AccountDrawer" in app


def test_frontend_reuses_analysis_token_for_book_analytics():
    types = _source("types.ts")
    api = _source("api.ts")
    app = _source("App.vue")
    assert "analysis_token" in types
    assert "analysis_token: analysisToken" in api
    assert "data.value.analysis_token" in app


def test_frontend_reports_empty_or_malformed_json_responses_with_endpoint_context():
    api = _source("api.ts")
    assert "async function readJsonResponse" in api
    assert "const text = await response.text()" in api
    assert "Empty JSON response from ${path}" in api
    assert "Invalid JSON response from ${path}" in api
    assert "JSON.parse(text)" in api


def test_frontend_exposes_book_metrics_and_account_paging():
    books = (FRONTEND / "src" / "components" / "BookPerformance.vue").read_text()
    accounts = (FRONTEND / "src" / "components" / "AccountsTable.vue").read_text()
    assert "Core + observation" not in books
    for metric in ["max_drawdown", "symbol_heatmap", "company_profit_comparison", "monthly", "distribution", "style_breakdown", "top_accounts"]:
        assert metric in books
    assert "daily_turnover" not in books
    assert "Turnover" not in books
    assert "leverage_histogram" in books
    assert 'type: \'bar\'' in books
    for control in ["sortBy", "pageSize", "page"]:
        assert control in accounts


def test_user_structure_has_independent_cumulative_and_daily_pnl_charts():
    books = _source("components/BookPerformance.vue")
    assert "cumulativeChart" in books
    assert "dailyChart" in books
    assert "Abook 累计客户 P&L" in books
    assert "Bbook 累计客户 P&L" in books
    assert "Abook 每日客户 P&L" in books
    assert "Bbook 每日客户 P&L" in books
    assert "yAxis: [" in books
    assert "yAxisIndex: 1" in books
    assert books.count("yAxis: [") >= 2
    assert "Abook 每日客户 P&L" in books and "Bbook 每日客户 P&L" in books
    assert "daily_series" in books


def test_frontend_has_martingale_drawer_and_responsive_sidebar_contract():
    drawer = (FRONTEND / "src" / "components" / "AccountDrawer.vue").read_text()
    css = (FRONTEND / "src" / "style.css").read_text()
    assert "layer1" in drawer and "layer5" in drawer
    assert "martingale_risk_level" in drawer
    assert "confirmed_windows" in drawer
    assert "martingale_detection_status" in drawer
    assert "martingale_detection_status" in (FRONTEND / "src/components/AccountsTable.vue").read_text()
    assert "max-height: calc(100vh - 36px)" in css
    assert "overflow-y: auto" in css


def test_account_drawer_de_extreme_rows_scope_v_if_after_v_for():
    drawer = (FRONTEND / "src" / "components" / "AccountDrawer.vue").read_text()

    assert '<template v-for="item in deExtremeItems" :key="item[0]">' in drawer
    assert '<tr v-if="detail.de_extreme?.[item[0]] !== undefined">' in drawer
    assert '<tr v-for="item in deExtremeItems"' not in drawer


def test_bbook_phase_pnl_is_explicit_in_overview_and_user_structure():
    overview = _source("components/AbookAnalysis.vue")
    user_structure = _source("components/BookPerformance.vue")

    assert "Bbook 分析" in overview
    for label in ["盈利金额", "亏损金额", "净 P&amp;L"]:
        assert label in overview
        assert label in user_structure


def test_frontend_exposes_refresh_all_data_button_and_request_contract():
    sidebar = (ROOT / "frontend/src/components/FilterSidebar.vue").read_text()
    api = (ROOT / "frontend/src/api.ts").read_text()
    app = (ROOT / "frontend/src/App.vue").read_text()

    assert "刷新全部数据" in sidebar
    assert "refreshSnapshots" in api
    assert "refresh-snapshots" in api
    assert "refreshing" in app
    assert "bookData.value = null" in app


def test_frontend_exposes_abook_analysis_and_account_detail_contract():
    component_path = ROOT / "frontend/src/components/AbookAnalysis.vue"
    assert component_path.exists()
    component = component_path.read_text()
    app = (ROOT / "frontend/src/App.vue").read_text()
    api = (ROOT / "frontend/src/api.ts").read_text()

    for text in ["Abook 分析", "筛选期", "验证期", "盈利金额", "亏损金额", "净 P&amp;L", "client_net_pnl"]:
        assert text in component
    assert "MisjudgeAnalysis" not in app
    assert "fetchAccountDetail" in api
    drawer = (ROOT / "frontend/src/components/AccountDrawer.vue").read_text()
    assert "metrics" in drawer and "de_extreme" in drawer and "markout" in drawer
    assert "历史交易明细" not in drawer


def test_frontend_exposes_pnl_audit_and_funnel_criteria_contract():
    app = (ROOT / "frontend/src/App.vue").read_text()
    funnel = (ROOT / "frontend/src/components/SelectionFunnel.vue").read_text()
    analysis = (ROOT / "frontend/src/components/AbookAnalysis.vue").read_text()

    assert "2026-07-16" in app
    for rule in [
        "min_trades", "min_win_rate", "min_profit_factor",
        "min_payoff_ratio", "max_top1_day_profit_contribution",
        "max_leverage_p95_ratio", "excluded_martingale_levels",
    ]:
        assert rule in funnel
    assert "min_active_days" not in funnel
    assert "selection_months_positive" not in funnel
    assert "R4 独立通道" in funnel
    assert "r4_thresholds" in funnel
    assert "筛选标准" in funnel
    assert "selection_client_net_pnl" in analysis
    assert "june_client_net_pnl" in analysis
    assert "5月 P&amp;L" in analysis and "6月 P&amp;L" in analysis and "7月 P&amp;L" in analysis
    assert "may_client_net_pnl" in analysis
    assert "Bbook 公司影响" not in analysis
    assert "profit_overview?.pnl_basis" in analysis


def test_frontend_uses_complete_population_for_overview_precision_and_book_analytics():
    app = _source("App.vue")
    analysis = _source("components/AbookAnalysis.vue")
    assert "population_accounts" in app
    assert "population_accounts" in analysis
    assert "P&amp;L &gt; 0 / Abook 总人数" in analysis


def test_book_performance_renders_pnl_distribution_as_stacked_bars():
    books = _source("components/BookPerformance.vue")
    assert "distributionCharts" in books
    assert "distribution_by_period" in books
    assert "stack: 'pnl-distribution'" in books
    assert "type: 'bar'" in books


def test_book_performance_renders_company_profit_comparison_as_chart():
    books = _source("components/BookPerformance.vue")
    assert "baseline_company_profit" in books
    assert "after_routing_company_profit" in books
    assert "incremental_change" in books
    assert "name: '基准公司利润'" in books
    assert "name: '分流后公司利润'" in books
    assert "name: '增量变化'" in books
    assert "v-for=\"row in analytics.routing_quality?.company_profit_comparison" not in books


def test_frontend_account_lists_sort_and_drawer_hides_history_orders():
    analysis = (FRONTEND / "src/components/AbookAnalysis.vue").read_text()
    drawer = (FRONTEND / "src/components/AccountDrawer.vue").read_text()

    assert "<details" in analysis
    assert "sortBy" in analysis
    assert "sortDirection" in analysis
    for label in ["最大盈利日贡献率", "前三盈利日贡献率", "最大盈利订单贡献率", "最佳品种贡献率", "原始净利润", "去掉最大盈利日", "Entry +1s / +5s", "Matched trades 平均 bps"]:
        assert label in drawer
    assert "历史交易明细" not in drawer


def test_frontend_bbook_leakage_has_reason_tags_and_reuses_account_drawer():
    analysis = (FRONTEND / "src" / "components" / "AbookAnalysis.vue").read_text()

    assert "Bbook 原因" in analysis
    assert "bbook_reason_tags" in analysis
    assert "emit('open'" in analysis
    assert "leakageAccount" in analysis


def test_frontend_closes_account_drawer_only_when_backdrop_is_clicked():
    app = (FRONTEND / "src/App.vue").read_text()
    drawer = (FRONTEND / "src/components/AccountDrawer.vue").read_text()
    assert "preserveAccount" in app
    assert "selectedAccount.value = null" not in app.split("async function loadAnalysis", 1)[1].split("async function openAccount", 1)[0]
    assert '@click.self="emit(\'close\')"' in drawer
    assert '@click.stop' in drawer
    assert 'class="close"' not in drawer
