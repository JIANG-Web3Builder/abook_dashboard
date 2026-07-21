# A-Book 用户筛选与 7 月验证面板

FastAPI 后端 + Vue 3/ECharts 前端，用于使用 5–6 月数据筛选用户，并用 7 月数据独立验证筛选效果。

## 运行

### Phase 0–6 升级功能

马丁快照探查和构建：

```bash
.venv/bin/python scripts/probe_martingale_schema.py
.venv/bin/python scripts/build_martingale_snapshot.py --selection-start 2026-05-01 --selection-end 2026-06-30
```

前端构建：

```bash
cd frontend
pnpm install --ignore-scripts
pnpm run build
cd ..
./run_dashboard.sh
```

构建产物输出到 `static/`，FastAPI 通过 `/assets` 托管。页面包含马丁状态与五层明细、误判成本、筛选漏斗、Abook/Bbook 分析和 Abook CSV 导出；用户结构页包含双纵轴累计客户 P&L 与每日客户 P&L 图表，参数寻优功能已删除。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd /Users/jianghe/abook_hedging && ./run_dashboard.sh
```

首次运行前，把本地 ClickHouse 配置写入被 Git 忽略的 `.env`（可参考 `.env.example`）。脚本会自动加载 `.env` 并启动服务。
打开 http://localhost:8000。生产环境请使用只读 ClickHouse 账号，并通过密钥管理注入 `CLICKHOUSE_PASSWORD`。

当前 Abook 默认路由规则为：筛选期总交易数 `>= 75`、胜率 `>= 50%`、`Profit Factor > 1.25`、盈亏比 `>= 0.4`、Top1 日利润贡献率 `< 30%`、用户杠杆率 P95 `<= 5000`，并执行确认式马丁硬拦截。马丁快照同时保留宽松候选和完整确认证据：只有至少两个不同 7 日窗口命中完整 `confirmed_gate` 的用户，且其等级在排除列表内，才进入马丁硬拦截；单窗口 extreme 或只命中宽松 `gate` 的用户标记为“疑似马丁”，仍参与普通 Abook 规则。筛选期总客户净 P&L、5 月/6 月单月客户净 P&L、活跃交易天数、平均日净 P&L、盈利月份占比、月度一致性、日盈利率 95% 下限、单月日利润贡献率、稳定性评分和 `avg_profit` 最低值不再作为 Abook 路由条件；这些 P&L 和稳定性指标只用于分析展示及后续样本外验证。其他未通过 Abook 的账户统一进入 Bbook。账户没有亏损交易时，PF 在筛选上视为无穷大，API 中以 `null` 表示，避免 JSON 非法数值。

这组默认值是在当前 5–6 月筛选、7 月 1–16 日验证快照上复核得到的候选组合：94 个 Abook 用户，7 月客户净 P&L 约 `+25,236 USD`，其中 48 个盈利、30 个亏损。路由关闭默认 R4 通道，杠杆率 P95 `<= 5000`、高杠杆持仓例外 `300s`、盈亏比 `>= 0.4`；R4 仍可在侧栏手动开启。它是样本内寻优结果，不能视为未来收益保证；上线前应在新的完整月份重新复核。详见 `docs/analysis/2026-07-21-july-pnl-over20k.md`。

R4 是独立于普通规则的 Abook 通道，只使用筛选期（默认 5–6 月）的 `7D_SLIDING` 持仓时长桶数据，不使用 7 月数据。当前运行阈值为：软对齐、主战场交易占比 `>= 35%`、整体胜率 `>= 55%`、总交易数 `>= 20`、主战场交易数 `>= 8`；通过 R4 且杠杆风险条件通过的用户可以进入 Abook，最终仍受马丁硬拦截。相比研究中的 38.1%/58.9%/10/5 阈值，当前版本减少精确分位点带来的脆弱性，同时提高样本门槛。R4 快照通过 `scripts/build_r4_snapshot.py` 生成，源文件默认是 `/Users/jianghe/holding_buckets/account_holding_buckets.csv`，也可使用 `ABOOK_R4_SOURCE_PATH` 和 `ABOOK_R4_SNAPSHOT_PATH` 覆盖。7 月才开始交易的用户不参与 R4 放行，只标记为“7月新用户”。默认关闭 R4；侧栏可按需启用。

账户组中不区分大小写包含 `test` 或 `demo` 的账户是测试账号，所有查询、服务层聚合和账户详情都会硬性排除。7 月验证阶段会保留没有交易的筛选账户，并标记为“无交易”。

左侧“个人候选名单（加入 Abook）”默认关闭；开启后读取 `ABOOK_PERSONAL_CANDIDATE_LIST_PATH` 指定的 CSV，未设置时使用本机的个人候选名单路径。名单只作为 Abook 强制加入名单，仍受平台、账户组和 test/demo 边界约束，并按 `(platform, login)` 去重。

## API

- `GET /api/health`
- `GET /api/abook/filters`
- `POST /api/abook/analysis`
- `GET /api/abook/export` / `POST /api/abook/export`
- `POST /api/abook/book-analytics`
- `GET /api/abook/accounts/{platform}/{login}`

分析请求使用显式阶段窗口：

```json
{
  "selection": {"start": "2026-05-01", "end": "2026-06-30"},
  "validation": {"start": "2026-07-01", "end": "2026-07-16"},
  "rules": {
    "min_trades": 75,
    "min_active_days": 0,
    "min_win_rate": 0.5,
    "min_profit_factor": 1.25,
    "min_payoff_ratio": 0.4,
    "min_avg_daily_profit": 0,
    "min_avg_profit": 0,
    "min_positive_month_rate": 0.5,
    "max_top1_day_profit_contribution": 0.3,
    "max_daily_profit_month_contribution": 0.6,
    "max_leverage_p95_ratio": 5000,
    "max_high_leverage_holding_seconds": 300,
    "min_direction_day_rate_lower_bound": 0.55,
    "min_stability_score": 70,
    "enable_r4": false,
    "r4_min_passing_weeks": 1
  }
}
```

所有分析查询均使用 `FINAL`，并过滤 `is_deleted = 0`。查询会为用户生成完整的月份网格，因此验证期没有交易的账户仍会返回。交易 P&L 只计算 `action IN (0,1)`；`action IN (2,3)` 的资金/信用流水单独返回。页面中的统一 `market_pnl`、毛盈亏和 PF 均以 Deals 聚合口径为准；撮合表的 `matched_market_pnl` 仅作为对账字段保留。理论镜像收益与客户净交易 P&L 分开返回。

当前 Abook 利润影响是理论估算：假设进入 Abook 后交易所的对手盘利润为 0，因此迁移增量为用户净交易 P&L；不包含真实外部成交、点差、对冲成本、滑点和流动性成本。平均盈利快照只用于分析展示，不参与 Abook 资格判断；低于阈值的用户不会从人口中删除，而是进入 Bbook，保证所有用户只有 Abook/Bbook 两种归属。

页面的符号口径固定为：customer_net_pnl 是客户净交易 P&L，不代表公司利润；留在 Bbook 时公司利润为客户净交易 P&L 取负；Abook 用户的正负只用于命中、误判和样本外验证，不能直接当作公司盈亏。Bbook “漏网”只统计最终进入 Bbook 但验证期转正的用户，公司损失为负值；总额按全部漏网账户计算，页面名单默认只显示金额大于 100 的账户。

volume 沿用 dwd_matched_trades.volume 原始单位；不同品种可能有不同交易量精度（例如外汇数据可出现 1000），不能直接当作统一“手数”。Book 分析页面不统计或展示 turnover。风险快照的基准杠杆恢复为 `abs(dwd_matched_trades.turnover) / 当日最新余额`，按平仓日归集；同时用匹配订单的时间线重建并发未平仓敞口，最后取两者较高值用于风险筛选。若原始 deals 表存在，则再补上未被匹配订单覆盖的入场量；没有该表时会保留匹配订单估计，不把未知仓位当作 0。

## 本地风险快照

杠杆筛选不在每次页面请求中重新聚合。使用以下命令按筛选期生成本地快照：

```bash
.venv/bin/python scripts/build_user_risk_snapshot.py --selection-start 2026-05-01 --selection-end 2026-06-30

