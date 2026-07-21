<script setup lang="ts">
import { computed, ref } from 'vue'
import type { AccountRow, AnalysisPayload } from '../types'

const props = defineProps<{ data: AnalysisPayload }>()
const emit = defineEmits<{ (event: 'open', account: AccountRow): void }>()

type Phase = 'selection' | 'validation'

function amount(value: unknown): number {
  const parsed = Number(value ?? 0)
  return Number.isFinite(parsed) ? parsed : 0
}

function format(value: unknown): string {
  return amount(value).toFixed(2)
}

function ratio(value: unknown): string {
  return `${(amount(value) * 100).toFixed(1)}%`
}

function phasePnl(account: AccountRow, phase: Phase): number {
  return amount(account[phase]?.client_net_pnl)
}

function phaseAmountValues(account: AccountRow, phase: Phase): number[] {
  const monthly = (account.monthly || [])
    .filter((item: any) => item.phase === phase)
    .map((item: any) => amount(item.client_net_pnl))
  return monthly.length ? monthly : [phasePnl(account, phase)]
}

function monthPnl(account: AccountRow, month: string): number {
  const row = (account.monthly || []).find((item: any) => item.month === month)
  return amount(row?.client_net_pnl)
}

function phaseSummary(accounts: AccountRow[], phase: Phase) {
  const values = accounts.map(account => phasePnl(account, phase))
  const amountValues = accounts.flatMap(account => phaseAmountValues(account, phase))
  const totalProfit = amountValues.filter(value => value > 0).reduce((sum, value) => sum + value, 0)
  const totalLoss = amountValues.filter(value => value < 0).reduce((sum, value) => sum + Math.abs(value), 0)
  const trades = accounts.reduce((sum, account) => sum + amount(account[phase]?.trade_count), 0)
  const wins = accounts.reduce((sum, account) => sum + amount(account[phase]?.winning_trades), 0)
  return {
    totalProfit,
    totalLoss,
    netPnl: totalProfit - totalLoss,
    accounts: accounts.length,
    strictPositiveAccounts: values.filter(value => value > 0).length,
    precision: values.length ? values.filter(value => value > 0).length / values.length : 0,
    activeAccounts: accounts.filter(account => amount(account[phase]?.trade_count) > 0).length,
    trades,
    winRate: trades > 0 ? wins / trades : 0,
  }
}

const abookAccounts = computed(() => (props.data.accounts || []).filter(account => account.book === 'abook'))
const bbookAccounts = computed(() => (props.data.accounts || []).filter(account => account.book === 'bbook'))
const populationAccounts = computed(() => props.data.population_accounts || props.data.accounts || [])
const populationAbookAccounts = computed(() => populationAccounts.value.filter(account => account.book === 'abook'))
const populationBbookAccounts = computed(() => populationAccounts.value.filter(account => account.book === 'bbook'))
const summaries = computed(() => ({
  selection: phaseSummary(populationAbookAccounts.value, 'selection'),
  validation: phaseSummary(populationAbookAccounts.value, 'validation'),
}))
const bbookSummaries = computed(() => ({
  selection: phaseSummary(populationBbookAccounts.value, 'selection'),
  validation: phaseSummary(populationBbookAccounts.value, 'validation'),
}))
const leakage = computed(() => (props.data.misjudge?.bbook_profitable || []).filter((row: any) => amount(row.profit_amount) > 100))
type AbookSortField = 'may' | 'june' | 'july' | 'selectionWinRate' | 'validationWinRate' | 'selectionTrades' | 'score'
const sortBy = ref<AbookSortField>('july')
const sortDirection = ref<'asc' | 'desc'>('desc')
const leakageSortBy = ref<'may' | 'june' | 'july'>('july')
const leakageSortDirection = ref<'asc' | 'desc'>('desc')

function toggleSort(field: AbookSortField) {
  if (sortBy.value === field) sortDirection.value = sortDirection.value === 'asc' ? 'desc' : 'asc'
  else { sortBy.value = field; sortDirection.value = 'desc' }
}

function sortMark(field: AbookSortField): string {
  return sortBy.value === field ? (sortDirection.value === 'asc' ? '↑' : '↓') : '↕'
}

function accountSortValue(account: AccountRow, field: AbookSortField): number {
  if (field === 'may') return monthPnl(account, '2026-05')
  if (field === 'june') return monthPnl(account, '2026-06')
  if (field === 'july') return phasePnl(account, 'validation')
  if (field === 'selectionWinRate') return amount(account.selection?.win_rate)
  if (field === 'validationWinRate') return amount(account.validation?.win_rate)
  if (field === 'selectionTrades') return amount(account.selection?.trade_count)
  return amount(account.stability?.score)
}

