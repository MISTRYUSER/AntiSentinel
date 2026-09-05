# AntiSentinel Memory 系统开发与优化记录

## 记录规则

每次 Memory 变更必须记录：

1. 变更阶段和目标。
2. 具体输入与数据集版本/SHA256。
3. 实现路径和涉及模块。
4. 针对性测试、数据集 Smoke、真实 Case、全量回归。
5. Recall/Precision/MRR、Token、Cache、准确率等实际结果。
6. 失败 Case、根因、修复和修复后的复跑结果。
7. 持久化产物、Redis key、Trace、后台异常和恢复结果。
8. 结论必须区分：`设计完成`、`Case 已确认`、`真实运行通过`、`回归通过`、`用户 review 通过`。

没有真实数据证据时使用 `INSUFFICIENT_DATA`，不把目标值写成已达成。

## 总体开发路径

```text
PRD-003
  ↓
短时 Session Tree + 长时 Memory/Preference Graph
  ↓
JSON/JSONL skeleton + Redis cache/queue
  ↓
SQLite durable persistence + JSONL audit
  ↓
Memory Evaluation Harness
  ↓
LongMemEval-S 数据集接入
  ↓
MemoryRecord + Session Tree + Rank/Filter + EvidenceRef
  ↓
Runtime Context 注入
  ↓
后续：RAG 文档检索与融合
```

## 阶段 0：基础 Memory Pipeline

### 目标

建立候选提取、Memory 分类、Rollout Memory、Preference Graph、异步 Worker、Redis ZSET + Hash、Event/Evidence/State 和 tracing 基础。

### 当前能力

- Session Tree：从 Event 构建短时工作树。
- Rollout Memory：保存诊断摘要、结论和 Evidence 引用。
- Preference Graph：operator scope、同 subject/predicate 合并、supersede、conflict。
- Worker：候选异步分类与落盘。
- Redis：Cache-aside、ZSET 排序索引、Hash 内容、Queue lease/retry。
- Tracing：Session/Turn/Tool/Memory/Model/Token 关联。

### 验证

- 历史回归曾通过 `165 passed`。
- 当前累计回归已提升到 `206 passed`，包含后续 SQLite、Evaluation 和 Runtime Memory 注入测试。

## 阶段 1：SQLite Persistence Adapter

### 路径

```text
JSON/JSONL legacy
  → SQLite schema v1
  → stable ID / FK / transaction
  → import_ledger 幂等导入
  → SQLite 查询事实源
  → JSONL audit 副本
```

### 真实结果

数据来自当前真实 `storage/` 副本，Case 目录曾使用：

`/tmp/antisentinel-stage1-case.XCVjZ1/`

- 第一次导入：`scanned=1133, inserted=1133, failed=0`
- 第二次导入：`inserted=0, skipped=1133, failed=0`
- Event：952
- Evidence：12
- State：19
- Memory：21
- Span：129
- `foreign_key_check`：0
- `quick_check`：`ok`

### 验收

- 针对性回归：`11 passed`
- 全量回归：`176 passed`
- 真实迁移 Case：通过
- 阶段状态：真实运行通过、回归通过、用户 review 通过

## 阶段 2：Runtime / Memory / Redis / Tracing 接入

### 路径

```text
Runtime Result
  → SQLite primary persistence
  → JSONL audit
  → Redis namespace cache/index
  → Redis Job Queue
  → Memory Worker
  → SQLite Memory/Trace
```

### 关键修复

1. 旧回归的 Redis fake 没有 `.client` / `zcard`，补齐合法 Cache interface。
2. Redis cache/index 增加 namespace，避免不同 Case 污染。
3. Queue 使用 pending/processing ZSET + job HASH，支持 lease 过期恢复。
4. Cache outcome 分成 `hit`、`miss`、`bypass`。
5. Worker stop 增加 drain 结果和后台异常判断。

### 真实结果

- Runtime：`completed`
- ToolCall / Attempt：`2 / 2`
- Worker drain：成功
- Worker errors：0
- Audit degraded：0
- 重启恢复：成功
- SQLite：Incident 1、Session 1、Message 4、Event 39、Evidence 1、State 1、Memory 3、Trace 2、Span 7
- `foreign_key_check`：0
- `quick_check`：`ok`
- Redis：专用 prefix 下 ZSET 2、HASH 2、versioned STRING 2
- Queue ack 后 pending/processing/job key 清空

### 验收

- 针对性回归：`11 passed`
- 全量回归：`185 passed`
- 累计真实 Case：通过
- 阶段状态：真实运行通过、回归通过、用户 review 通过

## 阶段 3：Memory Evaluation Harness

### 路径

```text
同一 Case
  ├─ baseline：完整历史、cache bypass
  └─ optimized：Memory Recall、scoped context、Redis cache
       ↓
SQLite Evaluation Run / Case / Result
       ↓
cache / token / fact accuracy
```

### 真实 DeepSeek 结果

阶段 2 的真实 Session 上使用 3 个人工标注 Case：

- Pair 成功：3
- Provider error：0
- Cache hit：66.67%（2/3）
- Token 降幅：26.69%
- Fact accuracy：100%（3/3）
- Gate：三项均为 `INSUFFICIENT_DATA`

原因：样本只有 3 Case / 1 Session / 1 Incident，未达到 30 Case / 5 Session / 3 Incident。

### 验收

- 针对性回归：`12 passed`
- 全量回归：`192 passed`
- 真实 paired replay：通过
- 阶段状态：真实运行通过、回归通过、用户 review 通过

## 阶段 4：LongMemEval-S 数据集

### 数据

- 文件：[longmemeval_s_cleaned.json](../storage/benchmarks/longmemeval-s/longmemeval_s_cleaned.json)
- Case：500
- 文件大小：277,383,467 bytes
- SHA256：`d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442`
- Abstention：30
- 总 Haystack Session：23,867
- 总 Turn：246,750

### 全量完整性验收

- JSON 解析：通过
- question_id 重复：0
- Session/Turn/Evidence 引用异常：0
- 数据类型异常：0

### 关键词检索基线

470 个非 abstention Case：

- Recall@1：40.96%
- Recall@5：78.58%
- Recall@10：88.26%
- Precision@1：67.23%
- Precision@5：28.85%
- Precision@10：16.60%
- MRR@10：76.13%

### MemoryRecord + Rank/Filter 全量 @1/@3/@5

在不使用 `has_answer` 生成候选的严格版本上，对 LongMemEval-S 全部 500 Case 中的 470 个非 abstention Case 测试：

| 指标 | 结果 |
|---|---:|
| Recall@1 | 45.95% |
| Recall@3 | 70.58% |
| Recall@5 | 77.78% |
| Precision@1 | 71.49% |
| Precision@3 | 53.26% |
| Precision@5 | 36.09% |
| MRR@5 | 78.21% |

测试输入：全部 Turn 平等投影为 `turn_summary`，未使用答案标签参与候选生成；30 个 abstention 按官方规则不进入检索指标。该结果是当前 MemoryRecord + Rank/Filter 的全量基线，后续 RAG 必须在相同 470 Case、相同指标口径上比较。

### Adapter 验收

- `LongMemEvalLoader`：Session/Turn 稳定 ID 映射。
- `KeywordMemoryRetriever`：确定性 lexical baseline。
- `evaluate_retrieval()`：全量聚合。
- 30 Case Smoke：`4 passed`，无异常。
- 全量 CLI：500 Case / 470 evaluated / 30 skipped / 0 error。
- 全量回归当时为：`196 passed`。

## 阶段 5：Memory Record / Rank / Filter / EvidenceRef

### 优化路径

```text
LongMemEval Session
  → session_digest MemoryRecord
  → has_answer Turn → evidence_summary MemoryRecord
  → owner/Incident/status/time 硬过滤
  → lexical + scope + recency + confidence + evidence support + type Rank
  → token budget ContextView
  → EvidenceRef Resolver
```

### 30 Case 真实结果（严格版）

- 6 类 question type 各 5 条
- 平均候选 Memory：547.27；所有 Turn 平等投影，不使用 `has_answer` 生成候选
- 最终 Rank 输出：10 条
- source_refs 悬挂：0
- 严格版优化 Recall@10：83.89%
- 严格版优化 Precision@10：24.77%
- 关键词基线 Recall@10：90.56%
- 关键词基线 Precision@10：15.33%

此前 92.22% / 17.83% 结果使用 `has_answer` 标注生成候选，存在评测泄漏，已废弃。严格版相对关键词基线 Recall 下降 6.67 个百分点，Precision 提升 9.44 个百分点；后续只采用严格版结果。

### 真实失败与修复

1. 长摘要全部超过预算，导致 ContextView 为空。修复为预算内截取最高分候选。
2. PreferenceRecord 没有中文可检索 content。修复为保留 graph 字段并生成搜索文本。
3. 线上使用 `operator_id`，Filter 只识别 `owner_id`。修复 owner 兼容。
4. 线上使用 `source_ids`，Recall 只识别 `source_refs`。修复为转换 typed EvidenceRef。

### 验收

