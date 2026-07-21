<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { exportAbook, fetchAccountDetail, fetchAnalysis, fetchBookAnalytics, refreshSnapshots } from './api'
import type { AccountDetailPayload, AccountRow, AnalysisPayload, RequestModel, Tab } from './types'
import FilterSidebar from './components/FilterSidebar.vue'
import KpiCards from './components/KpiCards.vue'
import SelectionFunnel from './components/SelectionFunnel.vue'
import AbookAnalysis from './components/AbookAnalysis.vue'
import AccountsTable from './components/AccountsTable.vue'
import AccountDrawer from './components/AccountDrawer.vue'
import BookPerformance from './components/BookPerformance.vue'

const defaultRules: Record<string, number | string[] | boolean> = {
  min_trades: 75, min_win_rate: 0.5, min_profit_factor: 1.25,
  min_payoff_ratio: 0.4,
  max_top1_day_profit_contribution: 0.3,
  max_leverage_p95_ratio: 5000, max_high_leverage_holding_seconds: 300,
  high_confidence_trades: 100, high_confidence_days: 30,
  enable_r4: false, r4_min_passing_weeks: 1,
  excluded_martingale_levels: ['extreme', 'high', 'medium', 'low'],
}
const request = ref<RequestModel>({
  selection: { start: '2026-05-01', end: '2026-06-30' }, validation: { start: '2026-07-01', end: '2026-07-16' },
  platforms: ['mt4', 'mt5', 'hh_mt5'], filters: { groups: [], logins: [] }, rules: { ...defaultRules }, personal_candidate_list: false, news_candidate_list: false,
})
const data = ref<AnalysisPayload>({ accounts: [] })
const activeTab = ref<Tab>('overview')
const loading = ref(false)
const bookLoading = ref(false)
const bookSymbolsLoaded = ref(false)
const refreshing = ref(false)
const refreshMessage = ref('')
const error = ref('')
const rulesDirty = ref(false)
const bookData = ref<any | null>(null)
const selectedAccount = ref<AccountRow | null>(null)
const accountDetail = ref<AccountDetailPayload | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
let detailRequestId = 0

async function loadAnalysis(options: { preserveAccount?: boolean } = { preserveAccount: true }) {
  const accountBeforeRefresh = selectedAccount.value
  loading.value = true; error.value = ''; bookData.value = null; bookSymbolsLoaded.value = false
  if (!options.preserveAccount) {
    closeAccount()
  }
  try {
    data.value = await fetchAnalysis(request.value)
    if (options.preserveAccount && accountBeforeRefresh) {
      const refreshed = (data.value.accounts || []).find(account => account.platform === accountBeforeRefresh.platform && account.login === accountBeforeRefresh.login)
      if (refreshed) selectedAccount.value = refreshed
    }
    rulesDirty.value = false
  } catch (err) { error.value = err instanceof Error ? err.message : String(err) } finally { loading.value = false }
}
async function openAccount(account: AccountRow) {
  const requestId = ++detailRequestId
  selectedAccount.value = account
  accountDetail.value = null
  detailError.value = ''
  detailLoading.value = true
  try {
    const detail = await fetchAccountDetail(account, request.value)
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
  accountDetail.value = null
  detailError.value = ''
}
async function refreshAllSnapshots() {
  refreshing.value = true
  refreshMessage.value = ''
  error.value = ''
  try {
    const result = await refreshSnapshots(request.value)
    bookData.value = null
    refreshMessage.value = `刷新成功：${Object.keys(result.snapshots).length} 个快照已更新，正在重新计算`
    await loadAnalysis()
  } catch (err) {
    refreshMessage.value = `刷新失败：${err instanceof Error ? err.message : String(err)}`
  } finally {
    refreshing.value = false
  }
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
async function downloadExport() {
  const response = await exportAbook(request.value)
  if (!response.ok) throw new Error(await response.text() || `HTTP ${response.status}`)
  const link = document.createElement('a')
  link.href = URL.createObjectURL(await response.blob())
  link.download = 'abook_accounts.csv'
  link.click()
  URL.revokeObjectURL(link.href)
}
function selectTab(tab: Tab) { activeTab.value = tab; if (tab !== 'overview') loadBook() }
function reset() { request.value.rules = { ...defaultRules }; request.value.personal_candidate_list = false; request.value.news_candidate_list = false; loadAnalysis() }
watch(() => request.value.rules, () => { rulesDirty.value = true }, { deep: true })
onMounted(loadAnalysis)
const tabs: Array<{ id: Tab; label: string }> = [
  { id: 'overview', label: '总览' }, { id: 'users', label: '用户结构' },
  { id: 'risk', label: '风险敞口' }, { id: 'routing', label: '分流质量' },
]
</script>

<template>
  <div class="app-shell">
    <header class="topbar"><div><span class="kicker">RISK / A-BOOK ANALYTICS</span><h1>Abook 筛选与 Book 分析</h1><p>筛选期 → 样本外验证 · 用户表现与交易详情</p></div><div class="top-actions"><button class="ghost" @click="downloadExport">导出 Abook CSV</button><span class="status-pill" :class="loading ? 'busy' : 'ready'">{{ loading ? '查询中' : '就绪' }}</span></div></header>
    <div class="layout">
      <FilterSidebar :request="request" :data="data" :loading="loading" :rules-dirty="rulesDirty" :refreshing="refreshing" :refresh-message="refreshMessage" @apply="loadAnalysis" @reset="reset" @refresh="refreshAllSnapshots" />
      <main class="content"><div v-if="error" class="alert error">{{ error }}</div><nav class="tabs"><button v-for="tab in tabs" :key="tab.id" :class="{ active: activeTab === tab.id }" @click="selectTab(tab.id)">{{ tab.label }}</button></nav>
        <template v-if="activeTab === 'overview'"><AbookAnalysis :data="data" @open="openAccount" /><KpiCards :data="data" /><SelectionFunnel :data="data" :rules-dirty="rulesDirty" /><AccountsTable :accounts="data.accounts || []" @open="openAccount" /></template>
        <template v-else><BookPerformance :analytics="bookData" :loading="bookLoading" :active-tab="activeTab" /></template>
      </main>
    </div>
    <AccountDrawer :account="selectedAccount" :detail="accountDetail" :loading="detailLoading" :error="detailError" @close="closeAccount" />
  </div>
</template>
