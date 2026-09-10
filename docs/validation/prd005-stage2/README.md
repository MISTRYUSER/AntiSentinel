# PRD-005 5.2 Milvus Lite Case

状态：真实运行通过、回归通过；用户 review 待确认。

## Case

- 范围：5.2 Embedding 任务、Milvus 向量索引、Scope 过滤、幂等 upsert、SQLite channel 状态和关闭/重开恢复。
- 输入：两个固定 3 维向量点，分别属于 `fixture-repo/snapshot-a/commit-snapshot-a` 和 `fixture-repo/snapshot-b/commit-snapshot-b`；使用确定性本地向量，不调用外部 Embedding。
- 运行：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_retrieval_case.py --case vector --output <全新隔离目录> --timeout 120`。目录内 SQLite 与 Milvus Lite 文件独立保存。
- 观测：`facts.sqlite`、`milvus-lite.db`、`report.json`；检查 Milvus collection、主键集合、Scope filter、SQLite task/channel 状态、关闭/重开后的查询。
- 预期：向量 2、输出 2、SQLite 文档 2；两个 channel 和 task 均 ready；每个 Scope 对账一致；重开后 snapshot-a 只返回自身点；重复 upsert 不增加逻辑点；后台异常=0。
- 失败判定：跨 snapshot/repository/commit 返回、主键重复、字段/向量维度不一致、对账差集非空仍 ready、重开丢数据、报告缺数字字段、后台异常>0。
- 清理：仅删除本 Case 创建的 `<全新隔离目录>`；不删除仓库数据库或其他 Case 目录。

## 固定实现与基线

实现依赖：`pymilvus==3.0.1`、`milvus-lite==3.2.1`；Milvus Lite 文件 URI、COSINE、FLAT、VARCHAR `point_id`、Strong 读语义。生产目标形态为 Standalone 单节点 3.0.x、4 vCPU/8 GiB/50 GiB SSD，当前不启动。

运行前基线：5.1 SQLite 文档/manifest/FTS=2/2/2，Milvus 服务=0，向量=0，Embedding 外部调用=0；全量回归=458 passed/0 failed。适配器 focused=3/3、恢复 focused=2/2、runner Fake=1/1。

| 指标 | 基线 | 目标阈值 | 证据 |
|---|---:|---:|---|
| 输入文件/向量点 | 0/0 | 2/2 | report.json |
| 输出候选数 | 0 | 2 | report.json + Milvus search |
| 持久化 SQLite 文档 | 0 | 2 | SQLite query |
| 持久化 Milvus 向量 | 0 | 2 | Milvus get/query |
| 对账完整性检查 | 0 | 5 | report.json |
| ready task/channel 数 | 0/0 | 2/2 | SQLite query |
| 重开后 snapshot-a 结果 | N/A | 1 且无跨域 | Milvus search |
| 后台异常数 | N/A | 0 | report.json |
| 失败数 | 0 | 0 | report.json |
| 业务/持久化完成差值 | N/A | 明确非负毫秒值 | report.json |

预算：120 秒，最多 2 次重试；Lite 正确性通过不替代 Standalone 断连/重启 Case。

## 实际运行报告

第 1 次运行因未调用 `load_collection()` 导致结果结构不完整；保留目录 `/tmp/antisentinel-prd005-stage2-case1`。修复适配器加载与嵌套 `entity` 解析后，同一输入第 2 次运行使用 `/tmp/antisentinel-prd005-stage2-case1-retry1`，第 3 次运行修正 SQLite manifest/完整性计数后使用 `/tmp/antisentinel-prd005-stage2-case1-retry2`，最终通过。Case 重试计数=2，未超过预算。

最终命令：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_retrieval_case.py --case vector --output /tmp/antisentinel-prd005-stage2-case1-retry2 --timeout 120`。

运行状态：Milvus Lite collection 创建、加载、upsert、filtered search 完成；持久化状态：SQLite task/channel/manifest ready，Milvus 关闭后重开读回完成。真实产物：[report.json](/tmp/antisentinel-prd005-stage2-case1-retry2/report.json)、[facts.sqlite](/tmp/antisentinel-prd005-stage2-case1-retry2/facts.sqlite)、[milvus-lite.db](/tmp/antisentinel-prd005-stage2-case1-retry2/milvus-lite.db)。后台异常=0；`case_pass=true`。