const sortedAbookAccounts = computed(() => [...abookAccounts.value].sort((left, right) => {
  const delta = accountSortValue(left, sortBy.value) - accountSortValue(right, sortBy.value)
  return (sortDirection.value === 'asc' ? 1 : -1) * (delta || (left.login - right.login))
}))

function toggleLeakageSort(field: 'may' | 'june' | 'july') {
  if (leakageSortBy.value === field) leakageSortDirection.value = leakageSortDirection.value === 'asc' ? 'desc' : 'asc'
  else { leakageSortBy.value = field; leakageSortDirection.value = 'desc' }
}

function leakageSortMark(field: 'may' | 'june' | 'july'): string {
  return leakageSortBy.value === field ? (leakageSortDirection.value === 'asc' ? '↑' : '↓') : '↕'
}

const sortedLeakage = computed(() => [...leakage.value].sort((left: any, right: any) => {
  const key = leakageSortBy.value === 'may' ? 'may_client_net_pnl' : leakageSortBy.value === 'june' ? 'june_client_net_pnl' : 'validation_client_net_pnl'
  const delta = amount(left[key]) - amount(right[key])
  return (leakageSortDirection.value === 'asc' ? 1 : -1) * (delta || (Number(left.login) - Number(right.login)))
}))

const accountLookup = computed(() => new Map(
  (props.data.accounts || []).map(account => [`${account.platform}-${account.login}`, account]),
))

function leakageAccount(row: any): AccountRow {
  return accountLookup.value.get(`${row.platform}-${row.login}`) || row as AccountRow
}
</script>

