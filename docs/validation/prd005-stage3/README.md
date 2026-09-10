# PRD-005 5.3 Hybrid Retrieval Case

状态：真实运行通过、回归通过；用户 review 待确认。

## Case

- 范围：keyword/vector/hybrid 三通道候选、RRF(k=60)、重叠源码范围去重、vector 不可用降级和 vector-only 错误语义。
- 输入：同一 `fixture-repo/snapshot-a/commit-snapshot-a` 下 4 个代码文档、4 个确定性本地 3 维向量、20 条固定查询（每个错误码 5 条）；不调用外部 Embedding。
- 运行：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_retrieval_case.py --case hybrid --output <全新隔离目录> --timeout 120`。目录内 SQLite 与 Milvus Lite 文件独立保存。
- 观测：`facts.sqlite`、版本化 Milvus collection、`report.json`；检查三模式输出、mode_errors、RRF 通道贡献、去重、channel ready 和两库数量。
- 预期：query_count=20；keyword/vector/hybrid 均有 20 次结果且错误=0；最终候选按稳定 document_id 排序；每次 hybrid 不重复 document_id；SQLite 文档=4、Milvus 向量=4、ready channel/task=1/4；后台异常=0。
- 失败判定：直接相加 BM25/distance、重复源码范围未去重、vector 不可用却未标 degraded、vector-only 假装成功、跨 Scope 结果、数量/状态不一致、后台异常>0。
- 清理：仅删除本 Case 创建的 `<全新隔离目录>`。

## 基线与指标

运行前基线：5.2 reviewfix SQLite 文档/ready manifest/task/channel=2/2/2/2，Milvus 向量=2；混合服务/Case=0；全量回归=468 passed/0 failed。向量使用固定本地向量，query embedding 调用=0。

| 指标 | 基线 | 目标阈值 | 证据 |
|---|---:|---:|---|
| 输入文档/查询数 | 0/0 | 4/20 | report.json |
| 三模式执行次数 | 0 | 20/20/20 | report.json |
| 三模式错误数 | N/A | 0/0/0 | report.json |
| SQLite 文档/Milvus 向量 | 0/0 | 4/4 | SQLite + Milvus query |
| hybrid 重复 document_id | N/A | 0 | report.json |
| channel/task ready | 0/0 | 1/4 | SQLite query |
| 后台异常/失败数 | N/A/0 | 0/0 | report.json |
| 业务/持久化完成差值 | N/A | 明确非负毫秒值 | report.json |

说明：本 Case 会报告每组 P@5、Recall@5、MRR，但 4 文档 fixture 的相关性上限不作为整体效果达标证据；固定 20 查询与人工标签在 5.5 冻结评测中使用。

预算：120 秒，最多 2 次重试；图扩展尚未接入，不能报告 hybrid+graph。

## 实际运行报告

首次真实运行发现 vector 结果仅返回 canonical `point_id`，导致 vector 评测无法与 CodeSearchDocument 对齐；补充 Milvus `document_id` 字段后 retry1 恢复三组 Recall。随后补充 hybrid 重复候选审计，retry2 生成最终报告；旧目录和报告均保留。

最终命令：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_retrieval_case.py --case hybrid --output /tmp/antisentinel-prd005-stage3-case1-retry2 --timeout 120`。

运行状态：keyword/vector/hybrid 各完成20次；持久化状态：SQLite manifest=1、task=4、channel=1 ready，Milvus collection 写入4点并关闭后可读。真实产物：[report.json](/tmp/antisentinel-prd005-stage3-case1-retry2/report.json)、[facts.sqlite](/tmp/antisentinel-prd005-stage3-case1-retry2/facts.sqlite)、[milvus-lite.db](/tmp/antisentinel-prd005-stage3-case1-retry2/milvus-lite.db)。后台异常=0；`case_pass=true`。