| 指标 | 基线 | 实际值 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---:|---:|---:|---:|---|---|
| 输入文件/向量点 | 0/0 | 2/2 | 2/2 | +2/+2 | report.json | 达标 |
| 输出候选数 | 0 | 2 | 2 | +2 | report.json + Milvus search | 达标 |
| SQLite 文档/ready manifest | 0/0 | 2/2 | 2/2 | +2/+2 | SQLite query | 达标 |
| Milvus 持久化向量 | 0 | 2 | 2 | +2 | Milvus query | 达标 |
| 对账完整性检查 | 0 | 6 | ≥6 | +6 | report.json | 达标 |
| ready task/channel | 0/0 | 2/2 | 2/2 | +2/+2 | SQLite query | 达标 |
| 重开 snapshot-a 命中 | N/A | 1 | 1 且无跨域 | N/A | Milvus search | 达标 |
| 错误 Scope 命中 | 0 | 0 | 0 | 0 | Milvus search | 达标 |
| 后台异常/失败 | 0/0 | 0/0 | 0/0 | 0/0 | report.json | 达标 |
| 业务完成耗时 | N/A | 823.52ms | ≤120000ms | N/A | report.json | 达标 |
| 持久化完成耗时 | N/A | 1127.00ms | ≤120000ms | N/A | report.json | 达标 |
| 持久化 lag | N/A | 303.48ms | ≥0ms | N/A | report.json | 达标 |
| 产物文件数 | 0 | 3 | 3 | +3 | `find <output> -maxdepth 1` | 达标 |

独立核验命令：读取关闭后的 SQLite/Milvus，实际 `sqlite_documents=2`、`ready_manifests=2`、`ready_tasks=2`、`ready_channels=2`、`memory_records=0`、`artifact_files=3`、`milvus_rows=2`、`snapshot_a_hits=1`、`wrong_scope_hits=0`，全部满足硬门槛。

累计 focused：`/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_code_search_models.py tests/test_code_search_sqlite_store.py tests/test_code_keyword_retrieval.py tests/test_code_retrieval_case_runner.py tests/test_milvus_adapter.py tests/test_retrieval_recovery.py -q`，15 passed、0 failed、2.38s。全量回归：`/Users/xuewentao/miniconda3/bin/python -m pytest -q`，458 passed、0 failed、7 warnings、29.21s；与 5.1 基线 452→458 增加6项（+1.33%），失败保持0。当前阶段状态：`真实运行通过`、`回归通过`；用户 review 待确认。

## Review 修复复验（2026-09-07）

针对 review 补充了 canonical point ID、已有 collection schema/index 校验、按版本组合隔离物理 collection、SQLite channel 查询硬门控、Milvus Lite 嵌套 `entity` 解析、单 Scope 对账限制、任务审计字段（attempt/时间/耗时/错误/lease）和严格关闭后重开顺序；`pyproject.toml` 直接锁定 `pymilvus[milvus-lite]==3.0.1` 与 `milvus-lite==3.2.1`。

修复后真实 Case 命令：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_retrieval_case.py --case vector --output /tmp/antisentinel-prd005-stage2-reviewfix-case1 --timeout 120`。实际输入2/68字节、输出2、SQLite文档2、ready manifest/task/channel=2/2/2、Milvus向量2、完整性6/6、重开命中1、错误Scope命中0、后台异常0、失败0、产物3、耗时1032.25ms、业务完成800.83ms、持久化lag231.42ms、`case_pass=true`。

独立核验命令实际：collections=1、collection_versioned=true、point_ids_canonical=true、schema_dim=3、index_type=FLAT、metric_type=COSINE、milvus_rows=2、snapshot_a_hits=1、wrong_scope_hits=0；SQLite documents=2、ready manifests/tasks/channels=2/2/2、`memory_records=0`、产物3，断言全部满足。focused累计命令实际21 passed、0 failed、2.71s；全量回归命令实际 **464 passed、0 failed、7 warnings、22.60s**，458→464增加6（+1.31%），失败0→0。`git diff --check`返回0。

当前阶段状态保持：`真实运行通过`、`回归通过`；用户 review 待确认。该 Case 仍只验证 Milvus Lite 固定向量，Standalone断连/重启、真实Embedding和生产容量未验证。