- 检索相关测试：`22 passed`
- 全量回归当时为：`202 passed`
- 真实 30 Case：通过

## 阶段 6：Runtime Memory Context 注入

### 目标

让上述优化不只在 LongMemEval 评测层生效，而是在每个 Runtime Turn 的模型调用前真实执行。

### 路径

```text
RuntimeLoop Turn
  → MemoryRecorder.recall_context
  → SQLite Memory Record
  → Filter / Rank / Budget
  → digest + Memory IDs + EvidenceRef
  → ContextBuilder
  → Model
```

### 真实失败与修复

1. Case 只创建 Evidence 没有预置 Memory，运行时没有记忆可召回；修正 Case 输入。
2. Preference Record 的 `operator_id/ source_ids` 与检索层字段不一致；修复兼容。

### 最终真实结果

- Runtime：`completed`
- Memory message：1
- Memory digest：包含“排查时先看日志，再看指标”
- EvidenceRef：`evidence-runtime-1`
- SQLite Evidence：1
- SQLite Memory：3
- Trace：2
- Span：5
- 重启恢复：成功
- SQLite foreign key：0
- SQLite quick check：`ok`
- Redis Case key：6，清理后 0
- 后台异常：0

### 验收

- 针对性回归：`11 passed`
- 全量回归：`206 passed`
- 累计真实 Runtime Case：最终通过
- 当前状态：Runtime Memory 注入真实运行通过，等待用户 review

## 当前优化结论

已经证明：

- SQLite 持久化、Redis 热缓存、异步 Worker 和 tracing 可以联通。
- LongMemEval-S 可以全量解析并接入 Retrieval-only。
- Memory Record、Rank/Filter、EvidenceRef 可以进入真实 Runtime Context。
- 当前 lexical + structured rerank 能提高小样本 Recall/Precision，但 Precision 仍低。

## 阶段 7：SQLite FTS5 / BM25 实验

### 做这一步的原因

LongMemEval-S 的 Memory Record 规模较大，单条 SQLite transaction 写入和 Python 全量扫描不适合继续扩展，因此引入 SQLite 内置 FTS5 作为本地全文候选索引，并使用 BM25 对候选进行字面相关性排序。

### 实现路径

```text
Memory Record
  → memory_records 主表
  → memory_records_fts FTS5 索引
  → BM25 候选排序
  → operator / incident / status 硬过滤
  → SQLite 主表 JSON 回源
```

中文查询增加安全短语 fallback；英文查询使用 token OR fallback。`append_many()` 使用一次主表批量写入和一次 FTS 批量写入，避免每条记录独立提交事务。

### 针对性测试结果

- FTS5 / SQLite 测试：`3 passed`
- SQLite Database / Store / FTS 组合测试：`11 passed`
- FTS 索引支持写入、替换重建、BM25 查询、status 过滤、operator/Incident scope 过滤。

### 真实 LongMemEval 30 Case 结果

- Case：30
- 投影 Memory Record：16,418
- SQLite 批量写入：2,853 ms
- 实际落库：16,418
- 查询异常：0
- scope 污染：0
- 非空查询：0/30
- 空查询：30/30

### 结论

```text
FTS5 索引写入：通过
BM25 排序接口：通过
批量性能：通过
scope 隔离：通过
LongMemEval 真实召回：失败
```

原因是 LongMemEval 问题与历史事实存在语义改写，字面词检索无法完成跨表述召回。FTS5/BM25 不作为最终 Memory Retrieval，也不能用于宣称 Recall 或 Accuracy 提升。

保留定位：

```text
FTS5/BM25 = RAG/Memory 的关键词候选层
Semantic Retrieval = 后续语义候选层
融合 Rank = 后续最终排序层
```

### 回归状态

- FTS5 引入后的稳定全量回归：待在真实 Redis 权限下重新确认。
- 已知稳定基线：`206 passed`。
- 一次 sandbox Redis 限制下：`202 passed / 7 failed`，7 个失败由本机 Redis `Operation not permitted` 引起，未作为 FTS5 代码结论。

## 阶段 8：Canonical Event Replay / Projection

### 原因

按 Codex 的分层原则，原始 Event 必须能独立回放；Memory、FTS5、未来向量索引都应从 Canonical Facts 重建，不能依赖进程内存或缓存。

### 实现

- 新增 `EventReplayProjector`。
- 按 `occurred_at + event_id` 确定性排序。
- 相同 Event ID 且内容相同：幂等去重。
- 相同 Event ID 但内容不同：直接报告 `event conflict`。
- 从生命周期事件投影 `status/event_count/event_types/last_event`。

### 测试

- 回放器针对性测试：`2 passed`。
- 全量回归：`211 passed`。

### 真实 SQLite Event 回放

- 真实数据库：`/tmp/antisentinel-stage1-case.XCVjZ1/antisentinel.db`
- 加载 Event：952
- SQLite Event 总数：952
- 投影 Event：952
- 重复 ID：0
- 回放错误：0
- Projection status：`completed`
- Event type 聚合：27 类
- 最后事件时间：`2026-09-04T03:21:44.396032+00:00`

### 结论

```text
Canonical Event 读取：通过
确定性回放：通过
幂等去重：通过
冲突检测：通过
真实 SQLite 对账：通过
```

这一步只完成 Event Projection，不代表 Memory/vector index 已自动重建；下一步要把 Projection 输出接到 MemoryRecord projector，并记录每条派生 Memory 的 source event/version。

## 阶段 9：Memory Record Canonical Provenance

### 原因

按照 Codex 的“原始事实与派生索引分层”原则，Rollout/Preference Memory 必须能回答：它由哪些 Event 派生、当前内容属于哪个版本、能否回到 Evidence。

### 实现

- Rollout Memory 新增 `source_event_ids`。
- 新增 typed `source_refs`：Event 与 Evidence 分开标识。
- 保留原有 `source_ids` 兼容字段。
- 对缺少 `events` 的旧测试对象保持兼容，默认空 Event provenance。

### 测试

- Provenance 针对性测试：`17 passed`。
- 全量回归：`212 passed`。

### 真实 Runtime Case

- Case 目录：`/tmp/antisentinel-memory-provenance.igemTs/`
- Runtime：`completed`
- Runtime Event：39
- Rollout Memory：1
- `source_event_ids`：39
- source_ref 类型：Event、Evidence
- SQLite Event：39
- SQLite Memory：1
- SQLite Trace：1
- SQLite Span：6
- Worker errors：0

### 结论

```text
Memory → Event provenance：通过
Memory → Evidence provenance：通过
稳定 ID 数量对账：通过
真实 Runtime 生成：通过
```

后续派生索引（FTS5/向量）只能携带 Memory ID 与 version，回源 SQLite 获取这组 provenance；不能自行复制或修改 Canonical Event。

## 阶段 10：派生 Memory Rebuild / Repair

### 原因

仅有 `source_event_ids` 还不够；当 Rollout Memory 或 FTS/向量派生索引损坏、丢失时，系统必须能从 SQLite Session Result 与 Canonical Event 重建，而不是依赖 Redis 或当前进程。

### 实现

- 新增 `MemoryProjectionRebuilder`。
- 只读取 completed Session 的 `result_json`。
- 从 Event 表按 correlation/session 顺序取得 `source_event_ids`。
- 将 Result 中的 Evidence 引用规范化为 typed `source_refs`。
- 生成确定性 `rollout:{session_id}` Memory ID。
- 已存在的相同派生记录计 skipped，不重复写入。
- 不删除或修改 Canonical Event/Evidence。

### 测试

- Rebuild 针对性测试：`2 passed`。
- 全量回归：`214 passed`。

### 真实重建 Case

- 真实数据库：`/tmp/antisentinel-stage2-case.qdwAoS/antisentinel.db`
- 删除派生 Rollout Memory 后剩余 Memory：2
- 第一次重建：`sessions_seen=1, records_written=1, skipped=0, errors=0`
- 重建后 Rollout Memory：1
- 第二次重建：`records_written=0, skipped=1, errors=0`
- 恢复后 Memory 总数：3
- 外键错误：0
- `quick_check`：`ok`

### 结论

```text
派生 Memory 删除后重建：通过
source_event_ids 恢复：通过
Evidence source_refs 恢复：通过
重复重建幂等：通过
Canonical Event/Evidence 未被修改：通过
```

下一步可以将同一 Rebuilder 接到 FTS5 派生索引 rebuild，而不是只重建 Rollout Memory 主记录。

## 阶段 11：本地长期记忆 Facade

### 目标

确认长期 Memory 不依赖 LibreChat、云端 Memory 服务、进程内字典或 Redis 常驻缓存；SQLite 是本地长期事实，Redis 只做加速。

### 实现

- 新增 `LocalLongTermMemory`。
- 统一 `remember/get/search` 接口。
- 底层使用 SQLiteMemoryStore。
- FTS5/BM25 作为可选关键词检索索引。
- 重新创建 Facade 后直接从 SQLite 恢复。

### 测试

- 本地长期记忆针对性测试：`5 passed`。
- 全量回归：`216 passed`。

### LongMemEval-S 真实 Case

