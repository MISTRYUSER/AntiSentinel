# Embedding 模型与 Codex 长期 Memory 调研

> 2026-09-05 决策更新：用户已指定改用百炼 `qwen3.7-text-embedding-flash`，默认1024维。代码中的本地E5实现及BGE兼容别名已替换为QwenFlashEmbedder；实际远端接口+临时SQLite验证见 [报告](../validation/qwen-flash-20260905/report.json)。下文BGE-M3优先本地化决策、加载失败和模型对比保留为历史记录，不再代表当前选型。当前线上MemoryRecall仍使用词法排序，正式索引未自动迁移。

## 0. 调研基线与目标

基线：

- AntiSentinel 当前 Memory Recall@1：45.95%。
- Recall@3：70.58%。
- Recall@5：77.78%。
- Precision@5：36.09%。
- FTS5/BM25 30 Case 非空召回：0/30。
- SQLite/FTS5 批量写入 16,418 条 Memory：2,853 ms。
- 当前没有向量索引。

目标：

- 让语义改写能够召回长期 Memory。
- 保持 SQLite 本地事实源、Redis 热缓存和 EvidenceRef provenance。
- 在同一 LongMemEval-S 470 Case 上比较 Recall@1/@3/@5、Precision@5、MRR 和 P95 查询延迟。
- 后续再测 Token 降幅与 Memory accuracy，不因 Embedding 相似度高就宣称答案正确。

## 1. Codex 当前 Memory pipeline：一手实现事实

来源：

