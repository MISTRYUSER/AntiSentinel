# PRD-003 Memory & Evidence Foundation

## 基本信息

- **状态**：Ready
- **提出角色**：AntiSentinel PM
- **目标**：建立“短时会话树 + 长时偏好图谱”的分层记忆架构
- **优先级**：P0
- **依赖**：PRD-001 Domain Kernel、PRD-002 Runtime Loop
- **后续依赖**：PRD-004 Code Map Foundation、PRD-005 RAG Retrieval、PRD-006 Plan

## 0. 当前代码基线

当前 Domain 和 Runtime 已经具备：

- `Incident → Session → Turn → Task → ToolCall → Attempt` 关系。
- 不可变 `Evidence` 和轻量 `EvidenceRef`。
- 追加式 `Event` 与状态转换。
- Runtime ContextBuilder 已能使用摘要和引用构造上下文。
- Runtime Checkpoint 已有内存实现。

当前 `memory/` 和 `persistence/` 仍是 skeleton：

- EventStore、EvidenceStore、StateStore 尚未实现。
- SessionTree、Recorder、Recall、Indexer 尚未实现。
- PreferenceGraph、异步 LLM Extractor 和关键词文本记忆尚未实现。

## 1. 背景与问题

AntiSentinel 面对的是一个持续演进的 Bug，而不是一次性问答。系统必须同时保留：

1. Incident 上发生过什么，作为排障真相。
2. 当前 Session 如何一步步演进，作为短期工作集。
3. 历史 Rollout 如何解决过类似问题，作为长期经验。
4. 操作者有哪些稳定偏好、常用错误码和使用习惯，作为用户长期记忆。
5. 对话中哪些关键词值得保留，作为可搜索的文本记忆。

这些信息不能混为一个聊天历史，也不能让模型生成的总结覆盖原始故障证据。本 PRD 先完成可靠存储和最小记忆读写，为后续 Code Map、RAG、Plan 和 DAG 提供稳定输入。

## 2. 核心模型

```text
Incident Evidence Layer
  ├── Evidence（原始证据元数据与内容引用）
  ├── Event（不可变事实）
  └── State Projection（从 Event 投影的查询状态）

Short-term Session Timeline Tree
  ├── turns
  ├── task branches
  ├── hypotheses
  ├── evidence_refs
  └── timestamps / parent_node_id

Long-term Preference Graph
  ├── user/operator entities
  ├── preference edges
  ├── common error codes
  └── conflict/merge history

Long-term Rollout Memory
  ├── rollout summaries
  ├── aggregated LLM summaries
  └── evidence_refs

Keyword Text Memory
  ├── LLM-selected keywords
  ├── source session/turn
  └── append-only text records
```

关系：

```text
Incident 1 ── N Session
Incident 1 ── N Evidence
Incident 1 ── N Event
Session 1 ── N Turn
Turn 1 ── N Task
Attempt 1 ── N EvidenceRef
```

## 3. 目标

- 实现 `EventStore`：append-only 写入、按聚合查询、按 correlation 查询。
- 实现 `EvidenceStore`：不可变写入、按 `evidence_id` 读取、内容引用和 hash 校验。
- 实现 `StateStore`：保存从 Event 投影得到的查询状态，不作为独立事实源。
- 实现 `MemoryRecorder`：将 Runtime 产生的 Event、Evidence、Rollout 和摘要路由到对应存储。
- 实现短期 `SessionTimelineTree`：按时间和父节点记录 Turn、Task、假设、EvidenceRef 和 Tool Output 引用。
- 实现长期 `RolloutMemory`：保存已完成或失败的诊断 Rollout 结构化摘要和 LLM 聚合总结。
- 实现长期 `PreferenceGraph`：保存用户实体、用户习惯、常用错误码和偏好关系。
- 引入后台异步 LLM Extractor，定向抽取用户实体和偏好，不阻塞主诊断链路。
- 实现实体消解、同义实体归并和冲突合并，并保留合并来源与审计记录。
- 使用 Redis 持久化用户偏好热数据、常用错误码和 LLM 聚合总结。
- 实现 `KeywordMemory`：由 LLM 判定值得保留的关键词，并追加写入文本文件。
- 实现 `MemoryRecall`：按 Session、Incident、Rollout、Operator 和关键词范围召回，不跨范围泄漏。
- 实现 `SessionDigest`：在 Token Budget 下生成可丢失、可重建的上下文摘要。
- 为 Redis、文本文件和 Event/Evidence Store 定义独立 Port，保持实现可替换。
- 保留未来替换本地存储和 Redis 部署方式的 Adapter seam。

