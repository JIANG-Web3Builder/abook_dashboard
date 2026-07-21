<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { BookAnalyticsPayload } from '../types'

const props = defineProps<{ analytics: BookAnalyticsPayload | null; loading: boolean; activeTab: string }>()
const chart = ref<HTMLElement | null>(null)
const cumulativeChart = ref<HTMLElement | null>(null)
const dailyChart = ref<HTMLElement | null>(null)
let instance: echarts.ECharts | null = null
let cumulativeInstance: echarts.ECharts | null = null
let dailyInstance: echarts.ECharts | null = null
const distributionCharts = ref<HTMLElement[]>([])
let distributionInstances: echarts.ECharts[] = []
const books = ['abook', 'bbook']
const phases = ['selection', 'validation']
const leverageLabels = ['0–1x', '1–2x', '2–5x', '5–10x', '10–20x', '20–50x', '50–100x', '100–200x', '>200x']
const distributionBuckets = [
  { key: 'loss_over_1000', label: '亏损 > 1000', color: '#d95f66' },
  { key: 'loss_100_1000', label: '亏损 100–1000', color: '#ed7b82' },
  { key: 'loss_10_100', label: '亏损 10–100', color: '#f3a4a9' },
  { key: 'neutral', label: '中性 -10–10', color: '#7d8da3' },
  { key: 'profit_10_100', label: '盈利 10–100', color: '#8bd8bb' },
  { key: 'profit_100_1000', label: '盈利 100–1000', color: '#54d6a6' },
  { key: 'profit_over_1000', label: '盈利 > 1000', color: '#2eaf82' },
]

function money(value: unknown) { return Number(value || 0).toFixed(2) }
function pct(value: unknown) { return `${(Number(value || 0) * 100).toFixed(1)}%` }
function pnlClass(value: unknown) { return Number(value || 0) >= 0 ? 'positive' : 'negative' }
function bookLabel(book: string) { return book === 'abook' ? 'Abook' : 'Bbook' }
function phaseLabel(phase: string) { return phase === 'selection' ? '筛选期（5–6月）' : '验证期（7月）' }

function disposePnlCharts() {
  cumulativeInstance?.dispose()
  dailyInstance?.dispose()
  cumulativeInstance = null
  dailyInstance = null
}

function disposeDistributionCharts() {
  distributionInstances.forEach(chart => chart.dispose())
  distributionInstances = []
}

function renderDistributionCharts(analytics: BookAnalyticsPayload) {
  disposeDistributionCharts()
  books.forEach((book, index) => {
    const element = distributionCharts.value[index]
    if (!element) return
    const rows = analytics.pnl_structure?.[book as 'abook' | 'bbook']?.distribution_by_period || []
    const periods = [...new Set(rows.map((row: any) => row.period))]
    const counts = new Map(rows.map((row: any) => [`${row.period}:${row.key}`, row.accounts]))
    const chart = echarts.init(element)
    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: { type: 'scroll', textStyle: { color: '#9fb3c9' } },
      grid: { left: 58, right: 18, top: 42, bottom: 58 },
      xAxis: { type: 'category', data: periods.map(period => period === 'selection_total' ? '5–6月合计' : period), axisLabel: { color: '#8497ad', rotate: 20 } },
      yAxis: { type: 'value', name: '账户数', nameTextStyle: { color: '#8497ad' }, axisLabel: { color: '#8497ad' }, splitLine: { lineStyle: { color: '#20364e' } } },
      series: distributionBuckets.map(bucket => ({
        name: bucket.label,
        type: 'bar',
        stack: 'pnl-distribution',
        itemStyle: { color: bucket.color },
        data: periods.map(period => counts.get(`${period}:${bucket.key}`) ?? 0),
      })),
    })
    distributionInstances.push(chart)
  })
}

