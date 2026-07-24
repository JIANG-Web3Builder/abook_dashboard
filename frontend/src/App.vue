<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { exportAbook, fetchAccountDetail, fetchAnalysis, fetchBookAnalytics, fetchDirectionAnalytics, fetchNewcomerAnalytics, fetchWarehouseStatus, refreshLocalSnapshots as refreshLocalSnapshotsApi } from './api'
import type { AccountDetailPayload, AccountRow, AnalysisPayload, DirectionAnalyticsPayload, NewcomerAnalyticsPayload, RequestModel, Tab, WarehouseStatus } from './types'
import FilterSidebar from './components/FilterSidebar.vue'
import KpiCards from './components/KpiCards.vue'
import SelectionFunnel from './components/SelectionFunnel.vue'
import AbookAnalysis from './components/AbookAnalysis.vue'
import AccountsTable from './components/AccountsTable.vue'
import AccountDrawer from './components/AccountDrawer.vue'
import BookPerformance from './components/BookPerformance.vue'
import DirectionPanel from './components/DirectionPanel.vue'
import NewcomerPanel from './components/NewcomerPanel.vue'

const defaultRules: Record<string, number | string[] | boolean> = {
  min_trades: 75, min_win_rate: 0.5, min_profit_factor: 1.25,
  min_payoff_ratio: 0.6,
  min_long_trades_ratio: 0.3, max_long_trades_ratio: 0.7,
  max_top1_day_profit_contribution: 0.3,
  max_leverage_p95_ratio: 2000, max_high_leverage_holding_seconds: 60,
  high_confidence_trades: 100, high_confidence_days: 30,
  excluded_martingale_levels: ['extreme', 'high', 'medium', 'low'],
}
const request = ref<RequestModel>({
  selection: { start: '2026-05-01', end: '2026-06-30' }, validation: { start: '2026-07-01', end: '2026-07-22' },
  platforms: ['mt4', 'mt5', 'hh_mt5'], filters: { groups: [], logins: [] }, rules: { ...defaultRules }, personal_candidate_list: false, news_candidate_list: false,
})
const directionRequest = ref<RequestModel>(JSON.parse(JSON.stringify(request.value)) as RequestModel)
const newcomerRequest = ref<RequestModel>(JSON.parse(JSON.stringify(request.value)) as RequestModel)
const newcomerMaxActiveDays = ref(60)
const data = ref<AnalysisPayload>({ accounts: [] })
const activeTab = ref<Tab>('overview')
const loading = ref(false)
const bookLoading = ref(false)
const bookSymbolsLoaded = ref(false)
const warehouseStatus = ref<WarehouseStatus | null>(null)
const snapshotRefreshing = ref(false)
const error = ref('')
const rulesDirty = ref(false)
const bookData = ref<any | null>(null)
const directionData = ref<DirectionAnalyticsPayload | null>(null)
const directionLoading = ref(false)
const newcomerData = ref<NewcomerAnalyticsPayload | null>(null)
const newcomerLoading = ref(false)
const selectedAccount = ref<AccountRow | null>(null)
const selectedDirectionAccount = ref<Record<string, any> | null>(null)
const accountDetail = ref<AccountDetailPayload | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
let detailRequestId = 0

