# 本地 Warehouse 数据保存说明

## 目录结构

`data/warehouse/` 是 Dashboard 默认读取的本地数据目录。该目录包含：

- `manifest.json`：Warehouse 的版本、覆盖范围、平台、表和最近一次同步信息。
- `ods_mt4_users/part.parquet`、`ods_mt5_users/part.parquet`：账户维度表，按全量快照保存。
- 其他事实表：按 `month=YYYY-MM/part.parquet` 保存月分区。
- `.sync.lock`：刷新 CLI 与 Dashboard 进程之间的文件锁，不是数据文件。

Warehouse 文件和 Dashboard 的三个 JSON 快照默认不提交到 Git；`data/README.md` 本身会提交，作为数据恢复和维护说明。

每次刷新都会生成新的 `manifest.json` generation；同一个 generation 下的 Parquet 和三个 JSON 快照才属于同一数据版本。历史分析文档中的人数或 P&L 只对它记录时的数据版本负责，不能直接与后来刷新出的 Warehouse 逐项比较。

## 如何刷新

刷新必须在终端独立运行，不放在 Dashboard 请求中：

```bash
.venv/bin/python scripts/refresh_local_data.py \
  --selection-start 2026-05-01 \
  --selection-end 2026-06-30 \
  --validation-start 2026-07-01 \
  --validation-end 2026-07-22
```

默认流程是：

1. 从 ClickHouse 拉取 Warehouse 数据并原子写入 Parquet。
2. 发布新的 `manifest.json` generation。
3. 基于本地 Warehouse 重建 risk、avg_profit、martingale 三个快照。

常用选项：

- `--platform mt5 --platform hh_mt5`：重建快照时只使用指定平台；月分区同步仍会拉取全部平台，避免覆盖共享月文件时误删未选平台数据。
- `--dry-run`：只计算目标范围和要重拉的月份，不连接 ClickHouse。
- `--sync-only`：只刷新 Warehouse，不重建 JSON 快照。
- `--snapshots-only`：只基于已有 Warehouse 重建快照。
- `--reconcile-month 2026-05`：显式重拉某个月，适合发现历史数据修正时使用。

## 日期和历史策略

CLI 要求同时提供筛选期和验证期四个日期。Warehouse 的目标范围会覆盖两段日期，并额外向前保留默认 3 个月 lookback，以满足分析查询的月度窗口。

默认只重写目标范围最后 7 天所在的月份；更早月份视为冻结历史。因为文件按月分区，重写一个月时会完整替换该月 Parquet，而不是在文件中追加，避免重复数据。若历史源数据发生修正，使用 `--reconcile-month YYYY-MM` 显式重拉该月。

同步过程使用临时文件 + `os.replace`，manifest 最后才原子发布；同步期间通过 `.sync.lock` 防止两个刷新进程同时写入。

## Dashboard 如何读取

Dashboard 默认使用 `ABOOK_DATA_SOURCE=local`，修改验证期后点击“应用筛选与验证”只会查询本地覆盖范围内的 Parquet，不会访问 ClickHouse。启动时可通过 `GET /api/warehouse/status` 查看本地覆盖范围、generation 和快照是否过期。

如果请求范围超出 manifest 覆盖范围，接口返回 `409 warehouse_coverage_missing`，应先运行刷新 CLI。刷新后 generation 改变，旧 JSON 快照会显示为过期；重新运行默认 CLI 流程即可同步更新。

如需临时排查远程数据，可在 `.env` 中设置 `ABOOK_DATA_SOURCE=remote`。这只改变 Dashboard 的查询源，不改变本地 Warehouse 文件。

## 字段边界

- MT5 Deals 保存 action `0,1,2,3`，并保留 `is_deleted`；分析 P&L 仍按业务逻辑区分交易 action 与 funding action。
- 所有本地事实表的 `platform`、`login` 和时间字段由同步 SQL 规范化；`login` 使用 `UInt64`，避免本地 JOIN 出现有符号/无符号类型冲突。
- `test` 或 `demo` 账户不在保存层删除，仍保留用于审计；业务查询和快照 SQL 会过滤 group 中大小写不敏感包含 `test` 或 `demo` 的账户。
- 空结果月份也会写入并登记覆盖，表示该月份已从远程查询确认无数据，而不是未同步。

不要手工编辑 Parquet 或 manifest。需要修复历史数据时，使用 `--reconcile-month`，这样文件和覆盖元数据会一起更新。
