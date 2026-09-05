# PRD-003 Memory & Evidence Foundation 设计草案

状态：已确认设计方向，待分阶段实施

## 1. 范围

本期聚焦两条主线：

1. 短时会话树：维护当前 Session 的 turns、task branches、hypotheses、EvidenceRef 与工具结果摘要，并生成受 Token Budget 控制的 SessionDigest。
2. 长时偏好图谱：以 operator/user 为边界，异步抽取实体、偏好、常用错误码，完成实体消解、别名归并、冲突合并和审计。

同时补齐 EventStore、EvidenceStore、StateStore、Redis cache Port、MemoryRecorder、MemoryRecall 和统一 tracing 字段，使后续 PRD-004/005/006 能在稳定接口上扩展。

## 2. 目标与非目标

目标：事实源可靠、短时上下文可裁剪、长期记忆可异步沉淀、缓存可重建、范围不泄漏，并以同一 trace 衡量命中率/Token/准确率。

非目标：Vector DB、Embedding、Reranker、BM25、Graph RAG、代码地图、自动把模型总结写成事实、复杂权限中心和跨租户合规平台。

## 3. 关键不变式

- Event append-only；同一 Event 重放不得产生重复状态副作用。
- Evidence immutable；同一 `evidence_id` 再写入时 hash 相同才幂等，hash 不同必须拒绝且记录冲突。
- State 是 Event projection，不是独立事实源；projection lag 可重放恢复。
- SessionDigest、Redis summary、rollout summary 都是 Context View，不得覆盖 Event/Evidence。
- User Memory 与 Incident Evidence 物理隔离；Recall 必须显式传入 scope。
- LLM 只能产生候选抽取/摘要，不能修改 Incident Evidence；冲突结果必须保留旧值、新值、依据、模型版本和来源。

## 4. 候选方案

### 方案 A：Redis 作为长期记忆主存储，文件仅保存事实源

写入 PreferenceGraph/Rollout 后直接写 Redis，读取简单、延迟低；但 Redis 故障、误删或版本迁移会影响长期记忆恢复，且本项目现有持久化骨架无法自然承载重建。

### 方案 B：本地 append-only durable store + Redis cache-aside（推荐）

Event/Evidence/State 继续作为 Incident 事实层；新增长期 memory durable adapter（首版 JSONL/JSON 文件）保存版本化的 Preference、Alias、Conflict、RolloutSummary、KeywordRecord；Redis 只缓存热结构和 digest。读取先查 Redis，miss 时从 durable adapter 重建并回填；Redis 丢失不影响事实与长期记忆恢复。

优点是符合 PRD 的“Redis 不承载原始真相”和“缓存失败可重建”，Adapter seam 清晰。代价是需要处理 cache stampede、版本失效和补偿队列。

### 方案 C：事件溯源驱动全部 memory projection，Redis 只做二级缓存

将 `memory.extracted`、`entity.resolved`、`preference.merged`、`summary.created` 等事件写入独立 MemoryEventStore，PreferenceGraph、RolloutMemory、KeywordMemory 均由 projector 生成；Redis 只做 cache。可追溯和重建能力最好，但首期实现面较大，会把 PRD-003 扩展成新的事件域。

### 推荐结论

推荐方案 B，保留方案 C 的事件命名和审计字段，为未来升级到完整 memory event projection 留 seam。Redis 明确是 cache-aside，不是长期记忆唯一来源。

## 5. 模块职责与 Port

建议新增/补齐：

- `EventStore`: `append(event)`, `list_by_aggregate(...)`, `list_by_correlation(...)`。
- `EvidenceStore`: `put_once(evidence)`, `get(evidence_id)`, `verify_ref(ref)`。
- `StateStore`: `save_projection`, `load_projection`, `mark_projection_lag`。
- `MemoryCache`: `get(key, expected_version)`, `set(key, value, ttl, version)`, `delete`，实现 Redis adapter 与 in-memory test double。
- `MemoryDurableStore`: 保存版本化长期记忆记录，支持按 operator/scope 重建。
- `SessionTimelineTree`: 增量 append/update、按 parent/时间读取、从 Event 回放重建。
- `RolloutMemory`: 只保存结构化摘要和 EvidenceRef，不复制 Evidence 原文。
- `PreferenceGraph`: entity/preference/error-code candidate 的 upsert、resolve、merge、audit。
- `AsyncMemoryExtractor`: enqueue/poll/claim/retry；LLM 输出严格结构化 candidate。
- `KeywordMemory`: append-only 文本记录，LLM 失败不影响主链路。
- `MemoryRecorder`: 在一次 runtime action 中路由 Event/Evidence/summary，并发起后台 extraction。
- `MemoryRecall`: 接受 `MemoryScope` 和 `TokenBudget`，分别召回 session/incident/rollout/operator/keyword。
- `SessionDigest`: 以 `tokens_without_memory`、`tokens_with_memory`、保留 EvidenceRef 和版本生成 Context View。

