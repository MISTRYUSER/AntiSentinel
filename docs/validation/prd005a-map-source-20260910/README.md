# PRD-005A：地图 + 受控源码切片验收

日期：2026-09-10。本地 fixture Case（非外部代码 benchmark）。

## 结果

| Case | case_pass | 关键 hard gates |
|---|---|---|
| **snapshot** | True | ready / fixed_commit / expected_map |
| **loop** | True | loop_completed / **source_context** / **evidence_refs** |

### snapshot

- nodes=4, edges=3, chunks=4；persisted 与 output 一致
- integrity_failed=0

### loop（符号 → 邻居 → read_source → 下一轮上下文）

- `source_context=True`：受控源码切片进入下一轮 model context
- `evidence_refs=True`：源码 EvidenceRef 可追溯

报告：

- `snapshot/report.json`
- `loop/report.json`
- `summary.json`

说明：本 Case 证明 **005A 接缝**（建图 + hash 源码片进上下文），不替代 SWE-bench / RepoBench 等外部代码评测。