| 指标 | 基线 | 实际值 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---:|---:|---:|---:|---|---|
| 输入文档/查询数 | 0/0 | 4/20 | 4/20 | +4/+20 | report.json | 达标 |
| 三模式执行次数 | 0/0/0 | 20/20/20 | 20/20/20 | +20/+20/+20 | report.json | 达标 |
| 三模式错误数 | N/A | 0/0/0 | 0/0/0 | N/A | report.json | 达标 |
| SQLite 文档/ready manifest | 0/0 | 4/1 | 4/1 | +4/+1 | SQLite query | 达标 |
| Milvus 向量/唯一 document_id | 0/0 | 4/4 | 4/4 | +4/+4 | Milvus query | 达标 |
| ready task/channel | 0/0 | 4/1 | 4/1 | +4/+1 | SQLite query | 达标 |
| hybrid 重复候选 | N/A | 0 | 0 | N/A | report.json | 达标 |
| keyword P@5/R@5/MRR | N/A | 0.2/1.0/1.0 | 记录，不作整体门槛 | N/A | report.json | 记录 |
| vector P@5/R@5/MRR | N/A | 0.2/1.0/1.0 | 记录，不作整体门槛 | N/A | report.json | 记录 |
| hybrid P@5/R@5/MRR | N/A | 0.2/1.0/1.0 | 记录，不作整体门槛 | N/A | report.json | 记录 |
| 后台异常/失败 | N/A/0 | 0/0 | 0/0 | N/A/0 | report.json | 达标 |
| 业务完成耗时 | N/A | 1183.64ms | ≤120000ms | N/A | report.json | 达标 |
| 持久化 lag | N/A | 11.86ms | ≥0ms | N/A | report.json | 达标 |
| 产物文件数 | 0 | 3 | 3 | +3 | `find <output> -maxdepth 1` | 达标 |

独立核验命令实际：`case_pass=true`、`query_count=20`、`mode_errors={keyword:0,vector:0,hybrid:0}`、`hybrid_duplicates=0`、SQLite documents=4、ready manifest/task/channel=1/4/1、`memory_records=0`、Milvus rows=4、canonical IDs=4、document IDs=4、产物3，全部断言满足。

累计 focused：`/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_code_search_models.py tests/test_code_search_sqlite_store.py tests/test_code_keyword_retrieval.py tests/test_code_retrieval_case_runner.py tests/test_milvus_adapter.py tests/test_retrieval_recovery.py tests/test_code_retrieval_hybrid.py -q`，26 passed、0 failed、2.21s。全量回归：`/Users/xuewentao/miniconda3/bin/python -m pytest -q`，469 passed、0 failed、7 warnings、16.69s；468→469增加1项（+0.21%），失败保持0。

说明：4 文档 fixture 每条查询仅标注1个相关文档，P@5=0.2 是固定分母5的真实结果；本 Case 不声称达到 PRD 整体效果门槛，冻结语料/人工标签和五轮性能评测留在5.5。

## P50/P95 复验

为补齐逐查询延迟观测，使用同一 4 文档/20 查询输入在新目录 `/tmp/antisentinel-prd005-stage3-case2` 重跑。实际 `case_pass=true`、三模式错误0、hybrid重复0、SQLite文档4、Milvus向量4、完整性7/7、产物3；P50 keyword/vector/hybrid=2.1337/4.2697/6.7399ms，P95=2.4602/5.1481/8.6746ms；业务完成1160.90ms、持久化完成1175.88ms、lag14.98ms。

独立核验命令实际 `queries=20`、`mode_errors={keyword:0,vector:0,hybrid:0}`、`sqlite_docs=4`、`manifest_ready=1`、`tasks_ready=4`、`channels_ready=1`、`vectors=4`、canonical/document IDs各4唯一、`p95_present=true`，全部断言满足。该复验沿用本地固定向量，Embedding调用0；P@5=0.2仍只作对照记录，不声称整体优化达标。

## Review 修复复验

针对 review 修复了异常分类（仅瞬时网络/超时降级）、lexical manifest/channel 门控、top_k≤5 与 candidate_limit≤30、可配置 `rrf_k`、顺序无关的范围连通分量融合，以及按实际唯一 document_id 统计 `output_count`。累计 focused **30 passed、0 failed、1.98s**；全量回归 **473 passed、0 failed、7 warnings、15.48s**。

最终复验命令：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_retrieval_case.py --case hybrid --output /tmp/antisentinel-prd005-stage3-reviewfix-case1 --timeout 120`。实际 query_count=20，keyword/vector/hybrid=20/20/20，错误=0/0/0，unique output=4/4/4，SQLite 文档=4、Milvus 向量=4、ready manifest/task/channel=1/4/1，完整性=8/8，hybrid 重复=0；P@5/Recall@5/MRR 三组均 0.2/1.0/1.0，P50=3.0048/3.4732/6.3834ms，P95=3.8228/4.0382/6.7037ms，后台异常=0、失败=0、产物=3，`case_pass=true`。

独立核验：canonical/document IDs 各4唯一、Milvus rows=4、错误 Scope=0、P95 字段齐全、`memory_records=0`。P@5=0.2 仅是固定小 fixture 对照，未达到 PRD 整体效果门槛；Graph、真实 Embedding、Standalone 和 5.5 冻结评测仍未完成。
