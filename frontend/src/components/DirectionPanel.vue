<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { DirectionAnalyticsPayload, RequestModel } from '../types'
import { phaseDividerMarkLine } from '../phaseDivider'

const props = defineProps<{ analytics: DirectionAnalyticsPayload | null; loading: boolean; request: RequestModel }>()
const emit = defineEmits<{
  (event: 'open', account: Record<string, any>): void
  (event: 'export'): void
  (event: 'run'): void
}>()

type Book = 'abook' | 'bbook'
type BookFilter = 'all' | Book
type Side = 'long' | 'short'
type Phase = 'selection' | 'validation'
type SortKey = 'login' | 'longTrades' | 'longWinRate' | 'longPnl' | 'shortTrades' | 'shortWinRate' | 'shortPnl' | 'quadrant'

const books: Book[] = ['abook', 'bbook']
const sides: Side[] = ['long', 'short']
const activeBook = ref<BookFilter>('all')
const activeSet = ref('all')
const selectedPassSet = ref<string | null>(null)
const hideSmallPnl = ref(true)
const phase = ref<Phase>('validation')
const sortBy = ref<SortKey>('longPnl')
const sortDirection = ref<'asc' | 'desc'>('desc')
const page = ref(1)
const pageSize = 8
const cumulativeCharts = ref<HTMLElement[]>([])
const pnlDistributionCharts = ref<HTMLElement[]>([])
let cumulativeInstances: echarts.ECharts[] = []
let pnlDistributionInstances: echarts.ECharts[] = []

const displayBooks = computed<Book[]>(() => activeBook.value === 'all' ? books : [activeBook.value])

function number(value: unknown): number {
  const parsed = Number(value ?? 0)
  return Number.isFinite(parsed) ? parsed : 0
}
function money(value: unknown): string { return number(value).toFixed(2) }
function pct(value: unknown): string { return `${(number(value) * 100).toFixed(1)}%` }
function precision(profitable: unknown, total: unknown): string {
  return pct(number(profitable) / Math.max(1, number(total)))
}
function pf(value: unknown): string { return value === null || value === undefined ? '∞' : money(value) }
function pnlClass(value: unknown): string { return number(value) >= 0 ? 'positive' : 'negative' }
function bookLabel(book: Book): string { return book === 'abook' ? 'Abook' : 'Bbook' }
function routeLabel(book: BookFilter): string { return book === 'all' ? 'Abook/Bbook' : bookLabel(book) }
function sideLabel(which: Side): string { return which === 'long' ? 'Long' : 'Short' }
function phaseLabel(value: Phase): string { return value === 'selection' ? '筛选期' : '验证期' }
function sampleLabel(status: string): string {
  return status === 'no_activity' ? '无该方向成交' : status === 'insufficient' ? '样本不足' : '样本达标'
}
function flagLabel(flag: string): string {
  const labels: Record<string, string> = {
    no_side_activity: '无方向成交', insufficient_side_sample: '方向样本不足',
    profit_factor: 'PF 未达标', win_rate: '胜率未达标', payoff_ratio: '盈亏比未达标',
    long_trades_ratio: '多空比例未达标', profit_concentration: 'Top1 日集中度过高',
    leverage_p95_ratio: '杠杆未通过', martingale_hard_block: '马丁硬拦截',
  }
  return labels[flag] || flag
}
function quadrantLabel(value: string): string {
  const labels: Record<string, string> = {
    all: '全部账户', both_pass: '双边过线', long_only_pass: '仅 Long 过线', short_only_pass: '仅 Short 过线',
    neither_pass: '双边质量未过', insufficient_side: '一侧样本不足', long_pass: 'Long 过线', short_pass: 'Short 过线',
  }
  return labels[value] || value
}
function passSet(which: Side): string { return which === 'long' ? 'long_pass' : 'short_pass' }
function payloadFor(book: Book, setName = activeSet.value): any {
  return props.analytics?.book_sets?.[setName]?.[book] || { long: {}, short: {} }
}
function phaseData(book: Book, which: Side): any {
  return payloadFor(book, activeSet.value)[which]?.phase_summary?.[phase.value] || {}
}
function passCount(book: Book, which: Side): number {
  return props.analytics?.book_counts?.[book]?.[passSet(which)] || 0
}
function passKey(book: Book, which: Side): string { return `${book}:${which}` }
function selectPassList(book: Book, which: Side) {
  const key = passKey(book, which)
  selectedPassSet.value = selectedPassSet.value === key ? null : key
}
const selectedPassLabel = computed(() => {
  if (!selectedPassSet.value) return ''
  const [book, which] = selectedPassSet.value.split(':') as [Book, Side]
  return `${bookLabel(book)} · ${sideLabel(which)} 过线`
})
const selectedPassAccounts = computed(() => {
  if (!selectedPassSet.value) return []
  const [book, which] = selectedPassSet.value.split(':') as [Book, Side]
  return (props.analytics?.accounts || [])
    .filter(account => account.book === book && Boolean(account[`${which}_pass`]))
    .sort((left, right) => Number(left.login) - Number(right.login))
})
function topAccounts(book: Book, which: Side, kind: 'winners' | 'losers'): any[] {
  return payloadFor(book)[which]?.top_accounts?.[phase.value]?.[kind] || []
}

