<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { AccountRow } from '../types'
const props = defineProps<{ accounts: AccountRow[] }>()
const emit = defineEmits<{ (event: 'open', account: AccountRow): void }>()
const search = ref('')
const sortBy = ref('validation_client_net_pnl')
const sortDirection = ref<'asc' | 'desc'>('desc')
const page = ref(1)
const pageSize = 50
const filtered = computed(() => props.accounts.filter(account => `${account.platform} ${account.login} ${account.account_group}`.toLowerCase().includes(search.value.toLowerCase())))
const sorted = computed(() => [...filtered.value].sort((left: any, right: any) => {
  const a = left[sortBy.value] ?? left.stability?.score ?? 0
  const b = right[sortBy.value] ?? right.stability?.score ?? 0
  if (a === b) return 0
  return (a > b ? 1 : -1) * (sortDirection.value === 'asc' ? 1 : -1)
}))
const pageCount = computed(() => Math.max(1, Math.ceil(sorted.value.length / pageSize)))
const paged = computed(() => sorted.value.slice((page.value - 1) * pageSize, page.value * pageSize))
function toggleSort(field: string) { if (sortBy.value === field) sortDirection.value = sortDirection.value === 'asc' ? 'desc' : 'asc'; else { sortBy.value = field; sortDirection.value = 'desc' } }
watch(search, () => { page.value = 1 })
watch(pageCount, () => { if (page.value > pageCount.value) page.value = pageCount.value })
</script>

<template><section class="panel"><div class="panel-head"><div><span class="kicker">ACCOUNTS</span><h2>账户表现</h2></div><input v-model="search" class="search" placeholder="搜索平台、Login、账户组"></div><div class="table-scroll"><table><thead><tr><th>账户</th><th>最终分流</th><th>来源</th><th>马丁</th><th><button class="table-sort" @click="toggleSort('selection_client_net_pnl')">筛选期客户 P&amp;L ↕</button></th><th><button class="table-sort" @click="toggleSort('validation_client_net_pnl')">验证期客户 P&amp;L ↕</button></th><th>稳定性 ↕</th></tr></thead><tbody><tr v-for="account in paged" :key="`${account.platform}-${account.login}`" @click="emit('open', account)"><td>{{ account.platform }} / {{ account.login }}<small>{{ account.account_group }}</small><span v-if="account.july_new_user" class="tag">7月新用户</span></td><td><span class="tag">{{ account.book === 'abook' ? 'Abook' : 'Bbook' }}</span></td><td><small>{{ account.selection_source }}</small></td><td><span v-if="account.martingale_risk_level" class="tag danger">{{ account.martingale_risk_level }} · {{ account.martingale_detection_status === 'confirmed' ? '确认' : '疑似' }}</span><span v-else>—</span></td><td :class="account.selection_client_net_pnl >= 0 ? 'positive' : 'negative'">{{ account.selection_client_net_pnl.toFixed(2) }}</td><td :class="account.validation_client_net_pnl >= 0 ? 'positive' : 'negative'">{{ account.validation_client_net_pnl.toFixed(2) }}</td><td>{{ account.stability?.score ?? 0 }} · {{ account.stability?.tier ?? '—' }}</td></tr></tbody></table><div v-if="!paged.length" class="empty">暂无账户</div></div><div class="pagination"><button class="ghost compact" :disabled="page <= 1" @click="page--">上一页</button><span>第 {{ page }} / {{ pageCount }} 页 · {{ sorted.length }} 个账户</span><button class="ghost compact" :disabled="page >= pageCount" @click="page++">下一页</button></div></section></template>
