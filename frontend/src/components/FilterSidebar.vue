<script setup lang="ts">
import type { AnalysisPayload, RequestModel, WarehouseStatus } from '../types'
const props = defineProps<{
  request: RequestModel
  data: AnalysisPayload
  loading: boolean
  rulesDirty: boolean
  warehouseStatus: WarehouseStatus | null
}>()
const emit = defineEmits<{ (event: 'apply'): void; (event: 'reset'): void }>()
const martingaleLevels = ['extreme', 'high', 'medium', 'low']
function selectedMartingaleLevelCount(): number {
  const selected = props.request.rules.excluded_martingale_levels
  return Array.isArray(selected) ? selected.length : 0
}
</script>

<template>
  <aside class="sidebar panel">
    <div class="panel-head"><div><span class="kicker">FILTERS</span><h2>分析窗口</h2></div><button class="ghost" @click="emit('reset')">重置</button></div>
    <div class="date-grid">
      <label>筛选开始<input v-model="request.selection.start" type="date"></label>
      <label>筛选结束<input v-model="request.selection.end" type="date"></label>
      <label>验证开始<input v-model="request.validation.start" type="date"></label>
      <label>验证结束<input v-model="request.validation.end" type="date"></label>
    </div>
    <label class="wide">最低交易笔数<input v-model.number="request.rules.min_trades" type="number" min="0"></label>
    <label class="wide">最大杠杆率 P95<input v-model.number="request.rules.max_leverage_p95_ratio" type="number" min="0"></label>
    <label class="wide">最低盈亏比<input v-model.number="request.rules.min_payoff_ratio" type="number" min="0" step="0.01"></label>
    <div class="filter-group"><strong>多空交易比例</strong><label>Long 比例下限<input v-model.number="request.rules.min_long_trades_ratio" type="number" min="0" max="1" step="0.01"></label><label>Long 比例上限<input v-model.number="request.rules.max_long_trades_ratio" type="number" min="0" max="1" step="0.01"></label><small>筛选期 Long 订单数 ÷（Long + Short 订单数），要求在配置区间内。</small></div>
    <label class="wide">Top1 日利润贡献率上限<input v-model.number="request.rules.max_top1_day_profit_contribution" type="number" min="0" max="1" step="0.01"></label>
    <div class="filter-group"><strong>核心规则</strong><label>最低胜率<input v-model.number="request.rules.min_win_rate" type="number" min="0" max="1" step="0.01"></label><label>最低 Profit Factor<input v-model.number="request.rules.min_profit_factor" type="number" min="0" step="0.01"></label></div>
    <div class="filter-group"><strong>确认马丁：阻断到 Bbook</strong><small>confirmed = 至少两个不同 7 日窗口完整命中；suspected = 仅疑似，不会被此列表硬阻断。</small><label v-for="level in martingaleLevels" :key="level" class="check"><input v-model="request.rules.excluded_martingale_levels" type="checkbox" :value="level"> {{ level }}（确认 {{ data.martingale?.confirmed_level_counts?.[level] ?? 0 }} 人）</label><small>勾选 = 阻断该等级；当前已选 {{ selectedMartingaleLevelCount() }}/4。未勾选的确认马丁仍会参与普通 Abook 规则；全选最严格。</small></div>
    <label class="check"><input v-model="request.personal_candidate_list" type="checkbox"> 个人候选名单（加入 Abook）</label>
    <label class="check"><input v-model="request.news_candidate_list" type="checkbox"> 新闻候选名单（加入 Abook）</label>
    <div class="platforms"><label v-for="platform in ['mt4', 'mt5', 'hh_mt5']" :key="platform" class="check"><input v-model="request.platforms" :value="platform" type="checkbox"> {{ platform }}</label></div>
    <div class="action-row">
      <button class="primary" :disabled="loading || !request.platforms.length" @click="emit('apply')">{{ loading ? '正在计算…' : '应用筛选与验证' }}</button>
    </div>
    <div v-if="data.martingale" class="status-box" :class="data.martingale.status === 'ready' ? 'ok' : 'warn'"><strong>{{ data.martingale.message || `马丁过滤：${data.martingale.status}` }}</strong><span>确认拦截 {{ data.martingale.blocked_users ?? 0 }} 人 · confirmed {{ data.martingale.confirmed_users ?? 0 }} 人 · 疑似马丁 {{ data.martingale.suspected_users ?? 0 }} 人</span></div>
    <div v-if="data.martingale?.missing_platforms?.length || data.risk_management?.missing_platforms?.length || data.avg_profit?.missing_platforms?.length" class="status-box warn"><strong>当前平台快照不完整</strong><span>缺少平台：{{ [...new Set([...(data.martingale?.missing_platforms || []), ...(data.risk_management?.missing_platforms || []), ...(data.avg_profit?.missing_platforms || [])])].join('、') }}；请先在终端运行 scripts/refresh_local_data.py。</span></div>
    <div v-if="data.avg_profit" class="status-box" :class="data.avg_profit.status === 'ready' ? 'ok' : 'warn'"><strong>avg_profit 本地快照：{{ data.avg_profit.status }}</strong><span>{{ data.avg_profit.records ?? 0 }} 条用户记录 · CUSTOM 活跃日均值<span v-if="data.avg_profit.source_min"> · {{ data.avg_profit.source_min }}～{{ data.avg_profit.source_max }}</span></span></div>
    <div v-if="warehouseStatus" class="status-box" :class="warehouseStatus.status === 'ready' ? 'ok' : 'warn'"><strong>本地 Warehouse：{{ warehouseStatus.status }}</strong><span v-if="warehouseStatus.data_start">覆盖 {{ warehouseStatus.data_start }}～{{ warehouseStatus.data_end }} · {{ warehouseStatus.updated_at || '尚未同步' }}</span><span v-else>请运行 scripts/refresh_local_data.py 同步数据</span></div>
    <p v-if="rulesDirty" class="hint warning">参数已修改，点击应用后重新计算。</p>
  </aside>
</template>