function renderPnlCharts(analytics: BookAnalyticsPayload) {
  if (!cumulativeChart.value || !dailyChart.value) return
  instance?.dispose()
  instance = null
  disposePnlCharts()
  const abook = analytics.pnl_structure?.abook?.daily_series || []
  const bbook = analytics.pnl_structure?.bbook?.daily_series || []
  const dates = [...new Set([...abook, ...bbook].map((row: any) => row.date))].sort()
  const valuesByDate = (rows: any[], field: string) => Object.fromEntries(rows.map(row => [row.date, row[field] ?? 0]))
  const aCumulative = valuesByDate(abook, 'cumulative_pnl')
  const bCumulative = valuesByDate(bbook, 'cumulative_pnl')
  const aDaily = valuesByDate(abook, 'pnl')
  const bDaily = valuesByDate(bbook, 'pnl')

  cumulativeInstance = echarts.init(cumulativeChart.value)
  cumulativeInstance.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { textStyle: { color: '#9fb3c9' } },
    grid: { left: 58, right: 58, top: 42, bottom: 28 },
    xAxis: { type: 'category', data: dates, axisLabel: { color: '#8497ad' } },
    yAxis: [
      { type: 'value', name: 'Bbook 客户 P&L', nameTextStyle: { color: '#ff8d91' }, axisLabel: { color: '#8497ad' }, splitLine: { lineStyle: { color: '#20364e' } } },
      { type: 'value', name: 'Abook 客户 P&L', nameTextStyle: { color: '#54d6a6' }, axisLabel: { color: '#8497ad' }, splitLine: { show: false } },
    ],
    series: [
      { name: 'Bbook 累计客户 P&L', type: 'line', yAxisIndex: 0, smooth: true, data: dates.map(date => bCumulative[date]), lineStyle: { color: '#ff8d91' }, areaStyle: { color: 'rgba(255,141,145,.08)' } },
      { name: 'Abook 累计客户 P&L', type: 'line', yAxisIndex: 1, smooth: true, data: dates.map(date => aCumulative[date]), lineStyle: { color: '#54d6a6' }, areaStyle: { color: 'rgba(84,214,166,.10)' } },
    ],
  })

  dailyInstance = echarts.init(dailyChart.value)
  dailyInstance.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { textStyle: { color: '#9fb3c9' } },
    grid: { left: 72, right: 72, top: 42, bottom: 28 },
    xAxis: { type: 'category', data: dates, axisLabel: { color: '#8497ad' } },
    yAxis: [
      { type: 'value', name: 'Bbook 每日客户 P&L', nameTextStyle: { color: '#ff8d91' }, axisLabel: { color: '#8497ad' }, splitLine: { lineStyle: { color: '#20364e' } } },
      { type: 'value', name: 'Abook 每日客户 P&L', nameTextStyle: { color: '#54d6a6' }, axisLabel: { color: '#8497ad' }, splitLine: { show: false } },
    ],
    series: [
      { name: 'Bbook 每日客户 P&L', type: 'line', yAxisIndex: 0, smooth: true, data: dates.map(date => bDaily[date]), lineStyle: { color: '#ff8d91' } },
      { name: 'Abook 每日客户 P&L', type: 'line', yAxisIndex: 1, smooth: true, data: dates.map(date => aDaily[date]), lineStyle: { color: '#54d6a6' } },
    ],
  })
}

