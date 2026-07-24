<script setup lang="ts">
import type { AnalysisPayload } from '../types'
const props = defineProps<{ data: AnalysisPayload; rulesDirty?: boolean }>()
const labels: Record<string, string> = { eligible: '全量账户', sample_qualified: '样本达标', direction_balance_passed: '多空比例通过', leverage_passed: '杠杆 P95 通过', non_martingale: '非马丁', abook: 'Abook' }
const reasonLabels: Record<string, string> = {
  not_in_query_population: '不在查询人口范围',
  insufficient_sample: '交易数不足',
  long_trades_ratio: 'Long 交易比例不在区间内',
  leverage_p95_ratio: '杠杆率 P95 超限',
  martingale_blocked: '命中马丁阻断规则',
  abook_rules_failed: 'Abook 规则未全部通过',
}

function number(value: unknown): number {
  const parsed = Number(value ?? 0)
  return Number.isFinite(parsed) ? parsed : 0
}

function percent(value: unknown): string { return `${(number(value) * 100).toFixed(0)}%` }

function appliedAbookRules(rules: Record<string, any>): string {
  const criteria = [
    `胜率 ≥ ${percent(rules.min_win_rate)}`,
    `PF > ${number(rules.min_profit_factor)}`,
    `盈亏比 ≥ ${number(rules.min_payoff_ratio)}`,
    `Top1 日利润贡献率 < ${percent(rules.max_top1_day_profit_contribution)}`,
  ]
  return criteria.join('；')
}

function criterion(name: string): string {
  const rules = props.data.rules || {}
  switch (name) {
    case 'eligible':
      return '所选平台、账户组和 Login 范围；排除账户组中含 test/demo 的测试账号'
    case 'sample_qualified':
      return `交易数 ≥ ${number(rules.min_trades)}；不限制活跃交易天数`
    case 'leverage_passed':
      return `杠杆率 P95 ≤ ${number(rules.max_leverage_p95_ratio)}；超过时，中位持仓 ≤ ${number(rules.max_high_leverage_holding_seconds)} 秒可例外`
    case 'direction_balance_passed':
      return `Long 交易比例 = Long 订单数 ÷（Long + Short 订单数），要求 ${number(rules.min_long_trades_ratio)} ≤ ratio ≤ ${number(rules.max_long_trades_ratio)}`
    case 'non_martingale':
      return `马丁风险等级不在 ${(rules.excluded_martingale_levels || []).join('、') || '阻断列表'}；快照不可用也不放行`
    case 'abook':
      return `已应用：${appliedAbookRules(rules)}；仍受平台/账户组边界和马丁硬阻断约束`
    default:
      return '—'
  }
}

function dropReasons(stage: { drop_reasons?: Record<string, number> }): string {
  return Object.entries(stage.drop_reasons || {})
    .map(([key, value]) => `${reasonLabels[key] || key}: ${value}`)
    .join(' · ') || '—'
}
</script>
<template>
  <section class="panel">
    <div class="panel-head"><div><span class="kicker">FUNNEL</span><h2>Abook 资格漏斗</h2></div><span class="hint">{{ props.rulesDirty ? '左侧参数已修改，点击应用后更新漏斗' : '漏斗显示本次已应用规则' }}</span></div>
    <div class="funnel">
      <div v-for="stage in data.funnel?.stages || []" :key="stage.name" class="funnel-row">
        <span>{{ labels[stage.name] || stage.name }}</span>
        <b>{{ stage.count }}</b>
        <div class="funnel-detail"><div>筛选标准：{{ criterion(stage.name) }}</div><small>本步淘汰：{{ dropReasons(stage) }}</small></div>
      </div>
    </div>
  </section>
</template>
