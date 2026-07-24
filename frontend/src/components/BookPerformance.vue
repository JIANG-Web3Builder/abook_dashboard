<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { AccountRow, BookAnalyticsPayload, RequestModel } from '../types'
import { phaseDividerMarkLine } from '../phaseDivider'

const props = defineProps<{ analytics: BookAnalyticsPayload | null; loading: boolean; activeTab: string; request: RequestModel; accounts?: AccountRow[] }>()
const emit = defineEmits<{ (event: 'open', account: AccountRow): void }>()
const chart = ref<HTMLElement | null>(null)
const routingChart = ref<HTMLElement | null>(null)
const cumulativeChart = ref<HTMLElement | null>(null)
const dailyChart = ref<HTMLElement | null>(null)
let instance: echarts.ECharts | null = null
let routingInstance: echarts.ECharts | null = null
let cumulativeInstance: echarts.ECharts | null = null
let dailyInstance: echarts.ECharts | null = null
const distributionPanelCharts = ref<HTMLElement[]>([])
let distributionInstances: echarts.ECharts[] = []
const distributionDetailCharts = ref<HTMLElement[]>([])
let distributionDetailInstances: echarts.ECharts[] = []
const symbolPanelCharts = ref<HTMLElement[]>([])
let symbolInstances: echarts.ECharts[] = []
const books = ['abook', 'bbook']
const phases = ['selection', 'validation']
const symbolTimeRange = computed(() => `筛选期 ${props.request.selection.start}～${props.request.selection.end}；验证期 ${props.request.validation.start}～${props.request.validation.end}`)
const distributionPeriods = computed(() => {
  const periods = new Set<string>()
  books.forEach(book => {
    const rows = props.analytics?.pnl_structure?.[book as 'abook' | 'bbook']?.distribution_by_period || []
    rows.forEach((row: any) => periods.add(row.period))
  })
  return [...periods].map(period => ({
    key: period,
    label: period === 'selection_total' ? '5–6月合计' : period,
  }))
})
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

function openSummaryAccount(row: Record<string, any>) {
  const account = (props.accounts || []).find(item => item.platform === row.platform && item.login === Number(row.login))
  if (account) emit('open', account)
}

function money(value: unknown) { return Number(value || 0).toFixed(2) }
function pct(value: unknown) { return `${(Number(value || 0) * 100).toFixed(1)}%` }
function precision(profitable: unknown, total: unknown) {
  return pct(Number(profitable || 0) / Math.max(1, Number(total || 0)))
}
function pnlClass(value: unknown) { return Number(value || 0) >= 0 ? 'positive' : 'negative' }
function bookLabel(book: string) { return book === 'abook' ? 'Abook' : 'Bbook' }
function phaseLabel(phase: string) { return phase === 'selection' ? '筛选期（5–6月）' : '验证期（7月）' }

function disposePnlCharts() {
  cumulativeInstance?.dispose()
  dailyInstance?.dispose()
  cumulativeInstance = null
  dailyInstance = null
}

function disposeRoutingChart() {
  routingInstance?.dispose()
  routingInstance = null
}

function disposeDistributionCharts() {
  distributionInstances.forEach(chart => chart.dispose())
  distributionInstances = []
}

function disposeDistributionDetailCharts() {
  distributionDetailInstances.forEach(chart => chart.dispose())
  distributionDetailInstances = []
}

function disposeSymbolCharts() {
  symbolInstances.forEach(chart => chart.dispose())
  symbolInstances = []
}

function renderSymbolCharts(analytics: BookAnalyticsPayload) {
  disposeSymbolCharts()
  books.forEach((book, index) => {
    const element = symbolPanelCharts.value[index]
    if (!element) return
    const rows = analytics.user_structure?.[book as 'abook' | 'bbook']?.symbol_heatmap || []
    const chartRows = rows
      .filter((row: any) => Number.isFinite(Number(row.market_pnl)))
      .sort((left: any, right: any) => Math.abs(Number(right.market_pnl)) - Math.abs(Number(left.market_pnl)))
      .slice(0, 20)
    const chart = echarts.init(element)
    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'item',
        formatter: (params: any) => {
          const row = chartRows[params.dataIndex]
          return `${bookLabel(book)} · ${row.symbol}<br/>−市场 P&amp;L：${money(Math.abs(-Number(row.market_pnl)))}<br/>交易笔数：${row.trade_count}<br/>交易量：${money(row.volume)}`
        },
      },
      grid: { left: 74, right: 24, top: 32, bottom: 86 },
      xAxis: { type: 'category', data: chartRows.map((row: any) => row.symbol), axisLabel: { color: '#8497ad', rotate: 45, interval: 0 } },
      yAxis: { type: 'value', name: '−市场 P&L', nameTextStyle: { color: '#8497ad' }, axisLabel: { color: '#8497ad' }, splitLine: { lineStyle: { color: '#20364e' } } },
      series: [{
        name: '−市场 P&L',
        type: 'bar',
        barMaxWidth: 30,
        data: chartRows.map((row: any) => ({ value: Math.abs(-Number(row.market_pnl)), itemStyle: { color: '#54d6a6' } })),
      }],
    })
    symbolInstances.push(chart)
  })
}