async function renderChart() {
  await nextTick()
  if (!props.analytics) return
  if (props.activeTab === 'users') {
    instance?.dispose()
    instance = null
    disposeDistributionCharts()
    renderPnlCharts(props.analytics)
    renderDistributionCharts(props.analytics)
    return
  }
  disposeDistributionCharts()
  disposePnlCharts()
  if (!chart.value) return
  instance?.dispose()
  instance = echarts.init(chart.value)
  const analytics = props.analytics
  let x: string[] = []
  let series: any[] = []
  let yAxis: any = { type: 'value', name: props.activeTab === 'risk' ? '用户数' : '', axisLabel: { color: '#8497ad' }, nameTextStyle: { color: '#8497ad' }, splitLine: { lineStyle: { color: '#20364e' } } }
  if (props.activeTab === 'risk') {
    const abook = analytics.risk_exposure?.abook?.leverage_histogram || { labels: leverageLabels, counts: [] }
    const bbook = analytics.risk_exposure?.bbook?.leverage_histogram || { labels: leverageLabels, counts: [] }
    x = abook.labels?.length ? abook.labels : leverageLabels
    series = [
      { name: 'Abook 用户数', type: 'bar', data: abook.counts || [], itemStyle: { color: '#54d6a6' } },
      { name: 'Bbook 用户数', type: 'bar', data: bbook.counts || [], itemStyle: { color: '#ff8d91' } },
    ]
  } else if (props.activeTab === 'routing') {
    const profitRows = analytics.routing_quality?.company_profit_comparison || []
    x = profitRows.map((row: any) => row.date)
    yAxis = [
      { type: 'value', name: '公司利润', axisLabel: { color: '#8497ad' }, nameTextStyle: { color: '#8497ad' }, splitLine: { lineStyle: { color: '#20364e' } } },
      { type: 'value', name: '增量变化', axisLabel: { color: '#f0bd72' }, nameTextStyle: { color: '#f0bd72' }, splitLine: { show: false } },
    ]
    series = [
      { name: '基准公司利润', type: 'bar', data: profitRows.map((row: any) => row.baseline_company_profit), itemStyle: { color: '#8798ad' } },
      { name: '分流后公司利润', type: 'bar', data: profitRows.map((row: any) => row.after_routing_company_profit), itemStyle: { color: '#54d6a6' } },
      { name: '增量变化', type: 'line', yAxisIndex: 1, data: profitRows.map((row: any) => row.incremental_change), lineStyle: { color: '#f0bd72', width: 2 }, itemStyle: { color: '#f0bd72' } },
    ]
  } else {
    return
  }
  instance.setOption({ backgroundColor: 'transparent', tooltip: { trigger: 'axis' }, legend: { textStyle: { color: '#9fb3c9' } }, grid: { left: 58, right: props.activeTab === 'routing' ? 58 : 18, top: 42, bottom: 42 }, xAxis: { type: 'category', data: x, axisLabel: { color: '#8497ad' } }, yAxis, series })
}

watch(() => [props.analytics, props.activeTab], renderChart, { deep: true })
onBeforeUnmount(() => {
  instance?.dispose()
  disposePnlCharts()
  disposeDistributionCharts()
})
</script>

