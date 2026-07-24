<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { NewcomerAnalyticsPayload, NewcomerAccountSensitivity, RequestModel } from '../types'
import { fetchNewcomerAccount } from '../api'
import { phaseDividerMarkLine } from '../phaseDivider'

const props = defineProps<{
  analytics: NewcomerAnalyticsPayload | null
  loading: boolean
  request: RequestModel
  maxActiveDays: number
  analysisToken?: string
}>()
const emit = defineEmits<{
  (event: 'run'): void
  (event: 'update:maxActiveDays', value: number): void
}>()

type Pool = 'admitted' | 'observe' | 'rejected'
type ChartMode = 'post_asof' | 'full_window'
const activePool = ref<Pool>('admitted')
const chartMode = ref<ChartMode>('post_asof')
const page = ref(1)
const pageSize = 12
const selected = ref<Record<string, any> | null>(null)
const sensitivity = ref<NewcomerAccountSensitivity | null>(null)
const sensitivityLoading = ref(false)
const sensitivityError = ref('')
const customMinTrades = ref(75)
const statsEnd = ref('')
const cumulativeChart = ref<HTMLElement | null>(null)
const distributionChart = ref<HTMLElement | null>(null)
let cumulativeInstance: echarts.ECharts | null = null
let distributionInstance: echarts.ECharts | null = null

const martingaleLevels = ['extreme', 'high', 'medium', 'low']

function number(value: unknown): number {
  const parsed = Number(value ?? 0)
  return Number.isFinite(parsed) ? parsed : 0
}
function money(value: unknown): string { return number(value).toFixed(2) }
function pct(value: unknown): string { return `${(number(value) * 100).toFixed(1)}%` }
function pf(value: unknown): string { return value === null || value === undefined ? '∞' : money(value) }
function pnlClass(value: unknown): string { return number(value) >= 0 ? 'positive' : 'negative' }
function precision(profitable: unknown, total: unknown): string {
  return pct(number(profitable) / Math.max(1, number(total)))
}
function flagLabel(flag: string): string {
  const labels: Record<string, string> = {
    no_history: '窗口内无成交',
    insufficient_sample: '样本不足/未成熟',
    profit_factor: 'PF 未达标',
    win_rate: '胜率未达标',
    payoff_ratio: '盈亏比未达标',
    long_trades_ratio: '多空比例未达标',
    profit_concentration: 'Top1 日集中度过高',
    leverage_p95_ratio: '杠杆未通过',
    martingale_hard_block: '马丁硬拦截',
    short_history_exhausted: '短历史轨已尽',
    active_days_exceeded: '活跃日超限',
    quality_failed: '质量未过线',
  }
  return labels[flag] || flag
}
function poolLabel(pool: string): string {
  if (pool === 'admitted') return '入选'
  if (pool === 'observe') return '短历史观察'
  if (pool === 'blocked') return '马丁拦截'
  if (pool === 'left_track') return '已离轨'
  return '未过线'
}

const rows = computed(() => {
  if (!props.analytics) return []
  if (activePool.value === 'admitted') return props.analytics.admitted || []
  if (activePool.value === 'observe') return props.analytics.observe || []
  return props.analytics.rejected || []
})
const pageCount = computed(() => Math.max(1, Math.ceil(rows.value.length / pageSize)))
const pagedRows = computed(() => rows.value.slice((page.value - 1) * pageSize, page.value * pageSize))
const postSummary = computed(() => props.analytics?.summary?.post_asof || {})
const validationSummary = computed(() => props.analytics?.summary?.validation || {})
const topPost = computed(() => props.analytics?.top_accounts?.post_asof || { winners: [], losers: [] })

function selectPool(pool: Pool) {
  activePool.value = pool
  page.value = 1
}
function runSearch() {
  page.value = 1
  selected.value = null
  sensitivity.value = null
  emit('run')
}

async function openAccount(account: Record<string, any>) {
  selected.value = account
  customMinTrades.value = number(account.min_trades_used || props.request.rules.min_trades || 75)
  statsEnd.value = account.stats_end || props.request.validation.end
  await loadSensitivity()
}

