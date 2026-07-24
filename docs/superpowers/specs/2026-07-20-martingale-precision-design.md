# 马丁误报收敛与确认式拦截设计

## 目标

降低正常顺势加仓、偶发异常放量和单窗口异常造成的马丁误判。只有重复出现、证据完整的确认马丁用户才进入 Abook 硬拦截；证据不足的用户保留“疑似马丁”标签，但继续参与普通 Abook 规则筛选。

## 当前问题

现有快照按用户取所有 7 日窗口中的最严重等级。单个窗口命中 extreme 就会把用户整体标成 extreme。窗口内的 extreme 使用宽松 `gate`，只要求基础亏损加仓模式再叠加一个附加信号，没有要求完整 `confirmed_gate`。此外，放量倍数、序列长度和序列亏损没有同时验证，且没有要求该行为在多个窗口重复出现。

这会把以下行为误判为马丁：

- 正常趋势策略在盈利后顺势加仓；
- 单周流动性异常导致的仓量放大；
- 仅有一个长序列，但没有持续亏损后逆势加仓；
- 亏损加仓比例高，但加仓部分并未产生负收益或没有持续性。

## 设计原则

1. **策略标签与路由拦截分离**：`suspected` 只展示标签，`confirmed` 才参与硬拦截。
2. **完整证据优先于单一指标**：extreme 必须使用完整 `confirmed_gate`，不能由宽松 `gate` 单独触发。
3. **重复性优先于最坏窗口**：单个异常窗口不改变用户级确认状态。
4. **保留审计证据**：响应继续返回各层命中次数、窗口总数、确认窗口数、严重窗口数和关键指标。
5. **不使用验证期数据**：马丁快照继续只使用选择期窗口，避免 7 月验证数据泄漏。

## 窗口级判定

保留已有基础层，但调整其作用：

- `layer1`：亏损加仓序列数量、比例和总序列数达到最低门槛；
- `layer2`：序列仓量放大；
- `layer3`：序列较长且序列交易占比高；
- `layer4`：亏损加仓显著高于盈利加仓，且盈利加仓比例较低；
- `layer5`：高胜率但序列整体亏损，或高放量、长序列且亏损加仓收益为负。

新增窗口级状态：

- `confirmed_gate = layer1 AND layer3 AND layer4`；
- `confirmed_extreme_candidate = confirmed_gate AND escalation >= 1.5 AND max_sequence_length >= 7 AND sequence_total_profit < 0 AND averaging_down_profit <= 0`；
- `expanded_candidate = gate`，仅用于疑似标签，不得直接形成 confirmed 拦截。

正常盈利后的顺势加仓通过 `pyramiding_ratio`、`averaging_down_ratio` 和 `averaging_down_profit` 的组合排除：仅有盈利加仓、没有足够亏损加仓证据的窗口不能成为 confirmed 马丁窗口。

## 用户级确认

不再使用“任意一个最坏窗口决定用户等级”。对于选择期内窗口：

- `confirmed_windows`：命中 `confirmed_gate` 的窗口数；
- `confirmed_extreme_windows`：命中 `confirmed_extreme_candidate` 的窗口数；
- `expanded_windows`：命中宽松 `gate` 但未命中完整确认门槛的窗口数。

用户状态定义：

- `confirmed`：至少 2 个独立窗口命中确认门槛；
- `suspected`：至少 1 个窗口命中宽松候选，或只有 1 个确认窗口；
- `none`：没有命中窗口。

用户风险等级仍可保留 `extreme/high/medium/low` 作为严重程度，但硬拦截条件变为：

```text
martingale_detection_status == "confirmed"
AND risk_level in excluded_martingale_levels
```

因此，一个只有单个异常窗口的 extreme 会显示为 `suspected`，不会自动拦截；连续多个确认 extreme 窗口才会进入 Bbook 硬拦截。

为避免短样本用户因两个相邻窗口重复计算而被放大，窗口计数使用去重后的窗口结束日/窗口区间；实现中至少保留两个不同 `window_end` 的确认窗口。

## 输出兼容性

保留现有字段：

- `risk_level`；
- `detection_mode`；
- `layer_hits`；
- 各等级窗口计数；
- 关键最大/最小指标。

新增字段：

- `martingale_detection_status`: `none | suspected | confirmed`；
- `confirmed_windows`；
- `confirmed_extreme_windows`；
- `expanded_windows`；
- `hard_block`: 是否满足当前排除等级且状态为 confirmed。

`detection_mode` 继续兼容现有含义，但统一规则为：存在确认窗口时为 `confirmed`，只有宽松窗口命中时为 `expanded`。

## 路由行为

- 现有 `martingale_status` 继续表示快照状态（`ready/missing/invalid/stale`），不改变含义；
- `martingale_detection_status` 表示用户级检测状态（`none/suspected/confirmed`）；
- `confirmed` 且等级在排除列表内：硬拦截到 Bbook；
- `suspected`：不触发马丁硬拦截，继续参与普通 Abook 规则；
- `none`：继续参与普通 Abook 规则；
- 快照缺失、损坏或过期：继续保持现有安全行为，不解释为“没有马丁用户”。

## 测试与验收

新增单元测试覆盖：

1. 单个 extreme 窗口标记为 suspected，不硬拦截；
2. 两个不同窗口命中确认 extreme，标记为 confirmed 并硬拦截；
3. 只有 `gate`、没有 `confirmed_gate` 的用户不硬拦截；
4. 只有盈利加仓、没有亏损加仓证据的顺势策略不标记 confirmed；
5. 多层命中计数和新增字段在快照、账户行和 API 响应中一致；
6. 现有快照状态、普通 Abook/Bbook 路由和 JSON 序列化测试保持通过。

真实数据验收记录：

- confirmed 与 suspected 用户数量；
- extreme 用户中 confirmed/suspected 的拆分；
- 新旧规则的 Abook 数量变化；
- 7 月验证期 P&L 和 Abook 误判成本变化；
- 至少抽查 20 个原 extreme 用户的窗口证据。

## 非目标

- 本次不重建底层 `risk.dws_account_martingale_window` 表；
- 本次不引入黑盒机器学习分类器；
- 本次不使用 7 月 P&L 参与马丁判定；
- 本次不改变其他分析模块的定义。
