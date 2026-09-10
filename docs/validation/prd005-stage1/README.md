# PRD-005 5.1 Keyword Case

状态：真实运行通过、回归通过；用户 review 待确认。

## Case

- 范围：5.1 文本投影与 FTS5/BM25，累计仅覆盖当前离线 runner。
- 输入：两个固定代码文档，分别属于 `fixture-repo/snapshot-a/commit-snapshot-a` 与 `fixture-repo/snapshot-b/commit-snapshot-b`；查询词 `ERR_PAYMENT_TIMEOUT`。
- 运行：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_retrieval_case.py --case keyword --output <isolated-dir> --timeout 120`。`<isolated-dir>` 必须不存在，SQLite 与 report 只写入该目录。
- 观测：`<isolated-dir>/code-search.sqlite`、`<isolated-dir>/report.json`；重开 SQLite 后检查两个 manifest、FTS 结果和来源 Scope。
- 预期：两个文档均可检索；输出数=2、持久化文档数=2；两个 snapshot/commit 不互相覆盖；report `case_pass=true`；后台异常=0。
- 失败判定：目录已存在仍写入、未发布/跨 snapshot 结果可见、输出与持久化数不一致、完整性检查不足、后台异常>0、报告缺数字字段或 `case_pass=false`。
- 清理：仅删除本 Case 创建的 `<isolated-dir>` 及其子文件；不触碰仓库现有数据目录。

## 基线与指标

运行前基线：代码检索 Case=0，Milvus 服务=0，Embedding 调用=0；当前工作区生产 Python 文件=184、测试文件=98、全量测试基线=448 passed/0 failed。向量相关指标为 N/A，本 Case 不启动 Milvus。

| 指标 | 基线 | 目标阈值 | 证据 |
|---|---:|---:|---|
| 输入文件数 | 0 | 2 | report.json |
| 输出/持久化文档数 | 0/0 | 2/2 | report.json + SQLite query |
| 来源与 Scope 完整性检查 | 0 | ≥4 | report.json |
| 后台异常数 | N/A | 0 | report.json |
| 业务完成与持久化完成时间差 | N/A | 明确非负毫秒值 | report.json |
| 失败数 | 0 | 0 | report.json |

默认预算：120 秒，最多 2 次重试；同一输入连续失败 2 次即停止并保留目录、日志和 report。

## 实际运行报告

命令：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_retrieval_case.py --case keyword --output /tmp/antisentinel-prd005-stage1-case1 --timeout 120`

运行状态：业务运行完成；持久化状态：SQLite 重开读回完成。真实产物：`/tmp/antisentinel-prd005-stage1-case1/report.json`、`/tmp/antisentinel-prd005-stage1-case1/code-search.sqlite`。独立核验命令读取 SQLite 得到 documents=2、manifests_ready=2、fts_rows=2、snapshots=2、memory_records=0；后台异常=0；恢复校验通过。`case_pass=true`。

| 指标 | 基线 | 实际值 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---:|---:|---:|---:|---|---|
| 输入文件数 | 0 | 2 | 2 | +2/N/A | runner report | 达标 |
| 输入字节数 | 0 | 68 | N/A | +68/N/A | runner report | 记录 |
| 输出候选数 | 0 | 2 | 2 | +2/N/A | runner report | 达标 |
| 持久化文档数 | 0 | 2 | 2 | +2/N/A | runner report + SQLite query | 达标 |
| 完整性检查数 | 0 | 4 | ≥4 | +4/N/A | runner report | 达标 |
| 后台异常数 | N/A | 0 | 0 | N/A | runner report | 达标 |
| 业务完成耗时 | N/A | 26.66ms | ≤120000ms | N/A | runner report | 达标 |
| 持久化完成耗时 | N/A | 43.11ms | ≤120000ms | N/A | runner report | 达标 |
| 持久化 lag | N/A | 16.45ms | ≥0ms | N/A | runner report | 达标 |
| 失败数 | 0 | 0 | 0 | 0 | runner report | 达标 |
| 重试数 | 0 | 0 | ≤2 | 0 | runner report | 达标 |

全量回归命令：`/Users/xuewentao/miniconda3/bin/python -m pytest -q`；实际 **452 passed、0 failed、7 warnings、16.88s**。相对前一基线451 passed，增加1项（+0.22%），失败保持0。当前阶段状态：`真实运行通过`、`回归通过`；尚未完成用户 review。