async function loadSensitivity() {
  if (!selected.value) return
  sensitivityLoading.value = true
  sensitivityError.value = ''
  try {
    sensitivity.value = await fetchNewcomerAccount({
      analysis: props.request,
      analysis_token: props.analysisToken,
      platform: selected.value.platform,
      login: selected.value.login,
      min_trades: customMinTrades.value,
      min_trades_values: [75, 100, 200, 500, customMinTrades.value],
      stats_end: statsEnd.value || undefined,
      max_active_days: props.maxActiveDays,
    })
  } catch (err) {
    sensitivityError.value = err instanceof Error ? err.message : String(err)
  } finally {
    sensitivityLoading.value = false
  }
}

function disposeCharts() {
  cumulativeInstance?.dispose()
  distributionInstance?.dispose()
  cumulativeInstance = null
  distributionInstance = null
}

function renderCharts() {
  disposeCharts()
  if (!props.analytics || !cumulativeChart.value || !distributionChart.value) return

  const cumulativeRows = chartMode.value === 'post_asof'
    ? (props.analytics.cumulative_pnl?.post_asof || [])
    : (props.analytics.cumulative_pnl?.full_window || [])
  const dates = cumulativeRows.map(row => row.date)
  cumulativeInstance = echarts.init(cumulativeChart.value)
  cumulativeInstance.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', valueFormatter: (value: number) => money(value) },
    grid: { left: 72, right: 28, top: 36, bottom: 38, containLabel: true },
    xAxis: { type: 'category', data: dates, axisLabel: { color: '#8497ad' } },
    yAxis: {
      type: 'value',
      name: chartMode.value === 'post_asof' ? '入选后累积客户净 P&L' : '全窗累积客户净 P&L',
      nameTextStyle: { color: '#8497ad' },
      axisLabel: { color: '#8497ad' },
      splitLine: { lineStyle: { color: '#20364e' } },
    },
    series: [{
      name: '入选 cohort',
      type: 'line',
      smooth: true,
      showSymbol: false,
      data: cumulativeRows.map(row => row.cumulative_pnl),
      lineStyle: { width: 3, color: '#54d6a6' },
      areaStyle: { color: 'rgba(84,214,166,.10)' },
      markLine: phaseDividerMarkLine(dates, props.request),
    }],
  })

  const dist = props.analytics.pnl_distribution?.post_asof || []
  const colors = ['#d95f66', '#ed7b82', '#f3a4a9', '#7d8da3', '#8bd8bb', '#54d6a6', '#2eaf82']
  distributionInstance = echarts.init(distributionChart.value)
  distributionInstance.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    grid: { left: 52, right: 20, top: 28, bottom: 70, containLabel: true },
    xAxis: {
      type: 'category',
      data: dist.map(row => row.label),
      axisLabel: { color: '#8497ad', rotate: 25, interval: 0 },
    },
    yAxis: {
      type: 'value',
      name: '用户数',
      nameTextStyle: { color: '#8497ad' },
      axisLabel: { color: '#8497ad' },
      splitLine: { lineStyle: { color: '#20364e' } },
    },
    series: [{
      type: 'bar',
      barMaxWidth: 34,
      data: dist.map((row, index) => ({
        value: row.accounts || 0,
        itemStyle: { color: colors[index] || '#7d8da3' },
      })),
    }],
  })
}

watch([() => props.analytics, chartMode], () => nextTick(() => {
  page.value = Math.min(page.value, pageCount.value)
  renderCharts()
}), { deep: true })
watch(pageCount, () => { if (page.value > pageCount.value) page.value = pageCount.value })
onBeforeUnmount(() => disposeCharts())
</script>