function renderDistributionCharts(analytics: BookAnalyticsPayload) {
  disposeDistributionCharts()
  const periods = distributionPeriods.value
  books.forEach((book, bookIndex) => {
    const rows = analytics.pnl_structure?.[book as 'abook' | 'bbook']?.distribution_by_period || []
    periods.forEach((period, periodIndex) => {
      const element = distributionPanelCharts.value[bookIndex * periods.length + periodIndex]
      if (!element) return
      const periodRows = distributionBuckets.map(bucket => rows.find((row: any) => row.period === period.key && row.key === bucket.key) || {
        key: bucket.key, label: bucket.label, accounts: 0, account_rate: 0, net_pnl: 0,
      })
      const chart = echarts.init(element)
      chart.setOption({
        backgroundColor: 'transparent',
        tooltip: {
          trigger: 'item',
          formatter: (params: any) => {
            const row = periodRows[params.dataIndex]
            return `${bookLabel(book)} · ${period.label}<br/>${row.label}<br/>用户数：${row.accounts}<br/>占比：${pct(row.account_rate)}<br/>区间净 P&amp;L：${money(row.net_pnl)}`
          },
        },
        grid: { left: 58, right: 18, top: 28, bottom: 72 },
        xAxis: { type: 'category', data: periodRows.map(row => row.label), axisLabel: { color: '#8497ad', rotate: 28, interval: 0 } },
        legend: { data: ['有效用户数', '中性/无效用户数'], textStyle: { color: '#9fb3c9' } },
        yAxis: [
          { type: 'value', min: 0, name: '有效用户数', nameTextStyle: { color: '#8497ad' }, axisLabel: { color: '#8497ad' }, splitLine: { lineStyle: { color: '#20364e' } } },
          { type: 'value', min: 0, name: '中性/无效用户数', position: 'right', nameTextStyle: { color: '#8497ad' }, axisLabel: { color: '#8497ad' }, splitLine: { show: false } },
        ],
        series: [
          {
            name: '有效用户数',
            type: 'bar',
            yAxisIndex: 0,
            barMaxWidth: 42,
            label: { show: true, position: 'top', color: '#9fb3c9' },
            data: periodRows.map((row, index) => index === distributionBuckets.findIndex(bucket => bucket.key === 'neutral') ? null : { value: row.accounts, itemStyle: { color: distributionBuckets[index].color } }),
          },
          {
            name: '中性/无效用户数',
            type: 'bar',
            yAxisIndex: 1,
            barMaxWidth: 42,
            label: { show: true, position: 'top', color: '#9fb3c9' },
            data: periodRows.map((row, index) => index === distributionBuckets.findIndex(bucket => bucket.key === 'neutral') ? { value: row.accounts, itemStyle: { color: distributionBuckets[index].color } } : null),
          },
        ],
      })
      distributionInstances.push(chart)
    })
  })
}