<template>
  <section class="panel">
    <div class="panel-head">
      <div><span class="kicker">ABOOK ANALYSIS</span><h2>Abook 分析</h2></div>
      <span class="hint">客户 P&amp;L 口径，不直接代表公司利润</span>
    </div>

    <div class="phase-cards">
      <article v-for="phase in (['selection', 'validation'] as Phase[])" :key="phase" class="metric-card">
        <span class="kicker">{{ phase === 'selection' ? '筛选期（5–6 月）' : '验证期（7 月）' }}</span>
        <div class="list-row"><span>盈利金额</span><b class="positive">{{ format(summaries[phase].totalProfit) }}</b></div>
        <div class="list-row"><span>亏损金额</span><b class="negative">{{ format(-summaries[phase].totalLoss) }}</b></div>
        <div class="list-row"><span>净 P&amp;L</span><b :class="summaries[phase].netPnl >= 0 ? 'positive' : 'negative'">{{ format(summaries[phase].netPnl) }}</b></div>
        <div v-if="phase === 'validation'" class="list-row"><span>严格 Precision（P&amp;L &gt; 0 / Abook 总人数）</span><b class="positive">{{ ratio(summaries[phase].precision) }}（{{ summaries[phase].strictPositiveAccounts }}/{{ summaries[phase].accounts }}）</b></div>
        <small>盈利金额 + 亏损金额 = 净 P&amp;L · {{ summaries[phase].activeAccounts }}/{{ summaries[phase].accounts }} 个活跃账户 · {{ summaries[phase].trades }} 笔交易</small>
      </article>
    </div>

    <div class="panel-head"><div><span class="kicker">BBOOK ANALYSIS</span><h3>Bbook 分析</h3></div><span class="hint">客户 P&amp;L 口径；公司利润单独计算</span></div>
    <div class="phase-cards">
      <article v-for="phase in (['selection', 'validation'] as Phase[])" :key="phase" class="metric-card">
        <span class="kicker">{{ phase === 'selection' ? '筛选期（5–6 月）' : '验证期（7 月）' }}</span>
        <div class="list-row"><span>盈利金额</span><b class="positive">{{ format(bbookSummaries[phase].totalProfit) }}</b></div>
        <div class="list-row"><span>亏损金额</span><b class="negative">{{ format(-bbookSummaries[phase].totalLoss) }}</b></div>
        <div class="list-row"><span>净 P&amp;L</span><b :class="bbookSummaries[phase].netPnl >= 0 ? 'positive' : 'negative'">{{ format(bbookSummaries[phase].netPnl) }}</b></div>
        <small>盈利金额 + 亏损金额 = 净 P&amp;L · {{ bbookSummaries[phase].activeAccounts }}/{{ bbookSummaries[phase].accounts }} 个活跃账户 · {{ bbookSummaries[phase].trades }} 笔交易</small>
      </article>
    </div>

    <details class="user-list" open>
      <summary><div class="panel-head"><div><h3>Abook 用户</h3><span class="hint">点击用户查看指标；点击列名切换升序/降序</span></div><span class="tag">{{ abookAccounts.length }} 人</span></div></summary>
      <div v-if="!abookAccounts.length" class="empty">暂无筛选到 Abook 用户</div>
      <div v-else class="table-scroll">
        <table>
          <thead><tr><th>账户</th><th>来源</th><th><button class="table-sort" @click="toggleSort('may')">5月 P&amp;L {{ sortMark('may') }}</button></th><th><button class="table-sort" @click="toggleSort('june')">6月 P&amp;L {{ sortMark('june') }}</button></th><th><button class="table-sort" @click="toggleSort('july')">7月 P&amp;L {{ sortMark('july') }}</button></th><th><button class="table-sort" @click="toggleSort('selectionWinRate')">5–6 月胜率 {{ sortMark('selectionWinRate') }}</button></th><th><button class="table-sort" @click="toggleSort('validationWinRate')">7 月胜率 {{ sortMark('validationWinRate') }}</button></th><th><button class="table-sort" @click="toggleSort('selectionTrades')">交易数 {{ sortMark('selectionTrades') }}</button></th><th><button class="table-sort" @click="toggleSort('score')">稳定性 {{ sortMark('score') }}</button></th></tr></thead>
          <tbody>
            <tr v-for="account in sortedAbookAccounts" :key="`${account.platform}-${account.login}`" @click="emit('open', account)">
              <td>{{ account.platform }} / {{ account.login }}<small>{{ account.account_group }}</small></td>
              <td><small>{{ account.selection_source }}</small></td>
              <td :class="monthPnl(account, '2026-05') >= 0 ? 'positive' : 'negative'">{{ format(monthPnl(account, '2026-05')) }}</td>
              <td :class="monthPnl(account, '2026-06') >= 0 ? 'positive' : 'negative'">{{ format(monthPnl(account, '2026-06')) }}</td>
              <td :class="phasePnl(account, 'validation') >= 0 ? 'positive' : 'negative'">{{ format(phasePnl(account, 'validation')) }}</td>
              <td>{{ ratio(account.selection?.win_rate) }}</td>
              <td>{{ ratio(account.validation?.win_rate) }}</td>
              <td>{{ amount(account.selection?.trade_count) }} / {{ amount(account.validation?.trade_count) }}</td>
              <td>{{ amount(account.stability?.score).toFixed(0) }} · {{ account.stability?.tier || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </details>

    <div class="inset">
      <div class="panel-head"><div><h3>Bbook 漏网</h3><span class="hint">验证期盈利且最终留在 Bbook 的用户</span></div><span class="negative">公司损失 {{ format(props.data.misjudge?.bbook_company_loss_total) }}</span></div>
      <details open>
        <summary>显示公司损失大于 100 的 {{ leakage.length }} 人</summary>
        <div v-if="!leakage.length" class="empty">暂无金额大于 100 的漏网用户</div>
        <div v-else class="table-scroll"><table><thead><tr><th>用户</th><th>Bbook 原因</th><th><button class="table-sort" @click="toggleLeakageSort('may')">5月 P&amp;L {{ leakageSortMark('may') }}</button></th><th><button class="table-sort" @click="toggleLeakageSort('june')">6月 P&amp;L {{ leakageSortMark('june') }}</button></th><th><button class="table-sort" @click="toggleLeakageSort('july')">7月 P&amp;L {{ leakageSortMark('july') }}</button></th></tr></thead><tbody><tr v-for="row in sortedLeakage" :key="`${row.platform}-${row.login}`" @click="emit('open', leakageAccount(row))"><td>{{ row.platform }} / {{ row.login }}<small>筛选期合计 {{ format(row.selection_client_net_pnl) }}</small></td><td><span v-for="tag in (row.bbook_reason_tags || ['Bbook'])" :key="tag" class="tag danger">{{ tag }}</span></td><td :class="row.may_client_net_pnl >= 0 ? 'positive' : 'negative'">{{ format(row.may_client_net_pnl) }}</td><td :class="row.june_client_net_pnl >= 0 ? 'positive' : 'negative'">{{ format(row.june_client_net_pnl) }}</td><td class="positive">{{ format(row.validation_client_net_pnl) }}</td></tr></tbody></table></div>
      </details>
    </div>

    <details class="inset">
      <summary>盈亏口径与数据来源</summary>
      <div class="basis-list">
        <div>客户净 P&amp;L：{{ props.data.profit_overview?.pnl_basis?.client_net_pnl || '—' }}</div>
        <div>市场 P&amp;L：{{ props.data.profit_overview?.pnl_basis?.market_pnl || '—' }}</div>
        <div>公司 P&amp;L：{{ props.data.profit_overview?.pnl_basis?.company_pnl || '—' }}</div>
        <div>数据覆盖：Deals {{ props.data.coverage?.source_deal_min || '—' }} 至 {{ props.data.coverage?.source_deal_max || '—' }}</div>
      </div>
    </details>
  </section>
</template>