- Case：30
- 投影 Memory：16,418
- SQLite Memory：16,418
- FTS5 索引：16,418
- 批量写入耗时：2,544 ms
- Facade 重建：成功
- `quick_check`：`ok`
- scope 污染：0
- 非空关键词查询：0/30

### 结论

```text
长期本地存储：通过
Facade 重启恢复：通过
SQLite/FTS 数量对账：通过
关键词语义召回：不足
```

长期记忆本体已经可以完全本地化；当前缺口是跨表述语义召回，后续由 RAG/向量索引解决，不改变 SQLite 事实源设计。

## 阶段 12：异步 Memory LLM 去重/分类

### 原因

规则分类只能识别固定模式，无法稳定完成语义分类、Normalize、实体消解和冲突判断。主诊断不能被这类后台操作阻塞，因此引入独立 Memory LLM adapter，通过 Redis Queue 异步执行。

### 当前选型

```text
主 LLM：DeepSeek
Memory LLM：DeepSeek deepseek-chat（暂用同一模型）
执行位置：Redis Queue → Memory Worker
持久化：SQLite Memory Record + JSONL audit
```

后续只需替换 `ANTISENTINEL_MEMORY_MODEL_NAME`，不改变主诊断链路。

### 实现

- 新增 `MemoryLLMClassifier`。
- 使用 OpenAI-compatible JSON response format。
- 输出严格限制 `should_remember/memory_type/confidence/reason_code`。
- JSON 解析或 Provider 失败统一返回 `discard + memory_model_invalid_output`，不伪造入库。
- `MemoryRecorder` 支持注入 classifier。
- real 模式默认使用 `ANTISENTINEL_MEMORY_MODEL_*`，未配置时回落主 DeepSeek 配置。
- Redis Job Hash 在 ack 后保留 `status=completed` 1 小时，便于审计；pending/processing 归零。

### 测试

- Memory LLM Adapter 与 Worker 针对性测试：`3 passed`。
- Redis/Worker/Bootstrap 针对性测试：`10 passed`。
- 全量回归：`219 passed`。

### 真实 DeepSeek + Redis Case

- Runtime status：`completed`
- Memory classifier：`MemoryLLMClassifier`
- Memory model：`deepseek-chat`
- Redis Job Hash：1
- Job status：`completed`
- pending：0
- processing：0
- SQLite Event：31
- SQLite Evidence：1
- SQLite Memory：2
- SQLite Trace：2
- SQLite Span：7
- Worker drained：`true`
- Worker errors：0
- Redis Case keys：7，清理后删除本 Case prefix

### 结论

```text
主诊断不等待 Memory LLM：通过
真实 DeepSeek 分类调用：通过
异步 Job 入队/完成：通过
Memory 入库：通过
Job completed 可审计：通过
后台异常：0
```

当前还没有声称小模型成本或质量更优；后续替换模型时必须按同一测试集比较分类准确率、冲突错误率、延迟和 Token。

### LongMemEval-S 30 Case 分类 Smoke

首次真实运行：

- Case：30
- DeepSeek 分类：30 次
- 结构化成功：29
- `memory_model_invalid_output`：1
- 进入 Memory：11
- Discard：19
- 运行结论：失败，因 1 个 invalid output。

修复：增加一次严格 JSON repair retry，仍使用相同字段约束，不接受任意文本或缺字段对象。

修复后同一 Case 重跑：

- Case：30
- 分类完成：30
- invalid output：0
- 进入 Memory：9
- Discard：21
- 类型分布：episodic 7、preference 2、semantic 0、discard 21
- SQLite Memory：9
- SQLite `quick_check`：`ok`
- 总耗时：20,311 ms
- 平均每 Case：约 677.03 ms

严格结论：

```text
DeepSeek Memory LLM JSON 输出：通过
repair retry：通过
30 Case 分类完整性：通过
SQLite 入库：通过
异步分类质量 Gate：未判定
去重/冲突准确率：未判定
```

本 Smoke 每个问题只抽取一个候选，尚未覆盖完整历史的候选召回、重复实体合并和冲突更新，因此不能把 `9/30` 作为记忆准确率。后续需要以人工/规则 Ground Truth 的 Preference、knowledge-update 子集做专项评测。

### LongMemEval-S 500 Case Memory LLM 全量分类

2026-09-04 使用当前 DeepSeek `deepseek-chat` 完成全量 Case 分类：

- Dataset Case：500
- Classified Case：500
- DeepSeek 调用：500
- 总耗时：42,418 ms
- 平均耗时：84.84 ms/Case（8 路并发）
- invalid output：0
- 进入 Memory：220
- Discard：280
- 类型：episodic 147、preference 56、semantic 17、discard 280
- 平均 confidence：0.8786
- 完整历史 Turn 扫描：246,750
- Evidence-bearing Turn：896
- Abstention：30
- question type：single-session-user 70、multi-session 133、single-session-preference 30、temporal-reasoning 133、knowledge-update 78、single-session-assistant 56

严格边界：本次 500 Case LLM 运行每个 Case 只抽取 1 个首个 user Turn，验证的是“500 Case 的 Memory LLM 输出稳定性”，不是完整 246,750 Turn 的候选提取准确率。完整历史候选准确率、去重准确率、Entity Resolution 准确率、Conflict Merge 准确率和 90% end-to-end Memory accuracy 均保持 `INSUFFICIENT_DATA`，不从 `220/500` 推导准确率。

### 真实结论

```text
500 Case Memory LLM 分类完整性：通过
结构化输出稳定性：500/500
完整历史候选提取准确率：INSUFFICIENT_DATA
去重准确率：INSUFFICIENT_DATA
Entity Resolution 准确率：INSUFFICIENT_DATA
Conflict Merge 准确率：INSUFFICIENT_DATA
90% Memory accuracy：INSUFFICIENT_DATA
```

要严格测完用户列出的五项，下一步必须把 246,750 个 Turn 经过 Candidate Extractor，使用可审计正负标签构造完整候选集，再对重复/实体/冲突建立独立 Ground Truth；这会产生远大于 500 次的 Memory LLM 调用，不能用本次 500 次 Case 分类替代。

## 阶段 13：LongMemEval 完整历史 Candidate Inventory

### 原因

在测候选提取、去重、实体消解和冲突合并之前，必须先建立全量稳定候选清单和真实分母，不能只抽每个 Case 的首个 Turn。

### 实现

- 新增 `LongMemEvalHistoryScanner`。
- 对全部 246,750 Turn 生成稳定 `candidate_id`。
- 保存 question/session/turn/role/text 和 `has_answer` 代理标签。
- 统计规范化文本的重复组，为去重评测提供输入。

### 真实全量扫描

- Dataset Case：500
- Turn：246,750
- Evidence-bearing 代理正例：896
- 唯一规范化内容：189,476
- 重复内容组：44,973
- 重复 Turn：102,235
- 扫描耗时：15,326 ms
- 模型调用：0
- 扫描异常：0

### 结论

```text
全量 Candidate ID 建立：通过
完整历史扫描：通过
重复内容统计：通过
候选提取准确率：尚不能由 has_answer 单独判定
```

102,235 个重复 Turn 说明 LongMemEval filler history 中存在大量可压缩内容；但 `has_answer` 只表示该 Turn 是否承载某个问题答案，不等于“长期记忆正例”。下一步必须基于这个清单构造人工/规则标注子集，再测真正的候选提取 Precision/Recall、去重和实体/冲突指标。

尚未证明：

- 85% Cache hit rate
- 30% Token reduction
- 90%+ end-to-end memory accuracy
- RAG 融合后的收益

## 架构决策：学习 Codex，但不复制领域模型

### 决策

采用：

```text
Canonical Facts
  → 可重建 SQLite Projection / Index
  → 后台派生 Memory
  → Rank / Filter / EvidenceRef
  → Scoped ContextView
```

Codex 的存储与回放原则作为基础设施参考；AntiSentinel 保留自己的 `Incident → Session → Turn → Task → ToolCall → Attempt → Evidence` 领域模型。

### 吸收 Codex 的部分

- 原始 Event/事实与派生 Memory 分离。
- JSONL/事件流可回放，SQLite projection 可重建。
- FTS5、ZSET、未来向量索引都只是派生索引，不能成为唯一事实源。
- 后台写入需要 drain、幂等、repair 和一致性校验。
- 查询路径走稳定 ID/索引，不扫描所有原始 rollout。

### 不照搬 Codex 的部分

- Codex 的 Project/Thread/Turn 不替代 AntiSentinel Incident/Session/Task。
- Codex 的本地状态文件布局不是 AntiSentinel 的公开接口。
- AntiSentinel 的 Evidence/EvidenceRef、Preference conflict 和诊断准确率仍由自己的领域规则负责。

### 原因

Codex 解决 Agent 工作历史的连续性；AntiSentinel 还必须证明诊断结论与不可变 Evidence 的关系。因此“记录机制”可以学习 Codex，“Memory 语义”不能直接复制。

## 后续路线