## 4. 非目标

- 不实现 BM25、Embedding、Vector DB、Reranker 或 Graph RAG；由 PRD-005 负责。
- 不实现代码地图和 AST 解析；由 PRD-004 负责。
- 不实现模型自动总结为事实。
- 不实现复杂知识图谱和自动实体推理。
- 不使用 Git 作为在线记忆存储。
- Redis 只承载结构化长期记忆，不承载 Incident 原始 Evidence 和 Event 真相。
- 关键词文本文件不作为结构化事实源，不替代 EvidenceStore。
- 不解决跨租户、复杂权限中心和数据合规平台问题。

## 5. 存储边界

推荐第一版本地布局：

```text
storage/incidents/{incident_id}/
├── evidence/{evidence_id}.json
├── events.jsonl
└── state.json

storage/sessions/{session_id}/
└── keywords.txt

Redis:
├── session:{session_id}:timeline
├── rollout:{rollout_id}
├── operator:{operator_id}:preference_graph
├── operator:{operator_id}:habits
├── operator:{operator_id}:common_error_codes
├── operator:{operator_id}:entity_aliases
├── summary:{scope}:{scope_id}
└── session:{session_id}:digest
```

规则：

- `events.jsonl` 只追加，不覆盖旧事件。
- Evidence 内容可以在外部地址，存储层保存 `content_ref`、`content_hash` 和元数据。
- `state.json` 可以删除并通过 Event 重建。
- Session Timeline Tree 保留有限窗口；超过窗口的节点进入 Rollout 摘要或由 Event 重建。
- Redis 中的 Rollout、偏好、实体别名、错误码和聚合总结是结构化长期记忆，必须带来源、时间、版本和置信度。
- `session:{id}:digest` 是缓存，不是事实源，丢失后必须能够重建。
- 关键词文本文件只追加，不覆盖历史记录；每条记录必须带 `incident_id/session_id/turn_id`。
- User Memory 与 Incident Evidence 物理隔离。

## 6. 写入流程

```text
Runtime / ToolExecutor
  ↓
Event + Evidence
  ↓
MemoryRecorder
  ├── EventStore.append
  ├── EvidenceStore.put_once
  ├── StateProjector.apply
  ├── SessionTimelineTree.update
  ├── RolloutMemory.maybe_record
  ├── PreferenceGraph.enqueue_extraction
  └── KeywordMemory.extract_and_append
```

`EvidenceStore.put_once` 对同一 `evidence_id` 重复写入必须保证幂等；如果内容 hash 不同，必须报错，不能覆盖原证据。

## 7. 读取与上下文流程

```text
Runtime 请求 Context
  ↓
SessionTree 读取当前工作集
  ↓
按 EvidenceRef 读取必要摘要
  ↓
按 Token Budget 裁剪
  ↓
ContextView
  ↓
Model
```

默认只将以下内容放入模型上下文：

- Incident 当前快照。
- 当前 Session Digest。
- 当前 Turn/Task 摘要。
- 相关 Evidence 摘要和 EvidenceRef。
- 未决假设和最近工具结果摘要。
- 经过明确披露的 Operator 偏好。

完整原始 Evidence 只在按需读取时使用，不能因为存在于存储中就全部注入模型。

## 8. Session Timeline Tree 规则

- 节点必须绑定 `session_id`、时间戳以及对应的 `turn_id/task_id`。
- 节点通过 `parent_node_id` 形成时序树；并行 Task 可以形成多个子分支。
- 保存关键词、实体、未决假设、EvidenceRef 和 Tool Output 引用，但不保存完整原始输出。
- 新 Event 到达时增量更新；全量重建结果必须与 Event 回放一致。
- 新 Session 默认不继承其他 Session 的临时假设。
- 同一 Incident 的历史 Session 可以被显式召回，但不能自动全部注入上下文。
- Timeline Tree 是短期记忆，不承担跨 Incident 的长期经验职责。
- Digest 只能是 Context View，不能写回覆盖 Evidence 或 Event。

