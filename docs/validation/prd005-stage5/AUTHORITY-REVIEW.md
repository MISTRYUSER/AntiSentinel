# PRD-005 Review：向量候选权威校验

日期：2026-09-08。当前修复范围仅为 review 第一项与 reconcile 缺失字段问题，未完成整份生产整改。

## 已核实与处理

MilvusAdapter.search 原先直接把 payload 转为 VectorSearchHit。现在在转换前调用 EmbeddingTaskStore.verify_vector_rows，在同一个 SQLite 事务中核验：channel ready、查询指定 projection 的 manifest ready、document_id 存在且确定性身份吻合、四字段 Scope、投影版本、node/chunk/path/source_hash/input_hash、model/dimension/template、canonical point_id、唯一匹配 task 的 ready 状态及版本/hash。任意不一致抛出 vector_authority_mismatch，不作为零命中、不返回部分候选。外层主键和 entity.point_id 也必须一致。缺少权威校验方法的第三方 channel store 会失败，不静默绕过。

reconcile 原先仅比较存在字段；现在要求所有预期标量字段存在且相等。向量数值本身不在本次标量对账范围内。

## 验证

新 tests/test_vector_authority.py：正常身份、15个字段分别删除/篡改、5种 SQLite 状态变更，以及真实 Milvus payload path 篡改通过统一 service 被拒绝。tests/test_retrieval_recovery.py 增加4个必需字段缺失测试。tests/test_milvus_adapter.py 的 channel stub 增加显式校验协议；权威行为使用真实 SQLite 测试，不靠这个 stub 证明。

首轮红灯：新增校验方法尚不存在，1 failed（--maxfail=1）。首批累计针对性命令 `python -m pytest tests/test_vector_authority.py tests/test_milvus_adapter.py tests/test_code_flash_encoder.py tests/test_retrieval_recovery.py -q` 为50 passed、0 failed、10.39s；该数字早于追加的真实篡改和4个缺失字段测试，全量结果另记开发日志。

## 未关闭的 review 项

最新累计验证：全量562 passed、0 failed、6 warnings、55.76s，较521增加41。随后使用 `python scripts/run_code_retrieval_case.py --case vector|hybrid --output <独立目录> --timeout 120` 串行复跑既有本地Case（外层subprocess另设120秒强制超时），两个退出码均0。原始报告位于 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-authority-review-vh6zj967`。

| 指标 | 既有fixture基线 | 本次实际 | 阈值 | 差值 | 证据 | 结果 |
|---|---:|---:|---|---:|---|---|
| vector SQLite/Milvus文档数 | 2/2 | 2/2 | 相等 | 0/0 | vector/report.json | 通过 |
| vector 完整性检查 | 6 | 6 | ≥6 | 0 | vector/report.json | 通过 |
| hybrid SQLite/Milvus文档数 | 4/4 | 4/4 | 相等 | 0/0 | hybrid/report.json | 通过 |
| hybrid 完整性检查 | 8 | 8 | ≥8 | 0 | hybrid/report.json | 通过 |
| hybrid 三模式错误数 | 0/0/0 | 0/0/0 | 全0 | 0 | hybrid/report.json | 通过 |
| hybrid 重复候选 | 0 | 0 | 0 | 0 | hybrid/report.json | 通过 |

vector 输入2文件/68字节，输出2，持久化滞后171.64ms，总958.62ms；hybrid输入4文件/194字节、20查询×3模式，实际每模式输出80条、唯一4条，滞后16.33ms，总1199.69ms。每Case3类产物，重试0、报告后台异常0。hybrid三模式P@5=.2、Recall=1、MRR=1，仅是小fixture正确性结果。hybrid P95=48.1288ms，无同条件五轮基线，不判定性能通过。两份报告case_pass=true仅适用于原fixture门槛，不代表PRD生产或RAG质量通过。

- FTS 明确投影指针、多 ready 版本隔离，以及 manifest 构建/对账/发布协议。本次只保证向量候选与显式查询 projection 一致，不声称旧发布状态可信性已经解决。
- FTS OperationalError 分类、Case timeout 强制终止、runner 重复逻辑、非UTF-8预算。
- 持久化 Embedding worker 的 claim/lease/retry/blocked/recovery；已有 Flash 评测编码器不等于生产 worker。
- Fusion 代表 document/source 同一性和 byte range；Graph 到 chunk 的确定选择。
- 完整 lexical 投影、hybrid_graph 四路评测、真实 Flash/答案裁判与 Ragas answer metrics。

本次 SQLite 验证使用现有 BEGIN IMMEDIATE 事务，保证核验期间一致性，但会与写入竞争；并发和延迟尚未测量，不能声称生产吞吐达标。核验保证返回时观察到的发布/task状态，不能替代 Evidence 回读时再次核验绑定。