<template>
  <section class="panel">
    <div class="panel-head"><div><span class="kicker">BOOK ANALYTICS</span><h2>Abook / Bbook 量化分析</h2></div><span class="hint">仅实际 Abook 与 Bbook</span></div>
    <div v-if="loading" class="empty">加载 Book 分析…</div>
    <template v-else-if="analytics">
      <div v-if="activeTab === 'users'" class="phase-cards book-phase-overview">
        <article v-for="book in books" :key="book" class="book-card">
          <div class="book-section-title"><h3>{{ bookLabel(book) }} 分阶段 P&amp;L</h3><span class="hint">客户 P&amp;L 口径</span></div>
          <div v-for="phase in phases" :key="phase" class="metric-card">
            <span class="kicker">{{ phaseLabel(phase) }}</span>
            <div class="list-row"><span>盈利金额</span><b class="positive">{{ money(analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.positive_pnl) }}</b></div>
            <div class="list-row"><span>亏损金额</span><b class="negative">{{ money(analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.negative_pnl) }}</b></div>
            <div class="list-row"><span>净 P&amp;L</span><b :class="pnlClass(analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.net_pnl)">{{ money(analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.net_pnl) }}</b></div>
          </div>
        </article>
      </div>
      <div class="book-grid"><article v-for="book in books" :key="book" class="book-card"><h3>{{ bookLabel(book) }}</h3><p>筛选期客户净 P&amp;L <b :class="pnlClass(analytics.pnl_structure?.[book]?.selection?.net_pnl)">{{ money(analytics.pnl_structure?.[book]?.selection?.net_pnl) }}</b></p><p>验证期客户净 P&amp;L <b :class="pnlClass(analytics.pnl_structure?.[book]?.validation?.net_pnl)">{{ money(analytics.pnl_structure?.[book]?.validation?.net_pnl) }}</b></p><p>验证期盈利/亏损/中性 <b>{{ analytics.pnl_structure?.[book]?.validation?.profitable_accounts ?? 0 }} / {{ analytics.pnl_structure?.[book]?.validation?.loss_accounts ?? 0 }} / {{ analytics.pnl_structure?.[book]?.validation?.neutral_accounts ?? 0 }}</b></p><p v-if="book === 'bbook'">Bbook 公司 P&amp;L <b :class="pnlClass(analytics.pnl_structure?.[book]?.validation?.company_profit_if_current_book)">{{ money(analytics.pnl_structure?.[book]?.validation?.company_profit_if_current_book) }}</b></p><p v-else>Abook 公司 P&amp;L <b>不由客户 P&amp;L 推断</b></p><p>验证期最大回撤 <b class="negative">{{ money(analytics.pnl_structure?.[book]?.max_drawdown) }}</b></p><p>Top 5 客户 P&amp;L 绝对集中度 <b>{{ pct(analytics.pnl_structure?.[book]?.profit_concentration?.top_5?.absolute_share) }}</b></p></article></div>
      <template v-if="activeTab === 'users'"><div ref="cumulativeChart" class="book-chart"></div><div ref="dailyChart" class="book-chart"></div></template>
      <template v-else><p v-if="activeTab === 'risk'" class="hint">杠杆筛选恢复使用成交额 ÷ 当日最新余额；并发未平仓敞口作为补充估计，最终使用两者 P95 较高值。柱状图按用户 P95 杠杆分布统计。</p><div ref="chart" class="book-chart"></div></template>
      <div v-if="activeTab === 'users'" class="book-analysis-list"><div v-for="book in books" :key="book" class="book-analysis"><div class="book-section-title"><h3>{{ bookLabel(book) }} 内部盈利分析</h3><span class="hint">客户 P&amp;L 口径{{ book === 'bbook' ? '；公司 P&amp;L 为客户 P&amp;L 取负' : '；不推导公司利润' }}</span></div><div class="phase-cards"><article v-for="phase in phases" :key="phase" class="metric-card"><span class="kicker">{{ phaseLabel(phase) }}</span><strong :class="pnlClass(analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.net_pnl)">{{ money(analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.net_pnl) }}</strong><small>{{ analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.accounts ?? 0 }} 用户 · 赚 {{ analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.profitable_accounts ?? 0 }} · 亏 {{ analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.loss_accounts ?? 0 }} · 中性 {{ analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.neutral_accounts ?? 0 }}</small><small>盈利额 {{ money(analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.positive_pnl) }} · 亏损额 {{ money(analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.negative_pnl) }}</small><small>PF {{ money(analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.profit_factor) }} · 胜率 {{ pct(analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.win_rate) }}</small></article></div><h4>月度盈利表</h4><div class="table-scroll"><table><thead><tr><th>期间</th><th>用户</th><th>活跃</th><th>赚</th><th>亏</th><th>中性</th><th>盈利额</th><th>亏损额</th><th>净 P&amp;L</th><th>平均</th><th>中位数</th><th>PF</th><th>胜率</th></tr></thead><tbody><tr v-for="row in analytics.pnl_structure?.[book]?.monthly || []" :key="row.month"><td>{{ row.month === 'selection_total' ? '5–6月合计' : row.month }}</td><td>{{ row.accounts }}</td><td>{{ row.active_accounts }}</td><td class="positive">{{ row.profitable_accounts }}</td><td class="negative">{{ row.loss_accounts }}</td><td>{{ row.neutral_accounts }}</td><td class="positive">{{ money(row.positive_pnl) }}</td><td class="negative">{{ money(row.negative_pnl) }}</td><td :class="pnlClass(row.net_pnl)">{{ money(row.net_pnl) }}</td><td>{{ money(row.average_pnl) }}</td><td>{{ money(row.median_pnl) }}</td><td>{{ money(row.profit_factor) }}</td><td>{{ pct(row.win_rate) }}</td></tr></tbody></table></div><h4>P&amp;L 用户分布（按月份/阶段）</h4><div ref="distributionCharts" class="book-chart distribution-chart"></div><div class="table-scroll"><table><thead><tr><th>期间</th><th>区间</th><th>用户数</th><th>占比</th><th>区间净 P&amp;L</th></tr></thead><tbody><tr v-for="row in analytics.pnl_structure?.[book]?.distribution_by_period || []" :key="row.period + '-' + row.key"><td>{{ row.period === 'selection_total' ? '5–6月合计' : row.period }}</td><td>{{ row.label }}</td><td>{{ row.accounts }}</td><td>{{ pct(row.account_rate) }}</td><td :class="pnlClass(row.net_pnl)">{{ money(row.net_pnl) }}</td></tr></tbody></table></div><h4>用户风格盈利表</h4><div class="table-scroll"><table><thead><tr><th>期间</th><th>风格</th><th>用户</th><th>活跃</th><th>赚</th><th>亏</th><th>中性</th><th>盈利额</th><th>亏损额</th><th>净 P&amp;L</th><th>胜率</th></tr></thead><tbody><tr v-for="row in analytics.pnl_structure?.[book]?.style_breakdown || []" :key="row.period + '-' + row.style"><td>{{ row.period === 'selection_total' ? '5–6月合计' : row.period }}</td><td>{{ row.style }}</td><td>{{ row.accounts }}</td><td>{{ row.active_accounts }}</td><td class="positive">{{ row.profitable_accounts }}</td><td class="negative">{{ row.loss_accounts }}</td><td>{{ row.neutral_accounts }}</td><td class="positive">{{ money(row.positive_pnl) }}</td><td class="negative">{{ money(row.negative_pnl) }}</td><td :class="pnlClass(row.net_pnl)">{{ money(row.net_pnl) }}</td><td>{{ pct(row.win_rate) }}</td></tr></tbody></table></div><div class="two-col"><article class="inset"><h4>Top 10 盈利用户（验证期）</h4><div v-for="row in analytics.pnl_structure?.[book]?.top_accounts?.winners || []" :key="row.login" class="list-row"><span>{{ row.platform }} / {{ row.login }} · {{ row.style }}</span><b class="positive">{{ money(row.pnl) }}（{{ pct(row.share) }}）</b></div></article><article class="inset"><h4>Top 10 亏损用户（验证期）</h4><div v-for="row in analytics.pnl_structure?.[book]?.top_accounts?.losers || []" :key="row.login" class="list-row"><span>{{ row.platform }} / {{ row.login }} · {{ row.style }}</span><b class="negative">{{ money(row.pnl) }}（{{ pct(row.share) }}）</b></div></article></div><h4>品种客户盈亏热力图（辅助）</h4><div class="table-scroll"><table><thead><tr><th>品种</th><th>交易笔数</th><th>交易量</th><th>市场 P&amp;L</th></tr></thead><tbody><tr v-for="row in analytics.user_structure?.[book]?.symbol_heatmap || []" :key="row.symbol"><td>{{ row.symbol }}</td><td>{{ row.trade_count }}</td><td>{{ money(row.volume) }}</td><td :class="pnlClass(row.market_pnl)">{{ money(row.market_pnl) }}</td></tr></tbody></table></div></div></div>
      <div v-else-if="activeTab === 'risk'" class="two-col metric-columns"><article v-for="book in books" :key="book" class="book-card"><h3>{{ bookLabel(book) }} 风险统计</h3><p>杠杆率 P50 / P75 / P95 / 最大 <b>{{ money(analytics.risk_exposure?.[book]?.leverage_p50) }} / {{ money(analytics.risk_exposure?.[book]?.leverage_p75) }} / {{ money(analytics.risk_exposure?.[book]?.leverage_p95) }} / {{ money(analytics.risk_exposure?.[book]?.leverage_max) }}</b></p><p>极端杠杆用户 <b>{{ analytics.risk_exposure?.[book]?.extreme_leverage_accounts ?? 0 }}（{{ pct(analytics.risk_exposure?.[book]?.extreme_leverage_rate) }}）</b></p><p>筛选期最大回撤 <b class="negative">{{ money(analytics.risk_exposure?.[book]?.selection_max_drawdown) }}</b></p><p>验证期最大回撤 <b class="negative">{{ money(analytics.risk_exposure?.[book]?.validation_max_drawdown) }}</b></p><p>验证期最差单日客户 P&amp;L <b class="negative">{{ money(analytics.risk_exposure?.[book]?.validation_worst_daily_customer_pnl) }}</b></p><p>验证期亏损尾部占比 <b>{{ pct(analytics.risk_exposure?.[book]?.validation_loss_tail_5_share) }}</b></p></article></div>
      <div v-else-if="activeTab === 'routing'" class="panel inset"><h3>公司利润对比</h3><div ref="chart" class="book-chart"></div></div>
    </template>
    <div v-else class="empty">切换到分析 Tab 以加载数据</div>
  </section>
</template>
