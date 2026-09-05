# Memory Record、Session Tree、Rank/Filter 与 EvidenceRef 调研

## 调研范围

目标是把已完成的 LongMemEval-S Retrieval-only 基线，升级为真正使用：

```text
Memory Record → Session Tree → Rank → Filter → EvidenceRef → ContextView
```

本记录区分外部项目的一手事实与 AntiSentinel 的建议，不把外部实现当成必须照搬的方案。

## 一、当前 AntiSentinel 基线

### 已有真实代码

- `SessionTimelineTree` 能从 Event 构建短时树，节点包含 `node_id/session_id/node_type/aggregate_id/parent_node_id/event_type/occurred_at/summary/related_ids`。
- `MemoryContextView` 目前只有 `session_id/digest/evidence_refs`，尚未包含结构化 Memory Record 列表、分数、过滤原因和版本。
- `RolloutMemory` 能保存 rollout summary、diagnosis、incident/session 关联，并写入 SQLite/JSONL/Redis cache。
- `PreferenceGraph` 能做 operator 维度的 preference upsert、supersede、conflict 和 Redis index。
- `EvidenceRef` 只有 `evidence_id + role`，Evidence 本体仍由 EvidenceStore 保存。
- LongMemEval 当前是独立关键词检索器，直接对原始 haystack Session 做 token overlap；尚未把命中结果转成真实 Memory/EvidenceRef，也没有进入 ContextBuilder。

### 当前全量基线

LongMemEval-S cleaned 500 Case 中，470 个非 abstention Case 的关键词结果为：

- Recall@1：40.96%
- Recall@5：78.58%
- Recall@10：88.26%
- Precision@10：16.60%
- MRR@10：76.13%

核心问题不是完全找不到，而是 Top-K 过宽：候选命中后缺少 rank/filter 和证据绑定，导致过多无关 Session 进入上下文。

## 二、外部一手资料事实

### Codex：canonical replay + query index

