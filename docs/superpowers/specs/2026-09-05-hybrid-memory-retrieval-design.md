# 混合记忆检索设计（B）

## 范围

将可信记忆 A 阶段后的线上 Recall 从单一路径升级为 RRF 混合候选召回：FTS5、精确标识符、Qwen `qwen3.7-text-embedding-flash` 向量。所有候选仍必须经过 A 阶段的 scope、生命周期、有效期、Evidence hash 和 token 预算门槛。

不实现 ANN、跨 operator 共享、经验 consolidation 或自动迁移正式索引；它们分别属于后续容量优化与 C 阶段。

## 当前基线

- A 阶段累计回归：104/104 通过，真实隔离 Case `case_pass=true`。
- `LocalVectorMemory` 已验证 Qwen Flash 1024 维写入、模型/维度/content_version 隔离；未接入线上 `MemoryRecall`。
- 当前 `MemoryRecall` 已调用 Trusted Recall，但不调用 FTS5/向量候选检索。
- 历史 LongMemEval-S 470 Case 指标为 Recall@5=77.78%、Precision@5=36.09%、MRR@5=78.21%；口径与新 typed record 不完全一致，不能作为 B 的通过证据。

## 参考事实与项目建议

Codex 官方 Memory README 公开描述 per-rollout 抽取、DB 中间结果、全局 consolidation 和 read-usage telemetry；不证明其线上长期 Memory 使用向量 KNN。Agno 官方文档区分 user memory、session history、state，并建议稳定 user_id、裁剪和真实规模测试。

本项目建议：学习其“派生索引不替代事实源、使用可观测”的边界，但用 AntiSentinel 自己的 Evidence/Scope 规则控制候选。Qwen 向量分数只表示语义相关性，不能改变可信度。

