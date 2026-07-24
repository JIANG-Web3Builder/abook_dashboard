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
    books = _source("components/BookPerformance.vue")
    sidebar = (FRONTEND / "src" / "components" / "FilterSidebar.vue").read_text()
    assert "总览" in app
    assert "盈亏结构" not in app
    assert "用户结构" in app
    assert "风险与分流" in app
    assert "风险敞口" in books
    assert "分流质量" in books
    assert "参数寻优" not in app
    assert "个人候选名单" in sidebar
    assert "马丁" in sidebar
    for rule in [
        "min_trades", "min_payoff_ratio", "max_top1_day_profit_contribution",
        "max_leverage_p95_ratio", "excluded_martingale_levels",
        "min_long_trades_ratio", "max_long_trades_ratio",
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
    assert "min_payoff_ratio: 0.6" in app
    assert "max_top1_day_profit_contribution: 0.3" in app
    assert "max_leverage_p95_ratio: 2000" in app
    assert "max_high_leverage_holding_seconds: 60" in app
    assert "platforms: ['mt4', 'mt5', 'hh_mt5']" in app
    assert "min_long_trades_ratio: 0.3" in app
    assert "max_long_trades_ratio: 0.7" in app


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


def test_user_structure_keeps_long_pnl_axis_labels_and_dates_symbol_charts():
    app = _source("App.vue")
    books = _source("components/BookPerformance.vue")
    assert "containLabel: true" in books
    assert "left: 96" in books
    assert ":request=" in app
    assert "symbolTimeRange" in books
    assert "筛选期" in books
    assert "验证期" in books


def test_frontend_combines_risk_and_routing_into_one_book_tab():
    app = _source("App.vue")
    books = _source("components/BookPerformance.vue")
    assert "id: 'risk-routing'" in app
    assert "label: '风险与分流'" in app
    assert "routingChart" in books
    assert "activeTab === 'risk-routing'" in books
    assert "风险敞口" in books
    assert "分流质量" in books


def test_chart_tabs_render_cached_analytics_when_their_components_mount_again():
    books = _source("components/BookPerformance.vue")
    direction = _source("components/DirectionPanel.vue")

    assert "watch(() => [props.analytics, props.activeTab], renderChart, { deep: true, immediate: true })" in books
    assert "}), { deep: true, immediate: true })" in direction


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


def test_frontend_does_not_expose_remote_refresh_controls():
    sidebar = (ROOT / "frontend/src/components/FilterSidebar.vue").read_text()
    api = (ROOT / "frontend/src/api.ts").read_text()
    app = (ROOT / "frontend/src/App.vue").read_text()

    assert "刷新全部数据" not in sidebar
    assert "refreshSnapshots" not in api
    assert "refresh-snapshots" not in api
    assert "refreshLocalSnapshots" in api
    assert "refresh-snapshots" in sidebar
    assert "snapshotRefreshing" in app
    assert "snapshotNeedsRefresh" in app
    assert "重建当前筛选期快照" in sidebar
    assert "应用筛选与验证" in sidebar


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

    assert "2026-07-22" in app
    for rule in [
        "min_trades", "min_win_rate", "min_profit_factor",
        "min_payoff_ratio", "max_top1_day_profit_contribution",
        "max_leverage_p95_ratio", "excluded_martingale_levels", "min_long_trades_ratio",
    ]:
        assert rule in funnel
    assert "min_active_days" not in funnel
    assert "selection_months_positive" not in funnel
    assert "筛选标准" in funnel
    assert "direction_balance_passed" in funnel
    assert "long_trades_ratio" in funnel
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


def test_frontend_exposes_direction_analytics_as_the_fifth_lazy_tab():
    app = _source("App.vue")
    api = _source("api.ts")
    types = _source("types.ts")
    panel = (FRONTEND / "src/components/DirectionPanel.vue").read_text()

    assert "direction" in types
    assert "多空分向" in app
    assert "DirectionPanel" in app
    assert "loadDirection" in app
    assert "/api/abook/direction-analytics" in api
    assert "matched.profit" in panel
    for label in ["both_pass", "long_only_pass", "short_only_pass", "insufficient_side", "总 Abook"]:
        assert label in panel
    for field in ["pnl_distribution", "cumulative_pnl"]:
        assert field in panel
    assert "personal_candidate" not in panel


def test_frontend_exposes_newcomer_rolling_screen_tab():
    app = _source("App.vue")
    api = _source("api.ts")
    types = _source("types.ts")
    panel = (FRONTEND / "src/components/NewcomerPanel.vue").read_text()

    assert "newcomer" in types
    assert "新人滚动筛" in app
    assert "NewcomerPanel" in app
    assert "runNewcomer" in app
    assert "newcomerRequest" in app
    assert "/api/abook/newcomer-analytics" in api
    assert "/api/abook/newcomer-account" in api
    assert "运行新人筛选" in panel
    assert "不会自动查询" in panel
    assert "个人 as-of" in panel
    assert "短历史观察" in panel
    assert "跳过无成交" in panel
    assert "trades" in panel
    assert "单用户敏感性" in panel
    assert "emit('run')" in panel
    assert "cumulativeChart" in panel
    assert "distributionChart" in panel
    assert "入选后 Top" in panel


def test_direction_panel_requires_explicit_run_button_dynamic_phase_table_and_sorting():
    app = _source("App.vue")
    panel = (FRONTEND / "src/components/DirectionPanel.vue").read_text()

    assert "运行分向筛选" in panel
    assert "emit('run')" in panel
    assert "分向筛选参数" in panel
    assert "不会自动查询" in panel
    assert "directionRequest" in app
    assert "@run=\"runDirection\"" in app
    assert "directionRequest.value.rules = { ...defaultRules }" in app
    assert "long_trades_ratio: '多空比例未达标'" in panel
    assert "sortBy" in panel
    assert "sortDirection" in panel
    assert "toggleSort" in panel
    assert "account.long?.[phase]" in panel
    assert "account.short?.[phase]" in panel


def test_direction_panel_splits_book_side_phase_and_keeps_only_material_pnl_accounts():
    panel = _source("components/DirectionPanel.vue")
    types = _source("types.ts")

    assert "books" in panel
    assert "Abook" in panel and "Bbook" in panel
    assert "book_sets" in panel and "book_sets" in types
    assert "Math.abs" in panel
    assert "side_pnl" in panel
    assert "Math.abs(number(longMetric.side_pnl)) > 10 || Math.abs(number(shortMetric.side_pnl)) > 10" in panel
    assert "cumulativeChart" in panel
    assert "echarts" in panel
    for removed in ["style_breakdown", "holding_duration_bins", "trade_volume_bins"]:
        assert removed not in panel
    for metric in ["平均 P&amp;L", "中位数 P&amp;L", "最差单日", "Top 5 P&amp;L 集中度"]:
        assert metric in panel


def test_direction_panel_uses_two_pnl_bars_eight_row_pagination_and_search_action():
    panel = _source("components/DirectionPanel.vue")

    assert "pnlDistributionCharts" in panel
    assert "Long" in panel and "Short" in panel
    assert "type: 'bar'" in panel
    assert "pageSize = 8" in panel
    assert "pagedRows" in panel
    assert "pageCount" in panel
    assert "上一页" in panel and "下一页" in panel
    assert "重新运行分向筛选" in panel
    assert "emit('run')" in panel
    assert "showSymbol: false" in panel
    assert "smooth: true" in panel
    assert "cumulative_pnl?.full" in panel


def test_direction_panel_filters_or_compares_abook_bbook_by_direction():
    panel = _source("components/DirectionPanel.vue")

    assert "activeBook" in panel
    assert "路由<select" in panel
    assert "books" in panel
    assert "long_pass" in panel and "short_pass" in panel
    assert "Abook" in panel and "Bbook" in panel
    assert "cumulativeCharts" in panel
    assert "series" in panel
    assert "account.book" in panel


def test_direction_panel_charts_follow_active_set_and_expose_material_pnl_toggle():
    panel = _source("components/DirectionPanel.vue")

    assert "payloadFor(book, activeSet.value)" in panel
    assert "hideSmallPnl" in panel
    assert "隐藏小额 ±10" in panel
    assert "显示 {{ rows.length }} / {{ filteredRows.length }}" in panel


def test_direction_pass_kpis_open_filtered_user_list_for_drawer_navigation():
    panel = _source("components/DirectionPanel.vue")

    assert "selectedPassSet" in panel
    assert "selectPassList(book, which)" in panel
    assert "通过用户列表" in panel
    assert "emit('open', account)" in panel


def test_account_drawer_exposes_direction_summary_and_applied_result():
    drawer = _source("components/AccountDrawer.vue")
    app = _source("App.vue")
    api = _source("api.ts")
    types = _source("types.ts")

    for label in ["多空分向摘要", "Long 交易比例", "Short 交易比例", "Long 胜率", "Short 胜率", "matched P&amp;L"]:
        assert label in drawer
    assert "directionAccount" in drawer
    assert "最近一次已运行分向筛选结果" in drawer
    assert "validation_start" in api and "validation_end" in api
    assert "direction_summary" in types
    assert "selectedDirectionAccount" in app


def test_direction_panel_places_filters_directly_above_account_list():
    panel = _source("components/DirectionPanel.vue")

    toolbar_start = panel.index("direction-toolbar")
    table_start = panel.index("direction-account-table")
    assert panel.index("phase-cards") < toolbar_start < table_start
    assert "phase-cards" not in panel[toolbar_start:table_start]


def test_phase_divider_is_dashed_without_text_and_risk_tab_has_no_account_list():
    divider = _source("phaseDivider.ts")
    books = _source("components/BookPerformance.vue")

    assert "formatter: '筛选期  |  验证期'" not in divider
    assert "show: false" in divider
    assert "风险账户列表" not in books
    assert "const riskAccounts" not in books


def test_direction_panel_removes_symbol_heatmap_and_all_profit_counts_show_precision():
    direction = _source("components/DirectionPanel.vue")
    books = _source("components/BookPerformance.vue")

    assert "symbolHeatmapChart" not in direction
    assert "品种热力" not in direction
    assert "precision(" in direction
    assert "precision(" in books
    assert "precision {{" in direction
    assert "precision {{" in books
    assert "<th>precision</th>" in books


def test_direction_account_table_labels_all_three_direction_metrics():
    panel = _source("components/DirectionPanel.vue")

    assert panel.count('class="table-metric-label">PF</span>') == 2


def test_direction_toolbar_removes_duplicate_search_button():
    panel = _source("components/DirectionPanel.vue")
    toolbar = panel.split('class="direction-toolbar"', 1)[1].split('</div>', 1)[0]

    assert "搜索 / 重新计算" not in toolbar
    assert "重新运行分向筛选" in panel


def test_direction_toolbar_can_filter_abook_or_bbook_across_route_dependent_views():
    panel = _source("components/DirectionPanel.vue")

    assert "activeBook" in panel
    assert "displayBooks" in panel
    assert '<option value="abook">Abook</option>' in panel
    assert '<option value="bbook">Bbook</option>' in panel
    assert "account.book !== activeBook.value" in panel


def test_direction_panel_does_not_expose_redundant_side_selector():
    panel = _source("components/DirectionPanel.vue")

    assert 'v-model="side"' not in panel


def test_book_performance_renders_pnl_distribution_as_period_bars():
    books = _source("components/BookPerformance.vue")
    assert "distributionPanelCharts" in books
    assert "distribution_by_period" in books
    assert "distribution-grid" in books
    assert "type: 'bar'" in books


def test_book_performance_renders_separate_period_profit_distribution_bars_with_tooltips():
    books = _source("components/BookPerformance.vue")
    assert "distributionPeriods" in books
    assert "distribution-grid" in books
    assert "formatter: (params" in books
    assert "account_rate" in books
    assert "net_pnl" in books
    assert "stack: 'pnl-distribution'" not in books


def test_book_performance_replaces_monthly_distribution_tables_with_two_grouped_book_charts():
    books = _source("components/BookPerformance.vue")

    assert "distributionDetailCharts" in books
    assert "renderDistributionDetailCharts" in books
    assert "stack: 'pnl-distribution'" not in books
    assert "barGap: '10%'" in books
    assert "全部盈利用户 P&amp;L 占比" in books
    assert "全部亏损用户 P&amp;L 占比" in books


def test_book_performance_renders_asset_market_pnl_bars_before_distribution_without_symbol_table():
    books = _source("components/BookPerformance.vue")
    assert "symbolPanelCharts" in books
    assert "symbol_heatmap" in books
    assert "−市场 P&amp;L" in books
    assert "Math.abs" in books
    assert ".slice(0, 20)" in books
    assert "{{ bookLabel(book) }} 品种客户盈亏" in books
    assert "品种客户盈亏热力图（辅助）" not in books
    assert books.index("品种客户盈亏") < books.index("P&amp;L 用户分布（按月份/阶段）")


def test_pnl_distribution_uses_right_axis_for_neutral_user_counts():
    books = _source("components/BookPerformance.vue")

    assert "name: '有效用户数'" in books
    assert "name: '中性/无效用户数'" in books
    assert "name: '有效用户数'" in books and "yAxisIndex: 0" in books
    assert "name: '中性/无效用户数'" in books and "yAxisIndex: 1" in books


def test_pnl_distribution_cards_use_two_columns_per_row():
    styles = _source("style.css")

    assert ".distribution-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));" in styles


def test_dashboard_layout_fills_available_width_without_page_overflow():
    styles = _source("style.css")

    assert "body { margin: 0; min-width: 0; overflow-x: hidden;" in styles
    assert ".app-shell { width: 100%;" in styles
    assert ".layout { display: grid; grid-template-columns: 290px minmax(0, 1fr); gap: 22px; width: 100%;" in styles
    assert ".content, .panel, .two-col" in styles


def test_book_performance_renders_company_profit_comparison_as_chart():
    books = _source("components/BookPerformance.vue")
    assert "baseline_company_profit" in books
    assert "after_routing_company_profit" in books
    assert "incremental_change" in books
    assert "name: '基准公司利润'" in books
    assert "name: '分流后公司利润'" in books
    assert "name: '增量变化'" in books
    assert "v-for=\"row in analytics.routing_quality?.company_profit_comparison" not in books


def test_all_cross_phase_time_charts_use_dynamic_selection_validation_divider():
    books = _source("components/BookPerformance.vue")
    direction = _source("components/DirectionPanel.vue")
    app = _source("App.vue")
    divider = _source("phaseDivider.ts")

    assert "phaseDividerMarkLine" in divider
    assert books.count("phaseDividerMarkLine") >= 4
    assert direction.count("phaseDividerMarkLine") >= 1
    assert ':request="directionRequest"' in app


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