```text
当前阶段 review
  → FTS5/BM25 或更强 lexical index
  → Evidence/Turn 粒度优化
  → 30 Case / 100 Case / 500 Case 分层回归
  → RAG 文档检索
  → Memory + RAG 融合排序
  → Token / Cache / Accuracy 重新成对评测
```

RAG 引入后仍遵循每一步测试集验收，不允许只在最终阶段测试。

## 阶段 14：本地向量索引与 BGE-M3 Adapter

### 原因与基线

当前 lexical Memory 基线为 Recall@5 77.78%、Precision@5 36.09%、MRR@5 78.21%；FTS5/BM25 在真实 30 Case 中非空召回为 0/30。目标是增加本地语义向量候选层，不把 Memory/Evidence 发送到云端。

### 实现

- SQLite 新增 `memory_vectors` 派生表。
- 新增 `LocalVectorMemory`：主 Memory Record 回源、向量版本/维度校验、scope 过滤、cosine 排序。
- 新增 `BGE_M3Embedder`：懒加载 `BAAI/bge-m3`，1024 维，批量 encode，归一化。
- 当前未引入 sqlite-vec 或额外向量服务；先用 Python cosine scan 验证正确性。

### 测试

- 本地向量/SQLite 针对性测试：`5 passed`。
- 随后全量回归：`224 passed`。

### 真实 BGE-M3 尝试

- 本机原先缺少推理依赖：`sentence-transformers/transformers/torch/numpy` 均不可用。
- 安装 `sentence-transformers` 及依赖成功，torch 包 127.3 MB。
- 模型真实加载预算：120 秒。
- 120 秒内未完成首次 Hugging Face 权重下载/加载。
- 进程以 `SIGINT` 停止，未产生向量结果。
- 真实 BGE-M3 指标：`N/A`。
- 评测集调用：0 个。

### 结论

```text
SQLite 向量派生表：代码级通过
LocalVectorMemory scope/维度：测试通过
BGE-M3 真实加载：未通过时间预算
BGE-M3 Recall/Precision/MRR：N/A
```

当前不能宣称本地 Embedding 已接入或效果提升；后续恢复模型权重下载后，必须重新执行同一 470 Case 对比。

## 2026-09-05 Memory 演进评估：针对性测试基线

本次为设计调研，无运行代码修改，无真实服务 Case。

- 命令：`python3 -m pytest -q tests/test_memory*.py tests/test_local_memory.py tests/test_local_vector_memory.py tests/test_sqlite_memory_search.py tests/test_operator_graph.py --junitxml=/tmp/antisentinel-memory-review-20260905/tests.xml`
- 本次：44 tests / 44 passed / 0 failed，0.64 秒，重试 0 次；成功阈值 44/44、失败阈值 0。
- 历史记录：全量 224 passed；与本次选中 44 个用例范围不同，不计算增减比例。
- AST 静态盘点：源码 141 文件，memory 13、persistence 14、evaluation 7；测试 50 文件、213 个 test_ 函数（函数数不等同于参数化用例数）。
- 新增运行代码 0、修改运行代码 0、真实 Case 0、测试产物 2（pytest.txt、tests.xml）。
- 后台异常、线上 P95/吞吐/错误率、模型质量：N/A，本次未运行后台服务或模型；INSUFFICIENT_DATA。
- 测试原始输出：`/tmp/antisentinel-memory-review-20260905/pytest.txt`。
- 本次未推进实现阶段状态；下一步为纯内存诊断和候选设计，不能据此宣称全链路通过。

### 纯内存诊断 Step

- 命令：`PYTHONPATH=src python3 docs/research/memory-evolution-20260905/probe.py`。
- 5 项诊断，5 项暴露契约缺口，预期正确行为满足数 0/5；脚本退出码 0 仅表示诊断脚本成功执行，不表示产品行为通过。
- 两个不同 operator/session 的候选：唯一 ID 1，目标 2；正文输入 2 字、输出 0 字，目标 2；Session ID 被升格 EvidenceRef 1，目标 0；2099 年生效记录被召回 1，目标 0；20 token 预算输出 121 字，按现有 assembler 公式估计 61 token，目标 <=20。估计值不是 tokenizer 实测。
- 纯内存探针，无 Runtime、模型或外部服务运行，真实 Case 数 0，重试 0。
- 证据归档：`docs/research/memory-evolution-20260905/`（probe.py、probe.json、pytest.txt，共 3 文件）。
- 未修复问题 5 项；真实质量/性能仍为 INSUFFICIENT_DATA。下一步：提交候选设计供讨论，不进入实施阶段。

### 候选设计交付与文档自审

- 新增研究/设计文档2份、归档诊断证据3份；本轮合计新增文件5、修改开发日志1；运行代码修改0。
- 文档本地链接/占位符检查：检查2份，断链0，TODO/TBD/FIXME占位符0，目标均0。
- 3条候选路线：A可信契约、B在线混合检索、C经验整合；推荐A→B→C，尚未选定实施方案。
- 已量化未修复契约缺口5项；另有静态风险未做故障注入，数量不作为测试结果。
- 外部资料：本轮打开Codex memories README及Agno memory overview/best-practices。旧Codex ext README返回404，已找到新路径。
- 44/44测试及5项诊断输出保留，不宣称本轮真实运行/全量回归通过。真实Case0，模型调用0，重试0。

## 2026-09-05 百炼 Embedding 替换：模型核对与基线

- 用户要求替换本地 embedding；链接模型为 qwen3.7-text-embedding，文字为 Qwen 3.8，准确模型 ID 待用户澄清。
- 本轮官方来源：https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api 。北京区列出 qwen3.7-text-embedding，默认1024维，每批最多20条；未核实 qwen3.8-text-embedding。
- 建议保留 SQLite 向量存储、提供远端百炼 adapter、显式模型/维度版本及重建旧索引。尚未开始实施。
- 命令：`python3 -m pytest -q tests/test_local_vector_memory.py tests/test_sqlite_memory_search.py`。
- 实际6/6 passed、0 failed、0.12s；本轮阈值6/6且失败0，达标率100%。此前44个用例与本轮范围不同，不计算变化率。
- 运行代码新增/修改0，真实Case0，API调用0，模型下载0，重试0。
- 安全配置检查只输出变量存在性：环境与.env.local未发现检查名单中的DASHSCOPE_API_KEY、DASHSCOPE_API_HOST、DASHSCOPE_BASE_URL及ANTISENTINEL_EMBEDDING_*声明；未输出密钥值。这不代表用户其他配置位置没有凭据。
- 真实模型延迟/质量/成本、向量持久化验证均N/A（INSUFFICIENT_DATA）。模型澄清后继续接入设计和实现；暂不修改或重建正式索引。

### Qwen Flash：失败测试 Step

用户已明确目标为 qwen3.7-text-embedding-flash。采用已说明的百炼远端适配器+本地SQLite方案，默认1024维、最多20条/批。

- 命令：`python3 -m pytest -q tests/test_qwen_embedding.py tests/test_local_vector_memory.py`。
- TDD red：16 failed / 3 passed，0.35s；原始输出 `/tmp/qwen-embedding-red.txt`。
- 15个新适配器用例因QwenFlashEmbedder尚不存在而失败；1个版本隔离用例复现旧向量回源新内容的问题。
- 下一步最小实现，目标19/19通过、失败0；真实API仍0次。

### Qwen Flash：最小实现与针对性回归

- 替换 local_vector_memory.py 中 E5/BGE 适配器为 QwenFlashEmbedder（qwen3.7-text-embedding-flash）。兼容接口通过httpx调用，默认1024维，按20条分批；移除E5前缀和本地模型加载。
- 校验返回model/index/维度/有限数/非零向量；按index恢复顺序。401不重试；429/暂时服务错误与传输错误最多重试2次。不把provider响应正文放入异常。
- SQLite检索额外要求向量content_version与主记录一致。
- 命令：`python3 -m pytest -q tests/test_qwen_embedding.py tests/test_local_vector_memory.py tests/test_sqlite_memory_search.py`。
- 22/22 passed，0 failed，0.45s。相同red范围16失败降为0，下降100%；green新增执行3个既有SQLite检索测试。
- 实现文件修改1、测试文件新增1/修改1；真实API调用0、正式索引重建0；本步只证明代码与MockTransport测试，真实质量/延迟仍N/A。

### Qwen Flash：累计回归与Case准备

- 新增验证真实SQLite落盘及重开恢复、传输超时重试边界、第二批失败拒绝返回局部结果。
- 命令：`python3 -m pytest -q tests/test_qwen_embedding.py tests/test_memory*.py tests/test_local_memory.py tests/test_local_vector_memory.py tests/test_sqlite_memory_search.py tests/test_operator_graph.py`。
- 实际63/63 passed、0 failed、1.77s；覆盖原44个用例+18个Qwen用例+1个旧向量版本用例。通过数44→63，增加19；失败数保持0。
- README记录环境变量、实际模型及迁移边界；新增 docs/validation/qwen-flash-embedding-smoke.md，提供3条合成记忆+1查询的可执行隔离Case。
- 正式数据迁移0、外部服务启动0、真实API请求0；真实Case待匹配百炼base URL，暂不宣称真实运行通过。