function renderDistributionDetailCharts(analytics: BookAnalyticsPayload) {
  disposeDistributionDetailCharts()
  const periods = distributionPeriods.value
  books.forEach((book, bookIndex) => {
    const element = distributionDetailCharts.value[bookIndex]
    if (!element) return
    const rows = analytics.pnl_structure?.[book as 'abook' | 'bbook']?.distribution_by_period || []
    const periodRows = periods.map(period => ({
      period,
      buckets: distributionBuckets.map(bucket => rows.find((row: any) => row.period === period.key && row.key === bucket.key) || {
        key: bucket.key, label: bucket.label, accounts: 0, account_rate: 0, net_pnl: 0,
      }),
    }))
    const chart = echarts.init(element)
    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'item',
        formatter: (params: any) => {
          const row = periodRows[params.dataIndex]?.buckets[params.seriesIndex]
          const period = periodRows[params.dataIndex]?.period
          return `${bookLabel(book)} · ${period?.label || ''}<br/>${row?.label || params.seriesName}<br/>用户数：${row?.accounts || 0}<br/>占比：${pct(row?.account_rate)}<br/>区间净 P&amp;L：${money(row?.net_pnl)}`
        },
      },
      legend: { textStyle: { color: '#9fb3c9' } },
      grid: { left: 62, right: 24, top: 56, bottom: 48, containLabel: true },
      xAxis: { type: 'category', data: periodRows.map(row => row.period.label), axisLabel: { color: '#8497ad' } },
      yAxis: { type: 'value', min: 0, name: '用户数', nameTextStyle: { color: '#8497ad' }, axisLabel: { color: '#8497ad' }, splitLine: { lineStyle: { color: '#20364e' } } },
      series: distributionBuckets.map((bucket, bucketIndex) => ({
        name: bucket.label,
        type: 'bar',
        barMaxWidth: 28,
        barGap: '10%',
        barCategoryGap: '28%',
        markLine: bucketIndex === 0 ? phaseDividerMarkLine(periodRows.map(row => row.period.key), props.request, periodRows.map(row => row.period.label)) : undefined,
        data: periodRows.map(row => ({ value: row.buckets[bucketIndex].accounts, itemStyle: { color: bucket.color } })),
      })),
    })
    distributionDetailInstances.push(chart)
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
    grid: { left: 96, right: 96, top: 42, bottom: 28, containLabel: true },
    xAxis: { type: 'category', data: dates, axisLabel: { color: '#8497ad' } },
    yAxis: [
      { type: 'value', name: 'Bbook 客户 P&L', nameTextStyle: { color: '#ff8d91' }, axisLabel: { color: '#8497ad' }, splitLine: { lineStyle: { color: '#20364e' } } },
      { type: 'value', name: 'Abook 客户 P&L', nameTextStyle: { color: '#54d6a6' }, axisLabel: { color: '#8497ad' }, splitLine: { show: false } },
    ],
    series: [
      { name: 'Bbook 累计客户 P&L', type: 'line', yAxisIndex: 0, smooth: true, data: dates.map(date => bCumulative[date]), lineStyle: { color: '#ff8d91' }, areaStyle: { color: 'rgba(255,141,145,.08)' }, markLine: phaseDividerMarkLine(dates, props.request) },
      { name: 'Abook 累计客户 P&L', type: 'line', yAxisIndex: 1, smooth: true, data: dates.map(date => aCumulative[date]), lineStyle: { color: '#54d6a6' }, areaStyle: { color: 'rgba(84,214,166,.10)' } },
    ],
  })

  dailyInstance = echarts.init(dailyChart.value)
  dailyInstance.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { textStyle: { color: '#9fb3c9' } },
    grid: { left: 96, right: 96, top: 42, bottom: 28, containLabel: true },
    xAxis: { type: 'category', data: dates, axisLabel: { color: '#8497ad' } },
    yAxis: [
      { type: 'value', name: 'Bbook 每日客户 P&L', nameTextStyle: { color: '#ff8d91' }, axisLabel: { color: '#8497ad' }, splitLine: { lineStyle: { color: '#20364e' } } },
      { type: 'value', name: 'Abook 每日客户 P&L', nameTextStyle: { color: '#54d6a6' }, axisLabel: { color: '#8497ad' }, splitLine: { show: false } },
    ],
    series: [
      { name: 'Bbook 每日客户 P&L', type: 'line', yAxisIndex: 0, smooth: true, data: dates.map(date => bDaily[date]), lineStyle: { color: '#ff8d91' }, markLine: phaseDividerMarkLine(dates, props.request) },
      { name: 'Abook 每日客户 P&L', type: 'line', yAxisIndex: 1, smooth: true, data: dates.map(date => aDaily[date]), lineStyle: { color: '#54d6a6' } },
    ],
  })
}

