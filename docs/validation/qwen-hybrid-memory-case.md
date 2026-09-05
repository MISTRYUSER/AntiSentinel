# Qwen Hybrid Memory 隔离真实 Case

状态：真实运行通过（2026-09-05）。

- 输入：12 条合成 typed MemoryRecord、10 条固定查询；内容覆盖中文语义改写、错误码、Redis key、无关记录和两个 scope。
- 运行：临时 SQLite 与临时 `memory_vectors`；环境只读取 `DASHSCOPE_API_KEY`、`ANTISENTINEL_EMBEDDING_BASE_URL`、可选维度。脚本批量生成 12 条文档向量与 10 条查询向量，最多 2 个成功 HTTP 请求；每个请求最多重试 2 次。
- 观测：模型名、维度、请求数、重试数、文档/查询向量数、vector/RRF 候选数、Recall/Precision/MRR@1/@5、P50/P95、scope/来源/version 校验、SQLite 重开恢复。
- 成功条件：模型为 `qwen3.7-text-embedding-flash`；维度=1024；文档向量=12；查询向量=10；scope 泄漏=0；模型/维度/content_version 混用=0；来源悬挂=0；后台异常=0；SQLite quick_check=ok。
- 失败条件：任一批次部分返回、超出重试预算、HTTP/向量契约异常、跨 scope 命中、临时产物不完整。
- 清理：仅删除输出的 `antisentinel-qwen-hybrid-*` 临时目录；不写正式 storage、不重建正式索引。
- 预算：120 秒；真实 API 请求最多 2 个批次，失败输入最多重试 2 次。

执行前，先运行累计测试集；用户确认后执行 runner，并把实际指标写入开发记录。

## 实际结果

| 指标 | 实际 | 阈值 | 结果 |
|---|---:|---:|---|
| 模型 | qwen3.7-text-embedding-flash | 指定模型 | 通过 |
| 维度 | 1024 | 1024 | 通过 |
| HTTP 批次 | 2 | <=2 | 通过 |
| 文档/查询向量 | 12 / 10 | 12 / 10 | 通过 |
| vector 状态 | active | active | 通过 |
| scope 泄漏/悬挂来源/后台异常 | 0 / 0 / 0 | 全为0 | 通过 |
| SQLite quick_check | ok | ok | 通过 |
| P95 / 总耗时 | 8.92ms / 590.73ms | <=120000ms | 通过 |

该 Case 每条查询只有一条相关记忆，固定分母 Precision@5=20%，不能用于判断 470 条主评测集的 Precision 改善。