<template>
  <section class="panel direction-panel newcomer-panel">
    <div class="panel-head">
      <div>
        <span class="kicker">NEWCOMER ROLLING SCREEN</span>
        <h2>新人滚动筛</h2>
        <p class="hint">仅纳入窗口内 trades &gt; 0 · 个人 as-of · 活跃日 ≤ N · 不含总览 Abook / 僵尸账号</p>
      </div>
      <div class="direction-actions">
        <button class="primary" :disabled="loading || !request.platforms.length" @click="runSearch">
          {{ analytics ? '重新运行新人筛选' : '运行新人筛选' }}
        </button>
      </div>
    </div>

    <div class="direction-parameters">
      <div class="parameter-heading">
        <div>
          <h3>新人筛选参数</h3>
          <p class="hint">独立于总览侧栏；修改后不会自动查询。窗口内 0 成交账号直接跳过。须先完成总览「应用筛选与验证」。</p>
        </div>
        <span class="tag">独立参数</span>
      </div>
      <div class="date-grid">
        <label>筛选开始<input v-model="request.selection.start" type="date"></label>
        <label>筛选结束<input v-model="request.selection.end" type="date"></label>
        <label>验证开始<input v-model="request.validation.start" type="date"></label>
        <label>验证结束<input v-model="request.validation.end" type="date"></label>
      </div>
      <div class="direction-parameter-grid">
        <label>短历史活跃日上限 N<input :value="maxActiveDays" type="number" min="1" max="365" @input="emit('update:maxActiveDays', Number(($event.target as HTMLInputElement).value) || 60)"></label>
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
        <div class="platforms">
          <label v-for="platform in ['mt4', 'mt5', 'hh_mt5']" :key="platform" class="check">
            <input v-model="request.platforms" :value="platform" type="checkbox"> {{ platform }}
          </label>
        </div>
        <div class="direction-martingale">
          <span>确认马丁阻断等级</span>
          <label v-for="level in martingaleLevels" :key="level" class="check">
            <input v-model="request.rules.excluded_martingale_levels" type="checkbox" :value="level"> {{ level }}
          </label>
          <small>与总览一致：命中硬拦截者不进可过线/入选。</small>
        </div>
      </div>
    </div>

    <div v-if="loading" class="empty">加载新人滚动筛…</div>
    <template v-else-if="analytics">
      <div class="kpi-grid direction-kpis">
        <article class="kpi accent"><span>入选人数</span><strong>{{ analytics.kpi?.admitted_accounts || 0 }}</strong><small>个人 as-of 过线</small></article>
        <article class="kpi"><span>入选后净 P&amp;L</span><strong :class="pnlClass(analytics.kpi?.post_asof_net_pnl)">{{ money(analytics.kpi?.post_asof_net_pnl) }}</strong><small>as-of 次日 → {{ analytics.validation?.end }}</small></article>
        <article class="kpi"><span>盈 / 亏人数</span><strong>{{ analytics.kpi?.admitted_positive || 0 }} / {{ analytics.kpi?.admitted_negative || 0 }}</strong><small>均值 {{ money(analytics.kpi?.average_post_asof_pnl) }} · 中位 {{ money(analytics.kpi?.median_post_asof_pnl) }}</small></article>
        <article class="kpi"><span>短历史观察</span><strong>{{ analytics.kpi?.observe_accounts || 0 }}</strong><small>有成交、尚未成熟</small></article>
        <article class="kpi"><span>跳过无成交</span><strong>{{ analytics.kpi?.skipped_inactive || 0 }}</strong><small>窗口 trades = 0</small></article>
      </div>

      <div class="phase-cards">
        <article class="metric-card">
          <span class="kicker">入选 · 个人 as-of 后</span>
          <strong :class="pnlClass(postSummary.net_pnl)">{{ money(postSummary.net_pnl) }}</strong>
          <small>{{ postSummary.accounts || 0 }} 用户 · 赚 {{ postSummary.profitable_accounts || 0 }} · 亏 {{ postSummary.loss_accounts || 0 }} · 中性 {{ postSummary.neutral_accounts || 0 }} · precision {{ precision(postSummary.profitable_accounts, postSummary.accounts) }}</small>
          <small>盈利 {{ money(postSummary.positive_pnl) }} · 亏损 {{ money(postSummary.negative_pnl) }}</small>
          <small>平均 {{ money(postSummary.average_pnl) }} · 中位 {{ money(postSummary.median_pnl) }} · Top5 集中度 {{ pct(postSummary.profit_concentration?.top_5?.absolute_share) }}</small>
        </article>
        <article class="metric-card">
          <span class="kicker">入选 · 统一验证窗诊断</span>
          <strong :class="pnlClass(validationSummary.net_pnl)">{{ money(validationSummary.net_pnl) }}</strong>
          <small>{{ validationSummary.accounts || 0 }} 用户 · 赚 {{ validationSummary.profitable_accounts || 0 }} · 亏 {{ validationSummary.loss_accounts || 0 }}</small>
          <small>仅作对照；主 KPI 仍以个人 as-of 后为准</small>
          <small>平均 {{ money(validationSummary.average_pnl) }} · 中位 {{ money(validationSummary.median_pnl) }}</small>
        </article>
      </div>

      <div class="book-section-title">
        <div>
          <h3>入选 cohort 表现</h3>
          <p class="hint">累积曲线按每人 as-of 次日起计；全窗曲线含筛选期仅作对照</p>
        </div>
        <label>曲线口径
          <select v-model="chartMode">
            <option value="post_asof">入选后（主口径）</option>
            <option value="full_window">全窗对照</option>
          </select>
        </label>
      </div>
      <div class="two-col direction-chart-pair">
        <article class="inset">
          <h5>累积客户净 P&amp;L</h5>
          <div ref="cumulativeChart" class="book-chart direction-cumulative-chart"></div>
        </article>
        <article class="inset">
          <h5>入选后 P&amp;L 分布</h5>
          <div ref="distributionChart" class="book-chart direction-distribution-chart"></div>
        </article>
      </div>

      <div class="two-col">
        <article class="inset">
          <h4>入选后 Top 盈利</h4>
          <div v-for="row in (topPost.winners || []).slice(0, 5)" :key="`w-${row.platform}-${row.login}`" class="list-row">
            <span>{{ row.platform }} / {{ row.login }}<small>as-of {{ row.as_of || '—' }} · {{ row.active_trade_days || 0 }} 日</small></span>
            <b class="positive">{{ money(row.pnl) }}</b>
          </div>
          <div v-if="!(topPost.winners || []).length" class="empty">暂无</div>
        </article>
        <article class="inset">
          <h4>入选后 Top 亏损</h4>
          <div v-for="row in (topPost.losers || []).slice(0, 5)" :key="`l-${row.platform}-${row.login}`" class="list-row">
            <span>{{ row.platform }} / {{ row.login }}<small>as-of {{ row.as_of || '—' }} · {{ row.active_trade_days || 0 }} 日</small></span>
            <b class="negative">{{ money(row.pnl) }}</b>
          </div>
          <div v-if="!(topPost.losers || []).length" class="empty">暂无</div>
        </article>
      </div>

      <div class="direction-toolbar">
        <button class="ghost compact" :class="{ active: activePool === 'admitted' }" @click="selectPool('admitted')">入选 {{ analytics.counts?.admitted || 0 }}</button>
        <button class="ghost compact" :class="{ active: activePool === 'observe' }" @click="selectPool('observe')">短历史观察 {{ analytics.counts?.observe || 0 }}</button>
        <button class="ghost compact" :class="{ active: activePool === 'rejected' }" @click="selectPool('rejected')">未过线/离轨 {{ (analytics.counts?.rejected || 0) + (analytics.counts?.left_track || 0) + (analytics.counts?.martingale_blocked || 0) }}</button>
        <span class="hint">跳过 Abook {{ analytics.counts?.skipped_abook || 0 }} · 跳过无成交 {{ analytics.counts?.skipped_inactive || 0 }} · 活跃候选 {{ analytics.counts?.active_candidates || 0 }} · {{ analytics.pnl_basis }}</span>
      </div>

      <div class="table-scroll direction-account-table">
        <table>
          <thead>
            <tr>
              <th>账户</th>
              <th>窗口笔数</th>
              <th>活跃日</th>
              <th>as-of</th>
              <th>筛选笔数 / 胜率 / PF</th>
              <th>状态</th>
              <th>{{ activePool === 'observe' ? '验证期诊断 P&L' : '入选后 P&L' }}</th>
              <th>未过原因</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="account in pagedRows" :key="`${account.platform}-${account.login}`" @click="openAccount(account)">
              <td>{{ account.platform }} / {{ account.login }}<small>{{ account.account_group }}</small></td>
              <td>{{ account.window_trade_count || 0 }}</td>
              <td>{{ account.active_trade_days || 0 }}</td>
              <td>{{ account.as_of || '—' }}</td>
              <td>{{ account.selection?.trade_count || 0 }} / {{ pct(account.selection?.win_rate) }} / {{ pf(account.selection?.profit_factor) }}</td>
              <td><span class="tag" :class="account.admitted ? '' : 'danger'">{{ poolLabel(account.pool) }}</span></td>
              <td :class="pnlClass(activePool === 'observe' ? account.validation_period_pnl : account.post_asof_pnl)">
                {{ money(activePool === 'observe' ? account.validation_period_pnl : account.post_asof_pnl) }}
              </td>
              <td>
                <span v-for="flag in (account.selection_flags || []).slice(0, 3)" :key="flag" class="tag danger">{{ flagLabel(flag) }}</span>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="!rows.length" class="empty">当前池没有账户</div>
      </div>
      <div v-if="rows.length" class="pagination direction-pagination">
        <button class="ghost compact" :disabled="page <= 1" @click="page--">上一页</button>
        <span>第 {{ page }} / {{ pageCount }} 页 · {{ rows.length }} 个账户 · {{ poolLabel(activePool) }}</span>
        <button class="ghost compact" :disabled="page >= pageCount" @click="page++">下一页</button>
      </div>

      <div v-if="selected" class="inset newcomer-sensitivity">
        <div class="book-section-title">
          <div>
            <h3>{{ selected.platform }} / {{ selected.login }} · 单用户敏感性</h3>
            <p class="hint">单独改 min_trades / 统计结束日，仅重算该用户；不污染批处理缓存。</p>
          </div>
          <button class="ghost compact" @click="selected = null; sensitivity = null">关闭</button>
        </div>
        <div class="direction-parameter-grid">
          <label>该用户 min_trades<input v-model.number="customMinTrades" type="number" min="1"></label>
          <label>统计结束日<input v-model="statsEnd" type="date"></label>
        </div>
        <div class="direction-actions" style="justify-content:flex-start;margin:12px 0">
          <button class="primary" :disabled="sensitivityLoading" @click="loadSensitivity">重算该用户</button>
        </div>
        <div v-if="sensitivityLoading" class="empty">重算中…</div>
        <div v-else-if="sensitivityError" class="alert error">{{ sensitivityError }}</div>
        <div v-else-if="sensitivity" class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>min_trades</th>
                <th>as-of</th>
                <th>活跃日</th>
                <th>是否过线</th>
                <th>入选后 P&amp;L</th>
                <th>原因</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="point in sensitivity.points" :key="point.min_trades_used">
                <td>{{ point.min_trades_used }}</td>
                <td>{{ point.as_of || '—' }}</td>
                <td>{{ point.active_trade_days }}</td>
                <td>{{ point.admitted ? '是' : '否' }}</td>
                <td :class="pnlClass(point.post_asof_pnl)">{{ money(point.post_asof_pnl) }}</td>
                <td><span v-for="flag in (point.selection_flags || []).slice(0, 3)" :key="flag" class="tag danger">{{ flagLabel(flag) }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
    <div v-else class="empty">请先在总览完成筛选，再点击「运行新人筛选」。只看窗口内真正有成交的短历史用户；结果不写回总览 Abook。</div>
  </section>
</template>