来源：[openai/codex `thread-store/src/local/mod.rs`](https://github.com/openai/codex/blob/main/codex-rs/thread-store/src/local/mod.rs)

官方源码将本地 ThreadStore 描述为文件系统/SQLite 双层实现：Rollout JSONL 是可耐久回放格式，SQLite state DB 是可查询 metadata index；活跃追加仍写 canonical JSONL，同时将派生 metadata 更新到 SQLite。

启示：原始事件/回放不能被 Memory 摘要替代；查询时应先用索引定位候选，再回到 durable record 读取可信内容。Index 丢失时必须可重建，不能让缓存成为唯一事实。

### Agno：Db seam、Memory 与 Context Control

来源：

- [Agno Database](https://docs.agno.com/database/overview)
- [Agno SqliteDb](https://docs.agno.com/reference/storage/sqlite)
- [Agno Storage Control](https://docs.agno.com/sessions/persisting-sessions/storage-control)

官方文档把 Session persistence、history、state、Memory、Knowledge、Trace 和 Evaluation 收口到 Db interface；SQLite adapter 提供本地轻量存储。Agno 还明确区分“保存什么”和“运行时上下文收到什么”：历史、Tool message、媒体可以分别裁剪，token metrics 仍需保留。

启示：Memory Record 的持久化格式与 ContextView 的注入格式应该分离；检索返回的是受控的 evidence-bearing context，而不是把整张 Session/工具原文塞给模型。

### Redis：ZSET 负责顺序，Hash/JSON 负责内容

来源：

- [Redis Sorted Sets](https://redis.io/docs/latest/develop/data-types/sorted-sets/)
- [Redis secondary indexes](https://redis.io/docs/latest/develop/clients/patterns/indexes/)
- [Redis data type comparison](https://redis.io/docs/latest/develop/data-types/compare-data-types/)

Redis 官方将 Sorted Set 定义为 member + floating-point score 的有序集合，适合二级索引、排行榜和按分数取 Top-K；Hash 适合字段较少的对象，JSON 适合嵌套结构。排序索引和内容对象是两种不同职责。

启示：Redis 只保存 `memory_id → score` 的候选索引和短 payload；完整 Memory Record/EvidenceRef 必须回源 SQLite。score 变化通过 ZADD 更新，不把大 JSON 直接塞进 ZSET member。

## 三、Memory Record 设计

推荐把 Memory Record 作为统一检索文档，不按实现来源分散成多套查询 API：

```json
{
  "memory_id": "preference:operator-1:diagnosis-order:v3",
  "memory_type": "preference | episodic | semantic | session_digest | evidence_summary",
  "owner_id": "operator-1",
  "incident_id": "optional",
  "session_id": "optional",
  "source_refs": [
    {"type": "event", "id": "event-1"},
    {"type": "evidence", "id": "evidence-1", "role": "supporting"},
    {"type": "turn", "id": "turn-1"}
  ],
  "subject": "diagnosis_order",
  "predicate": "prefers",
  "object": "logs_before_metrics",
  "content": "排查故障时先看日志，再看指标",
  "content_version": 3,
  "status": "active | superseded | conflict | expired",
  "confidence": 0.92,
  "valid_from": "2026-09-04T00:00:00Z",
  "valid_to": null,
  "created_at": "...",
  "updated_at": "..."
}
```

关键原则：

- `source_refs` 是 provenance，不等于把 Evidence 原文注入模型。
- `content_version/status/valid_from/valid_to` 是冲突合并和时效过滤所需的最小字段。
- preference、episodic、semantic、digest 统一进入候选召回，但使用不同的类型权重。
- `memory_id` 必须稳定；更新生成新 version 或可审计 supersede，不覆盖原始事实。

## 四、Session Tree 如何参与检索

Session Tree 不是长期知识图谱，也不是简单聊天记录。它是当前 Incident/Session 的短时工作集：

```text
Session
 ├─ Turn
 │   ├─ Task
 │   │   └─ ToolCall → Attempt → EvidenceRef
 │   └─ hypothesis / summary
 └─ current digest
```

建议将树节点转换为两类候选：

1. `session_digest`：窗口内稳定摘要，用于低成本上下文。
2. `evidence_summary`：只保存 EvidenceRef、类型、摘要和时间，不保存原文。

检索时先使用当前 Session Tree 做 scope boost：同一 Session/Incident 的候选加分；跨 Session 的长期 Memory 仍需满足 owner、时间和类型过滤。

## 五、Rank 设计

推荐使用可解释的线性分数，不先引入向量数据库：

```text
final_score =
  0.35 * lexical_score
+ 0.20 * scope_score
+ 0.15 * recency_score
+ 0.15 * confidence_score
+ 0.10 * evidence_support_score
+ 0.05 * type_score
- conflict_penalty
- stale_penalty
```

字段说明：

- `lexical_score`：问题与 Memory content/subject/object/digest 的匹配。
- `scope_score`：同 Session > 同 Incident > 同 operator > 全局。
- `recency_score`：时间衰减，但 active preference 的衰减慢于 session digest。
- `confidence_score`：人工/规则/模型来源及历史确认次数。
- `evidence_support_score`：是否有可追溯 EvidenceRef，且引用仍有效。
- `type_score`：Preference 问题提升 preference；诊断复盘提升 episodic/evidence_summary。
- `conflict_penalty`：存在 active conflict 时降权或要求显式说明。
- `stale_penalty`：过期或 superseded 记录不得进入默认 ContextView。

每个返回项必须带 `score_breakdown`，便于 Dashboard 审计“为什么召回”。

## 六、Filter 设计

Rank 之前先做硬过滤，避免靠分数把不应出现的记忆排到后面：

1. owner filter：operator/user scope 必须匹配。
2. validity filter：`valid_from <= query_time` 且 `valid_to` 为空或晚于 query_time。
3. status filter：默认只允许 `active`；`superseded/conflict/expired` 仅审计查询可见。
4. incident scope：当前故障上下文优先同 Incident，防止跨故障污染。
5. evidence integrity：EvidenceRef 必须能在 EvidenceStore 找到且 hash 校验通过。
6. token budget：按 item cost 做 knapsack/顺序截断，不先取 Top-10 再粗暴截字符串。
7. conflict policy：同 subject/predicate 只能注入一个 active object；有冲突时返回 conflict metadata，不同时注入互相矛盾的两个值。

过滤结果也要留 trace：`candidate_count`、各过滤原因、`selected_memory_ids`、`selected_evidence_ids`、最终 context tokens。

## 七、EvidenceRef 设计

EvidenceRef 是 Memory 与不可变 Evidence 之间的轻量连接，不是 Evidence 内容副本：

```text
MemoryRecord.source_refs
        ↓ resolve
EvidenceStore.get(evidence_id)
        ↓ verify hash / status / time
ContextView.evidence_refs
        ↓
Model Context 只收到摘要和引用 ID
```

建议扩展：

```python
EvidenceRef(
    evidence_id: str,
    role: str,
    source_type: str = "tool_result",
    confidence: float | None = None,
    excerpt: str | None = None,
)
```

`excerpt` 只允许有界、脱敏摘要；原始工具输出继续留在 Evidence Store。若 Evidence 不存在、hash 不一致或已过期，Memory 不能进入可注入集合，并记录 `evidence_unresolvable`。

## 八、三个候选方案

### 方案 A：SQLite 全表扫描 + Python Rank（推荐第一步）

SQLite 按 owner/status/time/type 过滤，读取小批候选后在 Python 做可解释打分；Redis 只做热缓存。

优点：最少新组件，容易审计和测试，适合先验证 Rank/Filter 正确性。

缺点：全文检索性能有限，LongMemEval 全量时需要预计算 token/关键词索引。

### 方案 B：SQLite FTS5 + Python Rank

为 Memory content、subject、predicate、object、digest 建 FTS5 虚拟表，再结合 scope/recency/confidence/EvidenceRef 做重排。

优点：仍是本地单文件，关键词召回比全表扫描稳定，接口不变。

缺点：中文分词和 FTS5 tokenizer 需要专项验证；仍不是语义检索。

### 方案 C：Redis Search/Vector + SQLite 回源

Redis 保存文档索引或向量，SQLite 保留事实；查询先 Redis，再回源校验。

优点：大规模和混合检索潜力更好。

缺点：引入 Redis module/版本差异，复杂度和内存上升；当前本地 Redis 只需要 ZSET/Hash，暂不值得。

推荐顺序：先 A 验证正确性，再 B 提升关键词召回；只有真实数据证明 lexical ceiling 不够，才评估 C。

## 九、LongMemEval-S 的适配方式

不把每个原始 Turn 永久复制成 Memory Record。导入时生成：

```text
LongMemEval session
  → session_digest MemoryRecord
  → 每个 has_answer Turn 的 evidence_summary MemoryRecord
  → source_refs 指向 session_id / turn_id
```

查询流程：

1. 读取 Case 的 question、question_date 和 haystack Session。
2. 生成候选 MemoryRecord，保留原始 session/turn provenance。
3. 硬过滤无效时间、错误 scope、superseded/conflict。
4. Rank 候选并返回 Top-K MemoryRecord。
5. 解析 `source_refs` 得到 predicted session IDs 和 turn IDs。
6. 用官方 `answer_session_ids` 与 `has_answer` 计算 Recall/Precision/MRR。
7. 将 selected Memory/EvidenceRef 和 score breakdown 写入 Evaluation Result。

这样可以同时测试：召回正确性、证据可追溯性、ContextView token 预算和过滤误召回。

## 十、真实 Case（实现前待确认）

```text
Case: LongMemEval-S 30 Case Memory Record + Session Tree + Rank/Filter
范围: Retrieval-only 优化，不调用回答模型，不改变线上 Runtime
输入:
  - 已下载并校验的 longmemeval_s_cleaned.json
  - 按 question_type 分层抽取 30 个 Case，包含 preference、knowledge-update、temporal、multi-session 和 abstention
运行:
  PYTHONPATH=src python scripts/run_longmemeval.py --mode memory-record --limit 30 --top-k 1 5 10
观测:
  - SQLite evaluation_runs/cases/results
  - MemoryRecord 数量、source_refs、EvidenceRef 可解析率
  - Session Tree 节点数和 digest token
  - 每个 Case 的 candidate_count、过滤原因、score_breakdown、selected IDs
  - Recall/Precision/MRR 与此前关键词基线对比
预期:
  - 30 Case 全部映射成功，source_refs 无悬挂引用
  - superseded/conflict/过期 Memory 不进入默认 ContextView
  - Top-K 结果可还原为 Session/Turn IDs
  - 无答案的 abstention 不生成伪 Evidence 命中
  - 结果可从 SQLite 重启恢复
失败判定:
  - Memory/EvidenceRef 引用悬挂、跨 operator/Incident 污染、冲突同时注入、分数不可解释、结果不可恢复或后台异常
清理:
  - 仅删除本 Case 生成的 Evaluation Run、临时 SQLite 和 Redis prefix，不触碰正式 storage/benchmarks 数据
```

## 十一、建议决策

下一步采用方案 A 的最小版本，但把 SQLite FTS5 作为可替换内部实现，不暴露给 Runtime：

```text
LongMemEval Adapter
  → MemoryRecordProjector
  → SessionTreeScope
  → CandidateFilter
  → ExplainableRanker
  → EvidenceRefResolver
  → RetrievalResult
```

先完成 Retrieval-only 30 Case 并和关键词基线对比，确认 Precision 是否改善；达到稳定后再跑全 500。不要在这一阶段引入向量模型、Redis Search module 或 LLM reranker。

## 十二、30 Case 真实验证结果（严格版）

2026-09-04 已完成 30 Case 分层真实验证：6 类 `question_type` 各 5 条，使用真实 LongMemEval-S 数据，未调用回答模型。

验证链路：

```text
LongMemEval → MemoryRecordProjector → RetrievalFilter
  → ExplainableRanker → EvidenceRefResolver
```

结果：

- Memory Record 平均候选：547.27 条；已改为所有 Turn 平等投影，不读取答案标注。
- Filter 后平均候选：547.27 条；本批数据没有过期/冲突/错误 owner，因此没有硬过滤命中。
- Rank 后固定输出：10 条。
- `source_refs` 悬挂引用：0。
- 优化版 Recall@10：83.89%。
- 优化版 Precision@10：24.77%。
- 原关键词基线 Recall@10：90.56%。
- 原关键词基线 Precision@10：15.33%。

此前 92.22% / 17.83% 的结果使用了 `has_answer` 标注生成候选，存在评测泄漏，已废弃。严格版相对关键词基线 Recall@10 下降 6.67 个百分点，Precision@10 提升 9.44 个百分点。当前 Rank/Filter 减少了误召回，但需要 FTS5/BM25 或查询扩展提高召回。

本次验证仅证明 Retrieval-only adapter 的映射、过滤接口、排序 breakdown 和 EvidenceRef 还原可运行；没有宣称达到 PRD 的 85% cache、30% token 或 90% end-to-end accuracy gate。

## 十三、架构决策落地

最终采用 Codex 的基础设施思想，而不是复制 Codex 的领域对象：

```text
Event/Evidence canonical facts
  → SQLite rebuildable projections/indexes
  → async Memory/Preference derivation
  → lexical/vector retrieval
  → Rank/Filter/EvidenceRef
```

这保证 Memory 摘要、FTS5/BM25 和后续向量索引都可以删除重建，且不会改变故障事实。AntiSentinel 的 EvidenceRef 仍是诊断证据链的唯一入口。