# 平均盈利快照（首次或日期范围变化时运行；运行网页时只读取本地 JSON）
.venv/bin/python scripts/build_avg_profit_snapshot.py --selection-start 2026-05-01 --selection-end 2026-06-30

# R4 快照（只用 5–6 月 7D_SLIDING 数据；运行网页时只读取本地 JSON）
.venv/bin/python scripts/build_r4_snapshot.py --selection-start 2026-05-01 --selection-end 2026-06-30
```

页面左侧“刷新全部数据”会按当前筛选期和平台重建风险、平均盈利、马丁、R4 四个本地快照。勾选 MT4 或其他新增平台后，必须点击一次“刷新全部数据”，否则旧快照只覆盖 `mt5/hh_mt5`；系统会把覆盖到的平台继续处理，并将未覆盖平台标记为 `partial`、暂留 Bbook，同时在左侧提示缺失平台，不再把整条筛选链路静默判成正常。四个快照全部成功后页面会自动重新计算；验证期结束日仍由页面日期控制。例如验证结束日设为 `2026-07-16`，主查询会读取至 7 月 16 日（排他上界为 2026-07-17）。如果 ClickHouse 尚未落库 7 月 16 日数据，覆盖信息会反映实际可用范围。

快照默认写入 `data/user_risk_snapshot.json`，也可用 `ABOOK_RISK_SNAPSHOT_PATH` 覆盖路径。快照从 `risk.ods_mt4_daily_balance` 和 `risk.ods_mt5_daily_balance` 读取用户每日余额，用 `login + datetime` 建立余额序列，再将筛选期每个订单的平仓日匹配到该日及之前最新余额，使用每日 `abs(dwd_matched_trades.turnover)` / 当日余额计算成交额杠杆的平均值、峰值和 P95。MT4/MT5 的日期字符串统一按可解析的日期时间处理。`balance_prev_month` 仅作为旧快照兼容字段，新的参考值为 `balance_latest`；余额小于等于 0 或平仓日没有可匹配余额时不计算该日杠杆。SQL 会排除 group 中大小写不敏感包含 `test` 或 `demo` 的账户，例如 `real\\FPlive\\TEST_USD_ZO_BA_NT_H`、`demo\\HHdemo\\forexhh-USD`。该本地文件不会提交到 GitHub。

全量风险名单会在用户 SQL 中按平台生成安全的 Login 排除条件，避免把数万 Login 放进 HTTP 参数导致 414；只有指定少量 Login 时才使用交集过滤。

余额为空、小于等于 0 或订单日没有余额记录时不会把杠杆率伪造为 0：该用户标记为 `unknown_nonpositive_balance` 或 `missing_daily_balance`，跳过杠杆上限，但继续执行胜率、PF、盈亏比、月度持续性和 Top1 日贡献率筛选。快照缺失或日期不匹配时，页面会显示风险快照状态，不会声称杠杆过滤已生效。

高杠杆例外不是无条件放行：只有杠杆率 P95 超过上限且筛选期中位持仓不超过例外秒数时才放行；短持仓时间来自本地快照，因此仍可在页面请求中使用本地 Login 过滤。

页面顶部的 Abook / Bbook 盈亏分析只展示两种最终归属。Abook 和 Bbook 分别展示筛选期、验证期客户 P&L；Bbook 公司盈亏为客户 P&L 取负，Abook 客户盈亏只用于命中、误判和验证，不能冒充公司利润。

总览中的 Abook 分析按筛选期（5–6 月）和验证期（7 月 1–16 日）分别展示总盈利、总亏损和净 P&L；净 P&L = 总盈利 - 总亏损。下方列最终分流到 Abook 的用户，分别显示 5 月、6 月、7 月 P&L、胜率、交易数和稳定性。点击用户可查看当前分析窗口内的账户交易明细、品种汇总、风险和马丁信息；交易明细仍受接口 5000 条上限约束。Bbook 漏网保留在 Abook 分析区域下方，并显示 5 月、6 月和 7 月单月 P&L。

阶段公司利润概览是总体人口基线：它使用相同日期、平台、账户组和 test/demo 排除条件，但不使用 Abook 的胜率、PF、稳定性或杠杆筛选。这样修改筛选参数不会改变每个月的总体公司利润；只有日期、平台、账户组或 Login 范围变化时，基线才会变化。

页面的 P&L 区域使用筛选期与验证期的日度增量曲线，悬浮到日期时显示公司净 P&L、对应 Book 净 P&L、盈利 P&L 和亏损 P&L 的当天值。每个账户只属于 Abook 或 Bbook，筛选原因和马丁等级仅作为审计信息。

这里的 `Profit Factor` 不是单笔交易的传统盈亏比：PF = 阶段总盈利 ÷ 阶段总亏损绝对值；页面的“盈亏比”是 `Payoff Ratio = 平均盈利交易 ÷ 平均亏损交易绝对值`。胜率、Payoff Ratio 和 PF 必须结合使用，单独提高胜率可能得到小赚大亏的策略。当前筛选杠杆率使用每日成交额 / 交易日最新余额，并以并发未平仓敞口估计做保守校正，是仓位管理的可审计代理，不等同于平台真实保证金风险率或净敞口。

## 稳定性与反过拟合指标

筛选期候选会额外返回稳定性评分和风险标记。评分只用于分析展示，不参与当前 Abook 路由；评分只使用筛选期，不读取 7 月：

- 月度持续性：盈利月份占比、最差月份 P&L、月度波动。
- 日度可信度：盈利/亏损天数、Wilson 95% 区间、日收益标准差。
- 收益质量：单笔期望、平均盈利、平均亏损、Payoff Ratio、收益/最大回撤。
- 集中度风险：Top 1 日正/负收益占阶段收益的比例。
- 样本等级：根据交易笔数和活跃月份分为高、中、低置信度；活跃交易天数不再作为路由门槛。

`Core` 表示稳定性指标和样本门槛通过，`Watch` 表示方向成立但仍有样本、集中度或日度置信度风险；两者都必须使用 7 月样本外结果复核，不能把 Core 当作未来盈利保证。当前有效交易和撮合数据从 2026-05-15 开始，5 月会显示为部分月份；原始 Deals 表中更早记录如果标记为 `is_deleted=1`，只作为原始覆盖审计，不纳入 P&L。

## 测试

```bash
.venv/bin/python -m pytest -q
```
real\FPlive\TEST_USD_ZO_BA_NT_H 或 demo\HHdemo\forexhh-USD，像这种 group 里面含有 test/demo 的，我们要过滤掉，是测试账号。