const filteredRows = computed(() => {
  const all = props.analytics?.accounts || []
  return all.filter(account => {
    if (activeBook.value !== 'all' && account.book !== activeBook.value) return false
    if (activeSet.value === 'all') return true
    if (activeSet.value === 'long_pass') return account.long_pass
    if (activeSet.value === 'short_pass') return account.short_pass
    return account.quadrant === activeSet.value
  })
})
const rows = computed(() => filteredRows.value.filter(account => {
  if (activeSet.value !== 'all' || !hideSmallPnl.value) return true
  const longMetric = account.long?.[phase.value] || {}
  const shortMetric = account.short?.[phase.value] || {}
  return Math.abs(number(longMetric.side_pnl)) > 10 || Math.abs(number(shortMetric.side_pnl)) > 10
}).sort((left, right) => {
  const value = (account: Record<string, any>): number | string => {
    const long = account.long?.[phase.value] || {}
    const short = account.short?.[phase.value] || {}
    if (sortBy.value === 'login') return Number(account.login) || 0
    if (sortBy.value === 'longTrades') return number(long.trade_count)
    if (sortBy.value === 'longWinRate') return number(long.win_rate)
    if (sortBy.value === 'longPnl') return number(long.side_pnl)
    if (sortBy.value === 'shortTrades') return number(short.trade_count)
    if (sortBy.value === 'shortWinRate') return number(short.win_rate)
    if (sortBy.value === 'shortPnl') return number(short.side_pnl)
    return String(account.quadrant || '')
  }
  const leftValue = value(left); const rightValue = value(right)
  if (leftValue === rightValue) return Number(left.login) - Number(right.login)
  const order = leftValue > rightValue ? 1 : -1
  return sortDirection.value === 'asc' ? order : -order
}))
const pageCount = computed(() => Math.max(1, Math.ceil(rows.value.length / pageSize)))
const pagedRows = computed(() => rows.value.slice((page.value - 1) * pageSize, page.value * pageSize))