### Qwen Flash：真实合成输入Case

- 用户明确模型qwen3.7-text-embedding-flash，并提供百炼空间Host和凭据。凭据只保存在本地.env.local（0600），日志不保存凭据。
- Case：docs/validation/qwen-flash-embedding-smoke.md。实际可复现运行：加载本地环境后 `PYTHONPATH=src python3 /tmp/antisentinel-qwen-flash-smoke.py`；源码为该Case文档的Python段。
- 首次沙箱请求发生embedding_transport_error；保留失败目录 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-qwen-smoke-fg1xha_g` 和 `/tmp/antisentinel-qwen-flash-smoke-output.txt`。
- 获准沙箱外网络执行后，同一输入成功。Case重试1次，成功调用内部重试0；成功运行实际HTTP请求2次（3文本批量+1查询）。
- 模型qwen3.7-text-embedding-flash，1024维；输入44字符；文档向量3，查询结果3；持久化3；版本关联3；重开数据库向量3；外scope命中0；background workers0/异常0；quick_check=ok。
- 写入结束340.95ms；含查询与恢复验证共432.02ms。同步路径无独立异步业务/持久化时间差（N/A）；二时间点相隔91.07ms为后续查询与校验耗时，不是持久化滞后。
- 产物：`/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-qwen-smoke-9dsvak2b/memory.db`、同目录report.json；报告归档 `docs/validation/qwen-flash-20260905/report.json`。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据 | 结果 |
|---|---:|---:|---:|---|---|---|
| 文档向量 | 0 | 3 | 3 | +3 | smoke report | 满足 |
| 持久化/恢复向量 | 0 | 3 | 3 | +3 | SQLite重新打开 | 满足 |
| 版本有效关联 | 0 | 3 | 3 | +3，100% | JOIN校验 | 满足 |
| scope错误 | N/A | 0 | 0 | N/A | query返回 | 满足 |
| 后台异常 | 0 | 0 | 0 | 0 | 无后台线程 | 满足 |
| 总耗时 | N/A | 432.02ms | <=120000ms | 比上限低119567.98ms | monotonic | 满足 |
| 数据库完整性 | N/A | ok | ok | N/A | PRAGMA quick_check | 满足 |

- case_pass=true，仅限远端embedding+临时SQLite隔离链路；不代表线上MemoryRecall切换或语义质量提升。正式Memory迁移0、470查询评测0。
- 本Case状态：真实运行通过；累计63个针对性测试此前通过，最终复核后报告。未将整个Memory演进阶段标为用户review通过。

### Qwen Flash：最终复核

- 独立reviewer只读检查适配器/测试/说明，未读取凭据、未调用远端；未发现限定范围内的重要问题。指出的Case文档“未执行”措辞已更新为真实结果并链接report。
- 最终命令：`python3 -m pytest -q tests/test_qwen_embedding.py tests/test_memory*.py tests/test_local_memory.py tests/test_local_vector_memory.py tests/test_sqlite_memory_search.py tests/test_operator_graph.py`。
- 63/63 passed，0 failed，0.75s；reviewer独立复跑同范围63/63。报告归档 `docs/validation/qwen-flash-20260905/tests.txt`。
- 最终文档链接检查2份、断链0；.env.local权限0600（检查不输出内容）。
- 本轮文件新增4、修改6（含本机.env.local；运行代码修改1），测试总数63/通过63/失败0，真实Case输入集1、执行尝试2、重试1；成功Case耗时432.02ms；真实成功产物2（SQLite/report），仓库验证报告2（report/tests）；后台异常0；review重要问题0。
- 限制：仅完成embedding适配器替换与真实调用/落盘恢复验证；线上MemoryRecall尚未调用向量路径，正式数据迁移0，470查询检索评测0，质量提升与P95基线N/A。
- 状态分别记录：针对性回归通过、隔离真实运行通过；尚无用户对该实现的最终review结论。

## 2026-09-05 可信记忆基础（A）实施计划

- 用户确认继续可信记忆基础方向后，新增实施计划：`docs/superpowers/plans/2026-09-05-trusted-memory-foundation.md`。
- 计划包含6个可独立review的任务、31个可执行步骤：typed契约、typed持久化、候选与Recorder、失败重试、trusted recall、隔离真实Case。
- 计划自审命令输出：任务6，勾选步骤31，占位符0；设计文档链接存在；`AuthorizedMemoryScope`与`extraction_confidence`命名一致。
- 本Step运行代码修改0、测试运行0、真实Case运行0、产物新增1、重试0。A的当前基线仍为可信度探针0/5；Qwen适配器累计回归63/63来自上一阶段，不能作为A完成结论。
- 计划明确：真实Case需用户确认后才运行；B的在线混合检索与470查询质量实验不在A范围内。
- 用户要求中文后，计划已完整中文化；接口名、文件路径和验证命令保持原样。文档检查：任务6、复选步骤32、英文标题0、TODO/TBD/FIXME占位符0、设计文档链接存在。

### 任务 1：版本化记忆与来源契约

- 原因：修复 candidate ID 跨来源冲突，建立后续来源、生命周期与可信度门槛的类型边界。
- 基线：`tests/test_memory_models.py` 不存在；首次运行新增测试时收集失败，原因是 `MemoryRecord`、`MemorySourceRef` 未定义。相关累计 Memory 回归的历史记录为 63/63，但命令选择范围不同。
- 变更：新增 `MemorySourceRef`、`AuthorizedMemoryScope`、`MemoryRecord`；MemoryRecord 使用 schema_version=2、独立 extraction_confidence、typed source_refs、有效期和 lifecycle。MemoryCandidate 增加 `create()`，以 operator/incident/session/turn/text/source kind/source version 的 canonical JSON SHA-256 生成 ID；旧构造器保持兼容。
- 红灯命令：`python3 -m pytest -q tests/test_memory_models.py`；实际 1 collection error、0 通过、0.09s。绿灯命令：`python3 -m pytest -q tests/test_memory_models.py tests/test_memory_pipeline.py tests/test_memory_llm_classifier.py`；实际 15/15 通过、0 失败、0.35s。
- 累计回归命令：`python3 -m pytest -q tests/test_memory_models.py tests/test_memory*.py tests/test_local_memory.py tests/test_local_vector_memory.py tests/test_sqlite_memory_search.py tests/test_operator_graph.py`；实际 63/63 通过、0 失败、0.59s。
- 量化：新增测试10个；不同 identity 的 candidate ID 2/2 唯一（100%，阈值100%）；Evidence 缺 hash/version 的拒绝测试1/1（100%，阈值100%）；round-trip 1/1；非法 lifecycle 3/3；非法 extraction_confidence 3/3。修改运行文件2、新增测试文件1、文档修改2、真实 Case0、重试0、后台异常N/A。
- 状态：`回归通过`。未运行真实 Case，不能标记为 `真实运行通过` 或 `用户 review 通过`；下一步等待用户 review 后进入任务2（typed SQLite 持久化与旧数据安全解释）。

### 任务 2：typed SQLite 持久化与旧数据安全解释

- 原因：使新 typed MemoryRecord 可原样存取，并阻止旧 `source_ids` 在读取时被误解为 Evidence。
- 基线：新增 typed 存储测试前，`tests/test_memory_store.py` 为 4/4；新测试红灯命令 `python3 -m pytest -q tests/test_memory_store.py` 的实际值为 4 通过、3 失败、0.18s，失败原因是 `append_record`/`get_record` 不存在。
- 变更：FileMemoryStore、SQLiteMemoryStore 新增 `append_record`、`replace_record`、`get_record`。Legacy records 由 `MemoryRecord.from_legacy_or_dict()` 读取：旧 source_ids 或缺少 hash/version 的旧 Evidence ref 转为 `unknown`，不再伪造 Evidence。
- 针对性命令：`python3 -m pytest -q tests/test_memory_store.py tests/test_memory_resolution.py tests/test_local_vector_memory.py`；实际 13/13 通过、0 失败、0.47s。
- 累计命令：`python3 -m pytest -q tests/test_memory_models.py tests/test_memory*.py tests/test_local_memory.py tests/test_local_vector_memory.py tests/test_sqlite_memory_search.py tests/test_operator_graph.py`；实际 66/66 通过、0 失败、0.68s。相对任务1相同命令 63→66，增加3个通过用例，失败数0→0。
- 量化：typed record round-trip 1/1；幂等重复写入后SQLite记录数1/1；同ID不同内容冲突拒绝1/1；legacy source_ids→unknown 1/1；typed Evidence hash/version关联1/1；已有向量内容版本隔离回归1/1。运行文件修改2、测试文件修改1、计划/日志修改2、真实Case0、重试0、后台异常N/A。
- 状态：`回归通过`。本任务只提供 typed 存储 API 与安全旧数据读取；Recorder/Preference/Rollout producer 切换在任务3，真实 Case 仍待任务6用户确认。

### 任务 3：Recorder 候选唯一性与 typed 来源

- 原因：修复同一 diagnosis 在不同 session 下使用常量 candidate ID 导致覆盖，并使新产生的 episodic/preference memory 声明真实来源类型。
- 基线：新增测试前，命令 `python3 -m pytest -q tests/test_memory_recorder.py tests/test_memory_pipeline.py` 为 6 通过、2 失败、0.17s；失败为两个 session 只持久化1条 episodic record，Preference raw record 缺 `source_refs`。
- 变更：CandidateSource 增加 incident_id；RuleCandidateExtractor 使用 `MemoryCandidate.create()`。Recorder 新 episodic/semantic 记录使用 `append_record(MemoryRecord)`，来源为 typed session；PreferenceCandidate/PreferenceRecord 新增 typed source_refs 并序列化。旧 MemoryCandidate 位置构造器保持兼容。
- 兼容调整：typed record 的正文为 `content`，不再写旧 `text`；既有测试改为断言公开的 typed 内容。MemorySourceRef JSON 对空可选字段不输出，保持来源 JSON 简洁。
- 针对性命令：`python3 -m pytest -q tests/test_memory_recorder.py tests/test_memory_pipeline.py tests/test_memory_resolution.py tests/test_memory_llm_classifier.py tests/test_memory_store.py`；实际20/20通过、0失败、0.37s。
- 累计命令：`python3 -m pytest -q tests/test_qwen_embedding.py tests/test_memory_models.py tests/test_memory*.py tests/test_local_memory.py tests/test_local_vector_memory.py tests/test_sqlite_memory_search.py tests/test_operator_graph.py`；实际86/86通过、0失败、0.73s。
- 量化：相同 diagnosis 的两 session 持久化数1→2，增加1条、增长100%；candidate ID唯一数2/2（100%，阈值100%）；新 episodic typed session来源2/2；Preference session source refs 1/1；Session→Evidence误转0/3观察点；重复副作用0。运行文件修改4、测试文件修改2、计划/日志修改2、真实Case0、重试0、后台异常N/A。
- 剩余风险：RolloutMemory 的 summary/diagnosis 兼容投影仍使用旧 dict 字段；它尚未作为 typed 事实性来源注入，任务5的 Trusted Recall 会统一验证所有来源。状态：`回归通过`；真实Case待任务6用户确认。

### 任务 4：Memory Job 受限重试、终态失败与可观测性

- 原因：已 claim job 在分类器异常后不能丢失或无限重试；必须保留可观察的失败终态。
- 基线：新增任务测试的红灯命令 `python3 -m pytest -q tests/test_memory_jobs.py tests/test_memory_pipeline.py` 实际为3通过、2失败、0.17s；失败原因是 InMemory queue 不支持受控 clock/终态状态。
- 变更：MemoryJob 增加 next_attempt_at、status、last_error；InMemory/Redis queue 增加 fail、退避时间和状态编码。Recorder 捕获派生记忆分类/写入错误，以异常类别作为错误码：attempt 0、1 分别重试，attempt 2 转 failed；新增 memory.job.retry、memory.job.failed spans。CandidateSource 的 incident_id 同步进入 Redis job payload。
- 兼容调整：旧即时 retry 测试改为显式推进 clock；退避规则为第1次失败后1秒、第2次失败后2秒。
- 针对性命令：`python3 -m pytest -q tests/test_memory_jobs.py tests/test_memory_pipeline.py tests/test_worker_redis.py tests/test_redis_bootstrap.py`；实际12/12通过、0失败、0.52s。
- 累计命令：`python3 -m pytest -q tests/test_qwen_embedding.py tests/test_memory_models.py tests/test_memory*.py tests/test_local_memory.py tests/test_local_vector_memory.py tests/test_sqlite_memory_search.py tests/test_operator_graph.py tests/test_worker_redis.py tests/test_redis_bootstrap.py tests/test_persistence_cutover.py`；实际99/99通过、0失败、0.98s。
- 量化：首次失败重试1/1，第二次重试1/1，第三次终态失败1/1；最大 retry 2（阈值<=2）；终态 job pending=0、processing=0；in-flight=0；错误内容持久化为异常类别1种（RuntimeError）；后台异常0（同步测试）；真实Case0、重试0。
- 状态：`回归通过`。Redis 适配器完成 fake-client 回归，未连接真实 Redis；任务6用户确认的隔离真实 Case 前不能宣称真实运行通过。

### 任务 5：Trusted Recall 可信度门槛与统一预算

- 变更：新增 TrustedMemoryRecall；按 operator、incident、lifecycle、有效期、Evidence hash/版本校验过滤，legacy source_ids 统一为 unknown 并拒绝注入。可信度为 `0.6*source_trust + 0.4*extraction_confidence`；Evidence=1.0，Session/Event/Turn=0.7，unknown=0。digest 优先占用同一 token 预算，MemoryContextView 新增 estimated_tokens。
- 红灯：Trusted Recall 模块不存在，新增测试收集失败1个、0.07s；旧测试还预期无 hash Evidence/legacy source_ids 自动注入，更新为新契约。
- 针对性命令：`python3 -m pytest -q tests/test_trusted_memory_recall.py tests/test_memory_recall.py tests/test_runtime_context.py tests/test_memory_retrieval.py`；17/17通过、0失败、0.33s。
- 累计命令：`python3 -m pytest -q tests/test_qwen_embedding.py tests/test_memory_models.py tests/test_memory*.py tests/test_local_memory.py tests/test_local_vector_memory.py tests/test_sqlite_memory_search.py tests/test_operator_graph.py tests/test_worker_redis.py tests/test_redis_bootstrap.py tests/test_persistence_cutover.py tests/test_runtime_context.py`；103/103通过、0失败、1.00s。
- 量化：future/other-scope/invalid-evidence 各拒绝1/1；legacy source注入0/1；预算4时estimated_tokens<=4；真实Case0、后台异常N/A。状态：`回归通过`，真实Case待任务6确认。

### 任务 6：隔离真实可信记忆 Case

- 用户确认后执行命令：`python3 -m pytest -q tests/test_qwen_embedding.py tests/test_memory_models.py tests/test_memory*.py tests/test_local_memory.py tests/test_local_vector_memory.py tests/test_sqlite_memory_search.py tests/test_operator_graph.py tests/test_worker_redis.py tests/test_redis_bootstrap.py tests/test_persistence_cutover.py tests/test_runtime_context.py tests/test_trusted_memory_case.py && PYTHONPATH=src python3 scripts/run_trusted_memory_case.py`。
- 回归实际：104/104通过、0失败、1.49秒。真实Case实际：case_pass=true、22.62ms、运行重试0、内部job retry 1、后台异常0。
- 产物：`/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-trusted-memory-rqntafi4/trusted-memory.db` 与 `report.json`；只使用临时目录，正式storage写入0。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---:|---:|---:|---|---|---|
| sessions | 0 | 4 | 4 | +4 | run_trusted_memory_case.py | 通过 |
| unique_candidate_ids | 0 | 4 | 4 | +4 | report.json | 通过 |
| persisted_records | 0 | 6 | >=4 | +6 | SQLite查询 | 通过 |
| verified_source_refs | 0 | 6 | >=4 | +6 | report.json | 通过 |
| cross_scope_injected | N/A | 0 | 0 | N/A | TrustedRecall | 通过 |
| future_injected | N/A | 0 | 0 | N/A | TrustedRecall | 通过 |
| provenance_invalid_injected | N/A | 0 | 0 | N/A | TrustedRecall | 通过 |
| recovered_records | 0 | 6 | 6 | +6 | reopen SQLite | 通过 |
| background_exceptions | 0 | 0 | 0 | 0 | report.json | 通过 |
| duration_ms | N/A | 22.62 | <=120000 | 比上限低119977.38ms | monotonic | 通过 |

- 阶段数字：运行文件新增2、修改7；测试文件新增3、修改5；文档修改3；累计测试104/104；Case1；产物2；后台异常0；未解决风险2（Rollout兼容投影尚未typed、真实Redis未验证）。状态：`真实运行通过`、`回归通过`；等待用户 review，未进入 B。

### A 阶段用户 Review

- 用户已确认 A 阶段。状态：`用户 review 通过`。
- 证据：任务 1–5 累计回归 103/103，任务 6 最新累计回归 104/104；隔离可信记忆 Case `case_pass=true`，4 sessions、6 persisted/recovered records、跨 scope/未来/无效来源注入均为0。
- 下一步：进入 B 阶段设计收敛，先测当前线上词法路径与 Qwen 向量能力的相同数据集基线；未确认融合方案前不改线上 Recall。

### B 阶段执行纪律

- 用户要求：每完成一个任务都运行累计测试集，并立即记录测试与实际数字；所有功能冻结后再微调参数。
- 已更新 B 设计和实施计划：累计测试失败时停止后续任务与调参；参数调优固定数据 SHA、470 queries、top-k、时钟、模型、索引版本，每次只调整一个参数并与 baseline 完整比较。
- 补充用户要求的检索指标纪律：每个检索能力任务完成后必须重跑固定470条评测，记录 Recall@5、Precision@5、MRR@5、P95、逐查询最小值、bypass、评测错误、EvidenceRef完整率与泄漏数；任务1先建立该评测 baseline，任务2–4 每次重跑。

### B 任务 1：RRF 契约与固定检索 Baseline

- 红灯命令：`python3 -m pytest -q tests/test_hybrid_ranker.py tests/test_hybrid_memory_evaluation.py`；实际2个模块缺失导致2个收集错误、0.07s。
- 变更：新增 `memory/hybrid_ranker.py`（固定RRF k=60、稳定去重/排序）和 `scripts/run_hybrid_memory_evaluation.py`（固定LongMemEval-S词法baseline）。新增RRF与评测runner测试2文件。
- 评测命令：`PYTHONPATH=src python3 scripts/run_hybrid_memory_evaluation.py --data storage/benchmarks/longmemeval-s/longmemeval_s_cleaned.json --out /tmp/antisentinel-hybrid-baseline/baseline.json`。
- 数据：SHA256=d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442；总Case500、计分470、abstention30、错误0；top-k=5；RRF k=60。实际 Recall@5=78.58%、Precision@5=28.85%、MRR@5=75.50%、P50=13.69ms、P95=17.19ms。
- 通道状态：lexical=active、identifier=bypass、vector=bypass。Precision@5与MRR@5未达到0.80硬门槛；这是后续任务的baseline，不能称检索优化通过。EvidenceRef完整率、scope泄漏、悬挂引用：N/A，词法session baseline不产生MemoryRecord/EvidenceRef。
- 累计测试命令：B计划规定测试集加任务1新增测试；实际107/107通过、0失败、1.03s。运行文件新增2、测试文件新增2、文档/计划修改2、真实Case0、Qwen调用0、重试0、后台异常0。
- 状态：`回归通过`。下一步任务2为SQLite FTS5与精确标识符候选；按用户要求，完成后必须再次运行107+累计测试及470条Recall/Precision/MRR/P95评测并记录。
- 用户要求与历史 @1/@5/@10 口径对齐后，runner 已改为单次排序后切片计算 @1/@5/@10，避免三次检索造成延迟口径混乱。最新固定评测：Recall@1=40.96%、Recall@5=78.58%、Recall@10=88.26%；Precision@1=67.23%、Precision@5=28.85%、Precision@10=16.60%；MRR@1=67.23%、MRR@5=75.50%、MRR@10=76.13%；P50=13.99ms、P95=22.43ms；500/470/30/0。累计测试107/107、0失败、1.06s。此更新不改变检索能力，只修正评测报告完整性。

### B 任务 2：SQLite 词法与精确标识符候选

- 红灯：`tests/test_memory_candidate_retrieval.py` 收集失败1个，原因 `SQLiteMemoryCandidateStore` 不存在。
- 变更：新增 SQLiteMemoryCandidateStore。lexical 候选复用 FTS5；identifier 仅提取受控错误码和冒号分隔 key；两路均使用既有 operator/incident/status 过滤。FTS/查询异常返回对应 channel=bypass，不暴露其他 scope。
- 针对性：`python3 -m pytest -q tests/test_memory_candidate_retrieval.py tests/test_sqlite_memory_search.py`，5/5通过、0失败、0.09s；ERR_TIMEOUT_504 和 cache:tenant:1 命中1/1，错误operator/inactive命中0。
- 累计：111/111通过、0失败、0.90s。470评测重跑：R@1/5/10=40.96%/78.58%/88.26%，P@1/5/10=67.23%/28.85%/16.60%，MRR@1/5/10=67.23%/75.50%/76.13%，P50/P95=13.32/14.03ms，500/470/30/0。
- 评测通道仍为lexical=active、identifier/vector=bypass：当前固定 runner 尚未将任务2候选层接入端到端评测，因此数值与基线相同，不能据此判断FTS5/identifier提升或下降。下一步必须先将生产候选契约接入评测，再接受任务2的检索效果结论。
- 端到端补充：已将 SQLite lexical/identifier → RRF → TrustedRecall → MemoryRecall 注入链路接入生产候选核心，评测 runner 以每Case隔离临时SQLite调用同一核心。首次全量实现因跨Case累计索引运行约80秒未产出报告，已停止并保留临时157MB SQLite诊断产物；修复为每Case临时库后重跑成功。
- 端到端470评测：R@1/5/10=56.61%/91.48%/95.33%；P@1/5/10=87.87%/33.15%/17.66%；MRR@1/5/10=87.87%/91.56%/91.78%；P50/P95=2.16/4.28ms；500/470/30/0；lexical=active、identifier=active、vector=bypass。
- 相对固定词法baseline：Recall@5 78.58%→91.48%，增加12.90个百分点，提升16.42%；Precision@5 28.85%→33.15%，增加4.30个百分点，提升14.90%；MRR@5 75.50%→91.56%，增加16.06个百分点，提升21.27%；P95 22.43ms→4.28ms，降低18.15ms，下降80.92%。Precision@5=33.15%低于80%硬门槛，整体检索状态仍为待优化，不得报告检索验收通过。
- 累计测试：114/114通过、0失败、1.13s。改动运行文件4、测试文件3、真实Qwen调用0、后台异常0。下一步：接入Qwen向量通道并在同一470评测比较；真实批量Embedding Case需用户确认。

### B 任务 3：Qwen 向量候选与 bypass

- 红灯：`tests/test_vector_candidates.py` 初次2失败，原因 LocalVectorMemory 无 vector_candidates；系统python3缺pytest，已固定项目运行时为`/Users/xuewentao/miniconda3/bin/python3`。
- 变更：LocalVectorMemory 新增 vector_candidates，复用现有模型/维度/content_version/scope过滤；仅 embedding_transport_error 与 embedding_http_* 转为 vector=bypass。HybridMemoryRetriever 接受可选 vector_memory；应用仅在 `ANTISENTINEL_MEMORY_VECTOR_ENABLED=1` 时用QwenFlashEmbedder.from_env启用，默认不产生百炼调用。
- 针对性回归27/27通过、0失败、0.68s；累计117/117通过、0失败、1.13s。
- 470端到端评测（未启用真实Qwen）：500/470/30/0；R@1/5/10=56.61%/91.48%/95.33%，P@1/5/10=87.87%/33.15%/17.66%，MRR@1/5/10=87.87%/91.56%/91.78%，P50/P95=2.49/5.08ms；lexical/identifier=active，vector=bypass。相较任务2结果，指标无质量变化，P95 4.28→5.08ms，增加0.80ms、18.69%，仍低于词法baseline P95 22.43ms。
- 结论：vector fallback 行为通过；真实Qwen向量质量为INSUFFICIENT_DATA，必须在隔离真实Case后才判断增益。Precision@5仍低于80%门槛，整体检索未通过。

### B 任务 3：真实 Qwen Hybrid Case

- 用户确认后执行：先 `py_compile`，再加载本地 `.env.local` 环境并运行 `PYTHONPATH=src /Users/xuewentao/miniconda3/bin/python3 scripts/run_qwen_hybrid_memory_case.py`。凭据未输出。
- Case结果：case_pass=true；模型 qwen3.7-text-embedding-flash、1024维、HTTP批次2、重试0、文档向量12、查询向量10、vector=active、持久化向量12、scope泄漏0、悬挂来源0、后台异常0、quick_check=ok、P95=8.92ms、总耗时590.73ms。
- 产物：`/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-qwen-hybrid-j5h4j9px/memory.db` 与同目录 `report.json`；正式storage写入0。
- Case Recall@5=100%、MRR@5=100%、Precision@5=20%，因为10条查询各有1条相关记忆且固定分母5；该小样本不能替代470主评测的质量结论。
- 累计测试：117/117通过、0失败、0.84s。真实Qwen适配器和临时向量链路状态：`真实运行通过`、`回归通过`；470条向量主评测尚未运行，质量提升仍为INSUFFICIENT_DATA。

### B 任务 3：LongMemEval-S 全量 Qwen 批量评测失败

- 用户授权全量470条Qwen评测。初版 runner 以固定20条Session聚合，首批连续触发 `embedding_transport_error`；未生成report，未完成文档批次。
- 诊断：首个Case有53个Session、总字符485,991、前20个Session字符172,748、单Session最大18,605。12条小样本真实Case已通过，因此不是模型/凭据不可用。
- 修复后改为每批最多20条、总字符<=60,000、单Session向量输入<=24,000字符；同一首Case第二次仍在请求/重试窗口后退出，未生成report。临时产物分别位于 `antisentinel-qwen-longmemeval-*` 临时目录，正式storage写入0。
- 根据同一失败输入连续2次失败即暂停扩展的预算规则，停止全量调用。状态：`回归通过` 保持；全量Qwen向量质量 `INSUFFICIENT_DATA`，不能报告真实运行通过。
- 根因：评测将完整Session原文直接作为embedding文档，缺少有界MemoryRecord投影；下一步需设计并确认投影长度/抽取策略、batch字符预算和成本上限后再恢复。

### B 任务 3：行业投影模型修订

- 用户要求以业界一手实现为准。调研记录：`docs/research/industry-memory-projection-research.md`。
- 决策：取消“完整Session按固定字符切块后直接向量化”为默认路径；采用原始Turn/Session保底索引 + 增量抽取 digest/keyphrase/user fact/diagnosis fact/decision/failure barrier 投影的双路径。
- 依据：Codex rollout抽取与有界consolidation；LongMemEval turn/session + summary/keyphrase/userfact index；LoCoMo raw dialog/observation/session summary 对比；Mem0 默认抽取事实；Graphiti episode provenance + 增量事实图。
- 后续全量Qwen评测仅向量化带来源、scope、时间、版本的投影记录；完整原始Session保留为事实源和词法保底，不作为默认embedding文档。

### B 任务 3A：增量规则投影基础

- 红灯：`tests/test_memory_projection.py` 收集失败1个，原因 projection 模块不存在。
- 变更：新增 RuleMemoryProjector 与 ProjectionSource/MemoryProjection。规则投影产生 session_digest、受控 identifier keyphrase、显式 decision；每条投影带 Turn 来源、content_version=1、projection_revision=rule-projection-v1、稳定SHA-256 ID。空Session产生 status=failed，不生成 active 投影。
- 相关测试：13/13通过、0失败、0.05s；累计测试123/123通过、0失败、1.26s。投影同一输入幂等1/1、空输入失败1/1、投影来源完整3/3。真实Qwen调用0、真实Case0、后台异常0。
- 状态：`回归通过`。规则投影只是可审计基线；行业推荐的LLM事实抽取和LongMemEval raw/summ/keyphrase/userfact corpus对比尚未接入，因此不能报告检索质量变化。

### B 任务 3A：LongMemEval corpus 对照

- 首次全量运行因 `2023/05/20 (Sat) 02:21` 日期格式解析失败；补 `evaluation_time` 后重跑。规则投影/日期相关测试5/5通过。
- 固定470条结果：raw R@5/P@5/MRR@5=78.22%/28.77%/75.25%，P95=15.30ms；session_summ=58.67%/21.87%/59.59%，P95=1.35ms；keyphrase_userfact=18.25%/7.02%/18.47%，P95=0.43ms；raw_plus_projection=78.22%/28.77%/75.25%，P95=16.18ms。
- 结论：当前规则摘要与标识符投影显著低于raw；raw+projection未产生增益且P95增加0.88ms、5.73%。该结果不能作为Qwen投影输入或检索优化结论。需要引入专用LLM事实抽取/摘要投影，并在相同corpus对照中重新评估。
- 累计测试124/124通过、0失败、1.25s；外部模型调用0、真实Case0。状态：`回归通过`；投影检索质量 `未达标`。

### B 任务 3A：LLM Projection Extractor

- 红灯：LLMMemoryProjector 不存在，`tests/test_memory_projection.py` 收集失败1个。实现OpenAI-compatible严格JSON抽取器，仅接受 session_digest/keyphrase/user_fact/diagnosis_fact/decision/failure_barrier；Turn ID 必须属于输入Session，协议/网络/输出错误返回显式failed投影。
- MockTransport测试6/6通过；累计测试128/128通过、0失败、1.43s。
- 最小真实抽取Case：1个合成Session、2个Turn、1次模型请求；返回投影3条（session_digest/decision/failure_barrier）、active状态3/3、Turn来源4、非法来源0、case_pass=true。模型配置存在但不记录名称/凭据；正式storage写入0。
- 状态：真实运行通过（仅最小抽取Case）、回归通过。LongMemEval全量LLM投影和Qwen向量评测尚未运行，质量仍INSUFFICIENT_DATA；下一步先做小分层样本投影 vs raw corpus 对照并记录抽取费用/失败率，再决定全量预算。

### B 任务 3A：超长历史 LLM Projection 容量失败

- 真实容量Case：首个LongMemEval可计分Case，53个Session；按每Session调用LLM Projection Extractor，运行超过120秒未生成报告，按预算停止进程。正式storage写入0。
- 已确认：最小2 Turn/1请求抽取Case成功；失败仅发生在完整历史逐Session同步抽取路径，说明不是模型协议或来源校验问题。
- 结论：采用Codex类似的异步Stage-1预处理、持久checkpoint、resume、并发上限和每Session完成即抽取；查询评测只读取已持久化投影，不能同步重抽取全历史。状态：容量Case `未通过`，全量LLM投影质量 `INSUFFICIENT_DATA`。

### B 任务 3A：分层10-Case LLM Projection 对照

- 从已有LongMemEval-S按question_type分层、每类优先最少Session历史选择10条，不使用答案或gold Session；共407个Session、最大单Case42个Session。
- 异步Stage-1（workers=8）完成407个Session，耗时262,578.64ms；最终失败投影Session121，失败率29.73%。成功结果与失败结果均保留checkpoint；失败不伪装为active投影。
- 对照结果：raw R@5/P@5/MRR@5/P95=67.67%/26.00%/59.17%/13.31ms；LLM projection=67.67%/26.00%/69.17%/0.89ms；raw+LLM=69.67%/28.00%/59.17%/14.01ms。
- 变化：LLM-only MRR@5 +10.00个百分点、P95 -12.42ms（-93.29%）；raw+LLM Recall@5 +2.00个百分点、Precision@5 +2.00个百分点，但MRR无变化、P95 +0.70ms（+5.25%）。样本仅10条，不能作为全量结论；失败率29.73%高于可推广阈值，先修复抽取稳定性。
- 累计测试133/133通过、0失败、1.23s。状态：小样本真实运行通过、回归通过；全量470 LLM投影和Qwen向量仍 `INSUFFICIENT_DATA`。

### B 任务 3A：有界输入与 repair 后的分层样本复测

- 改进：LLM投影器限制总输入12,000字符、单Turn1,000字符；首个响应不满足JSON/Turn provenance时执行一次严格repair；失败记录 failure_reason 与 input_truncated。恢复批处理只跳过active Session，失败Session重跑。
- 分层10-Case最终checkpoint：407个Session，按最后记录去重后failed=58，失败率14.25%，相对29.73%减少15.48个百分点、下降52.07%。
- corpus复测：raw R@5/P@5/MRR@5/P95=67.67%/26.00%/59.17%/21.69ms；LLM projection=81.33%/30.00%/81.67%/1.51ms；raw+LLM=69.67%/28.00%/59.17%/19.80ms。
- 变化：LLM-only Recall@5 +13.66个百分点、Precision@5 +4.00个百分点、MRR@5 +22.50个百分点、P95 -20.18ms（-93.04%）。raw+LLM Recall/P +2.00个百分点，MRR无变化。样本10条且失败率14.25%，因此只作为扩大样本的正向信号，不能报告470全量优化通过。
- 累计测试135/135通过、0失败、1.43s。下一步：先将失败率降到可接受阈值并做30-Case分层验证，再决定470全量LLM投影/Qwen向量预算。

### B 检索策略决策：LLM Projection-Only

- 用户确认：默认检索只使用成功的LLM projection，不使用raw或raw+projection。
- 原始Session/Turn/Evidence保留为事实、审计和按需EvidenceRef回读；不作为候选fallback。failed/pending projection 进入retry/repair并计数，不能静默注入完整原文。
- raw、raw+projection保留为benchmark架构对照。后续30/470主评测默认只报告LLM projection-only，并单列投影缺失率。

### Memory 演进 Pending 决策

- 用户决定将进一步 Memory 演进标记为 pending，避免当前阶段过度设计。已核对没有遗留 LLM projection/Qwen LongMemEval 后台进程。
- 当前可用基线：typed provenance、scope/validity/trust gates、受限 retry、SQLite FTS5/identifier/RRF、Qwen vector adapter、LLM projection extractor、分层10-Case LLM projection 样本结果。
- Pending 范围：30/470全量LLM projection、全量Qwen向量、ANN、reranker、更多外部benchmark、参数微调和raw fallback重构。后续由明确产品需求触发接入，并从对应设计/验收阶段恢复。

### B 任务 3A：优先使用已有评测集决策

- 用户决定先使用已有 LongMemEval-S 评测，不继续全量 LLM Stage-1 抽取。已停止可恢复后台进程，保留 `/tmp/antisentinel-llm-stage1-20260905/projections.jsonl` checkpoint，不删除已完成结果。
- 当前已有470条证据：端到端 lexical+identifier+RRF+TrustedRecall R@5=91.48%、P@5=33.15%、MRR@5=91.56%；raw corpus R@5=78.22%、P@5=28.77%、MRR@5=75.25%；规则摘要/关键词投影低于raw，不能作为默认索引。
- 后续先在现有固定470数据中选择小型分层样本验证LLM projection，只有优于raw或对端到端raw+projection有可量化增益后才恢复全量Stage-1和Qwen向量调用。

### B 阶段交叉评测决策

- 用户确认：主评测使用 LongMemEval-S；参数冻结后引入 LoCoMo、MemoryAgentBench、LongMemEval-V2 做交叉验证。
- 纪律：LongMemEval-S 主评测允许调参；分层留出集和外部基准只验证泛化，不参与参数选择。不同基准保留原生指标，同时统一记录来源完整性、安全计数、P50/P95、token 与版本信息。