## 9. 长期 Rollout 与 Preference Graph 规则

- Rollout 记录一次诊断/执行过程的结构化摘要：问题类型、关键步骤、工具调用摘要、EvidenceRef、结论和结果状态。
- Rollout 不复制 Evidence 原文，必须引用 `evidence_id` 和来源版本。
- Preference Graph 只保存用户习惯、常用错误码、通知偏好和明确操作偏好。
- 用户实体、习惯和错误码必须绑定 `operator_id/user_id`、来源 Session/Turn、更新时间和置信度。
- 后台 LLM 只抽取目标实体和偏好，不负责修改 Incident Evidence。
- 实体消解必须保留 canonical entity、alias、来源和消解理由。
- 低置信度或冲突记忆不能直接覆盖已有值，应保留候选并进入冲突合并流程。
- 冲突合并结果必须保留旧值、新值、决策依据和模型版本。
- Redis 记录需要 TTL/版本策略；删除和重建不影响 Incident Evidence。
- Rollout、Preference Graph 和聚合总结必须使用不同 key namespace，不能相互污染。

## 10. Redis 热数据与聚合总结

- Redis 缓存用户偏好热数据、常用错误码、实体别名和 LLM 聚合总结。
- 聚合总结必须标记 `source_ids`、生成时间、模型版本和上下文版本。
- 缓存命中失败时，必须从持久化来源重建，不能将缓存视为唯一事实源。
- 聚合总结只能作为 Context View，不能覆盖原始 Event、Evidence 或 Session Timeline。
- 目标指标：偏好/总结缓存命中率 ≥ 85%，相关场景上下文 Token 消耗降低约 30%。

## 11. Keyword Memory 规则

- 关键词由独立 LLM Extractor 判断，不由普通字符串分词自动写入。
- Extractor 只能从当前允许的 Session/Incident 内容中选择关键词。
- 每条关键词记录至少包含关键词、类型、来源 ID、判定时间和模型版本。
- 关键词文本文件使用 append-only 格式；重复关键词允许保留来源记录，由后续索引阶段去重。
- LLM 抽取失败不影响主诊断流程，只记录失败 Event。

## 12. 错误、恢复和一致性

- Event 写入失败：本次 Runtime 不能报告持久化成功，返回可识别错误。
- Evidence 重复 ID 且 hash 不一致：拒绝写入并记录冲突，不覆盖旧值。
- State Projection 失败：保留 Event，标记 projection lag，可从头重放。
- Session Timeline Tree 损坏：从 Event 和 EvidenceRef 重建，不依赖 digest。
- Redis 暂时不可用：保留主链路结果，标记长期记忆写入失败并支持补偿；不得阻塞只读诊断。
- 异步 LLM Extractor 超时或失败：保留待处理任务，不影响主诊断链路。
- 实体消解不确定时保留候选实体，不强行合并。
- Keyword LLM 抽取失败：不影响 Incident 诊断主链路。
- 文本文件追加失败：记录待补偿任务，不回滚已经产生的 Incident Evidence。
- 存储读取超时：返回局部结果并标记数据不完整，不伪造完整上下文。
- 同一个 Event 重放多次不得产生重复关系或重复状态副作用。

## 13. Event 与指标

### 13.1 Memory Tracing

Memory 命中率、Token 节省和诊断准确率必须通过同一条动作链路关联，不能只看 Redis 独立监控。使用项目选定的第三方 Tracing Backend 记录以下 Span/Attributes：

### 13.2 请求链路传播

Tracing 的关联规则：