来源：[Codex Memory README](https://github.com/openai/codex/blob/main/codex-rs/memories/README.md)、[Agno Memory Overview](https://docs.agno.com/memory/overview)。

## 已确认方案：RRF 混合召回

```text
Query + AuthorizedMemoryScope + QueryTime
  ├─ FTS5/BM25 候选（词汇、服务名、错误码）
  ├─ 精确标识符候选（错误码、Redis key、URL、IP、trace ID）
  └─ Qwen 向量候选（语义改写）
                 ↓
          RRF 融合，固定 k=60
                 ↓
   canonical MemoryRecord 回源 + Trusted Recall
                 ↓
      单一 token 预算 ContextView → Runtime
```

RRF 分数为 `sum(1 / (60 + rank_i))`。每一路先下推 operator/incident/status 和向量版本过滤；融合后再做完整来源、有效期和可信度校验。缺少某一路索引时，该路记为 `bypass`，其余候选仍可用；不得把 bypass 记为 hit。

## 模块边界

- `memory/retrieval.py`：`MemoryCandidateRetriever.retrieve(query, scope, limit)`，返回 `memory_id`、channel、rank、raw_score；不返回可注入正文。
- `memory/hybrid_ranker.py`：RRF、去重、候选通道解释。
- `memory/trusted_recall.py`：只接受 canonical `MemoryRecord`，负责可信度和预算；不关心 FTS5/向量实现。
- `persistence/sqlite_stores.py`：FTS5 和精确标识符候选查询。
- `persistence/local_vector_memory.py`：Qwen 向量候选查询；只返回模型/维度/version 匹配记录。
- `evaluation/`：调用生产 retriever，不向生产层泄漏 LongMemEval 类型。

## 错误与恢复

- Qwen 调用失败：记录 `vector_bypass`，继续 FTS5/精确候选；不得返回局部向量分数冒充完整结果。
- FTS5 索引缺失或损坏：记录 `lexical_bypass`，继续其他通道；不修改 canonical record。
- 候选 MemoryRecord 内容版本与向量版本不一致：拒绝候选，标记 `vector_stale`。
- RRF 后候选全部被 Trusted Recall 拒绝：返回 digest-only ContextView，记录拒绝原因；不注入低可信内容。

## 验收与评测

固定 LongMemEval-S SHA256 `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442`、470 non-abstention queries、top-k=5、时钟、提取/索引/模型版本。每条查询保存通道候选、RRF 分数、拒绝原因和最终 Memory ID。

| 指标 | 门槛 |
|---|---:|
| 宏平均 Precision@5 | >=0.80 |
| 宏平均 Recall@5 | >=0.60 |
| MRR@5 | >=0.80 |
| P95 查询延迟 | <= 词法基线 1.20x |
| EvidenceRef 完整率 | 100% |
| cross-scope / 悬挂引用 / 后台异常 | 0 |

固定分母 Precision@5 的理论上限必须在运行前报告；相关条数少于 4 的查询不静默改变分母。若门槛不适用，记录原因并请求重新确认验收定义。

每个实现任务完成后都运行同一套累计测试集并立即记录命令、通过/失败数、耗时、参数、修改文件数和风险。参数只在检索功能冻结后调优；一次只调整一个参数，并与冻结的 470 条数据集基线完整比较。

每个检索能力任务还必须重跑固定 470 条评测，记录 Recall@5、Precision@5、MRR@5、P95、每条查询最小值、bypass、评测错误、EvidenceRef完整率与泄漏数；没有这份前后对比，不得进入下一任务或开始参数微调。

## 交叉评测策略

LongMemEval-S 470 条是主评测集，只用于日常回归和参数调优。每次接受新参数后，先运行按任务类型分层的 LongMemEval-S 留出集；该留出集不参与参数选择。外部基准按 LoCoMo → MemoryAgentBench → LongMemEval-V2 的顺序引入，只验证泛化，不参与 RRF、候选数量或阈值调参。

LoCoMo 评估超长对话问答和事件摘要；MemoryAgentBench 评估多轮 Agent 任务中的更新、冲突和遗忘；LongMemEval-V2 评估 Agent trajectory 的静态/动态状态、工作流和环境陷阱。每个基准保留其原生任务指标，不能把 Session 级 Precision@5 阈值强行迁移到不同标注形式。

所有评测统一记录：数据版本/SHA、模型、embedding 维度、索引版本、RRF 参数、Recall@1/@5/@10、MRR/nDCG、原生答案或任务准确率、P50/P95、token、EvidenceRef 完整率、跨 scope/悬挂引用/过期记忆注入数。

## B 阶段真实 Case（待确认）

- 输入：隔离 SQLite 中至少 12 条 typed MemoryRecord，包含中文语义改写、错误码、Redis key、过期记录、跨 scope 记录及一条 Evidence hash 不匹配记录。
- 运行：临时 SQLite 和临时 Qwen 向量表；先跑针对性回归，再运行 10 条固定查询。是否调用真实 Qwen 由用户确认，API Key 只从环境读取。
- 观测：每通道候选数、RRF 去重数、最终注入数、拒绝原因、P50/P95、Qwen 请求数/重试数、SQLite 版本关联、恢复查询。
- 硬门槛：scope 泄漏0、悬挂来源0、Qwen 模型/维度/version 混用0、后台异常0；质量门槛按上表。
- 清理：仅删除 Case 临时目录与临时 SQLite；不重建正式索引。

## LongMemEval 向量投影边界

完整 Session 原文不直接进入 Qwen embedding。评测使用派生、可重建的 MemoryRecord 投影：单块最多2,000字符；每个Session最多8块；保留 question/session/turn范围、时间、scope、content_version 和原始来源引用。单个百炼批次最多20块且总字符不超过20,000。原始Session保持不变，向量索引只保存派生块；全量评测在1个超长Case通过后才恢复。

## 投影模型修订：原始证据与抽取记忆双路径

基于 Codex、LongMemEval、LoCoMo、Mem0、Graphiti 的官方实现调研，取消“完整Session按固定字符切8块后直接向量化”作为默认方案。它只降低请求长度，不能产生长期 Memory 语义，且在超长评测历史中批量成本不可控。

改用以下双路径：

```text
Canonical Session / Turn / Evidence
  ├─ 原始路径：Turn / Session 有界原文索引
  │    → 精确标识符 + FTS5 + 按需向量候选
  └─ 投影路径：Session digest / keyphrase / user fact /
               diagnosis fact / decision / failure barrier
       → 结构化 MemoryRecord + provenance + scope + 时间 + 版本
       → Qwen embedding
                 ↓
         RRF → 可选 rerank → Trusted Recall
```

投影由 Session/rollout 增量处理，而不是在评测查询时把完整历史重新切块。每条投影必须包含 `source_session_id`、`source_turn_ids` 或 EvidenceRef、`projection_kind`、`projection_revision`、`valid_from`、scope、content_version、可信度和截断/抽取失败状态。原始内容仍是事实源；投影错误不得覆盖原始内容。

LongMemEval 评测必须比较四种 corpus：`turn/session raw`、`session-summ`、`session-keyphrase/userfact`、`raw + projection`。分别报告 Session Recall、Turn Recall、最终 QA、token、P95、EvidenceRef 完整率和 abstention false-positive。参数调优只使用冻结的主评测集；LoCoMo 交叉比较 raw dialog、observation、session summary。

## 默认检索策略修订：LLM Projection-Only

用户确认默认检索只使用成功的 LLM projection record。原始 Session/Turn/Evidence 是不可变证据与审计层，按 EvidenceRef 按需回读，但不进入默认 FTS5、identifier、向量或 RRF 候选。

`session_digest`、`keyphrase`、`user_fact`、`diagnosis_fact`、`decision`、`failure_barrier` 是唯一默认索引对象。抽取失败或 pending 的 Session 不回退为原始全文候选；其状态进入 retry/repair 队列，查询返回缺失投影计数。这样默认上下文只包含可审计、短小且带来源的记忆，而不会在失败时静默把超长原文塞回模型。

raw、raw+projection corpus 仍保留在 benchmark 中，只作为架构对照，不参与默认线上检索或参数选择。

## 自审

范围只覆盖候选召回和融合；可信度仍由 A 阶段处理。模型已更新为 Qwen Flash，不再引用 E5/BGE 为当前实现。所有质量指标待固定数据集实测，本文不宣称提升。
