# 2026-07-20 七月 Abook 净 P&L 参数寻优

## 目标

在 5–6 月筛选、7 月 1–16 日验证窗口上，最大化 Abook 验证期客户净 P&L（在 Abook 公司利润=0、Bbook 公司利润=`-client_net_pnl` 假设下，等价于最大化理论公司净利润增量），要求至少 `10,000 USD`。

## 方法

- 基于一次完整分析请求复核默认参数
- 一次拉取 ClickHouse 全量人口 + 本地风险/马丁/avg_profit 快照
- 内存网格搜索 28,800 组主动路由参数
- 最优组合再走完整 `build_two_stage_payload` 复核

## 结果

最优默认参数（已写入 `app/models.py` 与前端 `App.vue`）：

| 参数 | 值 |
|------|-----|
| min_trades | 75 |
| min_win_rate | 0.5 |
| min_profit_factor | 1.25（严格 `>`） |
| min_payoff_ratio | 0.4 |
| max_top1_day_profit_contribution | 0.3（严格 `<`） |
| max_leverage_p95_ratio | 5000 |
| max_high_leverage_holding_seconds | 300 |
| excluded_martingale_levels | extreme/high/medium/low |

复核结果：

- Abook 账户：90
- 7 月 Abook 净 P&L：`+18,286.44 USD`
- 盈利 / 亏损（中性带 ±10）：46 / 29
- 达标组合数（≥10000）：313

相对旧默认（payoff≥0.5、杠杆 P95≤2000），主要增益来自放宽杠杆 P95 至 5000，以及略放宽盈亏比至 0.4。

## 限制

样本内复核，不能当作未来收益保证；完整新月数据到位后应重新运行分析复核。