- [Codex memories README](https://github.com/openai/codex/blob/main/codex-rs/ext/memories/README.md)
- [Codex local ThreadStore](https://github.com/openai/codex/blob/main/codex-rs/thread-store/src/local/mod.rs)
- [Codex state runtime](https://github.com/openai/codex/blob/main/codex-rs/state/src/runtime.rs)
- [Codex memory read path](https://github.com/openai/codex/blob/main/codex-rs/ext/memories/templates/memories/read_path.md)

### 已确认的 Memory 写入流程

Codex 当前公开的 Memory pipeline 是两阶段异步流程：

```text
root session starts
  → SQLite memory job claim
  → Phase 1：per-rollout model extraction
  → SQLite stage-1 output
  → Phase 2：global consolidation lock
  → local raw_memories.md / rollout_summaries/
  → optional consolidation agent
```

Phase 1：

- 从 state DB 按 bounded claim 取 eligible rollout。
- 过滤为 Memory-relevant response items。
- 并发调用模型，但有 concurrency cap。
- 期望结构化 `raw_memory`、`rollout_summary`、可选 `rollout_slug`。
- 对生成字段脱敏。
- 将成功结果保存为 SQLite stage-1 outputs。
- 失败 job 采用 retry backoff，不 hot-loop。

Phase 2：

- 使用 global lock 串行化全局 consolidation。
- 依据 usage_count、last_usage/generated_at 和 max_unused_days 选择输入。
- 更新 `raw_memories.md` 和 `rollout_summaries/`。
- 生成 workspace diff，必要时调用 consolidation agent。
- 写入 watermark 和 selected-for-phase2 状态。
- 没有变化时不重复调用 consolidation agent。

Read path：

- Memory 以本地文件路径和结构化内容被注入。
- 生成回答时要求引用实际使用过的 Memory 文件路径和行范围。
- 记录 memory usage telemetry。

### Codex 是否对长期 Memory 做向量检索

严格结论：从当前公开的 Codex Memory 源码，不能确认其长期 Memory 主链路使用向量数据库或 Embedding KNN。公开实现明确的是 SQLite-backed job/stage output、文件化 Memory artifact、bounded selection、全局 consolidation 和 citation/usage telemetry。

Codex 仓库中另有 [Semantic codebase indexing issue #5181](https://github.com/openai/codex/issues/5181)，提出用 Embedding + FAISS/SQLite-ANN 做代码语义索引；这是 issue 中的 proposed solution，不应当当作当前 Memory pipeline 已上线的实现。

### 对 AntiSentinel 的吸收

应吸收：

- Memory LLM 异步两阶段：per-session extraction → global consolidation。
- SQLite durable job claim、stage output、retry backoff、bounded concurrency。
- Memory 选择考虑 usage_count、last_usage 和时间窗口。
- Memory 文件/记录必须带 citation/provenance 和 usage telemetry。
- 派生索引可重建，不能替代 Canonical Event/Evidence。

不照搬：

- Codex 的 rollout/file citation 不能替代 AntiSentinel 的 Evidence/EvidenceRef。
- Codex 的本地 artifact 不直接替代我们 Incident/Session/Turn/Task 领域模型。
- 不因 Codex 有语义代码索引提案，就默认把 Memory 变成向量库。

## 2. Embedding 候选

### 候选 A：OpenAI `text-embedding-3-small`

一手来源：[OpenAI Embeddings Guide](https://developers.openai.com/api/docs/guides/embeddings)

官方事实：

- 默认维度 1,536。
- 最大输入 8,192 tokens。
- OpenAI 文档列出的 MTEB 表现为 62.3%。
- 文档示例约 62,500 pages/USD（示例口径，实际价格需按当前价目核对）。
- 支持 `dimensions` 参数缩短向量。

优点：接入最快、API 稳定、成本低，适合先建立可比较的语义基线。

缺点：需要把 Memory 内容发送到外部 Embeddings API；对本项目中文/中英混合诊断语料，真实效果必须用 LongMemEval 和内部 Case 验证。

### 候选 B：OpenAI `text-embedding-3-large`

一手来源同上。

官方事实：

- 默认维度 3,072。
- 最大输入 8,192 tokens。
- OpenAI 文档列出的 MTEB 表现为 64.6%。
- 文档示例约 9,615 pages/USD。
- 支持通过 `dimensions` 降维，例如 1,024。

优点：质量上限更高，适合需要更强语义区分的复杂 Memory/RAG。

缺点：官方示例成本约为 small 的 6.5 倍；当前还没有证明 Recall 提升足以抵消成本，不作为第一版默认模型。

### 候选 C：BAAI `bge-m3`

一手来源：[BAAI/bge-m3 model card](https://huggingface.co/BAAI/bge-m3)

官方事实：

- 1,024 维 dense embedding。
- 最大序列长度 8,192。
- 支持 100+ languages。
- 同一模型覆盖 dense、sparse lexical 和 multi-vector/ColBERT 方向。
- Model card 标注 MIT License。
- 官方建议 Embedding retrieval 不需要额外 query instruction。

优点：中英混合和中文本地化更合适；可本地运行，避免长期 Embedding 数据离开机器；一个模型可支撑 dense/sparse/hybrid 演进。

缺点：本地推理需要额外模型运行时和内存；macOS 资源占用、批量吞吐、实际中文 Recall/P95 尚未在本机测量；不能只看 Model Card 榜单替代项目实测。

## 3. 选型对比

| 模型 | 本地性 | 维度 | 上下文 | 成本/资源 | 当前定位 |
|---|---|---:|---:|---|---|
| text-embedding-3-small | 云 API | 1536 | 8192 | API 成本低 | 最快 baseline |
| text-embedding-3-large | 云 API | 3072 | 8192 | 约 6.5x small 示例成本 | 质量上限对照 |
| bge-m3 | 本地可运行 | 1024 | 8192 | 本地 CPU/RAM 成本 | 中文/隐私推荐 |

上述“更好”不能脱离本项目指标；选型必须以同一 470 Case 的实测为准。

## 4. 对 AntiSentinel 的建议

### 推荐顺序

第一选择：`bge-m3` 本地 Embedding，当前已确认采用。

原因：AntiSentinel 的长期 Memory 含大量中文、英文错误信息、Redis key、服务名和工具结果；本地部署符合 Evidence/Memory 不出机原则，也能避免每次 Memory 写入都产生外部 API 成本。

云端模型仅作为后续可选对照，不进入默认路径：`text-embedding-3-small`。

原因：它是最快的云 Embedding baseline，能区分“本地模型质量问题”和“向量检索设计问题”。

暂不默认：`text-embedding-3-large`。

原因：成本和向量空间更大，当前没有项目数据证明它相对 small 或 bge-m3 的收益。

### 本地化决策

用户已确认优先本地 Embedding：

```text
默认模型：BAAI/bge-m3
默认维度：1024
运行方式：本机 CPU/ONNX/量化优先，避免新增向量服务
数据出口：0，Memory/Evidence 不发送给 Embedding 云 API
云端费用：0
```

本地资源成本必须通过真实 Case 测量，包括模型加载内存、首次加载时间、批量吞吐和 P95 查询延迟；在这些数字产生前不宣称“性能更好”。

### 存储设计

```text
SQLite memory_records
  ├─ memory_id
  ├─ content_version
  ├─ embedding_model
  ├─ embedding_dimension
  ├─ embedding_status
  └─ vector_ref / vector_blob

SQLite/derived vector index
  └─ memory_id + vector + version

Canonical Event/Evidence
  └─ 不被向量索引覆盖
```

第一版可以使用 `sqlite-vec` 或等价可替换 adapter；若扩展不可用，回退到 Python cosine scan，并把回退记为 `vector_bypass`，不伪装为 vector hit/miss。

### Hybrid Retrieval

最终检索不应只用向量：

```text
Vector semantic candidates
+ FTS5/BM25 exact candidates
+ identifier/error-code candidates
→ RRF / Explainable Rank
→ owner / Incident / validity / conflict Filter
→ EvidenceRef Resolver
→ ContextView
```

Agno 官方 Knowledge 示例也把 vector 与 BM25 作为 hybrid search 的两个互补通道，并在 metadata filter 后再交给 Agent context。[Agno Knowledge](https://docs.agno.com/demo-os/knowledge)

## 5. 严格实验协议

### 固定输入

- LongMemEval-S cleaned SHA256：`d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442`
- 500 Case；检索指标评估 470 non-abstention Case。
- 固定 query、Session/Turn ID、Ground Truth 和 top-k。
- 固定 Memory Record projector、chunking、prompt revision 和 filter 规则。

### 每个模型都测

- Recall@1/@3/@5
- Precision@5
- MRR
- P50/P95 query latency
- embedding 生成耗时
- index build 耗时
- 向量索引大小
- SQLite 文件增量
- 失败数 / bypass 数
- source_refs / EvidenceRef 完整率

### 硬门槛

暂沿用项目检索门槛：

- Precision@5 >= 80%
- Recall@5 >= 60%
- MRR >= 80%
- P95 <= 词法基线的 1.20 倍
- EvidenceRef 完整率 = 100%
- 后台异常 = 0

任一硬门槛未满足，只能报告“局部改善”或“未达标”。

## 6. Codex 风格的最终 Memory 方案

推荐 AntiSentinel 采用：

```text
Phase 1：每个 Session/Turn 异步抽取 Memory Record
Phase 2：按 usage/time/conflict 全局 consolidation
Phase 3：生成 FTS5 + Vector 派生索引
Read：Hybrid Retrieval + Filter + EvidenceRef + ContextView
Audit：Event/Evidence canonical facts + usage telemetry
```

这比“每条对话直接生成向量并塞进向量库”更接近 Codex 的精髓，也更符合 AntiSentinel 的审计需求。

## 7. 下一步 Case（实现前）

```text
Case: Embedding 模型对比与 Hybrid Memory Retrieval
范围: bge-m3 本地 + text-embedding-3-small 对照；不改主诊断逻辑
输入: LongMemEval-S 470 non-abstention queries、已有 MemoryRecord projector
运行: 各模型独立构建临时向量索引，固定 top-k=1/3/5
观测: Recall@1/@3/@5、Precision@5、MRR、P95、build time、index bytes、EvidenceRef 完整率
预期: 所有模型 470 Case 可复现；向量版本、模型和 SHA256 写入 SQLite Evaluation Run
失败判定: 向量维度混用、版本未绑定、scope 污染、EvidenceRef 悬挂、异常未记录、P95 超基线 1.20x
清理: 仅删除临时向量索引和测试 SQLite，不删除正式 Memory/Benchmark 数据
基线: lexical Memory Recall@5=77.78%，Precision@5=36.09%，MRR@5=78.21%
指标: Precision@5>=80%、Recall@5>=60%、MRR>=80%、P95<=baseline*1.20、EvidenceRef=100%、异常=0
```