function renderRoutingChart(analytics: BookAnalyticsPayload) {
  if (!routingChart.value) return
  disposeRoutingChart()
  const profitRows = analytics.routing_quality?.company_profit_comparison || []
  routingInstance = echarts.init(routingChart.value)
  routingInstance.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { textStyle: { color: '#9fb3c9' } },
    grid: { left: 96, right: 96, top: 42, bottom: 42, containLabel: true },
    xAxis: { type: 'category', data: profitRows.map((row: any) => row.date), axisLabel: { color: '#8497ad' } },
    yAxis: [
      { type: 'value', name: '公司利润', axisLabel: { color: '#8497ad' }, nameTextStyle: { color: '#8497ad' }, splitLine: { lineStyle: { color: '#20364e' } } },
      { type: 'value', name: '增量变化', axisLabel: { color: '#f0bd72' }, nameTextStyle: { color: '#f0bd72' }, splitLine: { show: false } },
    ],
    series: [
      { name: '基准公司利润', type: 'bar', data: profitRows.map((row: any) => row.baseline_company_profit), itemStyle: { color: '#8798ad' }, markLine: phaseDividerMarkLine(profitRows.map((row: any) => row.date), props.request) },
      { name: '分流后公司利润', type: 'bar', data: profitRows.map((row: any) => row.after_routing_company_profit), itemStyle: { color: '#54d6a6' } },
      { name: '增量变化', type: 'line', yAxisIndex: 1, data: profitRows.map((row: any) => row.incremental_change), lineStyle: { color: '#f0bd72', width: 2 }, itemStyle: { color: '#f0bd72' } },
    ],
  })
}

async function renderChart() {
  await nextTick()
  if (!props.analytics) return
  if (props.activeTab === 'users') {
    instance?.dispose()
    instance = null
    disposeRoutingChart()
    disposeDistributionCharts()
    renderPnlCharts(props.analytics)
    renderSymbolCharts(props.analytics)
    renderDistributionCharts(props.analytics)
    renderDistributionDetailCharts(props.analytics)
    return
  }
  disposeSymbolCharts()
  disposeDistributionCharts()
  disposeDistributionDetailCharts()
  disposePnlCharts()
  disposeRoutingChart()
  if (props.activeTab !== 'risk-routing' || !chart.value) return
  instance?.dispose()
  instance = echarts.init(chart.value)
  const abook = props.analytics.risk_exposure?.abook?.leverage_histogram || { labels: leverageLabels, counts: [] }
  const bbook = props.analytics.risk_exposure?.bbook?.leverage_histogram || { labels: leverageLabels, counts: [] }
  instance.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { textStyle: { color: '#9fb3c9' } },
    grid: { left: 96, right: 18, top: 42, bottom: 42, containLabel: true },
    xAxis: { type: 'category', data: abook.labels?.length ? abook.labels : leverageLabels, axisLabel: { color: '#8497ad' } },
    yAxis: { type: 'value', name: '用户数', axisLabel: { color: '#8497ad' }, nameTextStyle: { color: '#8497ad' }, splitLine: { lineStyle: { color: '#20364e' } } },
    series: [
      { name: 'Abook 用户数', type: 'bar', data: abook.counts || [], itemStyle: { color: '#54d6a6' } },
      { name: 'Bbook 用户数', type: 'bar', data: bbook.counts || [], itemStyle: { color: '#ff8d91' } },
    ],
  })
  renderRoutingChart(props.analytics)
}