- `trace_id`：一次 Incident/Session 诊断运行的全局 ID。
- `request_id`：一次具体动作或边界请求的唯一 ID，例如一次 Model 调用、一次 Memory lookup、一次 ToolCall。
- `parent_request_id`：当前动作由哪个上游请求触发。
- `causation_id`：Event 由哪个前置 Event 或请求造成；用于事件因果链。
- `span_id`：由第三方 Tracing SDK 生成的具体 Span ID，不作为业务主键。

动作链路示例：

```text
request_id=req-001  session.run
└── request_id=req-002  model.complete
    └── request_id=req-003  task.create
        └── request_id=req-004  tool_call.execute
            ├── request_id=req-005  memory.evidence.write
            └── request_id=req-006  tool.result.received
                └── request_id=req-007  model.complete
```

每个后续动作都生成自己的 `request_id`，同时携带同一个 `trace_id` 和上游 `parent_request_id`；不能把所有动作复用成一个 `request_id`。协议边界、Event、Memory lookup、Evidence 写入和 Tool 执行都必须传播这组关联字段。

```text
incident.run
└── turn
    └── context.build
        ├── session_timeline.read
        ├── rollout_memory.lookup
        ├── preference_graph.lookup
        ├── summary_cache.lookup
        └── keyword_memory.lookup
```

每次 Memory lookup 至少记录：

```text
trace_id
request_id
parent_request_id
causation_id
incident_id
session_id
turn_id
memory_type
lookup_scope
cache_layer
cache_hit: true | false
fallback_used: true | false
latency_ms
input_token_estimate
output_token_estimate
source_count
```

每次 Context 构造至少记录：

```text
context_token_budget
tokens_without_memory
tokens_with_memory
tokens_saved
evidence_ref_count
summary_source_ids
```

禁止将 API Key、完整用户偏好、Evidence 原文或敏感工具输出写入 Trace Attributes。命中率按固定时间窗口、Memory 类型和缓存层分别统计；不能把不同层级的命中混成一个数字。

至少记录：

```text
memory.event_appended
memory.evidence_stored
memory.evidence_conflict
memory.state_projected
memory.state_projection_failed
memory.session_tree_updated
memory.session_tree_rebuilt
memory.digest_rebuilt
memory.rollout_recorded
memory.preference_extraction_queued
memory.preference_entity_extracted
memory.preference_entity_resolved
memory.preference_conflict_detected
memory.preference_conflict_merged
memory.preference_graph_updated
memory.summary_cached
memory.summary_cache_missed
```

至少提供：

- Event append 成功率和延迟。
- Evidence 写入成功率、hash 冲突数。
- Projection lag 和重放耗时。
- Session Timeline Tree 召回命中率。
- Digest 重建耗时和 Token 大小。
- Rollout 召回命中率和写入延迟。
- Preference Graph 更新成功率、实体消解准确率、冲突率和过期率。
- Redis 偏好/总结缓存命中率，目标 ≥ 85%。
- 上下文 Token 消耗变化，目标降低约 30%。
- 相关诊断准确率，目标达到 90% 以上。
- EvidenceRef 引用完整率。
- Keyword Extractor 成功率和文本追加延迟。
- Trace 完整率：Memory lookup 与 Context 构造具有关联 `trace_id` 的比例。
- 分层缓存命中率：Session Timeline、Rollout、Preference Graph、Summary Cache 分别统计。
- 回源率、回源延迟和缓存失效原因。
- 每次诊断的 Token 节省量与最终准确率关联。

## 14. 验收标准