Runtime 边界保持：`ModelPort → Context/Message → Runtime Loop → MemoryRecall/Tool Registry → Executor`。ContextBuilder 只接收已裁剪的 `MemoryContextView`，不直接读取 Evidence 原文。

## 6. 分层数据流

写入：

```text
Runtime action
  ├─ EventStore.append (事实)
  ├─ EvidenceStore.put_once (不可变证据)
  ├─ StateProjector.apply → StateStore
  ├─ SessionTimelineTree.append (短时工作集)
  ├─ completion → RolloutMemory.append (长期经验)
  ├─ background queue → Extractor → resolve/merge → durable preference store
  └─ background queue → KeywordExtractor → keywords.txt append
```

读取：

```text
Context build
  ├─ session timeline: Redis hot window → Event replay fallback
  ├─ digest: Redis → durable summary → rebuild from timeline/events
  ├─ operator graph: Redis → durable graph → scoped candidate filter
  ├─ rollout/keywords: explicit scope only
  └─ token budget裁剪 → MemoryContextView → ModelRequest
```

## 7. Redis key 与一致性策略

首版保留 PRD key namespace，并补充版本/元数据 envelope：

```text
session:{session_id}:timeline
session:{session_id}:digest
rollout:{rollout_id}
operator:{operator_id}:preference_graph
operator:{operator_id}:habits
operator:{operator_id}:common_error_codes
operator:{operator_id}:entity_aliases
summary:{scope}:{scope_id}
```

每个 value 至少包含 `schema_version`、`source_ids`、`updated_at`、`model_version`（如适用）、`confidence`（如适用）和 `content_version`。采用 cache-aside：durable write 成功后删除或更新相关 Redis key；读 miss 从 durable source 重建并设置 TTL。使用版本校验避免旧异步任务覆盖新版本；同一 key 的并发重建需 single-flight/短租约，避免 stampede。

Redis 不存：Incident 原始 Evidence 内容、Event 真相、完整工具原文、API Key、Trace 中的敏感字段。

## 8. 异步 LLM 抽取、实体消解与冲突合并

主链路只写事实并 enqueue `memory.extract.requested`，后台 worker 执行：

1. 读取受 scope 限制的 Session/Incident 摘要与允许的 EvidenceRef 元数据。
2. LLM 严格输出 `EntityCandidate`、`PreferenceCandidate`、`KeywordCandidate`，每项带 source IDs、confidence、model_version。
3. Deterministic normalizer 先做大小写、空白、错误码格式和已知 alias 归一化。
4. Resolver 以 operator/user scope 查询候选 canonical entity；高置信 exact alias 可自动归并，模糊匹配只保留 candidate。
5. Merger 按明确偏好 > 最近来源 > 高置信度 > stable tie-breaker 决策；冲突不覆盖旧值，而是追加 `ConflictRecord` 与 `MergeAudit`。
6. durable write 成功后失效相关 Redis key；失败则保留 retryable job 和失败 Event。

抽取超时/解析失败/Redis 不可用都不能阻塞诊断；但 EventStore/EvidenceStore 主事实写失败时，runtime 不能报告持久化成功。

### 8.1 Loop 与 Memory Pipeline 的边界

Runtime Loop 每次推进都可以产生运行事实，但不等待长期记忆完成。节点关系为：

```text
Incident → Session → Turn → Task → ToolCall/FunctionCalling → Attempt → EvidenceRef
```

`turn.started`、`task.created`、`tool_call.requested`、`attempt.completed` 等事件用于恢复、投影和 tracing；只有用户输入、有效 Tool 结果、Agent 结论和 Session 完成等有信息密度的边界进入 Candidate 提取。

Loop 的同步返回只包含当前诊断所需的 `RuntimeResult`：当前状态、Task/Tool 结果摘要、EvidenceRef、未决假设和最终结论。完整 Event 由 EventStore 保存；MemoryCandidate 通过异步任务队列投递，不作为下一次 Model 调用的隐式副作用。

### 8.2 Memory 分类小模型

后续引入独立的小模型作为 `MemoryClassifier`，只判断 Candidate 是否值得长期保存、分类和置信度，不修改 Event/Evidence，也不直接执行跨实体覆盖。接口输出固定结构：

```json
{
  "should_remember": true,
  "memory_type": "semantic|episodic|preference",
  "confidence": 0.93,
  "ttl_policy": "long|session|discard",
  "reason_code": "stable_operator_preference"
}
```

`CandidateExtractor`、`MemoryClassifier`、`Normalizer`、`Resolver/Merger`、`MemoryStore` 分离，允许小模型独立替换、离线评估和灰度。低置信度结果进入候选或冲突审计，不直接进入 active memory。

## 9. Tracing 与验收指标