async function loadAnalysis(options: { preserveAccount?: boolean } = { preserveAccount: true }) {
  const accountBeforeRefresh = selectedAccount.value
  loading.value = true; error.value = ''; bookData.value = null; directionData.value = null; newcomerData.value = null; selectedDirectionAccount.value = null; bookSymbolsLoaded.value = false
  if (!options.preserveAccount) {
    closeAccount()
  }
  try {
    data.value = await fetchAnalysis(request.value)
    try { warehouseStatus.value = await fetchWarehouseStatus() } catch { /* analysis succeeded; retain the last Warehouse status */ }
    if (options.preserveAccount && accountBeforeRefresh) {
      const refreshed = (data.value.accounts || []).find(account => account.platform === accountBeforeRefresh.platform && account.login === accountBeforeRefresh.login)
      if (refreshed) selectedAccount.value = refreshed
    }
    rulesDirty.value = false
  } catch (err) { error.value = err instanceof Error ? err.message : String(err) } finally { loading.value = false }
}
async function refreshLocalSnapshots() {
  snapshotRefreshing.value = true
  error.value = ''
  try {
    await refreshLocalSnapshotsApi(request.value)
    warehouseStatus.value = await fetchWarehouseStatus()
    await loadAnalysis({ preserveAccount: false })
  } catch (err) { error.value = err instanceof Error ? err.message : String(err) } finally { snapshotRefreshing.value = false }
}
async function openAccount(account: AccountRow) {
  const appliedDirectionAccount = directionData.value?.accounts?.find(item => item.platform === account.platform && item.login === account.login) || null
  const requestId = ++detailRequestId
  selectedAccount.value = account
  selectedDirectionAccount.value = appliedDirectionAccount
  accountDetail.value = null
  detailError.value = ''
  detailLoading.value = true
  try {
    const detail = await fetchAccountDetail(account, appliedDirectionAccount ? directionRequest.value : request.value)
    if (requestId === detailRequestId) accountDetail.value = detail
  } catch (err) {
    if (requestId === detailRequestId) detailError.value = err instanceof Error ? err.message : String(err)
  } finally {
    if (requestId === detailRequestId) detailLoading.value = false
  }
}
function closeAccount() {
  detailRequestId += 1
  selectedAccount.value = null
  selectedDirectionAccount.value = null
  accountDetail.value = null
  detailError.value = ''
}
async function loadBook() {
  const includeSymbols = activeTab.value === 'users'
  if ((bookData.value && (!includeSymbols || bookSymbolsLoaded.value)) || bookLoading.value || activeTab.value === 'overview') return
  bookLoading.value = true
  const population = data.value.population_accounts || data.value.accounts || []
  try {
    bookData.value = await fetchBookAnalytics(
      request.value,
      population.filter(a => a.book === 'abook').map(a => ({ platform: a.platform, login: a.login })),
      data.value.analysis_token,
      includeSymbols,
    )
    bookSymbolsLoaded.value = includeSymbols
  } catch (err) { error.value = err instanceof Error ? err.message : String(err) } finally { bookLoading.value = false }
}
async function loadDirection() {
  if (directionData.value || directionLoading.value || activeTab.value !== 'direction') return
  directionLoading.value = true
  try {
    directionData.value = await fetchDirectionAnalytics(directionRequest.value, data.value.analysis_token)
  } catch (err) { error.value = err instanceof Error ? err.message : String(err) } finally { directionLoading.value = false }
}
async function runDirection() {
  activeTab.value = 'direction'
  directionData.value = null
  selectedDirectionAccount.value = null
  await loadDirection()
}
async function loadNewcomer() {
  if (newcomerData.value || newcomerLoading.value || activeTab.value !== 'newcomer') return
  if (!data.value.analysis_token) {
    error.value = '请先在总览点击「应用筛选与验证」，再运行新人筛选'
    return
  }
  newcomerLoading.value = true
  try {
    newcomerData.value = await fetchNewcomerAnalytics(
      newcomerRequest.value,
      data.value.analysis_token,
      newcomerMaxActiveDays.value,
    )
  } catch (err) { error.value = err instanceof Error ? err.message : String(err) } finally { newcomerLoading.value = false }
}
async function runNewcomer() {
  activeTab.value = 'newcomer'
  newcomerData.value = null
  await loadNewcomer()
}
async function downloadExport() {
  const response = await exportAbook(request.value)
  if (!response.ok) throw new Error(await response.text() || `HTTP ${response.status}`)
  const link = document.createElement('a')
  link.href = URL.createObjectURL(await response.blob())
  link.download = 'abook_accounts.csv'
  link.click()
  URL.revokeObjectURL(link.href)
}
function downloadDirectionExport() {
  const accounts = directionData.value?.accounts || []
  const headers = ['platform', 'login', 'quadrant', 'long_trade_count', 'long_win_rate', 'long_profit_factor', 'long_side_pnl', 'short_trade_count', 'short_win_rate', 'short_profit_factor', 'short_side_pnl', 'in_total_abook']
  const lines = accounts.map(account => {
    const long = account.long?.selection || {}
    const short = account.short?.selection || {}
    return [account.platform, account.login, account.quadrant, long.trade_count, long.win_rate, long.profit_factor ?? '', long.side_pnl, short.trade_count, short.win_rate, short.profit_factor ?? '', short.side_pnl, account.in_total_abook].map(value => JSON.stringify(value ?? '')).join(',')
  })
  const blob = new Blob([[headers.join(','), ...lines].join('\n')], { type: 'text/csv;charset=utf-8' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob); link.download = 'abook_direction_accounts.csv'; link.click(); URL.revokeObjectURL(link.href)
}
function openDirectionAccount(account: Record<string, any>) {
  const match = (data.value.accounts || []).find(item => item.platform === account.platform && item.login === account.login)
  if (match) {
    openAccount(match)
    return
  }
  openAccount({
    platform: account.platform,
    login: account.login,
    account_group: account.account_group || '',
    book: account.book || 'bbook',
    selection_source: 'direction',
    selection_client_net_pnl: 0,
    validation_client_net_pnl: 0,
    validation_status: 'unknown',
    selection: {},
    validation: {},
    stability: { score: 0, tier: '—' },
    selection_flags: [],
  } as AccountRow)
}
function selectTab(tab: Tab) {
  activeTab.value = tab
  if (tab !== 'overview' && tab !== 'direction' && tab !== 'newcomer') loadBook()
}
function reset() {
  request.value.rules = { ...defaultRules }
  request.value.personal_candidate_list = false
  request.value.news_candidate_list = false
  directionRequest.value.rules = { ...defaultRules }
  directionRequest.value.personal_candidate_list = false
  directionRequest.value.news_candidate_list = false
  newcomerRequest.value.rules = { ...defaultRules }
  newcomerRequest.value.personal_candidate_list = false
  newcomerRequest.value.news_candidate_list = false
  newcomerMaxActiveDays.value = 60
  loadAnalysis()
}
watch(() => request.value.rules, () => { rulesDirty.value = true }, { deep: true })
const snapshotNeedsRefresh = computed(() => {
  const status = warehouseStatus.value
  if (!status || status.source !== 'local' || !status.snapshots) return false
  return Object.values(status.snapshots).some(snapshot => (
    snapshot.status !== 'ready'
    || snapshot.selection_start !== request.value.selection.start
    || snapshot.selection_end !== request.value.selection.end
  ))
})
onMounted(async () => {
  try { warehouseStatus.value = await fetchWarehouseStatus() } catch (err) { error.value = err instanceof Error ? err.message : String(err) }
  await loadAnalysis()
})
const tabs: Array<{ id: Tab; label: string }> = [
  { id: 'overview', label: '总览' }, { id: 'users', label: '用户结构' },
  { id: 'risk-routing', label: '风险与分流' }, { id: 'direction', label: '多空分向' },
  { id: 'newcomer', label: '新人滚动筛' },
]
</script>

<template>
  <div class="app-shell">
    <header class="topbar"><div><span class="kicker">RISK / A-BOOK ANALYTICS</span><h1>Abook 筛选与 Book 分析</h1><p>筛选期 → 样本外验证 · 用户表现与交易详情</p></div><div class="top-actions"><button class="ghost" @click="downloadExport">导出 Abook CSV</button><span class="status-pill" :class="loading ? 'busy' : 'ready'">{{ loading ? '查询中' : '就绪' }}</span></div></header>
    <div class="layout">
      <FilterSidebar :request="request" :data="data" :loading="loading" :rules-dirty="rulesDirty" :warehouse-status="warehouseStatus" :snapshot-needs-refresh="snapshotNeedsRefresh" :snapshot-refreshing="snapshotRefreshing" @apply="loadAnalysis" @refresh-snapshots="refreshLocalSnapshots" @reset="reset" />
      <main class="content" :class="{ 'direction-content': activeTab === 'direction' || activeTab === 'newcomer' }"><div v-if="error" class="alert error">{{ error }}</div><nav class="tabs"><button v-for="tab in tabs" :key="tab.id" :class="{ active: activeTab === tab.id }" @click="selectTab(tab.id)">{{ tab.label }}</button></nav>
        <template v-if="activeTab === 'overview'"><AbookAnalysis :data="data" @open="openAccount" /><KpiCards :data="data" /><SelectionFunnel :data="data" :rules-dirty="rulesDirty" /><AccountsTable :accounts="data.accounts || []" @open="openAccount" /></template>
        <template v-else-if="activeTab === 'direction'"><DirectionPanel :analytics="directionData" :loading="directionLoading" :request="directionRequest" @open="openDirectionAccount" @export="downloadDirectionExport" @run="runDirection" /></template>
        <template v-else-if="activeTab === 'newcomer'"><NewcomerPanel :analytics="newcomerData" :loading="newcomerLoading" :request="newcomerRequest" :max-active-days="newcomerMaxActiveDays" :analysis-token="data.analysis_token" @update:max-active-days="newcomerMaxActiveDays = $event" @run="runNewcomer" /></template>
        <template v-else><BookPerformance :analytics="bookData" :loading="bookLoading" :active-tab="activeTab" :request="request" :accounts="data.population_accounts || data.accounts || []" @open="openAccount" /></template>
      </main>
    </div>
    <AccountDrawer :account="selectedAccount" :detail="accountDetail" :direction-account="selectedDirectionAccount" :loading="detailLoading" :error="detailError" @close="closeAccount" />
  </div>
</template>