- [ ] EventStore 支持追加、按 Incident/Session/Task 查询和幂等重放。
- [ ] EvidenceStore 写入后不可覆盖，hash 冲突会失败。
- [ ] StateStore 可以从 Event 重建当前状态。
- [ ] Runtime 产生的 EvidenceRef 可以回读对应 Evidence 元数据。
- [ ] SessionTimelineTree 能记录多个 Turn、Task、分支、未决假设和 EvidenceRef。
- [ ] SessionTimelineTree 删除后可以从 Event 重建。
- [ ] Digest 丢失后可以重建，且不影响 Incident 真相。
- [ ] 已完成 Rollout 和 LLM 聚合总结可以结构化写入 Redis 并按范围召回。
- [ ] 用户实体、习惯和常用错误码可以写入 Preference Graph 和 Redis，并与 Incident 隔离。
- [ ] 后台异步 LLM 可以定向抽取用户实体和偏好，不阻塞主诊断。
- [ ] 同义实体可以消解为 canonical entity，并保留 alias 与来源。
- [ ] 冲突偏好可以进入合并流程，且保留决策审计记录。
- [ ] 关键词经 LLM 判定后可以追加写入文本文件。
- [ ] 不同 Incident/Session/Operator 的记忆不会越界召回。
- [ ] Rollout/Preference Graph/Keyword Memory 不会覆盖 Incident Evidence。
- [ ] Redis 缓存命中率在目标数据集上达到 ≥ 85%。
- [ ] 相关场景上下文 Token 消耗相比无记忆基线降低约 30%。
- [ ] 在固定 benchmark 上，使用记忆后的诊断准确率达到 90% 以上。
- [ ] 每次 Memory lookup 和 Context 构造都能关联到同一条 Trace。
- [ ] 每次后续动作都有独立 `request_id`，并通过 `parent_request_id/causation_id` 连接上游动作。
- [ ] Model、ToolCall、Evidence 写入和 Memory lookup 不会丢失请求链路字段。
- [ ] 能按 Memory 类型区分命中、未命中、回源和延迟。
- [ ] 能从 Trace 计算 Token 节省，并与诊断结果关联分析。
- [ ] Trace 中不包含 API Key、Evidence 原文或未脱敏用户偏好。
- [ ] 存储 Port 不依赖 Web 框架或具体数据库 SDK；LLM Extractor 通过接口注入。
- [ ] 现有 Domain/Runtime 测试保持通过，并新增 Memory 集成测试。

## 15. 推荐测试用例

```text
test_event_store_appends_and_queries_by_correlation
test_event_replay_is_idempotent
test_evidence_store_put_once
test_evidence_hash_conflict_is_rejected
test_state_projection_rebuilds_from_events
test_memory_recorder_links_event_and_evidence
test_session_timeline_tree_records_turn_task_and_evidence_ref
test_session_timeline_tree_rebuild_matches_incremental_update
test_digest_can_be_deleted_and_rebuilt
test_rollout_memory_writes_structured_summary_to_redis
test_preference_graph_stores_habits_and_common_error_codes
test_async_llm_extracts_target_user_entities
test_entity_resolution_merges_aliases
test_conflicting_preferences_are_not_overwritten
test_conflict_merge_keeps_audit_history
test_summary_cache_hit_and_rebuild_on_miss
test_memory_recall_is_scoped_to_session_incident_and_operator
test_keyword_extractor_appends_llm_selected_keywords_to_text
test_keyword_extractor_failure_does_not_fail_diagnosis
test_rollout_and_preference_memory_do_not_store_raw_evidence
test_partial_storage_failure_is_visible
```

## 16. 完成定义

使用本地 Event/Evidence 文件实现、Redis（本地实例或测试替身）和 Fake/真实可替换 Extractor，可以完成：

```text
Runtime Event / Evidence
→ EventStore / EvidenceStore
→ State Projection
→ SessionTimelineTree
→ Redis Rollout / Preference Graph / Summary Cache
→ Async LLM Entity Extraction
→ Keyword Text Memory
→ SessionDigest
→ ContextBuilder
→ 下一次 Model Turn
```

并证明：原始 Incident Evidence 永不被总结覆盖，Session 时序树可重建，Rollout 和用户偏好图谱可召回，实体可消解、冲突可合并，关键词文本可追溯，并在固定 benchmark 上达到缓存命中率 ≥ 85%、上下文 Token 降低约 30%、诊断准确率 90% 以上的目标。

## 17. 参考资料

外部实现仅作为边界参考：

- [Agno Session Storage](https://docs.agno.com/database/session-storage)：Session 以 `session_id` 组织对话、运行和元数据。
- [Agno Memory Overview](https://docs.agno.com/memory/overview)：长期 Memory 与 Session History 是不同的数据概念。
- [Agno Storage Control](https://docs.agno.com/sessions/persisting-sessions/storage-control)：历史消息、工具消息和媒体应支持独立控制持久化范围。