watch(() => [props.analytics, props.activeTab], renderChart, { deep: true })
onBeforeUnmount(() => {
  instance?.dispose()
  disposeRoutingChart()
  disposePnlCharts()
  disposeSymbolCharts()
  disposeDistributionCharts()
  disposeDistributionDetailCharts()
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
      <div class="book-grid"><article v-for="book in books" :key="book" class="book-card"><h3>{{ bookLabel(book) }}</h3><p>筛选期客户净 P&amp;L <b :class="pnlClass(analytics.pnl_structure?.[book]?.selection?.net_pnl)">{{ money(analytics.pnl_structure?.[book]?.selection?.net_pnl) }}</b></p><p>验证期客户净 P&amp;L <b :class="pnlClass(analytics.pnl_structure?.[book]?.validation?.net_pnl)">{{ money(analytics.pnl_structure?.[book]?.validation?.net_pnl) }}</b></p><p>验证期盈利/亏损/中性 <b>{{ analytics.pnl_structure?.[book]?.validation?.profitable_accounts ?? 0 }} / {{ analytics.pnl_structure?.[book]?.validation?.loss_accounts ?? 0 }} / {{ analytics.pnl_structure?.[book]?.validation?.neutral_accounts ?? 0 }} · precision {{ precision(analytics.pnl_structure?.[book]?.validation?.profitable_accounts, analytics.pnl_structure?.[book]?.validation?.accounts) }}</b></p><p v-if="book === 'bbook'">Bbook 公司 P&amp;L <b :class="pnlClass(analytics.pnl_structure?.[book]?.validation?.company_profit_if_current_book)">{{ money(analytics.pnl_structure?.[book]?.validation?.company_profit_if_current_book) }}</b></p><p v-else>Abook 公司 P&amp;L <b>不由客户 P&amp;L 推断</b></p><p>验证期最大回撤 <b class="negative">{{ money(analytics.pnl_structure?.[book]?.max_drawdown) }}</b></p><p>Top 5 客户 P&amp;L 绝对集中度 <b>{{ pct(analytics.pnl_structure?.[book]?.profit_concentration?.top_5?.absolute_share) }}</b></p></article></div>
      <template v-if="activeTab === 'users'"><div ref="cumulativeChart" class="book-chart"></div><div ref="dailyChart" class="book-chart"></div></template>
      <template v-else-if="activeTab === 'risk-routing'"><div class="risk-routing-section"><p class="hint">风险敞口使用用户 P95 杠杆分布；分流质量展示基准公司利润、分流后公司利润与增量变化。</p><div class="two-col metric-columns"><article v-for="book in books" :key="book" class="book-card"><h3>{{ bookLabel(book) }} 风险统计</h3><p>杠杆率 P50 / P75 / P95 / 最大 <b>{{ money(analytics.risk_exposure?.[book]?.leverage_p50) }} / {{ money(analytics.risk_exposure?.[book]?.leverage_p75) }} / {{ money(analytics.risk_exposure?.[book]?.leverage_p95) }} / {{ money(analytics.risk_exposure?.[book]?.leverage_max) }}</b></p><p>极端杠杆用户 <b>{{ analytics.risk_exposure?.[book]?.extreme_leverage_accounts ?? 0 }}（{{ pct(analytics.risk_exposure?.[book]?.extreme_leverage_rate) }}）</b></p><p>筛选期最大回撤 <b class="negative">{{ money(analytics.risk_exposure?.[book]?.selection_max_drawdown) }}</b></p><p>验证期最大回撤 <b class="negative">{{ money(analytics.risk_exposure?.[book]?.validation_max_drawdown) }}</b></p><p>验证期最差单日客户 P&amp;L <b class="negative">{{ money(analytics.risk_exposure?.[book]?.validation_worst_daily_customer_pnl) }}</b></p><p>验证期亏损尾部占比 <b>{{ pct(analytics.risk_exposure?.[book]?.validation_loss_tail_5_share) }}</b></p></article></div><article class="panel inset"><div class="book-section-title"><h3>风险敞口 · 杠杆率分布</h3><span class="hint">用户 P95 杠杆</span></div><div ref="chart" class="book-chart"></div></article><article class="panel inset"><div class="book-section-title"><h3>分流质量 · 公司利润对比</h3><span class="hint">基准与分流后公司利润</span></div><div ref="routingChart" class="book-chart"></div></article></div></template>
      <div v-if="activeTab === 'users'" class="symbol-sections"><div v-for="book in books" :key="book" class="symbol-book"><div class="book-section-title"><h3>{{ bookLabel(book) }} 品种客户盈亏</h3><span class="hint">{{ symbolTimeRange }} · Top 20 资产 · 横轴资产 · 纵轴 −市场 P&amp;L</span></div><div class="symbol-chart-scroll"><div ref="symbolPanelCharts" class="symbol-chart"></div></div></div></div>
      <div v-if="activeTab === 'users'" class="distribution-sections"><div v-for="book in books" :key="book" class="distribution-book"><div class="book-section-title"><h3>{{ bookLabel(book) }} P&amp;L 用户分布</h3><span class="hint">按期间拆分 · 柱高为用户数</span></div><div class="distribution-grid"><article v-for="period in distributionPeriods" :key="`${book}-${period.key}`" class="distribution-card"><h4>{{ period.label }}</h4><div ref="distributionPanelCharts" class="book-chart distribution-chart-panel"></div></article></div></div></div>
      <div v-if="activeTab === 'users'" class="book-analysis-list"><div v-for="book in books" :key="book" class="book-analysis"><div class="book-section-title"><h3>{{ bookLabel(book) }} 内部盈利分析</h3><span class="hint">客户 P&amp;L 口径{{ book === 'bbook' ? '；公司 P&amp;L 为客户 P&amp;L 取负' : '；不推导公司利润' }}</span></div><div class="phase-cards"><article v-for="phase in phases" :key="phase" class="metric-card"><span class="kicker">{{ phaseLabel(phase) }}</span><strong :class="pnlClass(analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.net_pnl)">{{ money(analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.net_pnl) }}</strong><small>{{ analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.accounts ?? 0 }} 用户 · 赚 {{ analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.profitable_accounts ?? 0 }} · 亏 {{ analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.loss_accounts ?? 0 }} · 中性 {{ analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.neutral_accounts ?? 0 }} · precision {{ precision(analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.profitable_accounts, analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.accounts) }}</small><small>盈利额 {{ money(analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.positive_pnl) }} · 亏损额 {{ money(analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.negative_pnl) }}</small><small>PF {{ money(analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.profit_factor) }} · 胜率 {{ pct(analytics.pnl_structure?.[book]?.phase_summary?.[phase]?.win_rate) }}</small></article></div><h4>月度盈利表</h4><div class="table-scroll"><table><thead><tr><th>期间</th><th>用户</th><th>活跃</th><th>赚</th><th>亏</th><th>中性</th><th>precision</th><th>盈利额</th><th>亏损额</th><th>净 P&amp;L</th><th>平均</th><th>中位数</th><th>PF</th><th>胜率</th></tr></thead><tbody><tr v-for="row in analytics.pnl_structure?.[book]?.monthly || []" :key="row.month"><td>{{ row.month === 'selection_total' ? '5–6月合计' : row.month }}</td><td>{{ row.accounts }}</td><td>{{ row.active_accounts }}</td><td class="positive">{{ row.profitable_accounts }}</td><td class="negative">{{ row.loss_accounts }}</td><td>{{ row.neutral_accounts }}</td><td>{{ precision(row.profitable_accounts, row.accounts) }}</td><td class="positive">{{ money(row.positive_pnl) }}</td><td class="negative">{{ money(row.negative_pnl) }}</td><td :class="pnlClass(row.net_pnl)">{{ money(row.net_pnl) }}</td><td>{{ money(row.average_pnl) }}</td><td>{{ money(row.median_pnl) }}</td><td>{{ money(row.profit_factor) }}</td><td>{{ pct(row.win_rate) }}</td></tr></tbody></table></div><h4>P&amp;L 用户分布（按月份/阶段）</h4><div ref="distributionDetailCharts" class="book-chart distribution-detail-chart"></div><h4>用户风格盈利表</h4><div class="table-scroll"><table><thead><tr><th>期间</th><th>风格</th><th>用户</th><th>活跃</th><th>赚</th><th>亏</th><th>中性</th><th>precision</th><th>盈利额</th><th>亏损额</th><th>净 P&amp;L</th><th>胜率</th></tr></thead><tbody><tr v-for="row in analytics.pnl_structure?.[book]?.style_breakdown || []" :key="row.period + '-' + row.style"><td>{{ row.period === 'selection_total' ? '5–6月合计' : row.period }}</td><td>{{ row.style }}</td><td>{{ row.accounts }}</td><td>{{ row.active_accounts }}</td><td class="positive">{{ row.profitable_accounts }}</td><td class="negative">{{ row.loss_accounts }}</td><td>{{ row.neutral_accounts }}</td><td>{{ precision(row.profitable_accounts, row.accounts) }}</td><td class="positive">{{ money(row.positive_pnl) }}</td><td class="negative">{{ money(row.negative_pnl) }}</td><td :class="pnlClass(row.net_pnl)">{{ money(row.net_pnl) }}</td><td>{{ pct(row.win_rate) }}</td></tr></tbody></table></div><div class="two-col"><article class="inset"><h4>Top 10 盈利用户（验证期，括号=全部盈利用户 P&amp;L 占比）</h4><div v-for="row in analytics.pnl_structure?.[book]?.top_accounts?.winners || []" :key="row.login" class="list-row clickable" @click="openSummaryAccount(row)"><span>{{ row.platform }} / {{ row.login }} · {{ row.style }}</span><b class="positive">{{ money(row.pnl) }}（{{ pct(row.share) }}）</b></div></article><article class="inset"><h4>Top 10 亏损用户（验证期，括号=全部亏损用户 P&amp;L 占比）</h4><div v-for="row in analytics.pnl_structure?.[book]?.top_accounts?.losers || []" :key="row.login" class="list-row clickable" @click="openSummaryAccount(row)"><span>{{ row.platform }} / {{ row.login }} · {{ row.style }}</span><b class="negative">{{ money(row.pnl) }}（{{ pct(row.share) }}）</b></div></article></div></div></div>
    </template>
    <div v-else class="empty">切换到分析 Tab 以加载数据</div>
  </section>
</template>