所有 `incident.run → context.build → memory.lookup → model.complete → tool.execute → memory.write` 使用同一 `trace_id`，每个动作生成独立 `request_id`，并传播 `parent_request_id`、`causation_id`；`span_id` 只由 tracing SDK 管理。

每次 lookup 记录：scope、memory_type、cache_layer、cache_hit、fallback_used、latency、source_count、输入/输出 Token 估算。每次 context build 记录 budget、without/with memory tokens、tokens_saved、evidence_ref_count、summary_source_ids。禁止记录 Evidence 原文、完整用户偏好、敏感工具输出和密钥。

验收口径：

- 缓存命中率：固定时间窗内按 memory type/cache layer 统计；目标整体相关场景 ≥85%，同时报告各层最低值。
- Token 节省：同一 replay 数据集以 baseline（不使用 digest/偏好热缓存）与新链路对比，`(baseline - actual) / baseline` 目标约 30%。
- 记忆准确率：建立人工标注的 entity/preference/merge gold set，分别测 extraction precision、resolution accuracy、conflict decision accuracy；目标关键任务综合准确率 ≥90%，不能用 LLM 自评替代。
- 防泄漏：跨 operator、跨 incident、未授权 scope 的 recall 必须返回空或拒绝。

### 9.1 前端观测看板

在现有 Runtime UI 旁增加 `/dashboard` 观测页。看板只消费后端 metrics/query API 和 trace index，不直接把 Redis key 当作业务事实。页面分为四个区域：

1. 缓存分区：按 `memory_type`、`cache_layer`、operator、时间窗口显示命中率、miss、fallback、P50/P95 延迟和 stampede 次数。
2. 上下文分区：显示 `tokens_without_memory`、`tokens_with_memory`、节省率、digest 命中率、EvidenceRef 数量和 summary 版本。
3. 记忆质量分区：显示 Candidate 数、分类接受率、实体消解准确率、合并冲突数、低置信度数和人工复核结果。
4. 运行审计分区：按 `trace_id` 展示 `incident.run → context.build → memory.lookup → model.complete → tool.execute → memory.persist`，并显示 projection lag、队列积压、retry/dead-letter。

首版看板支持时间窗口、memory type、cache layer、operator、session/incident scope 筛选；trace 详情只显示脱敏摘要、ID、版本和统计字段，不展示 Evidence 原文、完整偏好、工具敏感输出或密钥。指标接口应返回“数据不完整/采样率/更新时间”标记，避免把 metrics 缺失渲染为零。

## 10. 错误语义、恢复与安全边界

- Event append 失败：返回可识别持久化错误，runtime 结果不可标记为 durable success。
- Evidence hash 冲突：拒绝覆盖，写冲突审计。
- Projection 失败：Event 保留，标记 lag，可全量 replay。
- Timeline/digest 损坏：从 Event/EvidenceRef 重建。
- Redis 故障：读取返回 fallback/partial 标记；写入进入补偿队列。
- 异步 LLM 失败：任务保留、指数退避、超过上限进入 dead-letter；主诊断不回滚。
- 所有长期记忆记录包含 operator/user scope；ContextView 只披露明确允许的偏好。

## 11. 测试策略

- Domain：Event 幂等、Evidence hash 冲突、scope 隔离、entity merge 决策。
- Adapter：JSONL append/replay、Redis cache hit/miss/TTL/version、故障 fallback。
- Memory：timeline 增量与 replay 等价、digest token budget、recall 不跨 scope 泄漏。
- Async：enqueue/claim/retry/dead-letter、LLM malformed/timeout 不阻塞 runtime、重复任务幂等。
- Runtime integration：Recorder 路由完整 action chain，trace 字段传播且敏感信息不出现在 attributes。
- Evaluation：固定 replay fixture + gold set，输出 hit rate、tokens saved、precision/accuracy 报表。

## 12. 分阶段大标题（后续实施粒度）

1. 事实存储与投影：EventStore、EvidenceStore、StateStore、基础 tracing correlation。
2. 短时会话树与 digest：timeline、replay、budget-aware ContextView。
3. 长时 rollout 与 operator graph：durable schema、Redis cache-aside、scope recall。
4. 异步 LLM pipeline：抽取、实体消解、冲突合并、补偿与审计。
5. 指标与验证闭环：cache/token/accuracy fixtures、trace exporter、回归验收。

每个阶段实现前先写失败测试；阶段完成后运行阶段测试与全量回归，暂停等待 review。

## 13. 设计结论

采用方案 B：本地 append-only durable memory store 作为 Preference/Rollout 的可重建来源，Redis 仅做 cache-aside 热层；Event/Evidence 仍是 Incident 事实源。Session Tree 表达单次诊断的过程分支，Preference Graph 表达跨 Session 的稳定实体关系；Runtime Loop 同步返回当前诊断所需结果，Memory Pipeline 通过后台队列异步完成分类、归一化、消解和合并。前端看板作为 tracing/metrics 的只读观测面，不改变事实存储边界。