function toggleSort(key: SortKey) {
  if (sortBy.value === key) sortDirection.value = sortDirection.value === 'asc' ? 'desc' : 'asc'
  else { sortBy.value = key; sortDirection.value = 'desc' }
}
function sortMark(key: SortKey): string {
  return sortBy.value === key ? (sortDirection.value === 'asc' ? '↑' : '↓') : '↕'
}
function runSearch() {
  page.value = 1
  emit('run')
}
const martingaleLevels = ['extreme', 'high', 'medium', 'low']
function disposeCharts(charts: echarts.ECharts[]) {
  charts.forEach(chart => chart.dispose())
  charts.splice(0, charts.length)
}
function renderCumulativeCharts() {
  disposeCharts(cumulativeInstances)
  sides.forEach((which, index) => {
    const element = cumulativeCharts.value[index]
    if (!element) return
    const setName = activeSet.value
    const dataByBook = Object.fromEntries(displayBooks.value.map(book => [book, payloadFor(book, setName)[which]?.cumulative_pnl?.full || []])) as Record<Book, any[]>
    const dates = [...new Set(displayBooks.value.flatMap(book => dataByBook[book].map(row => row.date)))].sort()
    const chart = echarts.init(element)
    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', valueFormatter: (value: number) => money(value) },
      legend: { textStyle: { color: '#9fb3c9' } },
      grid: { left: 88, right: 28, top: 38, bottom: 38, containLabel: true },
      xAxis: { type: 'category', data: dates, axisLabel: { color: '#8497ad' } },
      yAxis: { type: 'value', name: '累积 matched profit', nameTextStyle: { color: '#8497ad' }, axisLabel: { color: '#8497ad' }, splitLine: { lineStyle: { color: '#20364e' } } },
      series: displayBooks.value.map((book, bookIndex) => {
        const values = Object.fromEntries(dataByBook[book].map(row => [row.date, row.cumulative_pnl]))
        return { name: bookLabel(book), type: 'line', smooth: true, showSymbol: false, data: dates.map(date => values[date] ?? null), connectNulls: false, lineStyle: { width: 3, color: book === 'abook' ? '#54d6a6' : '#ff8d91' }, areaStyle: { color: book === 'abook' ? 'rgba(84,214,166,.10)' : 'rgba(255,141,145,.08)' }, markLine: bookIndex === 0 ? phaseDividerMarkLine(dates, props.request) : undefined }
      }),
    })
    cumulativeInstances.push(chart)
  })
}
function renderPnlDistributionCharts() {
  disposeCharts(pnlDistributionInstances)
  const colors = ['#d95f66', '#ed7b82', '#f3a4a9', '#7d8da3', '#8bd8bb', '#54d6a6', '#2eaf82']
  sides.forEach((which, index) => {
    const element = pnlDistributionCharts.value[index]
    if (!element) return
    const setName = activeSet.value
    const dataByBook = Object.fromEntries(displayBooks.value.map(book => [book, payloadFor(book, setName)[which]?.pnl_distribution?.[phase.value] || []])) as Record<Book, any[]>
    const abookRows = dataByBook.abook || []
    const bbookRows = dataByBook.bbook || []
    const rowsByKey = Object.fromEntries((abookRows.length ? abookRows : bbookRows).map(row => [row.key, row]))
    const categories = Object.values(rowsByKey).map((row: any) => row.label)
    const chart = echarts.init(element)
    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis' },
      legend: { textStyle: { color: '#9fb3c9' } },
      grid: { left: 52, right: 20, top: 38, bottom: 70, containLabel: true },
      xAxis: { type: 'category', data: categories, axisLabel: { color: '#8497ad', rotate: 25, interval: 0 } },
      yAxis: { type: 'value', name: '用户数', nameTextStyle: { color: '#8497ad' }, axisLabel: { color: '#8497ad' }, splitLine: { lineStyle: { color: '#20364e' } } },
      series: displayBooks.value.map(book => ({ name: bookLabel(book), type: 'bar', barMaxWidth: 30, data: Object.keys(rowsByKey).map((key, bucketIndex) => ({ value: dataByBook[book].find(row => row.key === key)?.accounts || 0, itemStyle: { color: book === 'abook' ? colors[bucketIndex] : `${colors[bucketIndex]}99` } })) })),
    })
    pnlDistributionInstances.push(chart)
  })
}
watch([() => props.analytics, activeBook, activeSet, hideSmallPnl, phase], () => nextTick(() => {
  page.value = Math.min(page.value, pageCount.value)
  renderCumulativeCharts()
  renderPnlDistributionCharts()
}), { deep: true, immediate: true })
watch(pageCount, () => { if (page.value > pageCount.value) page.value = pageCount.value })
onBeforeUnmount(() => {
  disposeCharts(cumulativeInstances)
  disposeCharts(pnlDistributionInstances)
})
</script>

