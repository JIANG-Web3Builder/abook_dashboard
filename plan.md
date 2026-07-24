本地 Warehouse 与独立刷新 CLI
实施状态：已完成代码实现，等待最终验收。

Summary
采用“独立 CLI 刷新、Dashboard 只读”的方案：
.venv/bin/python scripts/refresh_local_data.py \
  --selection-start 2026-05-01 \
  --selection-end 2026-06-30 \
  --validation-start 2026-07-01 \
  --validation-end 2026-07-23
脚本默认执行：
同步远程 ClickHouse 到 data/warehouse/
更新 manifest 与 generation
从本地 Warehouse 重建三份 JSON 快照
输出同步月份、覆盖范围、行数、耗时和快照状态
Dashboard 移除“刷新全部数据”按钮及刷新 API，只保留本地查询和只读数据状态。
主要改动
新增 scripts/refresh_local_data.py，日期参数四项必填；平台默认全部，可用重复的 --platform mt5 指定平台。
新增 app/warehouse.py：Parquet 目录与 canonical schema；
manifest/generation 管理；
精确按表、平台、日期区间校验 coverage；
近 7 天重拉，超过 7 天默认冻结；
文件锁、临时文件、原子发布；
支持 --sync-only、--snapshots-only、--dry-run；
支持显式 --reconcile-month YYYY-MM 修复历史月份。

新增统一的 Local/Remote QueryExecutor，所有分析查询、账户详情、方向分析、Book 分析、filters 和快照 builder 共用。
Deals 保留 action 0,1,2,3 和必要的 is_deleted 信息，避免丢失资金流水及覆盖审计口径。
dwd_matched_trades 的分区同时支持 exit_time、entry_time 和未平仓记录，避免风险 exposure 缺数据。
同步 SQL 将 login、platform 和核心时间/数值字段规范化后再落盘；本地查询不再依赖远程 FINAL。
snapshot 保存对应的 warehouse_generation；generation 不一致时标记为 stale，不参与风险过滤。
同步或快照完成后清理 analysis、direction 等内存缓存。
Dashboard 新增只读的 Warehouse 状态展示：数据源、最后同步时间、generation、覆盖范围、快照是否 stale。
移除 [app/main.py](/Users/jianghe/abook_hedging/app/main.py) 中的刷新 API、[app/models.py](/Users/jianghe/abook_hedging/app/models.py) 中仅用于刷新 API 的模型，以及前端刷新按钮和调用逻辑。
数据说明文件
新增并提交：
[data/README.md](/Users/jianghe/abook_hedging/data/README.md)
内容包括：
data/warehouse/ 目录结构；
各表的来源、分区字段和字段口径；
action、is_deleted、test/demo 账户过滤规则；
近 7 天重拉与历史冻结策略；
CLI 使用示例；
sync-only、snapshots-only、reconcile-month 的用途；
失败恢复、generation 和 stale 快照说明；
不要手动删除或修改 Parquet 文件的操作规范。
data/warehouse/、Parquet 文件和 JSON 快照继续加入 .gitignore，但 data/README.md 不忽略。
测试与验收
新增测试覆盖：
action=2/3 资金流水；
tombstone 和 is_deleted；
MT4 无 platform 字段；
test/demo group 过滤；
部分月份与平台 coverage；
未平仓 exposure；
重复同步幂等；
近 7 天重写、历史月份冻结；
sync 失败时 manifest 不发布；
snapshot 失败时 generation/stale 状态正确；
Local/Remote 相同 fixture 结果一致；
断网运行 analysis、daily、direction、Book、detail、filters 和 export；
chdb 函数、参数绑定、UTC 时间和 Parquet glob 兼容性；
CLI 的退出码、日志和 --dry-run。
验收标准：
Dashboard 查询不再访问远程 ClickHouse；
首次没有本地数据时，接口返回明确的 coverage 错误和 CLI 提示；
CLI 重复执行不会重复累积数据；
历史冻结月份文件内容和 mtime 不变；
本地关键指标与 remote 对照结果一致；
现有测试全部通过。
Assumptions
默认采用“近 7 天可重写，超过 7 天冻结”。
历史修复必须显式使用 --reconcile-month。
四个日期参数始终显式传入，避免误刷新错误时间范围。
ABOOK_DATA_SOURCE=local 在完成远程对照验收后启用；remote 仅保留作排障和对照。
验收记录：后端 `229 passed, 1 warning`；前端 Vite production build 通过；CLI `--dry-run` 可在无远程连接时输出目标范围和待刷新月份。实际首次同步耗时取决于 ClickHouse 网络和数据量，运行后 CLI 会输出各表行数与状态。