<template>
  <section class="panel direction-panel">
    <div class="panel-head">
      <div><span class="kicker">DIRECTION ANALYTICS</span><h2>多空分向独立筛选</h2><p class="hint">Long / Short 按 matched.profit 独立筛选；Abook/Bbook 同屏对比</p></div>
      <div class="direction-actions"><button class="primary" :disabled="loading || !request.platforms.length" @click="runSearch">{{ analytics ? '重新运行分向筛选' : '运行分向筛选' }}</button><button class="ghost" :disabled="!analytics || loading" @click="emit('export')">导出分向 CSV</button></div>
    </div>
    <div class="direction-parameters">
      <div class="parameter-heading"><div><h3>分向筛选参数</h3><p class="hint">这里的参数只用于本次多空分向分析；修改后不会自动查询，请确认后点击右上角绿色按钮。</p></div><span class="tag">独立参数</span></div>
      <div class="date-grid">
        <label>筛选开始<input v-model="request.selection.start" type="date"></label>
        <label>筛选结束<input v-model="request.selection.end" type="date"></label>
        <label>验证开始<input v-model="request.validation.start" type="date"></label>
        <label>验证结束<input v-model="request.validation.end" type="date"></label>
      </div>
      <div class="direction-parameter-grid">
        <label>最低交易笔数<input v-model.number="request.rules.min_trades" type="number" min="0"></label>
        <label>最低胜率<input v-model.number="request.rules.min_win_rate" type="number" min="0" max="1" step="0.01"></label>
        <label>最低 Profit Factor<input v-model.number="request.rules.min_profit_factor" type="number" min="0" step="0.01"></label>
        <label>最低盈亏比<input v-model.number="request.rules.min_payoff_ratio" type="number" min="0" step="0.01"></label>
        <label>Long 比例下限<input v-model.number="request.rules.min_long_trades_ratio" type="number" min="0" max="1" step="0.01"></label>
        <label>Long 比例上限<input v-model.number="request.rules.max_long_trades_ratio" type="number" min="0" max="1" step="0.01"></label>
        <label>Top1 日利润贡献率上限<input v-model.number="request.rules.max_top1_day_profit_contribution" type="number" min="0" max="1" step="0.01"></label>
        <label>最大杠杆率 P95<input v-model.number="request.rules.max_leverage_p95_ratio" type="number" min="0"></label>
        <label>高杠杆持仓例外秒数<input v-model.number="request.rules.max_high_leverage_holding_seconds" type="number" min="0"></label>
      </div>
      <div class="direction-parameter-footer">
        <div class="platforms"><label v-for="platform in ['mt4', 'mt5', 'hh_mt5']" :key="platform" class="check"><input v-model="request.platforms" :value="platform" type="checkbox"> {{ platform }}</label></div>
        <div class="direction-martingale"><span>确认马丁阻断等级</span><label v-for="level in martingaleLevels" :key="level" class="check"><input v-model="request.rules.excluded_martingale_levels" type="checkbox" :value="level"> {{ level }}</label><small>当前选中的确认马丁等级会被分到 Bbook。</small></div>
      </div>
    </div>
    <div v-if="loading" class="empty">加载 Long / Short matched 分析…</div>
    <template v-else-if="analytics">
      <div class="kpi-grid direction-kpis"><template v-for="book in displayBooks" :key="book"><article v-for="which in sides" :key="`${book}-${which}`" class="kpi clickable" :class="which === 'long' ? 'accent' : 'danger'" :aria-expanded="selectedPassSet === passKey(book, which)" @click="selectPassList(book, which)"><span>{{ bookLabel(book) }} · {{ sideLabel(which) }} 过线</span><strong>{{ passCount(book, which) }}</strong><small>独立筛选通过账户 · 点击查看用户</small></article></template></div>

      <div v-if="selectedPassSet" class="direction-pass-list inset">
        <div class="book-section-title"><div><h3>{{ selectedPassLabel }} · 通过用户列表</h3><p class="hint">同一账户可能同时出现在 Long 和 Short 两个名单中；点击用户查看交易详情抽屉。</p></div><button class="ghost compact" @click.stop="selectedPassSet = null">关闭</button></div>
        <div v-if="!selectedPassAccounts.length" class="empty">当前名单没有可展示的用户</div>
        <div v-else class="table-scroll"><table><thead><tr><th>账户</th><th>路由</th><th>Long 筛选期 P&amp;L</th><th>Short 筛选期 P&amp;L</th><th>象限</th></tr></thead><tbody><tr v-for="account in selectedPassAccounts" :key="`${account.platform}-${account.login}`" @click="emit('open', account)"><td>{{ account.platform }} / {{ account.login }}<small>{{ account.account_group }}</small></td><td><span class="tag">{{ bookLabel(account.book || 'bbook') }}</span></td><td :class="pnlClass(account.long?.selection?.side_pnl)">{{ money(account.long?.selection?.side_pnl) }}</td><td :class="pnlClass(account.short?.selection?.side_pnl)">{{ money(account.short?.selection?.side_pnl) }}</td><td>{{ quadrantLabel(account.quadrant) }}</td></tr></tbody></table></div>
      </div>

      <div class="two-col metric-columns"><article class="book-card"><h3>总 Abook vs 双边过线</h3><p>总 Abook 用户 <b>{{ analytics.comparison?.total_abook_accounts || 0 }}</b></p><p>总 Abook ∩ Both <b>{{ analytics.comparison?.total_abook_and_both_pass || 0 }}</b></p><p>Both 但非总 Abook <b>{{ analytics.comparison?.both_pass_not_total_abook || 0 }}</b></p></article><article class="book-card"><h3>验证期 matched P&amp;L 对比</h3><p>总 Abook matched profit <b :class="pnlClass(analytics.comparison?.total_abook_validation_matched_profit)">{{ money(analytics.comparison?.total_abook_validation_matched_profit) }}</b></p><p>Both matched profit <b :class="pnlClass(analytics.comparison?.both_pass_validation_matched_profit)">{{ money(analytics.comparison?.both_pass_validation_matched_profit) }}</b></p><p>总 Abook 中仅一边过线 <b class="warning">{{ analytics.comparison?.abook_but_one_sided || 0 }}</b></p><small>Deals 客户净 P&amp;L（对照） {{ money(analytics.comparison?.total_abook_validation_client_net_pnl) }}</small></article></div>

      <div class="phase-cards"><template v-for="book in displayBooks" :key="`${book}-phase`"><article v-for="which in sides" :key="`${book}-${which}-phase`" class="metric-card"><span class="kicker">{{ bookLabel(book).toUpperCase() }} · {{ sideLabel(which).toUpperCase() }} · {{ phaseLabel(phase) }}</span><strong :class="pnlClass(phaseData(book, which).net_pnl)">{{ money(phaseData(book, which).net_pnl) }}</strong><small>{{ phaseData(book, which).accounts || 0 }} 用户 · 活跃 {{ phaseData(book, which).active_accounts || 0 }} · {{ phaseData(book, which).matched_trades || 0 }} 笔</small><small>赚 {{ phaseData(book, which).profitable_accounts || 0 }} · 亏 {{ phaseData(book, which).loss_accounts || 0 }} · 中性 {{ phaseData(book, which).neutral_accounts || 0 }} · precision {{ precision(phaseData(book, which).profitable_accounts, phaseData(book, which).accounts) }}</small><small>盈利 {{ money(phaseData(book, which).positive_pnl) }} · 亏损 {{ money(phaseData(book, which).negative_pnl) }}</small><small>胜率 {{ pct(phaseData(book, which).win_rate) }} · PF {{ pf(phaseData(book, which).profit_factor) }} · 最大回撤 {{ money(phaseData(book, which).max_drawdown) }}</small><small>平均 P&amp;L {{ money(phaseData(book, which).average_pnl) }} · 中位数 P&amp;L {{ money(phaseData(book, which).median_pnl) }}</small><small>最差单日 {{ money(phaseData(book, which).worst_daily_pnl) }} · Top 5 P&amp;L 集中度 {{ pct(phaseData(book, which).profit_concentration?.top_5?.absolute_share) }}</small></article></template></div>

      <div class="direction-toolbar"><label>路由<select v-model="activeBook"><option value="all">Abook + Bbook</option><option value="abook">Abook</option><option value="bbook">Bbook</option></select></label><label>名单<select v-model="activeSet"><option value="all">全部账户</option><option value="both_pass">both_pass</option><option value="long_only_pass">long_only_pass</option><option value="short_only_pass">short_only_pass</option><option value="neither_pass">neither_pass</option><option value="insufficient_side">insufficient_side</option><option value="long_pass">Long 过线</option><option value="short_pass">Short 过线</option></select></label><label>阶段<select v-model="phase"><option value="selection">筛选期</option><option value="validation">验证期</option></select></label><label class="check"><input v-model="hideSmallPnl" type="checkbox"> 隐藏小额 ±10</label><span class="hint">{{ routeLabel(activeBook) }} · 当前 {{ quadrantLabel(activeSet) }} · 显示 {{ rows.length }} / {{ filteredRows.length }} 个账户</span></div>

      <div class="table-scroll direction-account-table"><table><thead><tr><th><button class="table-sort" @click="toggleSort('login')">账户 {{ sortMark('login') }}</button></th><th>路由</th><th><button class="table-sort" @click="toggleSort('longTrades')">Long 交易 {{ sortMark('longTrades') }}</button> / <button class="table-sort" @click="toggleSort('longWinRate')">胜率 {{ sortMark('longWinRate') }}</button> / <span class="table-metric-label">PF</span></th><th><button class="table-sort" @click="toggleSort('longPnl')">Long P&amp;L {{ sortMark('longPnl') }}</button></th><th><button class="table-sort" @click="toggleSort('shortTrades')">Short 交易 {{ sortMark('shortTrades') }}</button> / <button class="table-sort" @click="toggleSort('shortWinRate')">胜率 {{ sortMark('shortWinRate') }}</button> / <span class="table-metric-label">PF</span></th><th><button class="table-sort" @click="toggleSort('shortPnl')">Short P&amp;L {{ sortMark('shortPnl') }}</button></th><th><button class="table-sort" @click="toggleSort('quadrant')">象限 {{ sortMark('quadrant') }}</button></th><th>未过原因</th></tr></thead><tbody><tr v-for="account in pagedRows" :key="`${account.platform}-${account.login}`" @click="emit('open', account)"><td>{{ account.platform }} / {{ account.login }}<small>{{ account.account_group }}</small></td><td><span class="tag">{{ bookLabel(account.book || 'bbook') }}</span></td><td>{{ account.long?.[phase]?.trade_count || 0 }} / {{ pct(account.long?.[phase]?.win_rate) }} / {{ pf(account.long?.[phase]?.profit_factor) }}<small>{{ sampleLabel(account.long?.[phase]?.sample_status || '') }}</small></td><td :class="pnlClass(account.long?.[phase]?.side_pnl)">{{ money(account.long?.[phase]?.side_pnl) }}</td><td>{{ account.short?.[phase]?.trade_count || 0 }} / {{ pct(account.short?.[phase]?.win_rate) }} / {{ pf(account.short?.[phase]?.profit_factor) }}<small>{{ sampleLabel(account.short?.[phase]?.sample_status || '') }}</small></td><td :class="pnlClass(account.short?.[phase]?.side_pnl)">{{ money(account.short?.[phase]?.side_pnl) }}</td><td><span class="tag" :class="account.quadrant === 'neither_pass' || account.quadrant === 'insufficient_side' ? 'danger' : ''">{{ quadrantLabel(account.quadrant) }}</span></td><td><span v-for="flag in [...(account.selection_flags_long || []), ...(account.selection_flags_short || [])].slice(0, 4)" :key="flag" class="tag danger">{{ flagLabel(flag) }}</span></td></tr></tbody></table><div v-if="!rows.length" class="empty">当前阶段没有重要账户</div></div><div v-if="rows.length" class="pagination direction-pagination"><button class="ghost compact" :disabled="page <= 1" @click="page--">上一页</button><span>第 {{ page }} / {{ pageCount }} 页 · {{ rows.length }} 个账户</span><button class="ghost compact" :disabled="page >= pageCount" @click="page++">下一页</button></div>

      <div class="book-analysis-list"><div class="book-analysis"><div class="book-section-title"><h3>用户结构：Long / Short 独立表现 · {{ phaseLabel(phase) }}</h3><span class="hint">当前名单：{{ quadrantLabel(activeSet) }} · matched profit</span></div><div class="two-col"><article v-for="book in displayBooks" :key="`${book}-tops`" class="inset"><template v-for="which in sides" :key="`${book}-${which}-tops`"><h4>{{ bookLabel(book) }} · {{ sideLabel(which) }} Top 用户</h4><div v-for="row in topAccounts(book, which, 'winners').slice(0, 5)" :key="`${book}-${which}-w-${row.login}`" class="list-row"><span>{{ row.platform }} / {{ row.login }}</span><b class="positive">{{ money(row.pnl) }}</b></div><div v-for="row in topAccounts(book, which, 'losers').slice(0, 5)" :key="`${book}-${which}-l-${row.login}`" class="list-row"><span>{{ row.platform }} / {{ row.login }}</span><b class="negative">{{ money(row.pnl) }}</b></div></template></article></div><h4>累积 P&amp;L 曲线（筛选期 → 验证期）</h4><div class="two-col direction-chart-pair"><article v-for="which in sides" :key="`${which}-cumulative`" class="inset"><h5>{{ sideLabel(which) }}</h5><div ref="cumulativeCharts" class="book-chart direction-cumulative-chart"></div></article></div><h4>P&amp;L 分布（{{ phaseLabel(phase) }}）</h4><div class="two-col direction-chart-pair"><article v-for="which in sides" :key="`${which}-distribution`" class="inset"><h5>{{ sideLabel(which) }}</h5><div ref="pnlDistributionCharts" class="book-chart direction-distribution-chart"></div></article></div></div></div>
    </template>
    <div v-else class="empty">请点击上方“运行分向筛选”加载当前窗口结果</div>
  </section>
</template>
