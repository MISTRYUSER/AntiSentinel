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

### 知识管理：Obsidian 调研归档

- 将 `docs/research/embedding-model-and-codex-memory-research.md` 的当前结论收录到 Obsidian Vault：`11.agent learning/antiSentinel/Memory/Embedding 模型与 Codex 长期 Memory 调研.md`。
- 复用现有 `Memory` 专题目录，未新建重复的项目分支；同步更新 `Memory Index` 与 `AntiSentinel 索引` 两个入口。
- 本次为知识归档，运行测试数 `N/A`；校验目标为笔记文件 1 个、索引链接 2 个、断链 0 个。

### 知识管理：Embedding 选型 ADR 完整化

- 将 Obsidian 中的 Embedding 调研由 119 行索引摘要补全为独立技术决策记录：问题与约束、方案对比、Qwen Flash 选型原因、Codex 对照、写入/检索链路、数据边界、收益、已验证数字、风险和全量评测恢复条件。
- 数字严格区分：Qwen 仅完成 12 文档/10 查询的真实 API + SQLite 隔离 Case（P95=8.92ms、scope 泄漏=0），470 Case 指标仍是 lexical/identifier/RRF 基线，未误写为向量质量结论。
- 本次为文档完善，运行测试数 `N/A`；校验目标为笔记 1 个、两个索引仍有效、技术结论与实现代码/验证报告一致。

### 2026-09-05 PRD-004 设计前基线 Step

- 范围：读取用户 PRD、定向审计、SQLite/Context/Tool/Evidence/Projection/Tracing 接口；尚未修改生产代码。
- 命令：`pytest -q`；实际 288 passed、0 failed、1 warning、2.61s；历史通过数288→288，变化0（0%），通过率100%，门槛288项全部通过且新增失败0。历史耗时未记录，耗时回归比例 N/A。
- 命令：`rg --files src -g '*.py' | wc -l`、`rg --files tests -g 'test_*.py' | wc -l`；147个生产Python文件、64个测试文件。HEAD：9d998849cf53b44e48cf745628058a3dde31dead。
- 原始测试输出摘要：docs/validation/prd004-design-baseline/pytest.txt。
- 真实Case 0、重试0、生产代码变更0；后台异常/持久化延迟/吞吐 N/A（未启动真实Case）。INSUFFICIENT_DATA：Code Map正确性和性能尚无运行证据。
- 保留开始时已有的4项文档改动。A1/A2/A3三项审计问题仍待修复；下一步生成候选设计供用户review，不推进实现阶段状态。

### 2026-09-05 PRD-004 候选设计与文档自审 Step

- 新增3个文档产物：docs/superpowers/specs/2026-09-05-prd004-code-map-design.md、docs/research/prd004-code-map-research.md、docs/validation/prd004-design-baseline/pytest.txt；另追加本日志1个文件，生产/测试文件改动0。
- 自审命令：Python读取设计文档，正则 `^\| (\d+) ` 计数验收行、`\b(?:TODO|TBD|FIXME)\b` 扫描占位符；实际11行/阈值11，0占位符/阈值0；`git diff --name-only -- src tests`输出0项；`git diff --check`退出0，无输出。
- 候选方案0→3（目标3，100%）；验收映射0→11（目标11，100%）；研究来源4类（目标4，100%）。新增文档产物0→3；相对增长因初值0不定义。
- 测试仍引用本轮刚执行基线288/288、失败0、2.61s、警告1，未重复运行。真实Case0、重试0；后台异常与异步完成延迟N/A。总调研耗时未计时，N/A，不用估算代替。
- 2项剩余设计风险：关系全量重算耗时、源码BLOB增长；3项审计前置问题A1/A2/A3仍未修复。设计没有宣称性能或质量达标。
- 下一步：用户选择A/B/C并review草案后定稿/生成计划。Case尚未确认，未启动外部服务，未写入项目storage，不推进实现状态。
### 2026-09-05 PRD-004 Skill / Plugin 基线 Step（本轮）

- 当前输入为用户粘贴的 Skill / Plugin Integration PRD；旧 Code Map 草案不作为本轮范围。
- `pytest -q`：288/288 passed，0 failed，1 warning，2.86s；对历史288项通过数变化0（0%），阈值288项全部通过，通过率100%。历史单次耗时2.61s→2.86s，增加0.25s（9.58%）；非同环境重复性能实验，不能声称耗时回归门槛达标。
- `rg --files src -g '*.py' | wc -l`=147；`rg --files tests -g 'test_*.py' | wc -l`=64；HEAD=9d998849cf53b44e48cf745628058a3dde31dead。
- 证据：docs/validation/prd004-skill-design-baseline/pytest.txt；本Step新增证据1份、修改日志1份、生产及测试代码改动0、真实Case0、重试0。
- 后台异常、持久化耗时、吞吐、Skill加载延迟均N/A（没有执行真实Case）；INSUFFICIENT_DATA：Skill接入与质量尚未验证。
- 发现3处接入约束：messages要求一次工具后final；工具回灌只保留摘要；重复缓存调用导致non_convergent。后续设计须明确修改边界；下一步候选设计，尚不推进阶段状态。

### 2026-09-05 PRD-004 Skill / Plugin 候选设计及自审 Step

- 新增3份产物：docs/superpowers/specs/2026-09-05-prd004-skill-plugin-design.md、docs/research/prd004-skill-plugin-research.md、docs/validation/prd004-skill-design-baseline/pytest.txt；另修改开发日志1份。源代码/测试变更0；未覆盖已有Code Map草案及用户PRD文档。
- 自审命令：Python读取设计并以正则计数 `^\| [ABC](?: |：)`、`^## \d+\. 4\.[1-5] `、`^\| \d+ \|`；扫描 TODO/TBD/FIXME；`git diff --name-only -- src tests`；`git diff --check`。
- 实际输出：options=3、stages=5、acceptance_rows=15、placeholders=0、production_test_diff_files=0；design_structure_check=passed；git diff --check退出0。候选3/3、阶段5/5、验收映射15/15，三项达标率100%；占位符0/阈值0；生产及测试差异0/阈值0。设计产物从0增至3，相对变化因分母0不定义。
- 本轮测试总数288、通过288、失败0、警告1、测试耗时2.86s，见上述原始输出；真实Case数0、重试0、产物3、新增文件3、修改文件1、未解决主要设计风险3。后台异常N/A（未启动真实Case），总调研墙钟耗时N/A（没有完整计时），持久化/加载性能N/A。INSUFFICIENT_DATA：尚无Skill真实运行与效果数据。
- 自审修正边界：本轮Vt冻结、缓存前授权、运行指令块每请求仅1份、控制调用不进偏好提取、运行绑定不可变版本、OTel父子链须实际验证。
- 当前只提交候选草案供review，不推进实施阶段状态。下一步用户选择A/B/C；推荐A本地不可变版本包，需确认部署升级语义。Case runner仅约定接口，尚未实现或运行；设计确认后生成实施计划，具体Case准备好并确认后才运行。

### 2026-09-05 PRD-004 Skill版本兼容设计补充

- 根据用户要求补齐版本控制：多版本并存、ReleaseLock、旧运行和checkpoint精确恢复、工具/运行时契约校验、发布失败保留原release、回滚不改变已有绑定。本期不提供自动删除历史产物功能。
- 修改3份文档：Skill设计、Skill研究和本日志；生产/测试代码变更0；新文件0；真实Case0；重试0。测试未重跑（仅文档），此前288/288仅作历史基线；耗时/后台异常/兼容运行成功率N/A，INSUFFICIENT_DATA。
- 自审命令：Python正则统计设计中的V1/V2/V3规则及数字验收行，扫描占位符，并运行git diff --name-only -- src tests。实际compatibility_rules=3、acceptance_rows=18、placeholders=0、production_test_diff_files=0；目标3/18/0/0全部达到，结构检查达标率100%。兼容专项规则0→3，验收映射15→18，增加3（20%）；这不是运行验收通过。
- 剩余风险1项：工具行为兼容不能仅靠版本或schema证明，须旧Skill契约Case和可保留的执行实现。下一步继续设计review，尚未生成实施计划或启动真实Case。

### Stage4.1 工作树基线

- 路径：/Users/xuewentao/.codex/worktrees/antisentinel-prd004；branch=codex/prd004-skills。首次命令python因PATH缺失退出127，使用既有miniconda绝对路径重试1次。
- 命令：/Users/xuewentao/miniconda3/bin/python -m pytest -q；输出：288 passed, 1 warning in 2.85s；原始证据docs/validation/prd004-skill-stage41/baseline.txt。真实Case0；尚未调用模型或外部服务。

### Stage4.1 失败测试Step

- 新增test_skill_catalog.py和test_skill_cli.py，验证错误包排除、无正文读取、显式工具导出、不可变索引与可用性。
- 命令：python -m pytest tests/test_skill_catalog.py tests/test_skill_cli.py -q；1 error in 0.12s；预期catalog/CLI缺失断言失败，生产代码仍0改动。证据docs/validation/prd004-skill-stage41/red.txt；下一步最小实现；真实Case0。

- 失败测试更正：首次出现测试辅助导入路径错误（1 collection error），不算有效RED；改为tests.test_skill_catalog后复跑：29 failed in 0.21s；本次才作为缺少接口的RED证据。重试1。

### 2026-09-05 PRD-004 Skill / Plugin Stage 4.1 实现与回归

- 范围：本地 `plugin.json` 严格校验、元数据发现、可信显式工具导出、可用性过滤、不可变Catalog视图、`validate/inspect` CLI，以及隔离Case runner。正文/资料按需读取、ReleaseLock、Runtime接入、真实模型和评测均未进入本阶段。
- 新增生产文件6个：`capabilities/manifest.py`、`registry.py`、`availability.py`、`bootstrap.py`、`cli.py`、`scripts/validate_skill_integration.py`；新增测试2个文件。生产代码与测试新增合计8个文件；验证产物3个（baseline、targeted-tests、full-tests），开发日志修改1个文件。
- 先失败测试：有效RED为29 failed，0 passed，0.21s；首次导入路径collection error未计入产品失败，已在第1次重试修正。新增嵌套资源路径测试先失败1项，修复后通过。
- 针对性命令：`/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_skill_catalog.py tests/test_skill_cli.py tests/test_tools.py -q`；实际45/45 passed、0 failed、0.41s，证据`docs/validation/prd004-skill-stage41/targeted-tests.txt`。
- 累计命令：`/Users/xuewentao/miniconda3/bin/python -m pytest -q`；实际319/319 passed、0 failed、1 warning、3.22s，证据`docs/validation/prd004-skill-stage41/full-tests.txt`。工作树开始值288/288、2.85s→319/319、3.22s：通过数增加31，失败数0→0；耗时增加0.37s（12.98%），测试数增长后不作为无Skill性能回归证据，性能指标仍INSUFFICIENT_DATA。
- 4.1成功指标：合法Skill登记2/2（100%）、错误包诊断6/6（100%）、注册正文读取0/0（100%）、错误包工具泄漏0/0（100%）、路径逃逸/符号链接误接受0/0（100%）。失败指标：未捕获后台异常0；本阶段无后台worker。回归指标：既有288测试通过数288/288，新增失败0。
- 已准备但未执行真实Case：`/Users/xuewentao/miniconda3/bin/python scripts/validate_skill_integration.py --stage 4.1 --output /tmp/antisentinel-prd004-stage41-<unique-run-id>`。输出目录必须不存在；只创建该目录下fixture和3个JSON产物；不启动服务、不使用模型、不写项目storage。待用户确认Case后运行，最多重试2次；当前Case数0、真实产物0、持久化数量N/A、业务/持久化完成时间N/A、后台异常N/A、case_pass未判定。
- 当前阶段状态：回归通过（证据为上述pytest命令）；真实运行通过与用户review通过均未达到。下一步仅等待Case确认，不进入4.2。

### 2026-09-05 PRD-004 Skill / Plugin Stage 4.1 真实运行 Case

- Case：本地能力包登记与错误包隔离；范围4.1。输入：1个合法本地包（2个Skill）和6个错误包（缺工具、缺资源、父路径、绝对路径、非法字段、非法reference）；运行：`/Users/xuewentao/miniconda3/bin/python scripts/validate_skill_integration.py --stage 4.1 --output /tmp/antisentinel-prd004-stage41-20260905-r1`。输出目录在运行前不存在，未访问项目storage、Redis、HTTP或模型。
- 运行状态：exit_code=0，业务完成0.20s；持久化状态：3个报告JSON写入成功，业务完成与持久化完成由同一同步进程完成，精确t2-t1=N/A（runner未记录两个独立时钟，不能伪造）；后台异常0。真实产物：`/tmp/antisentinel-prd004-stage41-20260905-r1/catalog.json`、`diagnostics.json`、`report.json`，另有21个fixture源文件，物理文件总数24。
- 关键数量／关联：合法Skill 2/2；错误诊断6/6，分别带package_root、field和reason；正文读取0；错误包工具泄漏0；catalog工具数0；snapshot_id长度64。独立进程恢复读回命令：`python -c '...json.loads...'`，实际3/3 JSON可解析，读回skill_count=2、diagnostic_count=6、case_pass=true。异步持久化N/A（本Case无worker）。
- 量化结果：输入包7，输入Skill声明14，运行0.20s，输出报告3，持久化报告3，关联校验8（2合法Skill+6诊断），后台异常0，恢复读回3/3，重试0。Case输出：`case_pass=true`。

| 指标 | 基线 | 实际值 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---:|---:|---:|---|---|---|
| 合法Skill登记 | 0 | 2 | 2 | +2，100% | stage 4.1 runner | 通过 |
| 错误诊断 | 0 | 6 | 6 | +6，100% | stage 4.1 runner | 通过 |
| 注册正文读取 | 0 | 0 | 0 | 0，0% | stage 4.1 runner body-read guard | 通过 |
| 错误包工具泄漏 | 0 | 0 | 0 | 0，0% | stage 4.1 runner | 通过 |
| 报告产物读回 | 0/3 | 3/3 | 3/3 | +3，100% | 独立JSON readback | 通过 |
| 后台异常 | N/A | 0 | 0 | N/A，无后台worker | runner exit/output | 通过 |
| 既有回归 | 288/288 | 319/319 | 288/288且失败0 | +31测试，失败变化0 | `pytest -q` | 通过 |

- 阶段数值：新增/修改生产文件6，新增测试文件2，新增验证脚本1，文档/日志/验证文件至少5；测试总数319、通过319、失败0、Case数1、测试+Case可观测耗时3.63s（3.22+0.41，未含shell开销）、Case重试0、报告产物3、后台异常0、未解决实施风险2（4.2版本锁和4.3运行授权尚未实现）。状态：真实运行通过；回归通过证据保留；用户review通过未达到。

### 2026-09-05 PRD-004 Skill / Plugin Stage 4.2 实现与回归

- 范围：不可变本地版本包、release lock、内容hash、同版本内容冲突拒绝、显式按需正文/资料读取、16MiB LRU内容缓存、运行专属原子激活/资料披露状态、瞬时I/O最多2次重试、CLI `pack`与显式release inspect，以及累计4.1–4.2 Case runner。未接入RuntimeLoop、ToolExecutor、模型、API或真实外部服务。
- 新增生产文件3个：`capabilities/package.py`、`loader.py`、`runtime_state.py`；修改CLI和Case runner各1个；新增测试3个文件并扩展CLI测试。4.2新增测试16项（Release 4、Loader 7、Activation 4、CLI 1）；累计Skill针对性测试61项。
- 有效RED：发布/加载/激活模块缺失时12 failed；CLI pack缺失及瞬时重试/4.2 runner缺失时2 failed；累计4.2 runner包含4.1子Case的测试先失败1项。最小实现后均转绿。单元测试重试0；真实Case重试0（尚未执行）。
- 针对性命令：`/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_skill_catalog.py tests/test_skill_cli.py tests/test_skill_release.py tests/test_skill_loader.py tests/test_skill_activation.py tests/test_tools.py -q`；实际61/61 passed、0 failed、0.67s，证据`docs/validation/prd004-skill-stage41/stage42-targeted-tests.txt`。
- 累计命令：`/Users/xuewentao/miniconda3/bin/python -m pytest -q`；实际335/335 passed、0 failed、1 warning、2.93s，证据`docs/validation/prd004-skill-stage41/stage42-full-tests.txt`。上阶段319/319、3.22s→335/335、2.93s：通过数增加16，失败0→0；耗时减少0.29s（9.01%），不同单次运行且测试集合增加，不作为性能改善结论。
- 4.2成功指标：两次相同pack release_id一致100%（1/1）、同版本异内容拒绝100%（1/1）、旧release未披露资料读取正确100%（unit 1/1）、篡改资源拒绝100%（1/1）、激活或reference持久化失败半状态0/0（2个失败点）、瞬时读取重试次数2/最大2。失败指标：错误激活0、越界资源读取0；回归：既有288测试288/288、累计335/335通过。
- 已准备但未执行累计真实Case：`/Users/xuewentao/miniconda3/bin/python scripts/validate_skill_integration.py --stage 4.2 --output /tmp/antisentinel-prd004-stage42-20260905-r1`。它在独立目录先运行4.1子Case，再生成v1/v2两个release、在v2发布后读取v1此前未披露reference、注入checkpoint写失败并写入`releases.json`、`state.json`、`report.json`。不启动服务、模型或网络，不写项目storage；默认120秒、最多2次重试。真实Case计数0、持久化数量N/A、后台异常N/A、case_pass未判定。
- 当前阶段状态：回归通过；真实运行通过和用户review通过未达到。下一步仅等待4.2 Case确认，不进入4.3。

### 2026-09-05 PRD-004 Skill / Plugin Stage 4.2 真实运行 Case

- 用户首次从`~`执行相对脚本路径，系统在打开脚本前报`[Errno 2]`，未创建输出目录、未执行Case；随后从工作树`/Users/xuewentao/.codex/worktrees/antisentinel-prd004`以相同参数重跑。Case：累计4.1登记验证、v1/v2发布、旧release未披露reference读取和checkpoint失败原子性；命令：`/Users/xuewentao/miniconda3/bin/python scripts/validate_skill_integration.py --stage 4.2 --output /tmp/antisentinel-prd004-stage42-20260905-r1`。
- 运行状态：exit_code=0，业务完成0.27s；持久化状态：`releases.json`、`state.json`、`report.json`及`stage41/`子Case报告写入成功。同步runner未记录独立t1/t2，t2-t1=N/A；后台异常0。真实产物根：`/tmp/antisentinel-prd004-stage42-20260905-r1`，物理文件43个；本阶段报告4个（顶层3+stage41/report），其余为release和fixture文件。
- 关键数量／关联：release数2，release ID不同；旧运行`release_id=18682…b61c2f`、selected_skill=`source/diagnosis@1.0.0`、state revision=2；披露reference 1个，hash=`f92ec7…957411`，内容为v1的`version one reference`；v2未改变旧状态。checkpoint失败实例selected_skill_id为空，半激活状态0。4.1子Case仍为合法Skill2、诊断6、正文读取0、工具泄漏0。
- 恢复校验：独立进程读取顶层`releases.json`、`state.json`、`report.json`和`stage41/report.json`共4/4，断言两个case_pass均true、state revision=2、selected_skill一致，实际通过。异步持久化N/A（无worker）。

| 指标 | 基线 | 实际值 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---:|---:|---:|---|---|---|
| 累计4.1子Case | 0 | 1 | 1 | +1，100% | stage 4.2 runner | 通过 |
| 并存release | 0 | 2 | 2 | +2，100% | `releases.json` | 通过 |
| 旧release未读资料 | N/A | v1内容1份 | 精确v1内容 | N/A，内容一致 | `report.json` | 通过 |
| 旧运行版本漂移 | 0 | 0 | 0 | 0，0% | `state.json` | 通过 |
| checkpoint半激活 | 0 | 0 | 0 | 0，0% | stage 4.2 runner | 通过 |
| 产物独立读回 | 0/4 | 4/4 | 4/4 | +4，100% | 独立JSON readback | 通过 |
| 后台异常 | N/A | 0 | 0 | N/A，无后台worker | runner exit/output | 通过 |
| 累计回归 | 319/319 | 335/335 | 既有288/288且失败0 | +16测试，失败变化0 | `pytest -q` | 通过 |

- 阶段数值：新增/修改生产文件5（package、loader、runtime state、CLI、runner），新增测试文件3并扩展1个，测试总数335、通过335、失败0、Case数1、可观测测试+Case耗时3.60s（2.93+0.67，未含shell开销）、Case重试0、顶层报告产物3、独立读回产物4、后台异常0、未解决实施风险1（4.3尚未把状态接入RuntimeLoop/ToolExecutor）。状态：真实运行通过；回归通过证据保留；用户review通过未达到。

### 2026-09-05 PRD-004 Skill / Plugin Stage 4.3 运行时接入 Step（进行中）

- 已接入：运行专属`SkillRuntime`把4.2 loader/state映射成`skill.load`与`skill.read_reference`；每回合在模型调用前冻结可见工具集合；ContextBuilder按未激活目录/激活指令/已读资料构建来源明确的上下文；ToolExecutor在缓存查询前校验当前回合scope、只读和审批策略；RuntimeSnapshot持久化当前Skill身份。
- DeepSeek适配修正：普通工具结果仍强制final JSON；当请求含skill或skill_catalog上下文时，不再在skill控制结果后发送`tool_choice=none`，让下一回合能调用新披露工具；自定义skill角色转换为兼容的user角色。该行为有MockTransport契约测试，不把网络响应伪造为真实DeepSeek Case。
- 有效RED：SkillRuntime/ToolExecutionScope缺失时5 failed；checkpoint skill_state缺失时1 failed；DeepSeek控制结果仍被强制final时1 failed。实现后转绿。针对性命令：`pytest tests/test_skill_runtime.py tests/test_deepseek_compatibility.py tests/test_runtime_loop.py tests/test_runtime_context.py tests/test_checkpoint.py tests/test_tools.py -q`，实际46/46 passed、0 failed、0.68s，证据`docs/validation/prd004-skill-stage41/stage43-targeted-tests.txt`。
- 累计命令：`pytest -q`，实际342/342 passed、0 failed、1 warning、3.45s，证据`docs/validation/prd004-skill-stage41/stage43-full-tests.txt`。上阶段335→342，增加7项；失败0→0；单次耗时2.93s→3.45s，增加0.52s（17.75%），测试集合不同且不是5次同fixture性能试验，性能结论INSUFFICIENT_DATA。
- 真实DeepSeek Case未运行：读取环境变量名结果为空，只有`.env.example`，未读取或输出任何密钥。真实调用、输入/输出Token、模型调用数、P50/P95、后台异常、持久化延迟均N/A；Case数0、重试0、case_pass未判定。需要将`ANTISENTINEL_MODEL_MODE=real`、`ANTISENTINEL_MODEL_BASE_URL=https://api.deepseek.com`、`ANTISENTINEL_MODEL_NAME`和`ANTISENTINEL_MODEL_API_KEY`配置到运行脚本所在进程后再提交具体真实Case。
- 当前状态：4.3离线回归通过；真实运行通过与用户review通过未达到。未进入4.4。

### 2026-09-05 PRD-004 Skill / Plugin Stage 4.3 真实 DeepSeek Case

- 使用原始工作区`.env.local`在子进程中配置真实DeepSeek；不读取或记录密钥。第1次HTTP 400（工具名含`.`），第2次读回安全错误摘要确认DeepSeek仅接受`^[a-zA-Z0-9_-]+$`工具名，第3次已调用业务工具但模型重复缓存调用，Loop返回`model_non_convergent`。修复内部名称映射和“仅Skill控制结果允许后续工具”后，第4次同一Case通过；重试预算由2提升至4，理由为前三次均定位到并修复了提供方协议/回合控制根因。
- 通过命令：从工作树加载原始`.env.local`后运行`python scripts/validate_skill_integration.py --stage 4.3 --output /tmp/antisentinel-prd004-stage43-20260905-r4`；实际exit_code=0、耗时1.96s、模型调用2、输入Token1398、输出Token64、工具调用1、read_health调用1、selected_skill=source/diagnosis、后台异常0、case_pass=true。
- 真实产物：`/tmp/antisentinel-prd004-stage43-20260905-r4/report.json`、`runtime-result.json`；独立进程读回2/2，status=completed且case_pass=true。持久化数量2，关联校验2（Skill身份和工具结果），异步t2-t1=N/A（无worker）。
- 阶段状态：真实运行通过；回归通过。用户review通过未达到，未进入4.4。

### 2026-09-05 PRD-004 Skill / Plugin Stage 4.4 diagnosis契约 Step（进行中）

- 新增diagnosis本地包清单、指令、健康资料和3条契约（明确匹配、无匹配、写操作禁止）；新增确定性`verify_contract`。有效RED：verifier模块缺失时2 failed；最小实现后`pytest tests/test_diagnosis_skill.py -q`为3/3 passed、0 failed、0.02s。
- 验证：加载Skill但无health_evidence失败1/1；健康证据+read_health通过1/1；restart_service触发禁止工具和no_write_tool两个失败断言2/2。真实DeepSeek/API/页面Case未重复运行；持久化、Trace和页面展示仍未实现，Case数0，状态仍为回归通过。

### 2026-09-05 PRD-004 Skill / Plugin Stage 4.4 真实运行 Case

- 变更：Runtime写入skill.loaded/skill.reference_read/skill.load_failed Event与Span；结果、API和Dashboard显示release、Skill版本和资料hash数量；应用Session接受skill_id/skill_version并在持久化前完成选择。正文不进入Trace、Result或页面。
- 回归：`pytest -q`实际348/348 passed、0 failed、1 warning、3.23s，证据`docs/validation/prd004-skill-stage41/stage44-full-tests.txt`。此前348→348，失败0→0；耗时4.10s→3.23s，减少0.87s（21.22%），测试集合相同但仅单次，不作为性能改善结论。
- 真实Case：加载原始`.env.local`后，隔离目录`/tmp/antisentinel-prd004-stage44-r2`，真实DeepSeek + 本地Redis read_health + FastAPI TestClient + SQLite。运行completed，3.944s，输入2687 token、输出193 token、工具调用2、Skill Span1、持久化报告3、后台异常0、重试0；Dashboard HTTP=200；重建服务读回skill_usage一致；独立读回report/session/observability 3/3。case_pass=true。
- 实际Skill：diagnosis/diagnosis@1.0.0，release_id=3fe6952d…e8876464，reference_hashes=0。指标：Skill身份一致1/1、持久化读回1/1、Dashboard200/200、Span≥1实际1、正文Trace泄漏0、既有回归288/288。阶段状态：真实运行通过、回归通过；用户review通过待确认。

### 2026-09-05 PRD-004 Skill / Plugin Stage 4.5 本地 A/B/C DeepSeek Step

- 冻结本地工程烟测7类各1条，共7条；本次只运行`skill-execution-01`一个适用Case，A无Skill、B显式预载、C自主发现，各3次，共9 trial。命令：加载原始`.env.local`后运行`python scripts/run_skill_abc_evaluation.py /tmp/antisentinel-prd004-skill-abc-r1`；总墙钟22.25s，业务trial累计21.616s，重试0，后台异常0。
- A：3/3成功，read_health 3次，选择Skill 0次，输入3088、输出235 token，P50 1.560s、P95 1.563s。B：3/3成功，选择3/3，read_health 3次，输入4240、输出404，P50 2.165s、P95 2.956s。C：3/3成功，选择3/3，read_health 3次，输入6665、输出425，P50 3.291s、P95 3.340s。
- B−A成功率0个百分点；输入+1152（+37.31%），输出+169（+71.91%），累计耗时+2.548s（+54.63%）。C−B成功率0个百分点；输入+2425（+57.19%），输出+21（+5.20%），累计耗时+2.528s（+35.05%），P95+0.384s（+12.99%）。单Case结果只证明链路可执行，不证明Skill提升任务成功率。
- 产物：`/tmp/antisentinel-prd004-skill-abc-r1/trials.jsonl`9行和`report.json`1份；独立读回2/2，trial 9/9、trace_id 9/9、失败/超时/基础设施错误0、case_pass=true。全量`pytest -q`为350/350 passed、0 failed、1 warning、4.30s，证据`docs/validation/prd004-skill-stage41/stage45-full-tests.txt`。
- 阶段状态：本地Case真实运行通过、回归通过。SkillsBench/BFCL固定子集、来源commit/license及适配器尚未交付，因此4.5整体仍待验证，不能声称PRD-004全部完成。

### 2026-09-05 PRD-004 Obsidian 设计与实现归档

- 在Vault `11.agent learning/antiSentinel/runtime 层开发/Skill 与 Plugin 集成设计与实现.md` 新增1份162行主笔记，记录范围、架构、版本控制、渐进披露、工具权限、已完成实现、4.1–4.4真实Case、DeepSeek A/B/C、SkillsBench和后续Plan边界。
- 更新2个索引：`AntiSentinel 索引.md`与`AntiSentinel Index.md`，两者均含`[[Skill 与 Plugin 集成设计与实现]]`。校验命令`test -f`、`rg -l`、`rg -n`、`wc -l`；实际主笔记1/1、索引链接2/2、关键数字5类命中、断链0，达标率100%。
- 范围决定：PRD-004保持单Agent单Skill；64个SkillsBench多Skill任务及多Skill架构交给Plan，不作为PRD-004完成门槛。测试N/A（知识归档未改生产代码）；新增Vault文件1、修改Vault索引2、后台异常N/A、重试0。

### 2026-09-05 PRD-004 多 Skill Runtime 扩展 Step

- 用户将范围扩展为单Agent多Skill。状态从单值selected_skill升级为有序active_skills；同Skill重复加载幂等，第二个不同Skill原子激活；reference key使用`skill_id:reference_id`防同名冲突；Context按激活顺序注入多份指令，工具集合取required_tools并集。
- 同时修复自适应Loop：业务工具结果后允许不同工具调用，运行级`max_total_tool_calls`、每回合预算、缓存和连续两回合重复检测共同收敛。重复调用首次返回`duplicate_tool_call`和既有摘要，允许模型最终结论；第二个纯重复回合才`model_non_convergent`。
- 新增/更新测试：多Skill状态与Context、重复后final、总工具预算；针对性10/10通过。全量命令`pytest -q`实际355/355 passed、0 failed、1 warning、3.32s，证据`docs/validation/prd004-skill-stage41/multiskill-full-tests-2.txt`。
- 真实DeepSeek多Skill Case：`/tmp/antisentinel-multiskill-r2/report.json`，两个Skill顺序`multi/diagnosis → multi/runbook`，共享read_health调用1，completed，2.732s，输入1961、输出83 token，后台异常0、重试1（首次重复调用策略失败后修复），case_pass=true。
- 未完成：checkpoint恢复重建所有ActiveSkill、多Skill正式A/B/C与SkillsBench任务环境适配。当前状态：真实运行通过、回归通过；用户review通过待确认。

### 2026-09-05 PRD-004 多 Skill A/B/C 正式 DeepSeek Case

- 运行`/tmp/antisentinel-multiskill-abc-r3`：固定两个Skill（multi/diagnosis、multi/runbook）、同一健康工具、A无Skill/B显式两Skill/C自主两Skill，各3 trial，共9条。case_pass=true、9/9通过、后台异常0、重试1（前次重复调用策略失败后修复）。
- A：3/3、输入3879、输出225、累计5.258s。B：3/3、输入8024、输出353、累计8.379s。C：3/3、输入11035、输出724、累计13.460s。B−A成功率0pp、输入+4145（106.86%）；C−B成功率0pp、输入+3011（37.53%），累计耗时+5.081s（60.64%）。结果证明多Skill链路和自主双选择可运行，不证明收益。
- 机制：重复调用首次回灌duplicate_tool_call；若后续请求仍带重复反馈，Provider强制final，避免死循环。全量`pytest -q`=356/356 passed、0 failed、1 warning、4.62s，证据`docs/validation/prd004-skill-stage41/multiskill-abc-full-tests.txt`。

### 2026-09-05 PRD-004 BFCL 派生子集 Step

- 安装`bfcl-eval==2026.3.23`，补充其遗漏的soundfile依赖。冻结simple_python、irrelevance、multi_turn_base各5条，共15条；每条保留case ID、源行hash、原问题、函数定义和ground truth，选择规则为每个类别源文件前5条。
- DeepSeek正式派生运行产物`docs/validation/bfcl-deepseek-report`：15条记录、10条可评分、7条通过、70.00%；输入3566、输出1109、模型耗时10.898s、基础设施错误0。5条multi_turn_base因缺有状态文件工具环境标记unsupported，不计入10条准确率分母。另一轮9/10仅记录模型波动，不替换正式结果。
- 协议映射：BFCL schema `dict/float/list/tuple/bool`归一为JSON Schema；非法Provider函数名映射为可逆alias。测试`tests/test_bfcl_adapter.py`=2/2通过。未完成：有状态多轮环境和官方BFCL leaderboard对齐，当前报告明确标为派生评测。

### 2026-09-05 PRD-004 多 Skill恢复与自适应Loop收口

- 根据审阅反馈，业务工具结果不再被Provider层强制final；允许不同工具继续调用。新增运行级max_total_tool_calls，重复调用首次回灌duplicate_tool_call和既有摘要，给模型一次最终结论/换工具机会，连续第二个纯重复回合才model_non_convergent。重复后final和持续重复终止测试通过。
- 多Skill恢复：checkpoint记录active_skills和`skill_id:reference_id`；resume逐项读取并核验release/version/instruction/reference hash，恢复后Context按顺序注入全部Skill。针对性9/9通过；全量`pytest -q`实际356/356 passed、0 failed、1 warning、3.10s，证据`docs/validation/prd004-skill-stage41/multiskill-restore-full-tests.txt`。
- 用户要求多Skill实现后，设计、计划和Obsidian已同步：ActiveSkill集合归Skill Runtime，Plan选择能力，DAG调度依赖。真实双Skill DeepSeek Case仍为`/tmp/antisentinel-multiskill-r2/report.json`：completed、2.732s、输入1961、输出83、共享工具1、case_pass=true。多Skill正式A/B/C与SkillsBench沙箱工具适配仍待执行。

### 2026-09-07 下一期PRD启动核对

- 核对HEAD `55ea620`及当前工作区，保留既有未提交文档；生产代码修改0。
- 回归命令：`/Users/xuewentao/miniconda3/bin/python -m pytest -q`，实际363/363 passed、0 failed、1 warning、3.74s；旧索引288→363（+75，26.04%），失败0→0，门槛全部当前测试通过。当前生产Python文件160、测试文件74。
- 报告：`docs/validation/2026-09-07-prd-readiness.md`。004已合入但PRD状态及单Skill描述落后；SkillsBench全量无分数、BFCL仍5条unsupported。003审计A1/A2/A3仍需处理。以上不据单测标记Done。
- 下一期推荐005A，先固定Commit快照与扫描，再符号关系/存储增量/查询Evidence；旧Code Map草案方案A待确认。新真实Case0、重试0，性能及持久化指标N/A（模块未实现）。本次状态仅累计回归通过，设计与用户review尚待确认。

### 2026-09-07 PRD-005A定时异步扫描需求更新

- 用户明确采用定时异步扫描；更新005A PRD、索引及本记录共3份文档，生产代码修改0，系统定时任务创建0。
- 纳入远端Git凭证引用、专用缓存、定时发现固定SHA、持久化去重任务、Worker租约恢复、原子发布、部署版本匹配和存储预算。建议默认周期5分钟、并发1、构建120秒、重试最多2次；均非实测容量。
- 阶段重排为5A.1–5A.5，先最小地图与Evidence后关系增量；RAG可在5A.3后进入设计。保留原Python范围及来源/迁移要求，标明百度前端仓库只验证Git访问。
- 继承本会话已跑基线363/363、0失败、3.74秒；此次纯文档变更不重复运行代码测试。新功能Case数0，性能/持久化N/A。PRD状态Draft，待新增细则review，不宣称实现或真实运行通过。
- 文档自审：12个主章节、5个阶段及索引状态等5项结构检查全部通过，旧4.x阶段表0、TODO/TBD占位0。补充同步观察与入队恢复、同步重试与构建重试分离、输入预算与累计存储配额区别。验证使用绝对Python路径（当前shell未提供python别名）；本次无代码测试执行。

### 2026-09-07 PRD-005A自适应轮询确认

- 用户确认将固定5分钟检查替换为自适应轮询：首次立即、初始/最小30分钟、无变化翻倍、上限4小时、新SHA重置30分钟。计算基准为服务端检查完成时间，不使用Commit时间。
- 同步PRD、索引与本记录3份文档；补充持久化调度状态、失败不翻倍、重启仅补1次检查、显式部署Commit直入队及4类调度验收。代码修改0、实际定时任务0、真实Case0；功能性能N/A。本次仅文档更新，不重跑代码测试。


## 2026-09-07 PRD-005A 调研：离线基线 Step

命令：`/Users/xuewentao/miniconda3/bin/python -m pytest -q`（subprocess timeout=120）。原始输出：`docs/validation/prd005a-design-baseline/pytest.txt`；统计：`baseline.json`。

{"head": "55ea620e54f876e3c564b6fb7c5daf02b09226cd", "production_python_files": 160, "test_files": 74, "code_map_files": 0, "returncode": 0, "wall_seconds": 4.58, "prd_sha256": "492c36b5369989498c4666310b0240c85f162a52c328e2742c8093af4ec5a89e", "real_cases": 0, "retries": 0}

........................................................................ [ 19%]
........................................................................ [ 39%]
........................................................................ [ 59%]
........................................................................ [ 79%]
........................................................................ [ 99%]
...                                                                      [100%]
=============================== warnings summary ===============================
../../miniconda3/lib/python3.13/site-packages/fastapi/testclient.py:1
  /Users/xuewentao/miniconda3/lib/python3.13/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
363 passed, 1 warning in 3.55s

历史基线363项/0失败；以本次输出核对。真实Case执行0；后台异常、扫描延迟、吞吐和异步持久化差值均N/A（尚未运行地图）。未修改生产代码；仅基线记录，不推进功能验收状态。下一步：一手资料调研、中文设计草案、Case提案。


## 2026-09-07 PRD-005A 调研与设计草案 Step

范围：按用户附件研究定时异步Code Map，保留已有未提交文档；新设计草案未获确认，不写实施计划或生产代码，不推进实现阶段状态。

产物：`docs/research/prd005a-code-map-research.md`、`docs/superpowers/specs/2026-09-07-prd005a-code-map-design.md`、`docs/validation/prd005a-cases-proposal.md`；原始源码/基线/自审见 `docs/validation/prd005a-design-baseline/`。

证据命令：`cat docs/validation/prd005a-design-baseline/{baseline.json,pytest.txt,document-review.json}`；`git diff --check`（退出0）；`git diff --name-only -- src tests`（空）；来源可按manifest URL重取并校验SHA256。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据 | 结果 |
|---|---:|---:|---:|---|---|---|
| 测试通过 | 363 | 363 | 全部363 | 0/0% | pytest.txt | 100% |
| 测试失败 | 0 | 0 | 0 | 0 | pytest.txt | 达标 |
| 新研究官方项目 | 0 | 2 | ≥2 | +2；比例N/A | sources/manifest.json | 达标 |
| 源码hash核验 | 0 | 4 | 4/4 | +4；比例N/A | document-review.json | 100% |
| 新草案阶段映射 | 0 | 5 | 5 | +5；比例N/A | document-review.json | 100% |
| 新Case提案 | 0 | 6 | 6 | +6；比例N/A | document-review.json | 100% |
| 生产/测试变更文件 | 0 | 0 | 0 | 0 | git diff | 达标 |
| 占位符/本地坏链接 | 0/0 | 0/0 | 0/0 | 0 | document-review.json | 达标 |

新增文件11、修改已有文件1（追加本日志）；测试总数363、通过363、失败0、警告1；真实Case执行0、提案6；测试总墙钟4.58秒（pytest内部3.55秒）；整个调研总耗时N/A（未记录开始计时，不能估算）；重试0；新增产物11；后台异常N/A（无真实Case）；开放风险5类。

自审修正：区分假构建receipt与真实snapshot发布；partial重试需绑定发布代次；Git对象pin防止force-push后历史来源丢失；共享SQLite配额采用披露的保守上界；5A.3仅contains邻居，避免提前依赖5A.4；Trace不在SQLite发布事务内导出；真实退避与120秒总预算的冲突由可控时钟/另提预算处理。

结论：离线回归363/363，一手调研与草案可供review；实现、容量、模型效果及真实恢复仍INSUFFICIENT_DATA。建议方案A（SQLite持久化队列+独立后台服务+AST缓存/全图关系重算）。剩余R1容量/锁、R2企业Git运行身份、R3静态语义范围、R4历史审计和Context兼容、R5性能预算，详见设计。下一步等待用户选择方案和评审设计，随后生成按5A.1–5A.5组织的实施计划；不自动运行Case。


## 2026-09-07 PRD-005A 实施计划 Step

用户确认方案A及设计方向后，使用 `writing-plans` 技能生成计划：`docs/superpowers/plans/2026-09-07-prd005a-code-map.md`。计划按5个阶段、11个任务、66个checkbox步骤组织，覆盖迁移、登记调度、租约Worker、固定Commit Git读取、Python AST/Chunk、精确查询、Evidence/Context、关系、增量、累计Case和企业Git前置。

证据命令：`rg -c '^## Stage' docs/superpowers/plans/2026-09-07-prd005a-code-map.md`、`rg -c '^### Task' docs/superpowers/plans/2026-09-07-prd005a-code-map.md`、`rg -c '^- \\[ \\] \\*\\*Step' docs/superpowers/plans/2026-09-07-prd005a-code-map.md`（阶段5、任务11、步骤66）；`rg -n '<[^>]+>|\\b(TBD|TODO|FIXME|placeholder)\\b' docs/superpowers/plans/2026-09-07-prd005a-code-map.md`（0命中）；`git diff --check`（退出0）。

量化结果：阶段映射基线5/5→5/5（100%，无变化）；候选方案基线3→3（100%覆盖）；测试基线363通过/0失败→本Step未重跑代码测试，沿用最近证据363/363；生产代码修改0→0；真实Case0→0；重试0；新增计划产物1；后台异常N/A（没有运行Worker）；开放风险5类。计划状态为“设计完成”，尚未进入“Case已确认”或实现阶段。

自审：占位符扫描0、计划引用的接口在文件拓扑中均有定义、5A.1–5A.5均有任务和累计回归命令；动态临时目录使用 `mktemp`，企业Git只接受服务端已登记的repository_id；未创建分支、未启动服务、未连接企业远端。下一步等待执行方式选择，再按计划从5A.1开始；真实Case仍需单独确认后执行。


## 2026-09-07 PRD-005A 计划审阅修订 Step

用户静态审阅指出6项遗漏；逐项核对仓库后确认6/6成立，未执行计划或修改生产代码。修订计划 `docs/superpowers/plans/2026-09-07-prd005a-code-map.md`：A1/A2/A3 与标准 OTel 父子链路变为 Task 0 硬前置；5A.1 的 Task 2A 创建并运行分阶段 Case runner；新增常驻 daemon、配置、`python -m antisentinel.code_map`、compose 和 `start_local.sh` 装配；Task 5 生成并持久化 `contains`；所有 Case 改为父目录已存在、子目录不存在；明确 005A 事实地图与 PRD-005 的 Sourcegraph/SCIP、全文库、向量库边界。

证据命令：`rg -c '^## Stage' ...` 实际5，`rg -c '^### Task' ...` 实际14，`rg -c '^- \\[ \\] \\*\\*Step' ...` 实际86；runner 创建任务1、后续修改任务5；`rg` 占位符扫描0；范围/装配/前置/contains关键词命中24；`git diff --check`退出0。完整命令与输出在本次会话工具记录中，文档修订后的代码测试未重跑。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据 | 结果 |
|---|---:|---:|---:|---|---|---|
| 计划阶段数 | 5 | 5 | 5 | 0/0% | `rg -c '^## Stage'` | 达标 |
| 计划任务数 | 11 | 14 | ≥14（含新增前置/runner/daemon） | +3/+27.27% | `rg -c '^### Task'` | 达标 |
| Checkbox步骤 | 66 | 86 | ≥86 | +20/+30.30% | `rg -c '^- [ ] **Step'` | 达标 |
| A1/A2/A3独立前置任务 | 0 | 1 | 1 | +1/N/A | Task 0 | 达标 |
| 常驻daemon任务 | 0 | 1 | 1 | +1/N/A | Task 3A | 达标 |
| 5A.1先创建runner | 0 | 1 | 1 | +1/N/A | Task 2A | 达标 |
| contains在5A.2生成 | 0 | 1 | 1 | +1/N/A | Task 5 | 达标 |
| 占位符命中 | 0 | 0 | 0 | 0 | `rg` | 达标 |
| 生产代码修改 | 0 | 0 | 0 | 0 | `git status` | 达标 |

本Step测试总数沿用最近363、通过363、失败0、警告1；本Step真实Case0、重试0、产物3份修订文档、后台异常N/A、持久化读回N/A、t2-t1=N/A。计划状态仍为“设计完成”，不得报告“Case已确认”或“真实运行通过”。


## 2026-09-07 PRD-005A Task 0：A1/A2/A3 与 OTel 前置修复 Step

原因：进入5A.1实现前必须修复历史审计项，并让跨进程任务的Trace真正使用标准OTel父子关系。当前基线：全量363/363、0失败；目标：Task 0 focused 23/23、0失败，A1/A2/A3回归均达标，标准父Trace断言达标；主要风险是兼容既有自定义trace_id属性与SQLite标准ID的迁移；预算120秒、最多2次重试，本次重试0。

变更：新增 `tests/test_cross_process_tracing.py`；扩展 `tests/test_memory_projection.py`、`tests/test_memory_jobs.py`、`tests/test_tracing_metrics.py`；修改 `src/antisentinel/memory/projection.py`（按实际披露turn校验、LLM revision）、`src/antisentinel/memory/jobs.py`（持久化trace carrier）、`src/antisentinel/memory/recorder.py`（消费端extract/child）、`src/antisentinel/tracing/telemetry.py`（W3C注入/提取、OTel真实parent、标准SQLite IDs、force_flush/shutdown）。

失败测试命令：`/Users/xuewentao/miniconda3/bin/python -m pytest -q tests/test_memory_projection.py tests/test_memory_jobs.py tests/test_tracing_metrics.py tests/test_cross_process_tracing.py`，首次实际5 failed、18 passed、0.99秒；保留失败输出于本次会话记录。修复后同一命令实际23 passed、0 failed、0 warning、0.75秒。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---:|---:|---:|---|---|---|
| Task 0 focused 测试通过 | 18 | 23 | 23 | +5/+27.78% | focused pytest | 达标 |
| Task 0 focused 失败 | 5 | 0 | 0 | -5/-100% | focused pytest | 达标 |
| A1 未披露turn被接受 | 1 | 0 | 0 | -1/-100% | `test_memory_projection.py` | 达标 |
| A2 LLM错误revision | 1 | 0 | 0 | -1/-100% | `test_memory_projection.py` | 达标 |
| A3 job trace carrier丢失 | 1 | 0 | 0 | -1/-100% | `test_memory_jobs.py` | 达标 |
| 跨进程真实parent失败 | 1 | 0 | 0 | -1/-100% | `test_cross_process_tracing.py` | 达标 |
| 后台异常 | N/A | N/A | 0 | N/A | 无真实worker Case | 待真实Case |
| 持久化读回 | N/A | N/A | 100% | N/A | 无真实worker Case | 待真实Case |

实际值：修改生产文件4个、新增/修改测试文件4个；focused测试23/23；真实Case0；业务完成/持久化完成及t2-t1=N/A；重试0；后台异常N/A。当前阶段状态仍为“设计完成”，Task 0 focused回归已验证，尚未达到“真实运行通过”或“回归通过”。下一步执行累计全量回归；若失败，停止扩展并定位根因。

Task 0 兼容性修复：Redis 旧 MemoryJob payload 缺少 carrier 时，解码保持 `trace_context=None`，避免旧对象比较回归。针对性命令 `pytest tests/test_worker_redis.py::test_redis_memory_job_queue_recovers_expired_lease_and_acks` 实际1/1通过、0失败、0.04秒；随后 `/Users/xuewentao/miniconda3/bin/python -m pytest -q` 实际368/368通过、0失败、1警告、4.75秒。失败由1降至0，减少1个、下降100%；测试总数363→368，增加5项、增长1.38%。测试集合发生变化，4.75秒相对旧3.55秒的性能比例标记N/A，不作回归结论。

Task 0 当前验证状态：focused 23/23，累计回归368/368；真实 Case 0，业务完成/持久化完成/t2-t1=N/A，后台异常N/A，重试0。A1/A2/A3与真实父Span测试均有刚刚命令证据；Code Map 尚未登记、调度或启动。生产修改仍限于本Task的4个既有文件，计划继续停在5A.1 Case确认之前。

Task 0 最终校正：`MemoryRecorder.record()` 在 `memory.job.enqueue` span 内通过 `TraceContext.from_otel(enqueue_span).inject()` 写入 carrier，后台消费再以该真实 span 为父创建独立 action context；没有把合成 `TraceContext.new()` carrier 当作生产span。校正后 focused 命令实际23/23通过、0失败、0.81秒；累计 `/Users/xuewentao/miniconda3/bin/python -m pytest -q` 实际368/368通过、0失败、1警告、4.41秒。兼容性失败已从1→0（减少1、下降100%）；新增测试总数保持5；真实Case仍0、后台异常/持久化lag仍N/A。下一步仅提交Task 0并暂停等待5A.1 Case确认。


## 2026-09-07 PRD-005A Task 1：领域 DTO、稳定身份与 V1–V4 迁移 Step

原因：为登记/任务/地图后续实现提供稳定身份和不破坏旧数据的迁移边界。当前基线：Task 0累计368/0；目标：Task 1 focused 8/8、旧数据库测试及全量回归失败0；主要风险是SQLite旧V1库升级时重复初始化、`PRAGMA user_version`和现有表行内容变化；预算120秒、最多2次重试，本次重试0。

变更：新增 `src/antisentinel/code_map/{__init__,identity,models,ports}.py`；将 `src/antisentinel/persistence/sqlite_database.py` 从SCHEMA_VERSION=1升级为4，加入V2登记/任务/租约表、V3快照/blob/node/edge/chunk表、V4部署/binding表及逐版本事务迁移；新增 `tests/test_code_map_models.py`、`tests/test_code_map_migrations.py`，更新 `tests/test_sqlite_database.py` 的表/版本断言。

失败测试命令：`/Users/xuewentao/miniconda3/bin/python -m pytest -q tests/test_code_map_models.py tests/test_code_map_migrations.py tests/test_sqlite_database.py`，首次5 failed、0 passed；修复后同命令实际8 passed、0 failed、0.36秒。累计命令 `/Users/xuewentao/miniconda3/bin/python -m pytest -q` 实际373 passed、0 failed、1 warning、5.69秒。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---:|---:|---:|---|---|---|
| Task 1 focused通过 | 0 | 8 | 8 | +8/N/A | focused pytest | 达标 |
| Task 1 focused失败 | 5 | 0 | 0 | -5/-100% | focused pytest | 达标 |
| 全量回归通过 | 368 | 373 | 373 | +5/+1.36% | full pytest | 达标 |
| 全量回归失败 | 0 | 0 | 0 | 0 | full pytest | 达标 |
| schema migration版本 | 1 | 4 | 4 | +3/+300% | migration tests | 达标 |
| 旧incident内容变化 | N/A | 0 | 0 | N/A | migration test | 达标 |
| 真实Case | 0 | 0 | 0（本Step不执行） | 0 | 无外部Case | 待Case |
| 后台异常/持久化lag | N/A | N/A | 0 / 明确记录 | N/A | 无真实worker | 待真实Case |

实际值：新增生产文件4个、修改生产文件1个、新增测试文件2个、修改测试文件1个；focused 8/8；累计373/373；失败0；真实Case0；重试0；迁移产物12个Code Map表；业务/持久化完成及t2-t1=N/A；开放风险5类保持不变。Task 1单测和累计回归已验证，但5A.1仍未进入“Case已确认”或“真实运行通过”。下一步记录提交并执行 Task 2 的调度单测，不启动真实服务。


## 2026-09-07 PRD-005A Task 2：登记、去重、自适应调度 Step

原因：为固定 Commit 入队和后续 Worker 提供持久化调度事实。当前基线：Task 1累计373/0；目标：调度/存储focused 6/6、失败0，首次立即检查、间隔序列、SHA变化重置、同key去重、暂停取消和认证阻断均满足；主要风险是SQLite lease竞争与持久化ISO时间转换；预算120秒、最多2次重试，本次重试0。

变更：新增 `src/antisentinel/code_map/store.py`、`scheduler.py`、`service.py`；新增 `tests/test_code_map_scheduler.py`、`tests/test_code_map_store.py`。实现RepositoryRegistration持久化、check lease、1800→3600→7200→14400秒自适应间隔、SHA变化重置、任务snapshot身份去重、暂停取消、blocked错误和explicit deployment入口。

失败测试命令 `/Users/xuewentao/miniconda3/bin/python -m pytest -q tests/test_code_map_scheduler.py tests/test_code_map_store.py` 首次6 failed、0 passed；第一次实现后2 failed，根因是 `last_change_observed_at` 从SQLite读取为字符串；修正 `_decode_optional` 后同命令实际6 passed、0 failed、0.31秒。累计命令 `/Users/xuewentao/miniconda3/bin/python -m pytest -q` 实际379 passed、0 failed、1 warning、5.14秒。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---:|---:|---:|---|---|---|
| 调度/存储focused通过 | 0 | 6 | 6 | +6/N/A | focused pytest | 达标 |
| 调度/存储focused失败 | 6 | 0 | 0 | -6/-100% | focused pytest | 达标 |
| 全量回归通过 | 373 | 379 | 379 | +6/+1.61% | full pytest | 达标 |
| 全量回归失败 | 0 | 0 | 0 | 0 | full pytest | 达标 |
| 同key逻辑任务重复数 | N/A | 0 | 0 | N/A | scheduler test | 达标 |
| 双调度器同时持有check lease | N/A | 0 | 0 | N/A | scheduler test | 达标 |
| SHA变化后间隔 | N/A | 1800秒 | 1800秒 | N/A | scheduler test | 达标 |
| 真实Case/后台异常/持久化lag | 0/N/A/N/A | 0/N/A/N/A | 0/0/明确 | N/A | 未启动daemon | 待真实Case |

实际值：新增生产文件3个、新增测试文件2个；focused 6/6；累计379/379；失败0；真实Case0；重试0；持久化新增12张表已可被调度存储读写；业务完成/持久化完成/t2-t1=N/A；开放风险5类保持不变。Task 2单测和累计回归已验证，阶段仍未进入“Case已确认”。下一步执行Task 2A runner测试，先不启动真实服务。


## 2026-09-07 PRD-005A Task 3：Worker租约、心跳与fenced publish Step

原因：调度任务必须能在进程崩溃后恢复，同时旧 Worker 不能覆盖新任务或发布错误 ready。当前基线：Task 2累计379/0；目标：Worker focused 4/4、失败0，单全局slot、60秒租约、过期恢复、stale token拒绝和成功发布均满足；主要风险是租约/slot双写及过期判断；预算120秒、最多2次重试，本Step重试0。

变更：新增 `src/antisentinel/code_map/worker.py`，扩展 `src/antisentinel/code_map/store.py` 的 `claim_job`、`heartbeat`、`publish`、`get_snapshot` 和 `StagedMapRows/PublishResult`；新增 `tests/test_code_map_worker.py`。发布事务同时校验 job lease、全局 slot 和 token，旧 lease 返回 `lease_lost`，成功后将 job/attempt/slot 原子收口。

失败测试命令 `/Users/xuewentao/miniconda3/bin/python -m pytest -q tests/test_code_map_worker.py` 首次3 failed、0 passed；实现后实际4 passed、0 failed、0.22秒。累计命令 `/Users/xuewentao/miniconda3/bin/python -m pytest -q` 实际392 passed、0 failed、1 warning、5.60秒；`docker compose config --quiet` 和 `bash -n scripts/start_local.sh` 均退出0。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---:|---:|---:|---|---|---|
| Worker focused通过 | 0 | 4 | 4 | +4/N/A | focused pytest | 达标 |
| Worker focused失败 | 3 | 0 | 0 | -3/-100% | focused pytest | 达标 |
| 活动全局slot并发 | N/A | 1 | 1 | N/A | worker test | 达标 |
| 过期lease恢复attempt | N/A | 2 | 2 | N/A | worker test | 达标 |
| stale token错误发布 | N/A | 0 | 0 | N/A | worker test | 达标 |
| 成功发布状态 | N/A | 1/1 | 1/1 | N/A | worker test | 达标 |
| 全量回归通过 | 379 | 392 | 392 | +13/+3.43% | full pytest | 达标 |
| 真实Case/后台异常/持久化lag | 0/N/A/N/A | 0/N/A/N/A | 0/0/明确 | 0/N/A/N/A | 未启动daemon | 待真实Case |

实际值：新增生产文件1个、修改生产文件1个、新增测试文件1个；focused4/4；累计392/392；失败0；真实Case0；重试0；业务完成/持久化完成/t2-t1=N/A；后台异常N/A；开放风险5类保持不变。Task 3单测和累计回归已验证，daemon真实恢复 Case 尚未执行。


## 2026-09-07 PRD-005A Task 3A：常驻 daemon 与启动装配 Step

原因：调度器/Worker 只有被调用时才运行不足以支持部署；需要独立进程、配置、信号退出和现有本地/Compose启动装配。当前基线：daemon模块0、启动Case0、全量392/0；目标：daemon/bootstrap focused8/8、退出可控、配置默认禁用、Compose解析和shell语法均通过；主要风险是API退出时子进程清理和未来Git reader延迟导入；预算120秒、最多2次重试，本Step重试0。

变更：新增 `src/antisentinel/code_map/config.py`、`daemon.py`、`__main__.py`；新增 `tests/test_code_map_daemon.py`、`tests/test_code_map_bootstrap.py`；修改 `compose.yaml` 增加独立 `code-map` 服务，修改 `scripts/start_local.sh` 支持可选子进程、trap和清理。daemon 当前通过 `RegisteredGitReader` 延迟加载 Task 4 的真实 Git reader，未启动实际进程。

focused 命令 `/Users/xuewentao/miniconda3/bin/python -m pytest -q tests/test_code_map_worker.py tests/test_code_map_daemon.py tests/test_code_map_bootstrap.py` 实际8 passed、0 failed、0.82秒；累计 `/Users/xuewentao/miniconda3/bin/python -m pytest -q` 实际392 passed、0 failed、1 warning、5.60秒；`docker compose config --quiet` 实际退出0；`bash -n scripts/start_local.sh` 实际退出0。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---:|---:|---:|---|---|---|
| daemon/bootstrap focused通过 | 0 | 8 | 8 | +8/N/A | focused pytest | 达标 |
| daemon/bootstrap focused失败 | 0 | 0 | 0 | 0 | focused pytest | 达标 |
| daemon默认启用 | 0 | 0 | 0（默认禁用） | 0 | bootstrap test | 达标 |
| graceful stop未运行tick | N/A | 0 | 0 | N/A | daemon test | 达标 |
| Compose解析 | N/A | 1/1 | 1/1 | N/A | docker compose config | 达标 |
| shell语法 | N/A | 1/1 | 1/1 | N/A | bash -n | 达标 |
| 全量回归通过 | 379 | 392 | 392 | +13/+3.43% | full pytest | 达标 |
| 真实启动→发现→发布→重启 | 0 | 0 | 1 | 0/-100% | 未执行CLI | 待Case确认 |

实际值：新增生产文件3个、修改部署脚本2个、新增测试文件2个；focused8/8；累计392/392；失败0；真实Case0；重试0；产物5个；后台异常N/A；持久化lag N/A。Task 3A单测、Compose和shell检查已验证，但真实启动恢复链路仍未运行，阶段状态不能标记为“真实运行通过”。下一步等待用户确认 C1 真实 Case 后执行。

Task 2A Case 准备校正：在请求用户确认 C1 前，补齐 runner 的真实 Worker lease 恢复路径。受控流程为 crashed Worker 领取、时钟前进61秒、recovered Worker 领取 attempt=2、旧 token publish 返回 `lease_lost`、新 token publish 成功；scheduler-only 假构建仍不生成地图节点。focused 命令 `/Users/xuewentao/miniconda3/bin/python -m pytest -q tests/test_code_map_case_runner.py tests/test_code_map_worker.py` 实际9 passed、0 failed、0.22秒；累计 `/Users/xuewentao/miniconda3/bin/python -m pytest -q` 实际392 passed、0 failed、1 warning、9.18秒。真实Case仍0，未启动CLI、daemon、Git远端或外部服务。下一步等待 C1 Case 确认，确认后才执行 runner CLI。


## 2026-09-07 PRD-005A C1：调度与 Worker 租约真实 Case

Case：5A.1 scheduler + Worker lease recovery。范围：Task 0–3A 的本地受控链路；输入为 `tests/fixtures/code_map/v1` 的2个文件、355字节，假Git固定完整SHA、两个同仓库调度器、崩溃与恢复Worker。运行命令：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_map_case.py --case scheduler --fixture tests/fixtures/code_map/v1 --output /tmp/antisentinel-prd005a-case-parent.rACSxf/scheduler-3 --clock controlled --timeout 120 --retry-index 2`。产物：`report.json`、`code-map.sqlite`、`traces.jsonl`，目录 `/tmp/antisentinel-prd005a-case-parent.rACSxf/scheduler-3`；失败尝试保留在 `/tmp/antisentinel-prd005a-case-parent.WVpAIK/scheduler-1`。

运行状态：业务完成=true；持久化状态=SQLite读回成功；真实产物中ScanJob=`succeeded|attempt=2`、MapSnapshot=`ready|published_generation=1`、SQLite Trace spans=1、JSONL Trace=1；恢复校验为旧token发布失败、新token发布成功；后台异常0；`case_pass=true`。t1=1788744975503.266ms、t2=1788744975505.531ms、t2-t1=2.26ms。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---:|---:|---:|---|---|---|
| 输入文件/字节 | 0/0 | 2/355 | ≥1/≥1 | +2/+355 | report.json | 达标 |
| 同SHA逻辑任务 | 0 | 1 | 1 | +1/N/A | report + SQLite | 达标 |
| 前5次调度间隔 | N/A | 1800/3600/7200/14400/14400秒 | 精确匹配 | 5/5 | report.json | 达标 |
| 同仓库双调度器重复领取 | N/A | 0 | 0 | N/A | recovery_checks | 达标 |
| Worker恢复attempt | N/A | 2 | 2 | N/A | SQLite | 达标 |
| stale Worker错误发布 | N/A | 0 | 0 | N/A | hard_gates | 达标 |
| 新快照发布 | 0 | 1 | 1 | +1/N/A | SQLite | 达标 |
| 节点/边/Chunk输出及持久化 | N/A | 0/0/0 | 0/0/0（scheduler-only） | 0 | report.json | 达标 |
| 完整性检查/失败 | 0/0 | 2/0 | ≥2/0 | +2/N/A | report + SQLite | 达标 |
| Trace持久化/后台异常 | 0/N/A | 1/0 | ≥1/0 | +1/N/A | SQLite + JSONL | 达标 |
| 持久化lag | N/A | 2.26ms | 明确记录 | N/A | report.json | 达标 |
| Case重试 | 0 | 2 | ≤2 | +2/预算100% | Case目录 | 达标 |

第1次运行失败，根因是 runner 错把另一仓库的正常并行check当成同仓库互斥失败；第2次运行通过但发现Trace只写JSONL、SQLite spans=0；第3次运行在同一输入下通过，且SQLite spans=1。失败输入和产物已保留，未超过初次+2次重试预算。随后 focused 命令 `/Users/xuewentao/miniconda3/bin/python -m pytest -q tests/test_code_map_case_runner.py tests/test_code_map_scheduler.py tests/test_code_map_worker.py` 实际13 passed、0 failed、0.77秒；累计 `/Users/xuewentao/miniconda3/bin/python -m pytest -q` 实际392 passed、0 failed、1 warning、6.13秒。

C1状态：`Case 已确认` → `真实运行通过` → `回归通过`。5A.1整体仍未标记为完成或用户review通过：daemon入口的“启动→真实Git发现→发布→停止重启”依赖 Task 4 `SubprocessGitReader`，当前尚未接入，属于跨阶段依赖；不在未确认时把该依赖提前实现。下一步等待用户review或确认调整阶段边界。


## 2026-09-07 PRD-005A Task 3B：固定 Commit Git reader 前置 Step

用户确认将最小Git读取前移至5A.1，仅支持 daemon Git Case；AST、Chunk、符号与关系仍留在5A.2。当前基线：399前为392/0、daemon真实Git Case0；目标：本地bare远端固定SHA、工作区修改/force-push后历史对象可读、预算和SSH地址白名单可验证；主要风险是bare cache污染、shell插值、scp SSH地址绕过；预算120秒、最多2次重试，本Step重试0。

变更：新增 `src/antisentinel/code_map/git_reader.py`、`tests/test_code_map_git_reader.py`；更新 `daemon.py` 以登记信息延迟构建reader、`scheduler.py` 支持登记式同步、runner 增加daemon Git Case；计划、设计和Case提案同步前置边界。reader只使用固定argv、bare cache文件锁、fetch/rev-parse/update-ref/ls-tree/cat-file；不checkout、不执行仓库文件、hook、filter、submodule或LFS。

失败测试：Git reader首次3 failed（模块不存在）；实现后3/3通过。补充 scp SSH未登记主机测试首次1 failed（被误判本地路径），修复后 `pytest tests/test_code_map_git_reader.py tests/test_code_map_daemon.py tests/test_code_map_case_runner.py` 实际14 passed、0 failed、2.78秒。累计 `/Users/xuewentao/miniconda3/bin/python -m pytest -q` 实际399 passed、0 failed、1 warning、8.51秒。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---:|---:|---:|---|---|---|
| Git reader focused通过 | 0 | 14 | 14 | +14/N/A | focused pytest | 达标 |
| Git reader focused失败 | 4 | 0 | 0 | -4/-100% | focused pytest | 达标 |
| 工作区修改后固定Commit内容变化 | N/A | 0 | 0 | N/A | git reader test | 达标 |
| force-push后pin对象可读 | N/A | 1/1 | 1/1 | N/A | git reader test | 达标 |
| 超限blob读取 | N/A | 0 | 0 | N/A | budget test | 达标 |
| 未登记scp SSH主机允许数 | N/A | 0 | 0 | N/A | allowlist test | 达标 |
| 全量回归通过/失败 | 392/0 | 399/0 | 399/0 | +7/+1.79% | full pytest | 达标 |
| 真实Git daemon Case | 0 | 1 | 1 | +1/N/A | C1D report | 达标 |

实际值：新增生产文件1个、新增测试文件1个、修改生产文件2个、修改runner1个；focused14/14；累计399/399；失败0；真实Case1；重试0；输出产物包含bare remote、service cache、SQLite、Trace和4份日志；后台异常0；开放风险中企业Git运行身份仍未验证。


## 2026-09-07 PRD-005A C1D：daemon 固定Commit真实 Case

Case：5A.1 daemon Git子Case。输入：隔离bare远端中1个Python文件、39字节，服务端登记local remote、完整Commit `56dfcf43fea5c927101d65527dd70eec88257f0d`。运行：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_map_case.py --case daemon --output /tmp/antisentinel-prd005a-daemon-parent.k7XUV4/daemon-3 --clock real --timeout 120 --retry-index 2`。产物路径：`/tmp/antisentinel-prd005a-daemon-parent.k7XUV4/daemon-3`，包含 `daemon.sqlite`、`storage/observability/code-map-traces.jsonl`、bare cache和first/restart日志。

运行状态：独立daemon进程启动，发现登记ref、pin固定SHA、创建1个job、发布1个scheduler-only ready快照、SIGTERM退出并重启后读回。SQLite：job=`succeeded|attempt=1`，snapshot=`ready|generation=1`，span=3；Trace JSONL 3行，root=`code_map.tick`，scheduler/worker同trace_id且parent均指向root；4份stdout/stderr日志总字节0。业务完成=true、持久化状态=true、后台异常0、恢复校验=true、`case_pass=true`；t1=1788745448834.445ms、t2=1788745448912.897ms、t2-t1=78.45ms。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---:|---:|---:|---|---|---|
| 输入文件/字节 | 0/0 | 1/39 | ≥1/≥1 | +1/+39 | report.json | 达标 |
| 固定SHA与已发布SHA匹配 | 0 | 1/1 | 100% | +1/N/A | SQLite + show-ref | 达标 |
| 已发布任务/快照 | 0/0 | 1/1 | 1/1 | +1/+1 | SQLite | 达标 |
| 进程重启后持久化读回 | 0 | 1/1 | 1/1 | +1/N/A | report.json | 达标 |
| Trace root/child关联 | 0 | 3/3 | 3/3 | +3/N/A | SQLite spans | 达标 |
| 孤立子Span/后台异常 | N/A/N/A | 0/0 | 0/0 | N/A | Trace + logs | 达标 |
| 完整性检查/失败 | 0/0 | 5/0 | ≥5/0 | +5/N/A | report.json | 达标 |
| 持久化lag | N/A | 78.45ms | 明确记录 | N/A | report.json | 达标 |
| Case重试 | 0 | 2 | ≤2 | +2/预算100% | daemon Case目录 | 达标 |

第1次运行虽然`case_pass=true`，但restart probe固定等5秒造成164个span与5082.18ms lag；第2次缩短probe后仍发现scheduler/worker为独立root trace；第3次以 `code_map.tick` parent运行并通过全部硬门槛。三次均保留产物，最终官方结果为daemon-3；无更多重试。C1D状态：`Case 已确认` → `真实运行通过` → `回归通过`。

5A.1累计状态：Task 0–3B 单测、C1和C1D真实Case、全量回归均有证据；当前等待用户review，未进入5A.2。生产/测试累计文件数、性能五轮基线、企业Git实际运行身份和真实Python地图仍是后续阶段风险，不能由scheduler-only快照替代。

用户回复“继续”，确认5A.1 review通过并同意进入5A.2。5A.1状态转换：`用户 review 通过`；证据为C1/C1D报告、累计399/399回归及用户继续指令。5A.2范围仅为冻结fixture、Python AST符号、Chunk、contains与最小快照；C2真实Case尚未确认或执行。


## 2026-09-07 PRD-005A 5A.2 最小地图准备 Step

原因：实现固定Git blob到Python结构事实的最小投影，并为C2真实Case准备同一runner。当前基线：399/399、C2=0；目标：parser/snapshot/builder focused14/14、累计回归失败0；主要风险是装饰器行范围、CRLF字节Chunk、Worker遗漏builder rows和SQLite原子读回；预算120秒、最多2次重试，本Step重试0。

变更：新增 `python_parser.py`、`snapshot_builder.py`、`test_code_map_parser.py`、`test_code_map_snapshot.py`、`test_code_map_builder.py`；扩展Store原子写入nodes/edges/chunks、Worker接受BuildOutput、runner增加 `snapshot` C2入口。最小范围仅普通/async/嵌套函数、类方法、`contains`、字节Chunk和语法错误显式化；不含calls/imports/inherits、全文检索、Evidence或模型Context。

失败测试：parser/snapshot首次4 failed（模块不存在）；builder首次1 failed（模块不存在）；Worker builder rows首次1 failed（Worker把BuildOutput当MapSnapshot）。修复后 `pytest tests/test_code_map_case_runner.py tests/test_code_map_parser.py tests/test_code_map_snapshot.py tests/test_code_map_builder.py` 实际14 passed、0 failed、3.16秒；累计 `/Users/xuewentao/miniconda3/bin/python -m pytest -q` 实际406 passed、0 failed、1 warning、16.01秒。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---:|---:|---:|---|---|---|
| 最小地图focused通过 | 0 | 14 | 14 | +14/N/A | focused pytest | 达标 |
| 最小地图focused失败 | 6 | 0 | 0 | -6/-100% | focused pytest | 达标 |
| 全量回归通过/失败 | 399/0 | 406/0 | 406/0 | +7/+1.75% | full pytest | 达标 |
| async/嵌套/方法符号 | 0 | 4/4 | 4/4 | +4/N/A | parser test | 达标 |
| contains边 | 0 | 2/2 | 2/2 | +2/N/A | parser/builder test | 达标 |
| CRLF长行Chunk | 0 | ≥2 | ≥2 | N/A | parser test | 达标 |
| SQLite重开ready读回 | 0 | 1/1 | 1/1 | +1/N/A | snapshot test | 达标 |
| C2真实Case/后台异常/lag | 0/N/A/N/A | 0/N/A/N/A | 1/0/明确 | 未运行runner | 待确认 |

实际值：新增生产文件2个、修改生产文件2个、修改runner1个、新增测试文件3个、修改测试文件1个；focused14/14；累计406/406；失败0；真实Case0；重试0；后台异常与t2-t1=N/A。5A.2仍为`设计完成`，C2 Case 已准备但未确认；不进入5A.3。


## 2026-09-07 PRD-005A C2：最小Python地图真实 Case

Case：固定Commit Git blob → Python AST → Worker → SQLite ready snapshot。输入：隔离bare远端2个Python文件、125字节；固定Commit `e5ecae57caf844fdf8579817a33842bf1502c630`。运行：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_map_case.py --case snapshot --output /tmp/antisentinel-prd005a-snapshot-parent.NIjNMc/snapshot-1 --clock controlled --timeout 120 --retry-index 0`。产物：`snapshot.sqlite`、`snapshot-traces.jsonl`、bare cache与`report.json`，路径 `/tmp/antisentinel-prd005a-snapshot-parent.NIjNMc/snapshot-1`。

运行状态：业务完成=true、持久化状态=true、job=`succeeded|attempt=1`、snapshot=`ready|generation=1`、Git pin SHA与snapshot SHA一致；SQLite读回nodes=4、edges=2、chunks=4、spans=1；Trace JSONL 1行；后台异常0、恢复读回=true、`case_pass=true`。t1=1788745977335.105ms、t2=1788745977337.912ms、t2-t1=2.81ms。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---:|---:|---:|---|---|---|
| 输入文件/字节 | 0/0 | 2/125 | ≥2/≥1 | +2/+125 | report.json | 达标 |
| 固定SHA/发布SHA匹配 | 0% | 1/1 | 100% | +100pp | show-ref + SQLite | 达标 |
| 输出nodes/edges/chunks | 0/0/0 | 4/2/4 | 4/2/4 | +4/+2/+4 | SQLite + report | 达标 |
| 持久化nodes/edges/chunks | 0/0/0 | 4/2/4 | 4/2/4 | +4/+2/+4 | SQLite | 达标 |
| 完整性检查/失败 | 0/0 | 11/0 | ≥11/0 | +11/N/A | report.json | 达标 |
| Trace/后台异常 | 0/N/A | 1/0 | ≥1/0 | +1/N/A | SQLite + JSONL | 达标 |
| 持久化lag | N/A | 2.81ms | 明确记录 | N/A | report.json | 达标 |
| Case重试 | 0 | 0 | ≤2 | 0 | report.json | 达标 |
| 全量回归通过/失败 | 406/0 | 406/0 | 406/0 | 0 | full pytest | 达标 |

C2状态：`Case 已确认` → `真实运行通过` → `回归通过`。实际值：本Case输入2文件/125字节、输出10个结构产物、持久化10个结构产物、完整性11项、后台异常0、重试0、产物6类、总墙钟269.70ms、t2-t1=2.81ms、未解决风险3类（完整fixture覆盖、partial语法错误发布、真实仓库性能）。5A.2等待用户review，不进入5A.3。

用户回复“继续”，确认5A.2最小范围 review通过。5A.2状态转换：`用户 review 通过`；证据为C2报告、累计406/406回归及用户继续指令。用户要求真实代码仓保留到最后验收；5A.3–5A.4继续使用隔离fixture，不访问企业或用户真实仓库。


## 2026-09-07 PRD-005A 5A.3 查询核心 Step

原因：先建立已发布快照的只读精确查询，后续 Evidence/Loop 不允许从最新分支回退或跨仓读取。当前基线：406/0；目标：symbol、contains邻居、缺失Commit、scope拒绝 focused2/2，累计失败0；主要风险是Query直接访问未发布或跨repo节点；预算120秒、最多2次重试，本Step重试0。

变更：新增 `src/antisentinel/code_map/query.py`、`tests/test_code_map_query.py`。实现 `QueryScope`、`QueryEnvelope`、get_snapshot、find_symbols、outbound contains邻居；允许仓库白名单、要求ready snapshot、缺Commit返回`snapshot_missing`、无授权仓库返回`scope_mismatch`。

失败测试命令首次2 failed（查询模块不存在）；首次实现后1项查询通过、另1项因测试缺少QueryScope导入失败，修正测试后 `pytest tests/test_code_map_query.py` 实际2 passed、0 failed、0.17秒。累计 `/Users/xuewentao/miniconda3/bin/python -m pytest -q` 实际408 passed、0 failed、1 warning、10.74秒。

实际值：新增生产文件1个、新增测试文件1个；focused2/2；累计408/408；失败0；真实Case0；重试0；后台异常/持久化lag N/A。5A.3仍为`设计完成`：源码blob持久化、Evidence、工具注册、Context/Loop和C3 Case尚未实施；不声称本阶段完成。

5A.3 source blob Step：新增快照内SourceBlob/SourceFile原子写入和 `read_chunk_source(repository,commit,chunk)` hash校验回读；首次builder/query 5项失败，根因是blob循环误放进register事务，修正后focused5/5通过。累计 `/Users/xuewentao/miniconda3/bin/python -m pytest -q` 实际409/409通过、0失败、1 warning、18.18秒。新增/修改生产文件2个、测试文件1个；真实Case0、重试0、后台异常/lag N/A。提交`af92cce`。下一步：SourceEvidenceService、工具注册、Context/Loop与C3。

5A.3 query/tools Step：补 `get_node`、按Chunk byte range+hash的`read_source`、以及启动绑定型5工具工厂；ToolRegistry默认能发现显式导出，但当前应用默认auto_discover=False，Code Map factory尚待应用装配，回合内工具仍保持冻结。focused query/tools/builder7/7通过；累计`pytest -q`实际411/411通过、0失败、1 warning、18.52秒；真实Case0、重试0、后台异常/lag N/A。提交`29ca76e`。下一步：应用装配、SourceEvidenceService、Context/Loop和C3。

5A.3 incident scope装配 Step：新增 `incident → repository/snapshot/generation` 服务端绑定与scope读取；`start_session()` 只在scope非空时绑定5个Code Map只读工具。focused6/6通过，累计`pytest -q`实际413/413通过、0失败、1 warning、22.26秒；真实Case0、重试0、后台异常/lag N/A。提交`5a1ea56`。下一步：SourceEvidenceService、Context/Loop和C3。

5A.3 Source Evidence Step：新增SourceEvidenceService，强制Incident/snapshot binding后才读取hash校验chunk并持久化`Evidence(kind=source_code)`；未绑定返回scope_mismatch。focused1/1通过，累计`pytest -q`实际414/414通过、0失败、1 warning、11.94秒；真实Case0、重试0、后台异常/lag N/A。提交`aeaa7ea`。下一步：Context/Loop回灌和C3。

5A.3 source Context Step：ContextBuilder接受受信任SourceContextSlice，最多4段、总UTF-8字节≤32KiB，注入Evidence/repo/snapshot/commit/hash；不从通用工具摘要复制源码。focused1/1通过，累计`pytest -q`实际415/415通过、0失败、1 warning、11.87秒；真实Case0、重试0、后台异常/lag N/A。提交`33ebf68`。下一步：Loop回灌与C3。

5A.3 Loop source回灌 Step：Loop仅在`code_map.read_source`成功且Evidence ID与结构化source_context一致时重建SourceContextSlice，下一模型回合可见；其他工具不进入源码Context。两回合测试1/1通过，累计`pytest -q`实际416/416通过、0失败、1 warning、11.57秒；真实Case0、重试0、后台异常/lag N/A。提交`137cf6a`。checkpoint恢复与C3仍待实现。

5A.3 checkpoint source引用 Step：RuntimeSnapshot保存Evidence/repo/snapshot/commit/path/hash，明确不保存源码content；Loop保存时从当前SourceContextSlice生成该清单。focused checkpoint/runtime9/9通过，累计`pytest -q`实际417/417通过、0失败、1 warning、11.80秒；真实Case0、重试0、后台异常/lag N/A。提交`41dbddc`。resume重新验证与C3仍待实现。

5A.3 resume rehydrate Step：RuntimeEngine/Loop接受source_context_rehydrator；应用在持有Code Map store与EvidenceStore时注入SourceEvidenceService.rehydrate，恢复首回合只使用重新blob/hash校验成功的片段。focused checkpoint/runtime/application11/11通过，累计`pytest -q`实际418/418通过、0失败、1 warning、18.24秒；真实Case0、重试0、后台异常/lag N/A。提交`7ecd5b1`。下一步：C3 runner和真实Case。


## 2026-09-07 PRD-005A C3：查询、Evidence 与 Loop 真实 Case

Case：固定 snapshot 上执行符号定位→contains邻居→source Evidence→下一模型回合源码Context→最终回答。输入：隔离bare远端2个Python文件、125字节；运行命令：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_map_case.py --case loop --output /tmp/antisentinel-prd005a-loop-parent.Ciib2A/loop-1 --clock controlled --timeout 120 --retry-index 0`。产物：`snapshot.sqlite`、`snapshot-traces.jsonl`、bare cache、`report.json`，路径 `/tmp/antisentinel-prd005a-loop-parent.Ciib2A/loop-1`。

运行状态：job=`succeeded|attempt=1`、snapshot=`ready|nodes=4|edges=2|chunks=4`；Evidence=1、Trace span=1、后台异常0；RuntimeLoop完成，第二模型请求包含source_context，EvidenceRef=1，持久化读回=true，`case_pass=true`。t1=1788748395673.591ms、t2=1788748395673.592ms、t2-t1=0.00ms。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据 | 结果 |
|---|---:|---:|---:|---|---|---|
| 输入文件/字节 | 0/0 | 2/125 | ≥2/≥1 | +2/+125 | report.json | 达标 |
| snapshot nodes/edges/chunks | 0/0/0 | 4/2/4 | 4/2/4 | +4/+2/+4 | SQLite | 达标 |
| Source Evidence | 0 | 1 | 1 | +1 | SQLite | 达标 |
| 下一回合source_context | 0 | 1/1 | 1/1 | +1 | hard_gates | 达标 |
| EvidenceRef | 0 | 1 | 1 | +1 | hard_gates | 达标 |
| Trace/后台异常 | 0/N/A | 1/0 | ≥1/0 | +1/N/A | SQLite/JSONL | 达标 |
| 持久化lag | N/A | 0.00ms | 明确记录 | N/A | report.json | 达标 |
| Case重试 | 0 | 0 | ≤2 | 0 | report.json | 达标 |
| 全量回归通过/失败 | 418/0 | 419/0 | 419/0 | +1/+0.24% | pytest -q | 达标 |

C3状态：`Case 已确认` → `真实运行通过` → `回归通过`。实际值：输出10个结构产物、持久化10个结构产物、完整性11项、后台异常0、重试0、产物6类、未解决风险2类（完整fixture扩展、真实仓库最终验收）。5A.3等待用户review，不进入5A.4。

用户回复“继续”，确认5A.3 review通过。5A.3状态转换：`用户 review 通过`；证据为C3报告、累计419/419回归及用户继续指令。5A.4范围为保守静态关系与增量等价，继续使用隔离fixture。


## 2026-09-07 PRD-005A Task 2A：分阶段 Case runner Step

原因：5A.1–5A.4 每阶段都需要同一输出契约和可复现真实 Case，不能把 runner 延迟到5A.5。当前基线：Task 2累计379/0；目标：runner focused 5/5、失败0，输出目录和 `case_pass` 终态契约明确；主要风险是防止预创建目录造成假失败，以及 scheduler-only 假构建receipt被误称地图ready；预算120秒、最多2次重试，本Step重试0。

变更：新增 `scripts/run_code_map_case.py`、`tests/test_code_map_case_runner.py`、`tests/fixtures/code_map/v1/{README.md,manifest.json}`。runner创建父目录下新子目录、写 marker、统一报告字段，提供 `run_scheduler_case()` 受控时钟入口；main尚未执行真实Case。后续Task 5/6/9/10将扩展同一runner，不再创建第二套目录协议。

首次 focused 测试实际2 failed、2 passed；根因是报告在明确持久化失败但数值字段未齐时仍返回None，以及成功测试未显式设置 `integrity_failed=0`。修正后 `/Users/xuewentao/miniconda3/bin/python -m pytest -q tests/test_code_map_case_runner.py` 实际5 passed、0 failed、0.03秒。累计命令 `/Users/xuewentao/miniconda3/bin/python -m pytest -q` 实际384 passed、0 failed、1 warning、5.55秒。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---:|---:|---:|---|---|---|
| runner focused通过 | 2 | 5 | 5 | +3/+150% | focused pytest | 达标 |
| runner focused失败 | 2 | 0 | 0 | -2/-100% | focused pytest | 达标 |
| 已有output目录拒绝 | N/A | 1/1 | 1/1 | N/A | runner test | 达标 |
| 完整报告字段 | N/A | 26/26 | 26/26 | N/A | runner test | 达标 |
| 明确持久化失败时case_pass=false | N/A | 1/1 | 1/1 | N/A | runner test | 达标 |
| 全量回归通过 | 379 | 384 | 384 | +5/+1.32% | full pytest | 达标 |
| 全量回归失败 | 0 | 0 | 0 | 0 | full pytest | 达标 |
| 真实Case/后台异常/持久化lag | 0/N/A/N/A | 0/N/A/N/A | 0/0/明确 | 0/N/A/N/A | 未执行CLI | 待用户确认 |

实际值：新增生产脚本1个、测试文件1个、fixture文件2个；focused 5/5；累计384/384；失败0；真实Case0；重试0；产物4个；后台异常N/A；业务/持久化完成/t2-t1=N/A；开放风险5类。Task 2A 单测与累计回归已验证，真实 Case 状态仍为“待确认”，未推进“Case已确认”。下一步需用户确认 C1 输入/隔离目录/预期，再执行 runner CLI。


## 2026-09-07 5A.4 收口：C4 六类变更与历史代次

修正全仓同名符号表导致的误连，将真实RelationResolver接入SnapshotBuilder，缓存复用中性文件事实/AST并重新绑定目标身份，Git diff记录六类变化。daemon使用真实构建器；增加有界入/出边查询。V5迁移保存不可变发布代次，partial显式retry保留旧绑定与Evidence字节/hash。

先验证失败：跨文件作用域2项失败、partial被错误标ready1项失败、入边查询1项失败；修复后均通过。新增C4真实Git测试覆盖6种变更及partial1→2代次，独立关系标注6条。正式C4命令：`python scripts/run_code_map_case.py --case incremental --output docs/validation/prd005a-stage4-c4/final --timeout 120`。实际6/6等价、608项完整性检查、8快照/9代次、30spans、旧快照变化0、旧Evidence回读true、后台异常0、耗时18.42秒、失败重试0。t1/t2与每项量化表见[阶段报告](validation/prd005a-stage4-c4/README.md)，不使用历史硬编码数量作为本次证据。

最终回归命令：`python -m pytest tests -q`：431 passed、0 failed、1 warning、52.02秒；基线426→431（+5/+1.17%）。原始输出：`docs/validation/prd005a-stage4-c4/regression-final.txt`。C1/C2/C3累计复跑均通过；C2相对路径首次启动失败，改绝对路径后通过，保留失败目录。源码/测试/runner本轮19文件；正式C4两次增强验证均通过，C4故障重试0；当前性能收益N/A（不同变更输入，未建立五轮容量基线）。

5A.4实现与本地C4验收已收口；5A.5真实代码仓、性能容量和企业权限仍在后续范围，不据本轮标记005A全部完成。

5A.5真实本地仓 Case（失败）：固定当前仓库 HEAD `1a0f039ae9839aa3ce575a68f616396d15871444`，运行 `scripts/run_real_code_map_case.py`，新目录 `/tmp/antisentinel-prd005a-real-parent.UH41bs/local-head`。基线：真实仓库扫描0、任务0；目标：120秒内固定Commit、ready快照、持久化读回、后台异常0。实际运行约116秒后仍在构建，按120秒预算终止；SQLite读回任务1、attempt1、快照0，未发布错误ready。失败根因：真实仓库逐文件启动Git子进程且同步解析，构建器没有总deadline/批量blob读取；当前输入文件/节点/边/Chunk未形成完整产物，均INSUFFICIENT_DATA。

本Case不重试（同一输入连续失败达到预算停止扩展）；失败cache/SQLite保留。C5状态为`真实运行失败`（该值不属于阶段5枚举，表示硬门槛未达标），5A.5不得标记通过。下一步必须先优化批量Git对象读取、构建总deadline和可终止解析，再以同一固定Commit重跑；企业Git Case尚未执行。

### 2026-09-07 PRD-005设计启动

- 用户要求继续，按上一轮建议推进RAG设计，未进入实现。新增PRD、设计草案、研究记录3份，更新索引与本记录2份；生产代码修改0。
- 基线沿用9cc3ef1本会话回归443/443、7警告、17.68秒；retrieval仍为骨架，CodeRAG Case0，性能与成本N/A。长期联调前修复drain线程泄漏，图关系需负例验收。
- 草案明确SQLite全文/事实与独立向量投影、输入hash/模型版本、双库幂等与对账、RRF、图边界和Evidence。三候选方案，推荐SQLite+Qdrant，等待用户选择；阶段5.1–5.5。读取SQLite/Agno/Codex/Qdrant一手资料。
- 新服务启动0、外部Embedding调用0、真实Case0；未改PRD为Ready/Done，未push。

### 2026-09-07 PRD-005 再次启动：基线测试 Step

用户本轮提供的PRD与仓库稿 `diff -q` 返回0，内容一致。HEAD为9cc3ef1429029ce17cdc063c86eb7fec1713af54。命令 `/Users/xuewentao/miniconda3/bin/python -m pytest -q`：443 passed、0 failed、7 warnings、19.51s；历史443/0/7/17.68s，本次通过数差0（0%），耗时+1.83s（+10.35%）。阈值443项全部通过、失败0：443/443=100%。不同运行条件下仅一次耗时，不能判断五轮同fixture中位数≤1.05倍的性能门槛，性能待验证。

计数命令：`/Users/xuewentao/miniconda3/bin/python -c 'from pathlib import Path; print(len(list(Path("src").rglob("*.py"))), len(list(Path("tests").rglob("test_*.py"))))'`，181个生产Python文件、95个测试文件。retrieval含6个骨架文件。真实检索Case0、外部Embedding调用0、新服务启动0、重试0；吞吐、检索P95、成本、后台异常和t1/t2均N/A（未运行检索Case），INSUFFICIENT_DATA。

只读发现：CodeMapQuery接口没有generation参数，SourceEvidenceService已有按诊断绑定读取generation的路径，应复用后者的身份语义；worker的drain使用无超时queue.get且超时分支提前返回，仍须验证连续超时新增线程0。没有修改生产代码，没有推进PRD-005阶段状态。下一步：评审存储方案与草案修订，不开始实施。

### 2026-09-07 PRD-005 存储决策：Milvus

用户明确采用Milvus向量索引；SQLite保留代码事实、FTS5、任务、manifest与Evidence。先以Standalone/Lite验证正确性；生产形态、版本和资源在5.2实施设计中固定。本轮更新PRD、设计、研究及本记录共4份文档，生产代码0、测试代码0、真实Case0、新服务0、Embedding调用0、重试0。

文档校验命令：Python读取PRD与设计，检查两处Milvus决策、5个阶段表行、旧QdrantAdapter已移除且MilvusAdapter存在；实际3/3、失败0、达标率100%。基线明确选择0→1（绝对+1，比例N/A）；阶段5→5（0%）；残留活动旧适配器1→0（-100%）。`git diff --check`用于空白检查。成功阈值：决策一致1、阶段5/5、旧适配器0；失败指标：文档校验失败0；回归指标：阶段映射丢失0。

证据复查命令：`rg -n 'Milvus|5\.[1-5]|QdrantAdapter' docs/superpowers/specs/2026-09-07-prd005-code-retrieval-design.md 'docs/prd/PRD-005 Code Retrieval and RAG.md'`。本轮仅文档变更，不重跑全量pytest；前轮443 passed/7 warnings/19.51s为历史，不作为本轮新测试。耗时、容量、后台异常、t1/t2、持久化数均N/A（未启动Case），INSUFFICIENT_DATA。

补充了标准预过滤、确定性VARCHAR主键、SQLite发布权威、读回及重开验证、Lite与Standalone不同验证范围。剩余风险3类：版本能力未锁定、双库恢复未实测、005A线程回收前置未验证。选型确认已记录，不将其等同于整体设计或实现阶段review通过。下一步细化5.1设计及Case；5.2配置在对应实施设计中冻结。

### 2026-09-07 PRD-005 实施计划 Step

原因：用户要求开始推进。当前基线为生产Python 181、测试文件95、retrieval骨架6、全量回归443/443；目标是将已确认Milvus/SQLite边界拆为可执行阶段。新增设计计划1份 `docs/superpowers/plans/2026-09-07-prd005-code-retrieval.md`，覆盖8个任务、30个checkbox步骤、8处文件职责映射。

验证命令：`git diff --check` 返回0；计划自检Python计数实际 tasks=8、checkbox_steps=30、files_touched_mentions=8；占位符扫描仅命中自检说明文本，计划步骤中无TODO/TBD。相关回归 `/Users/xuewentao/miniconda3/bin/python -m pytest -q tests/test_code_map_query.py tests/test_code_map_worker.py` 实际13 passed、0 failed、4 warnings、1.67s，失败0，达标率100%。Milvus依赖探针显示 `pymilvus=False`、`milvus_lite=False`；向量Case0、服务启动0、Embedding调用0、重试0，容量/延迟/恢复N/A，原因是尚未锁定版本且未确认真实Case。

剩余风险3类：5.2版本/schema尚未冻结、双库真实故障点未运行、真实Case需用户确认隔离目录与预期。计划完成不等同于代码实现或阶段通过；下一步选择执行方式并从Task 1开始，遵守测试先行和“针对性测试→用户确认Case→累计回归→review”。

### 2026-09-07 PRD-005 Task 1：SQLite文本投影数据契约

原因：为5.1建立与Memory隔离的代码检索事实投影。基线：生产Python181、测试文件95、全量443/443；目标：Task1 focused 3/3、失败0，manifest未发布时FTS不可见，回归失败0。

按TDD先运行 `/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_code_search_models.py tests/test_code_search_sqlite_store.py -q`，首次收集失败2项（模块缺失，2/2失败，失败率100%），路径修正后实现最小模型/存储。中间一次 focused 为1 passed/2 failed，根因为SQLite FTS5 `MATCH`别名和bm25列权重错误；修复后同命令实际3 passed、0 failed、0.12s。随后全量 `/Users/xuewentao/miniconda3/bin/python -m pytest -q` 实际446 passed、0 failed、7 warnings、18.94s；443→446增加3项（+0.68%），失败0→0。生产文件181→183（+2/+1.10%），测试文件95→97（+2/+2.11%）。

新增文件：`src/antisentinel/retrieval/models.py`、`src/antisentinel/retrieval/sqlite_store.py`、两份focused测试。真实检索Case0、Milvus服务0、Embedding调用0、重试0；输入规模、持久化读回、后台异常、t1/t2和P95均N/A（5.1 runner尚未完成）。Focused验收指标：幂等/代次隔离/manifest门控3/3=100%；失败指标回归失败0；回归指标全量失败0。主要实现风险已修复2项；剩余风险3类：FTS规范化尚未独立冻结、真实Case未运行、全量耗时尚未建立五轮fixture基线。

Task1代码与测试完成，但5.1阶段状态仍为“设计完成”：缺少用户确认的真实Case，不能推进到“真实运行通过”或“回归通过”阶段状态。

### 2026-09-07 PRD-005 Task 2：精确标识符与 FTS5/BM25

原因：让错误码、中文问题和完整符号在固定 Scope 内可检索。基线：Task1累计 focused 3/3、全量446/446；目标：Task2 focused新增2/2、失败0，精确标识符优先，特殊字符不改变SQL语义，回归失败0。

TDD 首次命令 `/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_code_keyword_retrieval.py -q` 因 `KeywordRetriever` 缺失收集失败1项；实现 `query_exact`、安全 FTS 词项表达式和稳定去重后，实际2 passed、0 failed、0.19s。累计 focused 命令实际8 passed、0 failed、0.24s；全量命令实际451 passed、0 failed、7 warnings、20.02s。测试总数448→451增加3（+0.67%），失败0→0；生产Python文件183→184（+1/+0.55%），测试文件97→98（+1/+1.03%）。

真实检索Case0、Milvus服务0、Embedding调用0、重试0；输入规模、输出/持久化读回、后台异常、t1/t2、P95均N/A（runner虽已实现但未执行真实Case）。成功指标：focused 8/8=100%、精确/中文/错误码/跨Scope/特殊字符断言5类均有覆盖；失败指标回归失败0；回归指标全量失败0。剩余风险：中文分词策略尚未在冻结数据集上测量、真实 Case 未确认、性能五轮基线未建立。

### 2026-09-07 PRD-005 Task 3：5.1 离线 runner 准备

原因：为5.1提供同一输出契约和可重现隔离目录。基线：真实Case0；目标：runner focused 3/3、失败0，双snapshot重开读回及完整性字段齐全。首次 focused 收集失败1项（runner模块缺失）；实现后 `/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_code_retrieval_case_runner.py -q` 实际3 passed、0 failed、0.11s。累计 focused 8/8、0 failed、0.24s；累计全量451/451、0 failed、7 warnings、20.02s。

新增 `scripts/run_code_retrieval_case.py` 与 `docs/validation/prd005-stage1/README.md`。runner 设计输入2文件/2 snapshot，输出2候选、持久化2文档、完整性检查≥4、后台异常0、业务/持久化完成时间差为明确非负毫秒值；报告缺任一数字字段时 `case_pass=false`。实际 CLI 未运行，故这些 Case 结果均为N/A，不能写真实通过结论。当前文件计数命令实际生产Python184、测试文件99、retrieval文件9；相对初始181/95/6分别为+3（+1.66%）、+4（+4.21%）、+3（+50%）。

阶段状态仍为“设计完成”：单测与回归已达标，真实 Case 必须由用户确认后才能执行。Case 确认后只写入用户指定的全新隔离目录，默认120秒、最多2次重试；执行后将按运行状态、持久化状态、产物路径、数量/关联、后台异常、恢复校验和量化表更新本记录。

### 2026-09-07 PRD-005 5.1 真实 Case：keyword

用户确认后执行固定 Case，首次启动失败：命令直接执行脚本时报 `ModuleNotFoundError: antisentinel`，未创建目录/SQLite，失败根因为 runner 未自举 `src/` 路径；按同一输入先新增 CLI 回归测试，首次1 failed，修复入口后该测试1 passed、0 failed、0.13s。未消耗 Case 重试预算。

第1次实际 Case 命令：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_retrieval_case.py --case keyword --output /tmp/antisentinel-prd005-stage1-case1 --timeout 120`。实际 report：输入文件2、输入字节68、业务完成26.66ms、持久化完成43.11ms、lag16.45ms、输出2、持久化2、完整性4、后台异常0、失败0、重试0、产物2、`case_pass=true`。真实产物为 `/tmp/antisentinel-prd005-stage1-case1/report.json` 和 `/tmp/antisentinel-prd005-stage1-case1/code-search.sqlite`。

独立核验命令读取 SQLite：documents=2、manifests_ready=2、fts_rows=2、snapshots=2、memory_records=0，Scope/代次完整性满足4/4；业务运行和SQLite重开持久化均完成。第二次独立重复核查目录 `/tmp/antisentinel-prd005-stage1-case2` 同样输出2/持久化2、`case_pass=true`，不作为失败重试。两次均无后台异常。

真实 Case 量化门槛：输入2/2=100%、输出2/2=100%、持久化2/2=100%、完整性4≥4、后台异常0、失败0、lag16.45ms≥0，全部达标。全量回归最终命令 `/Users/xuewentao/miniconda3/bin/python -m pytest -q`：452 passed、0 failed、7 warnings、16.88s；451→452增加1项（+0.22%），失败0→0。git diff --check 返回0。

5.1阶段状态转换证据：先前`设计完成`，本次真实 Case `case_pass=true` 且回归452/452，当前为`真实运行通过`、`回归通过`；用户 review 尚未确认，不能进入`用户 review 通过`。当前生产Python184、测试文件99、retrieval文件9；Milvus服务0、Embedding调用0（5.1明确不涉及）。剩余风险：Milvus 5.2版本/schema与双库恢复尚未实施，中文分词冻结集及检索P/R/MRR仍待后续阶段。

### 2026-09-07 PRD-005 Task 4：Milvus 适配器与 Embedding 状态

原因：按用户确认的向量索引选型建立独立 Milvus 投影。基线：pymilvus/milvus-lite 未安装、Milvus服务0、向量0；目标：固定版本、契约 focused 3/3、任务与channel状态分离、外部调用仍为0。

官方资料核对：[PyMilvus安装页](https://milvus.io/docs/install-pymilvus.md)示例版本3.0.1，[Milvus Lite](https://milvus.io/docs/milvus_lite.md)支持本地文件URI、CRUD、metadata过滤和持久化，且与Standalone共享客户端API。命令 `/Users/xuewentao/miniconda3/bin/python -m pip install 'pymilvus[milvus-lite]==3.0.1'` 实际安装pymilvus3.0.1、milvus-lite3.2.1及依赖；未启动服务。

TDD首次适配测试因 `VectorPoint`/`MilvusAdapter` 缺失收集失败；实现后 `/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_milvus_adapter.py -q` 实际3 passed、0 failed、0.90s。覆盖确定性VARCHAR point_id、COSINE/FLAT schema、完整Scope filter、维度校验和独立channel blocked状态。生产文件184→186（+2/+1.10%），测试文件99→101（+2/+2.02%）。真实Milvus Case0、外部Embedding0、后台异常N/A。

### 2026-09-07 PRD-005 Task 5：双库对账单测与 5.2 Case 准备

原因：阻止Milvus写入成功但SQLite未确认时错误置ready。首次恢复测试收集失败（跨测试导入路径），随后出现1 failed（Fake对账未按Scope）；修正测试 fixture 和按Scope扫描逻辑后 `/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_retrieval_recovery.py -q` 实际2 passed、0 failed、2.00s。对账报告覆盖 expected/actual/missing/extra/字段差异，只有空差集可标channel ready。

新增 `run_vector_case()` Fake runner 契约，focused测试实际15/15通过、0失败、2.17s；全量回归最终 `/Users/xuewentao/miniconda3/bin/python -m pytest -q` 实际458 passed、0 failed、7 warnings、27.06s，前一基线452→458增加6项（+1.33%），失败0→0。`git diff --check`返回0。

5.2真实Case已写入 `docs/validation/prd005-stage2/README.md`，待用户确认；计划中的真实Lite执行尚未发生。当前文件计数命令实际生产186、测试101、retrieval11；Milvus服务0、向量0、Embedding调用0、真实Case0；容量/延迟/恢复N/A，不能宣称Milvus已接入或通过。

### 2026-09-07 PRD-005 5.2 真实 Case：Milvus Lite

用户确认后执行三次同一输入，预算120秒/最多2次重试。第1次目录 `/tmp/antisentinel-prd005-stage2-case1` 在重开查询结果组装时失败：collection未load且Milvus Lite search返回嵌套`entity`，报告未生成；修复`load_collection()`和嵌套结果解析后，第2次 `/tmp/antisentinel-prd005-stage2-case1-retry1` 发现SQLite manifest未发布，判为失败；修复runner发布manifest、完整性阈值和动态产物计数后，第3次 `/tmp/antisentinel-prd005-stage2-case1-retry2` 通过。失败目录均保留，未删除或覆盖。

最终Case report实际：pymilvus3.0.1、milvus-lite3.2.1、输入2文件/68字节、输出2、SQLite文档2、ready manifest2、Milvus向量2、ready task/channel各2、完整性检查6/6、重开Scope-a命中1、错误Scope命中0、后台异常0、失败0、产物3、业务完成823.52ms、持久化完成1127.00ms、lag303.48ms、`case_pass=true`。真实产物：`/tmp/antisentinel-prd005-stage2-case1-retry2/{report.json,facts.sqlite,milvus-lite.db}`。

独立核验命令关闭SQLite连接后读取两库，实际 `sqlite_documents=2`、`ready_manifests=2`、`ready_tasks=2`、`ready_channels=2`、`memory_records=0`、`artifact_files=3`、`milvus_rows=2`、`snapshot_a_hits=1`、`wrong_scope_hits=0`；断言全部满足。focused累计命令实际15 passed、0 failed、2.38s；全量回归最终 `/Users/xuewentao/miniconda3/bin/python -m pytest -q` 实际458 passed、0 failed、7 warnings、29.21s；与5.1基线452→458增加6（+1.33%），失败0→0。`git diff --check`返回0。

5.2阶段状态转换证据：`设计完成`→`真实运行通过`→`回归通过`；用户 review 待确认，不能标记`用户 review 通过`。本次只验证Milvus Lite，Standalone断连/重启、容量和生产资源仍待后续Case；外部Embedding调用0，使用固定本地向量，未发送源码。

### 2026-09-07 PRD-005 5.2 Review 修复复验

用户 review 指出 point_id、collection/schema、channel 门控、嵌套结果、对账 Scope、任务审计、重开顺序和 Lite 版本锁定问题。逐项按 TDD 修复：canonical point ID 绑定 document/模型/维度/template/projection；版本组合生成独立 collection；已有 collection 校验字段、auto_id、维度、FLAT/COSINE index；search 强制 SQLite channel store 且非ready拒绝；Milvus Lite `entity` 结果解析；reconcile 拒绝跨Scope；任务增加 attempt、started/completed、duration、error、lease owner 并对未知 task 抛错；runner 先close再reopen；`pyproject.toml`显式锁 `milvus-lite==3.2.1`。

修复过程证据：canonical ID 新测试首次失败1、修复后通过；schema mismatch 新测试首次失败1、修复后通过；channel gate 新测试首次失败1、修复后通过；跨Scope reconcile 新测试首次失败1、修复后通过；close/reopen 顺序新测试首次失败1、修复后通过；任务审计新测试首次失败1、修复后通过。累计 focused `/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_code_search_models.py tests/test_code_search_sqlite_store.py tests/test_code_keyword_retrieval.py tests/test_code_retrieval_case_runner.py tests/test_milvus_adapter.py tests/test_retrieval_recovery.py -q`：21 passed、0 failed、2.71s。

review修复后的真实 Lite Case 命令：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_retrieval_case.py --case vector --output /tmp/antisentinel-prd005-stage2-reviewfix-case1 --timeout 120`。实际输入2/68字节、输出2、SQLite文档2、ready manifest/task/channel=2/2/2、Milvus向量2、完整性6/6、canonical point ID=2且唯一、版本化collection=1、schema dim=3、index=FLAT/COSINE、重开Scope-a命中1、错误Scope命中0、后台异常0、失败0、产物3、耗时1032.25ms、业务完成800.83ms、lag231.42ms、`case_pass=true`。

独立核验命令实际：`case_pass=true`、`sqlite_documents=2`、`ready_manifests=2`、`ready_tasks=2`、`ready_channels=2`、`memory_records=0`、`collections=1`、`collection_versioned=true`、`point_ids_canonical=true`、`milvus_rows=2`、`snapshot_a_hits=1`、`wrong_scope_hits=0`、`artifact_files=3`；全部断言满足。全量回归最终 `/Users/xuewentao/miniconda3/bin/python -m pytest -q`：464 passed、0 failed、7 warnings、22.60s；前一基线458→464增加6（+1.31%），失败保持0。`git diff --check`返回0。

5.2修复状态：`真实运行通过`、`回归通过`；用户 review 待确认。Lite正确性范围已加固，但真实Embedding、Standalone断连/重启、生产容量与资源基线仍未验证，不能标记整体PRD Done或进入5.3。

### 2026-09-07 PRD-005 Task 6：混合召回与统一入口准备

原因：在5.2的Scope/channel门控之上提供keyword/vector/hybrid三模式。基线：Fusion/Service/5.3 Case=0，上一轮全量464/464；目标：固定RRF(k=60)、三模式错误语义、重叠范围去重和回归失败0。

TDD首次运行 `tests/test_code_retrieval_hybrid.py` 因 `CodeRetrievalService` 缺失收集失败；实现Fusion/Service后4项基础测试通过，补充Fake runner后累计 focused `/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_code_search_models.py tests/test_code_search_sqlite_store.py tests/test_code_keyword_retrieval.py tests/test_code_retrieval_case_runner.py tests/test_milvus_adapter.py tests/test_retrieval_recovery.py tests/test_code_retrieval_hybrid.py -q` 实际26 passed、0 failed、5.20s。全量 `/Users/xuewentao/miniconda3/bin/python -m pytest -q` 实际469 passed、0 failed、7 warnings、24.72s；464→469增加5（+1.08%），失败0→0。

实现边界：RRF仅按通道rank贡献，固定k=60；hybrid vector异常返回keyword并标degraded/vector_unavailable；vector-only返回空结果与vector_unavailable；Fusion按path/source_hash及byte范围去重；Service把SQLite channel store传入Milvus。新增/修改生产文件2个（fusion.py、engine.py），runner扩展1个，测试新增1个；当前文件计数命令实际生产Python187、测试文件102、retrieval文件12。

5.3 Case已写入 `docs/validation/prd005-stage3/README.md`，固定4文档、20查询、三模式对照，使用本地固定向量且外部Embedding调用0。真实Lite hybrid Case0、后台异常N/A、P/R/MRR/P95真实结果N/A；等待用户确认后运行，不声称Graph组或整体检索优化达标。

### 2026-09-07 PRD-005 5.3 真实 Case：hybrid

用户确认后运行 `/Users/xuewentao/miniconda3/bin/python scripts/run_code_retrieval_case.py --case hybrid --output /tmp/antisentinel-prd005-stage3-case1 --timeout 120`。首次结果 `case_pass=true` 但暴露评测标签缺陷：vector 只返回canonical point_id，vector P@5/Recall@5/MRR=0；补充Milvus `document_id`字段并新增去重审计后，以同一输入 retry1/retry2 复验，旧目录保留。

最终 retry2 `/tmp/antisentinel-prd005-stage3-case1-retry2` 实际输入4文档/194字节、查询20、keyword/vector/hybrid执行20/20/20、输出4、SQLite文档4、Milvus向量4、ready manifest/task/channel=1/4/1、对账完整性7/7、三模式错误0/0/0、hybrid重复0、P@5/R@5/MRR均0.2/1.0/1.0、后台异常0、失败0、产物3、业务完成1183.64ms、持久化完成1195.50ms、lag11.86ms、`case_pass=true`。报告：[report.json](/tmp/antisentinel-prd005-stage3-case1-retry2/report.json)。

独立核验命令实际：`sqlite_documents=4`、`ready_manifest=1`、`ready_tasks=4`、`ready_channels=1`、`memory_records=0`、`milvus_rows=4`、`canonical_ids=true`、`document_ids=true`、`artifacts=3`、`hybrid_duplicates=0`、三模式错误均0；全部断言满足。focused最终26 passed、0 failed、2.21s；全量回归最终 `/Users/xuewentao/miniconda3/bin/python -m pytest -q`：469 passed、0 failed、7 warnings、16.69s；468→469增加1（+0.21%），失败保持0。`git diff --check`返回0。

5.3阶段状态：`真实运行通过`、`回归通过`；用户 review 待确认。P@5=0.2源于每条查询仅标注1个相关文档且分母固定5，仅作对照记录，不能声称整体检索优化达标。graph、真实Embedding、Standalone和5.5冻结评测仍未完成。

### 2026-09-07 PRD-005 5.3 延迟复验与 document_id 修复

首次hybrid Case虽 `case_pass=true`，但vector指标为0，根因是Milvus结果只映射canonical point_id；TDD新增失败断言后，Milvus schema/payload/DTO加入document_id，vector Recall恢复1.0。随后新增hybrid重复候选审计和逐查询延迟字段，Fake runner失败断言1项后修复。

最终复验命令：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_retrieval_case.py --case hybrid --output /tmp/antisentinel-prd005-stage3-case2 --timeout 120`。实际输入4文档/194字节、query20、三模式20/20/20、输出4、SQLite文档4、Milvus向量4、ready manifest/task/channel=1/4/1、完整性7/7、三模式错误0、hybrid重复0、P@5/R@5/MRR均0.2/1.0/1.0；P50 keyword/vector/hybrid=2.1337/4.2697/6.7399ms，P95=2.4602/5.1481/8.6746ms；业务完成1160.90ms、持久化完成1175.88ms、lag14.98ms、后台异常0、失败0、产物3、`case_pass=true`。

独立核验命令实际 `queries=20`、`mode_errors={keyword:0,vector:0,hybrid:0}`、`hybrid_duplicates=0`、`sqlite_docs=4`、`manifest_ready=1`、`tasks_ready=4`、`channels_ready=1`、`vectors=4`、canonical/document IDs各4唯一、`artifacts=3`、`p95_present=true`；全部断言满足。累计 focused最终26 passed、0 failed、2.21s；全量回归最终 `/Users/xuewentao/miniconda3/bin/python -m pytest -q`：469 passed、0 failed、7 warnings、18.33s；468→469增加1（+0.21%），失败保持0。`git diff --check`返回0。

5.3当前状态：`真实运行通过`、`回归通过`；用户 review 待确认。4文档fixture的P@5=0.2不作为整体效果门槛，graph、真实Embedding、Standalone和5.5冻结评测仍未完成。

### 2026-09-07 PRD-005 Task 6：Hybrid runner与Case确认准备

新增 `run_hybrid_case()`，固定4个同Scope代码文档、20条查询（每类5条），分别执行keyword/vector/hybrid并报告模式输出、错误、P@5、Recall@5、MRR、耗时；`case_pass`仅检查数据完整性和三模式错误为0，不把小fixture效果阈值写成整体优化结论。FakeAdapter测试实际1 passed、0 failed、2.35s；累计focused实际26 passed、0 failed、5.20s；全量回归实际469 passed、0 failed、7 warnings、24.72s。

真实5.3 Case尚未启动：Milvus服务0、外部Embedding调用0、真实Case0；输入/输出/持久化/完整性、后台异常、t1/t2、P/R/MRR/P95均N/A。Case说明见 `docs/validation/prd005-stage3/README.md`，需要用户确认后才运行Lite三模式对照。

### 2026-09-07 PRD-005 5.3 Review 修复复验

用户 review 指出 Service 吞异常、lexical 无 manifest 门控、top_k/candidate_limit 无上限、单通道硬编码60、范围去重顺序依赖和 runner 固定 output_count。逐项按 TDD 修复：仅 TimeoutError/ConnectionError/OSError 降级，lexical manifest 非 ready 返回 lexical_unavailable，强制 top_k≤5/candidate_limit≤30，单通道使用实例 `rrf_k`，Fusion 先建重叠连通分量保留多通道贡献，runner 按实际 unique document_id 计数并记录 duplicate 数。

修复后 focused `/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_code_search_models.py tests/test_code_search_sqlite_store.py tests/test_code_keyword_retrieval.py tests/test_code_retrieval_case_runner.py tests/test_milvus_adapter.py tests/test_retrieval_recovery.py tests/test_code_retrieval_hybrid.py -q`：30 passed、0 failed、1.98s；全量 `/Users/xuewentao/miniconda3/bin/python -m pytest -q`：473 passed、0 failed、7 warnings、15.48s，469→473增加4（+0.85%），失败保持0。

最终真实 Case：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_retrieval_case.py --case hybrid --output /tmp/antisentinel-prd005-stage3-reviewfix-case1 --timeout 120`。实际输入4文档/194字节、query20、三模式20/20/20、unique output=4/4/4、SQLite文档4、Milvus向量4、ready manifest/task/channel=1/4/1、完整性8/8、错误0/0/0、hybrid重复0、P@5/R@5/MRR=0.2/1.0/1.0、P50=3.0048/3.4732/6.3834ms、P95=3.8228/4.0382/6.7037ms、后台异常0、失败0、产物3、业务完成1165.45ms、持久化完成1180.00ms、lag14.55ms、`case_pass=true`。

独立核验命令实际 `case_pass=true`、`query_count=20`、`mode_errors={keyword:0,vector:0,hybrid:0}`、`hybrid_duplicates=0`、SQLite docs=4、manifest/tasks/channels=1/4/1、Milvus rows=4、canonical/document IDs各4唯一、错误Scope=0、P95字段齐全、产物3；全部断言满足。5.3状态保持`真实运行通过`、`回归通过`，用户 review 待确认；P@5=0.2只作小fixture对照，Graph、真实Embedding、Standalone和5.5冻结评测仍未完成。

### 2026-09-08 PRD-005 5.4 代次/预算修复 Step

新增负例 tests/test_retrieval_graph_boundaries.py：初次8 failed/1 passed（0.62s），修复后命令 `/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_retrieval_graph_boundaries.py -q`：9 passed/0 failed（0.38s）。Graph改读read_generation并校验快照身份和发布状态；仅经同文件/包含范围验证的contains可以扩展，calls等语义关系仍禁用并降级。Evidence在预算、绑定generation/commit及候选hash校验后才写入，metadata记录真实byte范围。

失败8→0（-8/-100%），针对性9/9=100%；历史全量基线479，当前全量待跑。真实Case新增0，外部模型调用0。接口修复尚待Runtime集成及累计回归，不推进5.4通过状态。

### 2026-09-08 PRD-005 5.4 Runtime连接与Case加固 Step

Runtime新测试复现checkpoint source_context_refs=0，修复为显式ToolExecutionResult.evidences→Loop证据引用→Context→checkpoint；源码metadata记录代次/字节范围。多调用测试复现5条Evidence超过4片Context限制，Loop执行前按剩余4片/32KiB调整预算并拒绝超限。

命令 `/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_retrieval_runtime_sources.py tests/test_retrieval_graph_boundaries.py tests/test_code_retrieval_graph_evidence.py tests/test_graph_case_runner.py tests/test_runtime_loop.py tests/test_code_map_source_context.py -q`：33 passed/0 failed、1.67s。新增scripts/code_retrieval_graph_case.py用真实PythonAstParser/CodeMap publish、归档读图、检索工具、Runtime、文件checkpoint重新加载和源码hash重算替代手工SQL常量Case。真实Case尚未复跑，单测不能代替真实验收。

### 2026-09-08 PRD-005 5.4 加固后累计真实复验

全量命令 `/Users/xuewentao/miniconda3/bin/python -m pytest -q`：490 passed、0 failed、7 warnings、19.49s；479→490（+11/+2.30%）。随后用subprocess timeout=120串行执行runner的keyword/vector/hybrid/graph四个Case，输出保留在 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-prd005-stage4-hardened-wjk6j5sw`，全部exit0/case_pass=true、重试0。四组耗时35.85/1043.60/1043.92/85.95ms。

Graph新Case输入1文件54字节、真实parser产出2节点/2chunk、扩展1、unresolved1、五个拒绝负例5/5、预算拒绝后Evidence0、完整性13/13、Runtime工具2次、文件checkpoint恢复1、答案引用1、Evidence1、hash重算1、源码39字节范围[15,54)、generation1、当前表扰动不影响归档、t1/t2/差值85.17/85.95/0.78ms。独立新进程检查SQLite integrity=ok、外键错误0、memory_records0、Checkpoint/Attempt/Evidence同ID，结果保存independent-check.json。

本轮代码/runner涉及10文件，测试涉及4文件，生产src文件189、测试文件106。正式Case4、失败0、重试0、Graph稳定产物3（SQLite、checkpoint、report），worker0、外部模型调用0、脚本退出异常0，剩余限制6类：keyword图种子、语义图边未验证、独立查询评测不足、真实Embedding、Standalone/容量、归档全量读取。完整量化表见stage4报告；本地验证可报告真实运行通过/回归通过，用户review尚未确认，不推进5.5。

### 2026-09-08 PRD-005 5.5 指标与冻结输入准备 Step

用户继续确认本地5.4 review后，启动5.5累计评测准备。历史全量490/490；5.5正式Case0。新增evaluation/code_retrieval.py与code_corpus.py、CLI scripts/evaluate_code_retrieval.py、测试tests/test_code_retrieval_evaluation.py及24查询manifest。先测缺模块失败，再测试权限异常/错Scope保留失败行（2 failed），修复后focused11 passed/0 failed（1.16s）。尚未执行正式Milvus评测。

只读命令 `/Users/xuewentao/miniconda3/bin/python scripts/evaluate_code_retrieval.py --preflight`：独立query24（阈值≥20）、有答案20、无答案4、文档82、Git blob10、字节50833、校验错误0。固定commit c89b679db7080b25590ef3cc99e4cb4116f41f95和21ad8faf354c97c83fdc29d7525ba24e81106077，读取Git对象，不读当前工作区源码。manifest SHA256=6473b9ae5f596fb3dc2287a747ad24f97da15165ce7fc77f7a1cd97d2adab3d0。标签来自源码阅读后的assistant建议，尚未经用户review，不能称人工验收真值。

Precision@5宏平均理论上限0.25，默认目标0.80，差-0.55（低68.75%），在执行前明确不可达；不降低门槛。只允许显式diagnostic运行并分别报告execution_pass与case_pass，hybrid_graph/真实Embedding/Standalone保持未验证。三模式五轮为360次测量，不冒充360条独立查询。无基线时性能比较为N/A，未建立5.5性能通过状态。

### 2026-09-08 PRD-005 5.5 准备验证收口（未执行正式Case）

focused命令 `/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_code_retrieval_evaluation.py -q`：12 passed/0 failed、1.41s；全量命令 `/Users/xuewentao/miniconda3/bin/python -m pytest -q`：502 passed/0 failed、7 warnings、21.24s。基线490→502（+12/+2.45%），失败0→0。预检24/24独立query（阈值≥20）、来源/标签引用错误0、源码82文档/50833字节、Precision上限0.25，目标0.80未调整。

本轮新增/修改交付9文件：3个代码模块/脚本、1份测试、manifest及说明2份、stage5报告/计划/日志3份。正式Case0、真实存储产物0、外部Embedding调用0、重试0；后台异常/五轮延迟/Token/成本N/A（未运行正式评测），INSUFFICIENT_DATA。成功指标为query唯一性24/24、标签解析错误0、单测12/12；回归502/502，性能门槛尚无基线。未解决验收条件6类：标签review、Precision可达性、hybrid_graph、真实Embedding、Standalone/容量、五轮性能对照。

已给出具体诊断Case：`python scripts/evaluate_code_retrieval.py --diagnostic --diagnostic-vectors --output /tmp/antisentinel-prd005-stage5-diagnostic-v1 --timeout 120`；待用户确认后用外层120秒timeout运行，完整测量每模式24query×5轮，不冒充独立query数量。质量未通过和各未验证组必须保留，不推进5.5真实运行通过或PRD Done。

### 2026-09-08 PRD-005 5.4 Review P1修复（尚未review通过）

用户明确不认可5.4进入用户review通过，暂停5.5正式运行；此前“继续”不再作为完整5.4验收授权。核对3个P1及返回状态问题，新增tests/test_graph_review_contracts.py首次10 failed（0.29s），修复后10 passed（0.20s）。修改GraphExpander只允许contains并直接拒绝其它关系；graph结果按种子/邻居交替选取、top_k=1优先邻居；rehydrate复用当前incident binding校验generation/commit，缺身份的旧Evidence拒绝恢复；截断传播incomplete，expand_graph统一ToolExecutionResult。

相关累计命令 `/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_graph_review_contracts.py tests/test_graph_case_runner.py tests/test_code_retrieval_graph_evidence.py tests/test_retrieval_graph_boundaries.py tests/test_retrieval_runtime_sources.py tests/test_code_retrieval_hybrid.py tests/test_code_map_source_context.py -q`：36 passed/0 failed、1.71s。旧3条测试依赖默认扫描calls，改为contains范围内的unresolved负例并显式验证calls拒绝；真实Case尚未复跑。默认只读语义明确为源码事实只读、允许验证后的内部Evidence存证。

### 2026-09-08 PRD-005 5.4 P1复验结果（用户review仍待确认）

新增P1相关测试从10 failed→0（-100%），加上旧Evidence缺身份拒绝测试，focused命令 `/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_graph_review_contracts.py tests/test_graph_case_runner.py -q`：12 passed/0 failed、1.24s。全量 `/Users/xuewentao/miniconda3/bin/python -m pytest -q`：513 passed/0 failed、7 warnings、16.63s；502→513（+11/+2.19%），不据不同测试集合耗时推断性能改善。

累计四组本地Case使用新目录 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-prd005-stage4-review-twbdcl53`，外层timeout120秒/每Case、重试0、4/4退出0。Graph fixture220字节/6chunk，keyword实际5种子、checkpoint候选5其中graph1，11个拒绝负例全通过（含4类关系入口拒绝及2个绑定变更恢复拒绝），完整性15/15、Evidence/恢复1/1、hash重算1、预算外写入0、Runtime工具2、t1/t2/lag=74.15/76.78/2.62ms、稳定Graph产物3、脚本异常退出0。原始stdout/stderr和数据库均保留，独立检查写入independent-check.json。

独立结果：checkpoint候选5/graph1、truncated/incomplete均true、Evidence元数据与当前binding的generation/commit匹配、源码39字节[15,54)hash一致、SQLite完整性ok/外键错误0、memory_records0。本轮影响源码4文件、runner1文件、测试4文件、文档4文件，共13；新测试11、正式Case4、失败0、重试0、外部模型调用0、worker0，源Python191/测试文件108。余留范围：用户review、hybrid+graph质量、真实Embedding、Standalone/容量与冻结评测。5.5正式运行继续暂停，不标记5.4用户review通过。

### 2026-09-08 PRD-005 5.5 首次正式本地诊断

用户继续并要求最后引入Ragas，先运行此前列明的24query诊断。命令 `python scripts/evaluate_code_retrieval.py --diagnostic --diagnostic-vectors --output /var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-prd005-stage5-0z324byh/baseline --timeout 120`，外层subprocess timeout120；退出0，execution_pass=true，quality_pass=false，case_pass=false。原始stdout/stderr保留在父目录。

三模式各120次观测（24独立query×5轮），错误/Scope错误/重复均0。keyword P@5/Recall/MRR=0.14/0.55/0.485，无答案误命中0.25；vector=0.08/0.2833333333333333/0.1825，误命中1；hybrid=0.12/0.48333333333333334/0.3283333333333333，误命中1。效果未达标，未修改标签或目标0.80。这里使用未训练哈希向量，不能推断真实Embedding效果；hybrid_graph、真实Embedding、Standalone、人工标签review和性能对照仍未完成。

Ragas调研固定0.4.3：官方源码支持IDBasedContextPrecision/Recall无需模型，但分母与标准P@5不同；Faithfulness需真实response和评估模型。依赖正在隔离venv安装，不改变主解释器。尚未执行Ragas评估，已向用户询问后续模型服务和代码发送范围；无外部模型调用。

### 2026-09-08 Ragas接入及企业仓选择 Step

用户要求最后引入Ragas，并授权查看百度代码仓权限、自行选仓。iCode get_person_repo实际返回27仓；分别check_repo_permission验证两候选read=true。选中仓已在临时隔离目录浅克隆，53文件/39 Go/328586字节，具体仓名与分支/权限清单仅存本地 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-icode-inventory-zy0qme5l/repositories.json`，未提交企业源码到本仓。

首个ragas0.4.3环境因最新版LangChain移除vertexai导入失败，未运行模型；固定LangChain 0.3系列与instructor1.12后新建干净隔离venv，pip check输出No broken requirements found。新增ragas-evaluation可选依赖、ID评估适配器与CLI；TDD缺模块失败后，实际Ragas测试2/2通过（11.16s），含禁止socket连接的离线测试。官方ID指标使用唯一检索ID数作precision分母，不替代固定分母P@5；空集合产生NaN时明确序列化null，不填满分。

对本地双Commit既有report运行Ragas360行，execution_pass=true、quality_pass/case_pass=false；faithfulness等因缺少真实response/授权裁判明确未评估。Go语料加载增加测试，首次因Python解析器处理Go失败，接入已有MultiLanguageParser及Go语法检查后focused13/13通过（1.31s）。企业候选四份纯逻辑Go文件预检63文档/39523字节、24独立query、标签错误0、Precision上限0.24；只进行本地诊断，外部模型调用0。

### 2026-09-08 Ragas最终接入与企业样本诊断结果

用户授权查询iCode仓库权限并自行选仓；27个可访问仓已列出，两个候选单独check_repo_permission read=true，选定仓develop分支SHA和名称仅保存在本地selection-and-validation.json。浅克隆53文件/39Go/328586字节；选择4个纯逻辑Go文件（39523字节）产出63文档、24不同query，语法/源码hash预检错误0。未读取凭证文件作为样本，未运行企业业务程序/Go集成测试，git status为空。

企业诊断命令使用evaluate_code_retrieval.py --diagnostic --diagnostic-vectors与私有manifest，产物 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-baidu-source-poitl0bv/diagnostic`，首次执行/重试0，三模式各120条观测，错误/Scope错误/重复0。SQLite/Milvus63/63并独立逐项核验63组ID/Scope/hash一致。P@5 keyword/vector/hybrid=0.10/0.06/0.10，Recall=0.50/0.30/0.50，精确符号Hit@1=1/0.1667/0.6667，无答案误命中=0.25/1/1；execution_pass=true、quality_pass=false、case_pass=false。哈希向量不代表真实Embedding效果。

Ragas命令（隔离venv）：`python scripts/evaluate_ragas.py --repository <已选本地仓> --report <diagnostic/report.json> --output <ragas新目录>`，真实ragas0.4.3处理360行，错误0/模型调用0；ID Recall均值0.50/0.30/0.50。原报告SHA及语料/标签指纹再次校验，重复/缺失/错误来源拒绝，空集合NaN显式转null。企业Ragas产物在同根目录ragas，samples.jsonl与report.json两份。未生成伪造response或Faithfulness分数。

实际测试：网络阻断Ragas等2/2；补指纹/重复观测/Scope防护后Ragas+检索评测focused17/17（9.31s），最终隔离环境全量518 passed、0 failed、6 warnings、35.48s；513→518（+5/+0.97%），pip check无破损依赖，git diff --check退出0。本轮新增生产模块/CLI2，修改corpus加载1、依赖配置1，测试新增/修改2；5.5诊断3次（原基线/性能复测/企业）、Ragas2次，外部模型调用0。

原本地性能复测中位比值1.17/1.18/1.28均未过1.05门槛，且与Ragas初始化并发、测量条件未隔离；保留原始记录，不据此声称性能通过或确定代码回归。未解决范围：人工标签review、质量门槛、精确符号在hybrid排序、真实Embedding、授权裁判和真实回答、hybrid_graph、Standalone/容量/稳定性能验收。整体PRD未Done。

### 2026-09-08 PRD-005 5.4 SQLite Case 实际执行

用户确认后首次执行 `/Users/xuewentao/miniconda3/bin/python scripts/run_code_retrieval_case.py --case graph --output /tmp/antisentinel-prd005-stage4-e85zBIZE/case --timeout 120`。基线正式Case0→1；输入1文件/31字节、扩展1、unresolved1、Evidence1、hash_verified1、耗时49.16ms、重试0、稳定产物2，report case_pass=true。新建SQLiteCodeMapStore/SourceEvidenceService后恢复1条Evidence，独立重算15字节源码SHA-256一致，范围[16,31)，generation1；PRAGMA integrity_check=ok、foreign_key_check错误0、memory_records0。

运行前针对性测试命令 `python -m pytest tests/test_code_retrieval_graph_evidence.py tests/test_graph_case_runner.py tests/test_code_map_source_context.py -q`：6 passed、0 failed、1.24s。源码实现修改0，当前更新报告与本日志2份；失败0、Case1、重试0、产物2。同步fixture无worker，后台异常观测N/A；runner声明0、t1/t2同值及lag0不能作为异步持久化证据。

本次只确认SQLite fixture扩展/存证/恢复，完整5.4不推进通过状态。剩余限制5类：generation未约束图读取、关系正确性未验证、Evidence预算前写入、scope_leaks常量、Runtime Loop/checkpoint未执行。详细量化表及产物见 docs/validation/prd005-stage4/README.md。后续需要补齐以上门槛，不能凭case_pass布尔值标阶段完成。

本轮最终回归命令 `/Users/xuewentao/miniconda3/bin/python -m pytest -q`：479 passed、0 failed、7 warnings、25.36s；历史479→479，差0，通过率100%，失败0。历史15.87→25.36s（+9.49s/+59.80%），性能五轮门槛待验证，不声称性能通过。`git diff --check`退出0；本轮未修改生产代码。

### 2026-09-08 PRD-005 Task 7：Graph/Evidence 实现准备

原因：进入5.4受控图扩展和源码Evidence回读。基线：Graph/Evidence真实Case=0，focused 30/30，全量473/473；目标：Graph/Evidence/tool/runner focused 37/37，关系越域0，Evidence hash恢复1/1。

新增 `GraphExpander`、`CodeEvidenceAssembler`、Runtime只读 tool definitions，扩展 `SourceContextSlice` 的 byte_start/byte_end；Graph仅扩展同snapshot/repository/commit且resolved、AST/tree-sitter关系，unresolved只计数并标 `graph_degraded`；Evidence最多4片/32KiB，基于既有 `SourceEvidenceService` 绑定和 hash 校验。

累计 focused `/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_code_search_models.py tests/test_code_search_sqlite_store.py tests/test_code_keyword_retrieval.py tests/test_code_retrieval_case_runner.py tests/test_milvus_adapter.py tests/test_retrieval_recovery.py tests/test_code_retrieval_hybrid.py tests/test_code_retrieval_graph_evidence.py tests/test_graph_case_runner.py tests/test_code_map_source_context.py -q`：37 passed、0 failed、2.16s；全量 `/Users/xuewentao/miniconda3/bin/python -m pytest -q`：479 passed、0 failed、7 warnings、15.87s；473→479增加6（+1.27%），失败保持0。`git diff --check`返回0。

5.4 Case说明已写入 `docs/validation/prd005-stage4/README.md`，当前真实Case0、Milvus0、Embedding0、后台异常N/A。等待用户确认后运行 `scripts/run_code_retrieval_case.py --case graph`；未执行前不标记5.4真实运行通过。

### 2026-09-08 PRD-005：代码评测复用已有 Flash 编码器

用户确认复用 `qwen3.7-text-embedding-flash`。新增 evaluation/code_embedding.py 和 tests/test_code_flash_encoder.py，修改 scripts/evaluate_code_retrieval.py；新增 --qwen-flash，与 --diagnostic-vectors 互斥。复用现有请求/响应校验，批量文档编码，查询不缓存；版本指纹包含维度。HTTP attempts 与返回 token 单独记录，未提供的成本和重试归因记 null，不冒充 0。失败产物保留调用计数，客户端正常关闭。

测试红灯：`/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_code_flash_encoder.py -q` 首次 collection error 1（模块尚不存在）。实现后 `python -m pytest tests/test_code_flash_encoder.py tests/test_qwen_embedding.py tests/test_code_retrieval_evaluation.py -q` 为 33 passed、0 failed、2.26s。新增完整 runner 验证后 `python -m pytest tests/test_code_flash_encoder.py -q` 为 3 passed、0 failed、6.55s；MockTransport + 真实 SQLite/Milvus 重开校验，非远程真实 Case。测试验证文档/向量数量一致、模型和维度一致、每个请求计数、240 次查询编码不缓存。

本轮外部模型请求 0；质量与真实 API 延迟 INSUFFICIENT_DATA。待累计回归与真实 Case；没有将 hash 质量结果归因于 Flash。

累计回归：`/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-ragas-clean-wxav1le_/bin/python -m pytest -q` 为 521 passed、0 failed、6 warnings、39.03s；历史518→521，增加3项，通过率100%。阶段仅标记回归通过，远程真实Case 0。本轮修改/新增实现与测试3文件，另更新2份文档。`.env.local` 已有密钥（仅核验存在性）、阿里云北京 MaaS 地址与1024维配置；当前shell未加载。待明确63代码文档和24查询的外发范围后执行FLASH.md中的Case。测试时长不作为检索性能验收证据。

### 2026-09-08 PRD-005 Review 首批：向量权威校验

原因：review指出 Milvus payload 未回查SQLite，以及reconcile忽略缺失字段。修改实现2文件、测试3文件，新增41项用例。目标为15种身份字段删除/篡改全部拒绝、5种SQLite状态变化全部拒绝、真实Milvus篡改无法经service返回、回归失败0。既有ready状态只能作为目前的发布依据，manifest完整性协议、FTS投影隔离仍未完成。

先运行新测试得到缺少verify_vector_rows方法的红灯1失败。实现后focused50 passed、0 failed、10.39s。追加真实Milvus篡改与4项reconcile缺失字段测试后，全量命令 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-ragas-clean-wxav1le_/bin/python -m pytest -q`：562 passed、0 failed、6 warnings、55.76s；521→562增加41，通过率100%。`git diff --check`退出0。阶段回归通过，不代表生产验收通过。明细与剩余风险在 docs/validation/prd005-stage5/AUTHORITY-REVIEW.md。

真实本地Case：`python scripts/run_code_retrieval_case.py --case vector|hybrid --output <独立目录> --timeout 120`，父进程为每Case施加120秒subprocess timeout。产物 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-authority-review-vh6zj967`。vector为2文件/68字节、输出2、SQLite/Milvus2/2、检查6/6、958.62ms；hybrid为4文件/194字节、20查询每模式、每模式80输出/唯一4、SQLite/Milvus4/4、检查8/8、1199.69ms。两Case共6类产物，重试0、后台异常0、错误0，原fixture case_pass均true。完整量化表在AUTHORITY-REVIEW.md。无远程API调用，不标生产就绪、不标RAG质量通过；后续FTS投影指针/manifest协议仍待处理。

### 2026-09-08 PRD-005 Flash真实基线与Ragas复测

用户确认先执行上轮Flash Case。安全加载已有.env.local配置，执行 `python scripts/evaluate_code_retrieval.py --repository <固定Go仓库> --manifest <固定manifest> --diagnostic --qwen-flash --timeout 120 --output <flash-baseline>`（完整路径见docs/validation/prd005-stage5/FLASH.md）。退出0，26596.19ms；4文件39523字节、63文档、24查询×3模式×5轮=360观测，SQLite/Milvus63/63，完整性校验通过、Memory写入0。244HTTP尝试、30032服务报告tokens，成本N/A，无缓存。hybrid Recall .50→.925（+.425/+85%），MRR .410→.81667（+.40667/+99.19%），P@5 .10→.22，exactHit@1 4/6→6/6；无答案误命中仍4/4。真实执行通过，整体质量未通过，case_pass=false。

串行运行隔离Ragas环境 `python scripts/evaluate_ragas.py --repository <固定Go仓库> --report <flash-baseline/report.json> --output <flash-ragas>`，退出0，360观测invalid0，hybrid/vector IDPrecision .183333、IDRecall .925。答案指标未执行。原始产物位于 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-baidu-source-poitl0bv`。本轮只执行测试并更新文档，未改代码；沿用上一轮562/562回归证据，未重复声称本轮全量回归。

发现原report硬编码real_embedding_not_verified，与本次实际调用证据不符；原始报告保留，FLASH.md注明口径缺陷，其他blocker仍有效。独立Milvus读回首次因未load collection失败，随后补load再验证，不重跑远程Embedding、不增加模型费用。

### 2026-09-08 PRD-005 无答案优化实验v1（未启用）

用户要求继续优化。冻结开发24查询与新查询留出24条（12正12负），沿用63条Flash向量。4次API请求、1129 tokens、2653.02ms，无新增源码发送。开发选择COSINE阈值.38671138882637024后才执行留出：误命中12/12→8/12，但Recall .916667→.833333（-9.09%）、MRR .833333→.75（-10%）、误拒绝0/12→1/12，4项护栏仅1项通过，不启用默认阈值。开发集误命中0不能证明泛化。

实现3个新文件：evaluation/abstention.py、scripts/evaluate_code_abstention.py、tests/test_code_abstention.py，均为离线实验/重放，不改生产检索策略。首次新测试红灯collection error1；新增5项测试后累计评测测试18 passed、0 failed、1.56s。重放命令及完整路径见docs/validation/prd005-stage5/ABSTENTION.md，重放和真实查询结果一致，输入hash记录。产物根目录 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-baidu-source-poitl0bv/abstention-experiment-v1`。质量tradeoff未通过，性能N/A，成本N/A，本轮无裁判调用，不宣称优化成功。

### 2026-09-08 PRD-005 支持性判别准备

用户要求继续。检查现有文本模型为deepseek-chat，api.deepseek.com，与先前批准的阿里云Embedding目的地不同；先完成可review输入再确认发送范围。新增evaluation/support_judge.py、scripts/prepare_code_support_judge.py、tests/test_code_support_judge.py共3文件，协议严格区分支持/未支持/证据不足，不泄露gold标签、不将截断视为缺失事实。

新测试红灯collection error1；实现后 `python -m pytest tests/test_code_support_judge.py tests/test_code_abstention.py tests/test_code_retrieval_evaluation.py -q`：22 passed、0 failed、1.26s（新增4项）。真实本地准备2组各24查询，总240候选出现次数、309316源码字节、14查询截断、6文件产物，位于此前私有语料根目录support-judge-development-v1和support-judge-heldout-v1。两个准备命令均退出0，外部调用0。后24查询已观察，仅作诊断，不宣称盲测。拟48次判别请求与600秒预算见docs/validation/prd005-stage5/SUPPORT-JUDGE.md，等待新服务源码发送范围确认；质量效果INSUFFICIENT_DATA。

### 2026-09-08 支持性判别真实Case v1：输出截断，停止

用户“运行”授权已披露DeepSeek发送范围。按冻结requests.jsonl逐条调用现有兼容接口，temperature0、max_tokens1024、单请求30秒、总600秒、重试0。请求deepseek-chat，服务实际返回deepseek-v4-flash。执行6/48，前5有效supported（9条引用全部精确匹配披露源码）；第6条q06 finish_reason=length、输出1024tokens、JSON不完整，记录incomplete_model_output并停止，剩余42未执行。

实际19155.57ms，prompt41476/completion2920/total44396tokens，费用N/A。产物2文件在 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-baidu-source-poitl0bv/support-judge-run-v1`。execution_pass=false，errors1，retries0，不统计整体无答案收益，不将失败计为拒答。本轮未改代码、不追加模型请求。输出引用缺少长度预算，修复应另冻结协议、先回归同一q06再扩展；详细证据在SUPPORT-JUDGE.md。

### 2026-09-08 支持性判别v2：移除max_tokens后续跑

用户明确要求取消tokens上限，v2请求省略max_tokens，保留服务端默认限制；冻结prompt不变，首跑失败q06然后继续，跳过前5有效查询。q06完成1230tokens、finish=stop，原截断故障修复。本轮10请求9有效，q15出现invalid disclosed quote：一条152字符引用不存在且不是空白差异，另一条684字符引用有效；不丢弃坏引用，不伪造通过，按既定规则停止。

耗时37256.53ms，prompt40093/completion5803/total45896tokens；累计16请求、14有效不同查询、总90292tokens，尚未完成48条，费用N/A。原始产物2文件在私有语料根目录support-judge-run-v2，自动重试0，用户授权q06重跑1。execution_pass=false。此次只改变实际请求参数和文档，不修改生产校验规则，不重跑已通过查询。

### 2026-09-08 支持性判别专项整改

核验q15真实错误为模型插入省略号，不是空白/截断问题。改为文档ID+行号、程序提取原文；q01额外query_id改为仅允许值匹配的已知元数据；q13漏status改用DeepSeek官方strict function schema，不猜填缺失字段。发现h06错误前提与检索相关性口径冲突，引入必须有引用的contradicted状态，保留可用于纠正前提的候选。原标签与历史失败结果不改。

新增10项针对测试，红灯缺少函数/元数据拒绝后，最终 `python -m pytest tests/test_code_support_judge.py tests/test_code_abstention.py tests/test_code_retrieval_evaluation.py -q`：32 passed、0 failed、1.49s；compileall与git diff --check退出0。修改source、prepare脚本、tests并新增可复现run_code_support_judge.py，合计4实现/测试文件。官方schema依据和完整运行命令见SUPPORT-JUDGE-REMEDIATION.md。

中途v3 2请求（q01字段兼容错误）、v4 12请求（q13漏status）、v5 35请求全有效与13重验合并，但该混合协议不是最终结果。v6最终同协议完整48/48有效，errors0，58引用校验通过，总99.27秒，371354tokens，自动重试0。原始产物support-judge-run-v3至v6均保存在私有语料根目录，本轮总97请求/733764tokens，成本N/A。

v6离线过滤只移除not_supported，unknown保留原候选且不计成功拒答。无答案16/16→2/16；两组Recall/MRR分别维持.925/.816667和.916667/.833333，有答案误拒绝0/32，unknown2/48。h16反证保留与h21unknown回退仍计误命中。平均新增判别2062.80ms/查询，未过生产性能护栏；当前48样本已观察、标签未review，不能声称盲测泛化或整体RAG质量通过。生产默认检索未启用此过滤。

最终全量 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-ragas-clean-wxav1le_/bin/python -m pytest -q` 为581 passed、0 failed、6 warnings、52.17s；最近全量562→581增加19（前两轮9+本轮10），通过率100%。在v6远程Case完成后串行执行，未干扰其延迟。此次协议真实运行通过、回归通过，不推进生产就绪或用户review状态。

### 2026-09-08 固定语料RAG真实答案链路

用户继续完成RAG。新增evaluation/rag_answers.py、scripts/run_rag_answer_case.py、tests/test_rag_answers.py共3实现/测试文件；复用实时Flash hybrid、SQLite权威校验、预算前Evidence存证、DeepSeek strict带引用答案、本地精确引用映射与真实Ragas0.4.3 Faithfulness/ResponseRelevancy。独立Case不是生产Runtime发布；同模型生成与judge偏差明确记录。

先见新模块缺失红灯collection error1；实现后focused29 passed、0 failed、1.56s；新增预算测试后focused30 passed、0 failed、1.36s。真实pilot(q01/q21)2/2、errors0、7份Evidence重开hash通过、28.05秒；faith1.0/.583333，relevancy.875861/0。12文本请求45000tokens、6Flash请求289tokens，费用N/A，客户端max_tokens未设置。产物私有根目录rag-answer-pilot-v1。完整24条保持同模型/提示词运行于rag-answer-full-v1，预算600秒/200文本请求、父进程630秒。完整Case结果待记录，不宣称质量通过。

完整v1运行发现Ragas只看到裸源码而生成模型看到路径/行号，q08评分负判据因此出现缺文件名/行号的假低分。主动SIGINT暂停（退出-2），保存14已生成答案/13评分/82模型调用。旧Faithfulness诊断失效，原始产物不改。修复disclosed_contexts与测试，使评估candidate JSON和实际生成披露一致；恢复入口校验query、源码内容、Evidence和引用后复用14真实答案，其余10条继续生成、全部24条重新评分。focused30 passed、0 failed、1.50s。v2输出rag-answer-full-v2，预算及token策略不变，不通过重复生成旧答案刷分。

完整v2真实运行：24/24观测、错误0、268.10秒；20有答案Faithfulness .973666、Answer Relevancy .778296（各20定义值），4无答案单列.897436/0（各4定义值）。44唯一Evidence、96披露片段出现次数/197010字节、59引用、24预算校验和24生成/评分披露一致均通过，SQLite integrity=ok。14答案恢复+10新生成，v2新130文本调用398652tokens/58Flash调用3005tokens。pilot+失效v1+v2文本累计224调用721205tokens，费用N/A；失效v1无最终Flash计量，保持N/A。

独立核验并生成rag-answer-full-v2/ANSWERS.md、independent-check.json、category-metrics.json。execution_pass=true，quality_pass/case_pass=false；相关性低于.80筛查目标，同模型judge/已观察样本/未review标签限制保留，未宣称生产RAG完成。完整证据与命令在docs/validation/prd005-stage5/RAG-ANSWERS.md。等待累计回归结果。

最终累计回归：`/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-ragas-clean-wxav1le_/bin/python -m pytest -q` 为584 passed、0 failed、6 warnings、29.57s；581→584新增3项，失败保持0。实际Ragas完成后才运行全量，不污染模型Case延迟。当前答案Case真实运行通过、回归通过，仍不标整体RAG质量或生产通过。

### 2026-09-08 回答相关性与过度推断专项

核验q20旧答案存在无法从局部代码推出的“不重放”结论；加入位置/条件/直接行为优先的focused实验提示词，默认baseline不变。runner增加保留原context但重新生成答案选项，不向模型提供旧答案/标签。修改3实现/测试文件，针对30 passed、0 failed、1.25s。

5条同context开发Case(q14/q17-20)5/5、errors0、58.75秒、7引用/17Evidence、20context出现/56891字节；Faith .96→.959596，Relevancy .647124→.758047（+17.14%），q19单条下降如实保留。30文本调用136130tokens、10Flash调用599tokens，成本N/A，产物rag-answer-focused-v1。q20删除了重放断言，但聚合作用域仍有歧义；加入范围约束后启动完整24条focused-v2同context实验，未用5条结果冒充整体改善。完整报告见docs/validation/prd005-stage5/ANSWER-FOCUS.md。

focused-full-v2 q05引用145–173行但对应chunk仅29行，本地校验拒绝；共5观测/4已评分/1错误，25文本调用181298tokens、8Flash调用431tokens、57.06秒，execution_pass=false。改通用strict schema为document_id+对应披露行数上限绑定的anyOf分支，禁止服务端生成跨文档行号。新增测试红灯缺anyOf1失败，修复后focused31 passed、0 failed、1.73s。q05单独真实回归1/1、errors0、17.65秒通过，随后重跑完整focused-full-v3；不覆盖历史失败，不拼接有利评分。本轮改动范围累计5实现/测试文件。

完整focused-full-v3：24/24、errors0、44Evidence/49引用、96context/197010字节、314.15秒；144文本调用544834tokens、48Flash调用2811tokens。独立24上下文与baseline逐对象一致，44Evidence hash通过、SQLite integrity=ok。20有答案Faith .973666→.990285，Relevancy .778296→.748641，整体方向未通过；semantic子组.814160→.669164，exact和日志组改善不能掩盖退化。默认baseline不切换，仅保留引用schema修复和focused实验选项。原始产物与对照在rag-answer-focused-full-v3，完整结论在ANSWER-FOCUS.md。

最终全量测试585 passed、0 failed、6 warnings、48.80s，584→585新增1测试；命令为隔离Ragas环境python -m pytest -q，在模型运行完成后执行。本轮总205文本调用907402tokens（含失败与回归），费用N/A。Case执行与回归通过，整体回答优化/生产验收未通过。

### 2026-09-08 回答意图路由与新查询对照

用户继续。新增只依据query和披露源码的保守风格路由：路径、声明标识符、日志字面量用focused，其他用baseline；不读query_id/category/labels。runner支持routed及逐条resolved_style，固定source指纹校验允许新查询复用已验证索引。默认baseline不变。3实现/测试文件改动，新增10测试；首次缺函数红灯，随后41 passed、0 failed、1.65s/2.35s。

新冻结24不同query（6函数/4日志/10自然语言/4无答案），与旧48文本无交集，源仍四文件63文档；manifest hash dc716c3036db0b14af92d6bc577e2fa6cfb5274bed96c31f2cfbc0429feb2d60，模型运行前冻结，preflight错误0。先跑新baseline再同context重生成routed，分别输出answer-routing-baseline-v1与answer-routing-routed-v1，预算每轮600秒/200文本请求、父进程630秒。标签未review，当前结果待实测，不宣称生产提升。详见ANSWER-ROUTING.md。

新baseline已实际完成24/24、errors0；20有答案Faith .996667、Relevancy .801647，各20定义值；4无答案.958333/0，单列。新查询结果不覆盖旧query基线。固定路由在这些实际上下文上选择focused10/baseline14，规则未据评分改变；随后routed运行中，尚不宣称优化收益。

routed完成24/24、errors0；20有答案Faith .967917、Relevancy .805992，较新baseline相关性仅+.004345（+.54%），Faith-.02875。24上下文相同、各46Evidence hash通过、45引用、96context/214500字节。配对query bootstrap(seed42,10000)相关性差值95%区间[-.034013,+.040980]跨0；未切换风格控制组的波动也大于总体微小增益，不证明路由收益，不切换默认baseline。

两轮真实Case分别285.16/280.55秒、各144文本请求，合计1116934tokens；Flash72/48请求，合计6554tokens，费用N/A。第二轮复用context，耗时不可作端到端提速比较。原始报告及paired-comparison.json保留在两私有产物目录。计划转回生产发布/恢复边界整改，不继续围绕同模型小幅评分反复调参。全量回归待记录。

最终累计回归：隔离Ragas环境 `python -m pytest -q` 为595 passed、0 failed、6 warnings、41.04s；585→595新增10项，通过率100%。真实模型Case结束后才执行全量。Case执行/回归通过，不推进路由默认启用、生产就绪或用户review状态。

### 2026-09-08 FTS投影隔离与manifest发布

新增LexicalPublication expected/reconcile/active-pointer流程，词法请求固定revision，service显式配置传递，向量权威验证拒绝退役revision。完整doc/FTS对账+digest+FTS integrity-check，事务提交ready/pointer，CAS阻止过期发布者，published_once保证已发布文档不可变，审计表保留尝试结果。FTS OperationalError不再吞，丢失FTS重开拒绝自动造空索引。legacy ready必须显式expected对账，未改历史私有模型索引。

14新增测试：先见begin_manifest缺失红灯1，基础9 passed，初步focused70 passed/4.61s，扩展72 passed/4.37s，全量606 passed/42.36s，再补边界609 passed/36.40s，最后重开FTS拒绝targeted71 passed/4.77s；失败均在预期红灯后修复。15实现/脚本/测试文件变更。完整设计语义、兼容和限制见LEXICAL-PUBLICATION.md。

真实落盘publication+keyword+vector+hybrid+graph共5Case均退出0/case_pass=true。新Case2文件58字节/3投影6文档/当前输出2/发布尝试6（预期失败4）/检查9通过/75.08ms；既有4Case检查4/6/8/15全部通过，耗时65.16/1230.81/1466.61/124.83ms。实际子目录22文件+10日志，产物 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-publication-case-kp5s1l2p`。独立确认active v2、integrity ok。外部模型调用0、重试0，无新增worker。索引版本级channel与worker生命周期、BM25统计隔离和性能仍未完成，不标整体生产就绪。最终累计回归待记录。

最终累计回归命令为隔离Ragas环境python -m pytest -q：609 passed、0 failed、6 warnings、39.90s；595→609增加14测试，通过率100%。compileall及git diff --check通过。阶段真实运行/回归通过，未标用户review或整体生产验收通过。

### 2026-09-09 Embedding Worker租约与恢复

新增版本化队列与run_once/reconcile处理器：Scope+model+dimension+template+projection身份、事务claim、owner/token/过期栅栏、续租、持久化退避/attempt上限、blocked显式恢复、向量先缓存后写入、就绪后对账、缺失记录缓存修复。检索不再借用Scope级ready；旧手工setter不能修改managed任务。未知错误保守阻塞，RPC配置显式有界，真实Qwen需关闭内部重试。

新增15测试从缺模块红灯起验证；4个队列测试先通过，随后8个Worker测试，RPC/恢复/无效向量扩展后针对68 passed、0 failed、11.35s。全量617 passed/52.81s，扩展后624 passed/50.29s、36.48s；最终诊断字段收尾待最后记录。9实现/测试/脚本文件变化，未调用付费模型。

真实首Case子进程在Milvus写入后os._exit(17)，恢复16/16通过、6.72秒。累计最终Case增加ready checkpoint重开为17/17；1文件27字节、3模型版本身份、3ready tasks/3ready版本通道、3向量/3输出，7attempt、4本地替身调用（含认证失败），外部调用0。提交44.92ms、持久化6415.31ms、滞后6370.39ms、总6418.40ms。真实产物 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-worker-final-1sb_19e9`。

同批publication/keyword/vector/hybrid/graph共5累计Case全部通过，耗时62.01/45.71/802.07/1250.19/122.90ms；连同Worker共6Case。父进程均120秒上限，Worker子进程30秒。完整协议、迁移和限制见EMBEDDING-WORKER.md。尚未接常驻调度/多主机/真实模型生产Worker，不标整体生产就绪。

最终诊断字段收尾后累计回归624 passed、0 failed、6 warnings、38.73s（隔离Ragas环境python -m pytest -q），609→624新增15。再次真实Worker Case17/17通过、6336.55ms，提交49.62ms、持久化6333.33ms、滞后6283.71ms；私有产物 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-worker-check-qe47kod_`。独立SQL确认ready错误已清、retryable正确、3缓存向量、integrity ok；独立Milvus与缓存向量3/3一致。第一次只读核验因漏src路径在连接前失败，修正后通过，无新增模型调用。compileall及git diff --check通过。本阶段真实运行/回归通过，不标整体生产或用户review通过。

### 2026-09-10 Fusion与Graph来源一致性

恢复上次中断的只读检查后实现：Fusion代表document/source同源、冲突ID拒绝、未知范围不猜合并、重叠链不传递；Graph输出按字节位置排序的具体chunk候选并限额，engine按chunk去重；Evidence写前校验node/path/range，恢复核验path/node。Graph Runtime Case去掉手工chunk_ids[0]。10实现/脚本/测试文件改动，新增16项测试。

红灯覆盖代表合并和重叠链；逐步targeted17/46/47/49通过，追加重复范围边界后targeted37 passed、0 failed、2.29s。旧临时Ragas环境已不存在，主Python未安装ragas，当前全量明确保留2项可选skip；未调用外部模型或升级依赖。

六累计本地Case worker/publication/keyword/vector/hybrid/graph全退出0；检查17/9/4/6/8/15通过，耗时7484.53/73.04/47.40/883.97/1463.44/112.02ms。持久产物 `/Users/xuewentao/.local/share/antisentinel/cases/source-consistency-1jfy1qnd`；每命令父进程120秒上限。Graph直接传具体chunk读取Evidence，多chunk合并回原文由真实归档blob测试验证。详细语义与未完成项在SOURCE-CONSISTENCY.md；仍未宣称字节范围全量索引、四路质量或生产完成。

最终主Python全量638 passed、2 skipped、0 failed、7 warnings、54.49s；总收集624→640，新增16用例。2项真实Ragas ID测试因隔离环境消失跳过，未伪装通过。最后hybrid/graph真实CLI再验保存在同一持久根的*-final目录；compileall/git diff --check通过。当前环境回归和本地Case完成，完整Ragas依赖复测及生产验收仍未通过。

### 2026-09-10 Runtime incident 检索绑定：实现与针对性验证

新增 IncidentRetrievalTools：每次调用重读 incident binding，仓库白名单、歧义拒绝、归档 commit 校验；生产工具不接收 query_vector，由服务端 encoder 生成；非法 Scope/预算/模式/空查询在外发前拒绝。DiagnosisApplicationService.start_session 支持注入并注册该工具组，默认 None；尚未添加环境配置工厂或常驻调度。

基线全量638通过/2跳过；本步骤目标：正常应用会话1条Evidence引用、非法请求外部调用0、默认配置检索客户端0。针对性命令 `/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_incident_retrieval_tools.py tests/test_graph_case_runner.py tests/test_retrieval_runtime_sources.py tests/test_graph_review_contracts.py -q`：28 passed，0 failed，2.40s。首次测试调用遗漏keyword-only参数，修正；扩展Graph Case新增独立Session产生第2条Evidence，原数量断言失败，改为精确核验两次运行引用并集及每次源码hash，未删除完整性检查。全量及持久目录CLI Case待本轮后续记录。外部模型调用0，生产启动/调度仍未完成。

验证续记：全量652 passed、2 skipped、0 failed、7 warnings、45.37s，新增14测试。六累计CLI Case全退出0，检查17/9/4/6/8/17全通过，0重试；产物根 `/Users/xuewentao/.local/share/antisentinel/cases/runtime-binding-ij1rzwsv`，6份report、6份CLI日志、1份commands。Graph包含正常应用start_session完整检索→Evidence→Context→引用，6文档、2条持久Evidence、1条应用引用、0 Scope泄漏、0后台异常，456.49ms；业务/存储读回差5.92ms。源码/脚本/测试共6文件变更，文档2文件；compileall与diff-check退出0。详细边界、量化表与3组未完成项见RUNTIME-BINDING.md。默认无检索客户端、无外部模型调用；常驻调度和环境配置工厂尚未完成，不宣称整个生产Runtime阶段结束。


### 2026-09-10 常驻检索协调器：实现与本地 Case

沿用前一轮确认的生产Runtime/常驻调度范围，新增默认关闭的环境配置工厂、FastAPI lifespan 启停、串行化检索/索引客户端、白名单generation自动发布与持久队列消费、周期ready reconciliation、投影失败隔离、运行状态API。默认不创建客户端；只有显式启用且配置仓库列表与Milvus URI才在lifespan启动。

针对性命令 `python -m pytest tests/test_retrieval_coordinator.py tests/test_incident_retrieval_tools.py tests/test_embedding_worker.py -q`：34 passed，0 failed，4.43s。新增启动失败清理与独立Case回归后，全量待确认。

真实命令 `/Users/xuewentao/miniconda3/bin/python scripts/run_retrieval_coordinator_case.py --output /Users/xuewentao/.local/share/antisentinel/cases/retrieval-lifecycle-b0c94kz6/lifecycle`：12项检查全部通过，1文件/52bytes/2chunks，首次编码2、重启编码0，2条任务、1条Evidence引用与存储读回，业务/持久化差1.49ms，总1297.74ms，后台异常0、外部模型调用0。SQLite及Milvus Lite真实，模型与Embedding为本地确定性替身。首次脚本CLI因src导入路径遗漏退出，修复后重试1次；原日志和修复日志保留根目录。report中的retries=0指业务内部未重试，CLI重试次数为1，不隐去失败。重启为同进程关闭重建客户端，进程崩溃恢复仍由累计worker Case覆盖。未宣称Standalone或远程模型质量已验收。

最终验证：全量659 passed、2 skipped、0 failed、7 warnings、41.87s（基线652 passed/45.37s，新增7通过项）。全量后累计7 Case全部通过，检查17/9/4/6/8/17/12；内部耗时总10334.65ms，最终lifecycle991.04ms，首启/重建编码2/0。累计运行0重试，早期CLI导入修复重试1次另记。6源码/脚本/测试文件、3文档文件，本轮新增7测试，最终报告7份、累计日志7份、累计命令摘要1份，真实存储目录保留，未解决验收风险归为远程Worker、Standalone、多进程容量、四路质量/Ragas4组。详见RETRIEVAL-LIFECYCLE.md；本地运行/回归通过，未标用户review或生产就绪。


### 2026-09-10 常驻检索关闭边界专项：复现与修复

发现停止后继续下一投影、停止期间真实异常被吞、客户端close失败仍显示stopped三个问题。先增加复现，命令 `python -m pytest tests/test_retrieval_coordinator.py -q -k 'stop_during or close_failure or stop_signal'`：3 failed、0 passed、2.46s；修复后协调器10测试通过、4.42s。停止现在显式stopping，循环/归档chunk有协作中断点；仅内部取消不视为投影错误，真实调度异常始终记failed；关闭错误记failed且stop向调用者报错；无效timeout在改变状态前拒绝。

增加真实SQLite/Milvus受控阻塞Case：编码第一条时stop超时，客户端保持打开；释放后完成当前任务并关闭，重建后只编码剩余一条，再次重建编码0。针对性命令 `python -m pytest tests/test_retrieval_coordinator.py tests/test_incident_retrieval_tools.py tests/test_embedding_worker.py -q`：45 passed、0 failed、6.96s。compileall、git diff --check退出0。当前基线659 passed/2 skipped；全量及持久目录累计CLI验证待追加。本轮外部模型调用0，未增加源码外发授权。

专项最终验证：668 passed、2 skipped、0 failed、7 warnings、34.86s，基线659→668（+9）；最后补全停止投影用例queue替身后单项1 passed/2.14s。8个累计Case全部退出0且case_pass=true，共90检查，内部总11935.69ms，CLI重试0；产物根 `/Users/xuewentao/.local/share/antisentinel/cases/retrieval-shutdown-a4foevrv` 保存8报告/8日志/1命令摘要及真实存储。shutdown Case17检查，1文件52bytes/2chunks、编码1/1/0、2持久任务、1Evidence及恢复引用、0后台异常、外部调用0；业务/持久化观察差26.96ms，总1231.03ms。3源码/脚本/测试文件变更、4文档文件变更；风险仍为远程Worker、Standalone容量、四路质量、Ragas环境4组。compileall/diff-check退出0，详细证据见SHUTDOWN-BOUNDARIES.md；本地真实运行/回归通过，不标用户review或生产就绪。


### 2026-09-10 恢复持久 Ragas 评测环境

旧临时环境消失导致主环境2项Ragas测试一直skip，本轮将隔离环境重建到 `/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3`，不修改主Python。执行 `python -m venv <path>` 后 `bin/python -m pip install -e '.[ragas-evaluation]' pytest` 退出0；`pip check`无依赖冲突。122包元数据及解析后的依赖已保存Case目录与ragas-requirements-py313.txt。

`bin/python -m pytest tests/test_ragas_evaluation.py -q`：4 passed、0 skipped、0 failed、24.29s（首次冷启动）。其中socket.connect拦截验证离线ID指标不访问网络；空集合、错误Scope、重复ID等不成为满分。固定本地语料preflight：82文档/24独立查询/50833bytes，fingerprint匹配、输入错误0；标签仍proposed，P@5上限0.25，不能报告质量通过。全量与固定语料CLI评测继续运行；无外部模型调用，持久产物根 `/Users/xuewentao/.local/share/antisentinel/cases/ragas-restored-z3qwhwym`。

Ragas恢复最终验证：隔离环境全量670 passed、0 skipped、0 failed、7 warnings、43.13s；主环境基线668+2skip→670+0skip，新增测试0，仅补执行2项。依赖pip check无冲突。固定82文档/24查询/三模式5轮，检索与Ragas共360观察，错误0，两库82/82身份核验通过，检索9594.47ms、观察存储差9.87ms；实际Ragas ID precision/recall：keyword0.203571/0.55、vector0.066667/0.283333、hybrid0.10/0.483333，有定义行分母详见报告；quality_pass/case_pass仍false，无外部模型调用或答案评估。8累计Case/90检查全部通过，CLI重试0；详细输出位于ragas-restored-z3qwhwym，8Case报告另加检索/Ragas2报告、版本快照、完整pytest日志。源码修改0，文档/依赖快照4文件，本轮实际缺口减少1组（Ragas环境），其余远程Worker、Standalone容量、四路/答案质量3组待验收。与旧环境测试耗时非同负载比较，未宣称性能回归达标。见RAGAS-ENVIRONMENT.md。


### 2026-09-10 hybrid_graph四路检索接入与首轮诊断

新增显式hybrid_graph模式：keyword/vector先RRF选最多5种子，再做现有contains有界扩展，交错选择top5。新增图候选使用与SQLite文档相同的scope/node/chunk/projection派生ID，可直接进入冻结标签校验和Evidence回读。默认hybrid未改，legacy graph仍keyword种子。冻结Git语料加载保留解析来源，通过真实CodeMap发布归档；评测CLI增加--hybrid-graph，原默认三路仍保留。

首次针对性26通过/1失败：新图发布helper缺少新数据库initialize，已修复；随后27 passed/1.82s。追加真实归档vector-only种子→graph→Evidence用例后针对性44 passed/3.61s。原82文档逐一核验归档内容及hash，2个snapshot。首轮四路各120查询、总480观察、错误0，65/120 hybrid_graph观察实际出现graph候选；hybrid Recall0.483333→hybrid_graph0.458333，下降0.025/2.5百分点，P@5均0.12/MRR均0.328333，不宣称优化成功。q13从0召回到0.5，q18从1降至0；反映固定交错占位的取舍，不改标签掩盖退化。首轮真实Ragas ID评分480行，错误0，答案指标未评估；仅本地诊断向量、外部调用0。完整回归和最终累计Case继续运行。产物根hybrid-graph-h3qoua05。

四路专项最终：676 passed、0 skipped、0 failed、7 warnings、39.99s，新增6项测试。全量后10条CLI（四路诊断、Ragas及8累计Case）全部退出0，重试0；8正确性Case90检查全部通过。最终图归档2snapshot/82节点/54边/82chunks，检索两库82/82，480查询错误/重复0；总8248.97ms、存储观察差8.07ms。Ragas480观察输入错误0，hybrid_graph Recall0.458333，相对hybrid0.483333下降2.5百分点，不改默认、不声称优化通过。6源码/脚本/测试文件，3文档文件；产物hybrid-graph-h3qoua05保留首次/最终报告、8Case报告、10命令日志与完整pytest输出。compileall/diff-check通过；远程Worker、Standalone容量、真实四路/答案质量3组验收仍待完成。详见HYBRID-GRAPH.md。


### 2026-09-10 图候选占位策略诊断实验

默认hybrid与hybrid_graph原interleave不变，新增显式graph_selection=replace_seed/replace_container实验选项，CLI记录策略及execution_key。先写6条选择器测试（实现前ImportError），实现后21 targeted通过；追加服务集成、非法策略与低rank不跳位后37 passed/1.99s。第一轮replace_seed指标与interleave相同，未改善；定位q18：run_once自己包含watch嵌套函数，会在本位置被替换。

第二策略仅允许归档seed_kind为class/module的种子被子节点替换，保留function/method及未知类型；类型从不可变归档读取，不信任当前可变nodes。新增归档类型验证与容器/函数边界后42 passed/2.93s。首次replace_container诊断P@5=.13、Recall=.508333、MRR=.338333，对照hybrid=.12/.483333/.328333；q13新增召回保留，q18从0恢复1。此为已用于诊断的24题，非独立留出集，不能报告总体优化成功。无外部模型调用，原标签与默认策略未改；当前产物graph-selection-yavf856b保留失败改善实验及第二策略结果。全量/最终三策略对照与Ragas继续验证。

图占位实验最终：688 passed、0 skipped、0 failed、7 warnings、44.34s；新增12测试。全量后14CLI（3策略诊断+3 Ragas+8累计Case）全部退出0、重试0，8Case90检查通过。三策略各480观察错误0，原keyword/vector/hybrid逐观察ID及source身份完全一致。replace_container P@5=.13/Recall=.508333/MRR=.338333，相对默认hybrid增加.01/.025/.01，但20可回答题仅1改善、0退化，seed42/20000 bootstrap区间[0,.075]包含0；这是诊断题上的描述性结果，非独立泛化。Ragas新策略ID precision=.108333/recall=.508333，外部模型调用0、答案未评估；所有quality_pass/case_pass仍false。源码/脚本/测试4文件、文档3文件。最终14报告及日志、命令摘要、comparison.json、完整pytest日志保存在graph-selection-yavf856b；compileall/diff-check退出0。默认hybrid与默认interleave均未改变。独立查询/真实Flash质量、Standalone容量、远程Worker、答案验收仍待完成。


### 2026-09-10 用户指定严格配对 A/B：准备

用户明确A=当前hybrid、B=只替换类/模块容器，不改默认。新增独立run_retrieval_ab.py；先冻结计划/策略/实现hash/向量hash/调度顺序，使用同一SQLite/Milvus和同一预计算query vector。24题×5轮×2臂=240测量，另有48预热调用；每查询逐轮换序、总体AB/BA各60对，随机化查询顺序seed42。只测检索延迟，不含编码；使用已授权本地诊断向量，不扩大源码外发。主门槛Recall paired bootstrap95%下界>0，P@5/MRR下界≥0、误命中差上界≤0，P95 B/A≤1.20，错误0；默认策略始终不推广。

针对性 `python -m pytest tests/test_retrieval_ab.py tests/test_graph_selection.py -q`：16 passed/2.38s，覆盖配对完整性、重复观测拒绝、分母一致和重复轮次不增加统计样本数。compileall/diff-check退出0。全量后单独运行A/B，避免测试并发影响时延。24题已用于先前诊断，held_out=false；即使比较指标改善也不视为独立泛化验收。

严格A/B最终：首轮测量完成后对账报错（两个Scope传入单Scope reconcile），原始240观察保留ab/并标无效；修复逐Scope对账、补预热即时存档和两Scope完整回归后5 targeted passed/9.42s。仅1次技术重试，ab-retry1为唯一有效试验；不合并/不挑选第一轮结果。最终693 passed、0 skipped、0 failed、7 warnings、58.82s，新增5测试。

有效A/B检查6/6通过、AB/BA60/60、48预热+240正式调用，原始顺序与计划一致，结果五轮稳定，A/B唯一输出文档51/48，1200结果位置，两库82/82，错误0。A/B P@5=.12/.13、Recall=.483333/.508333、MRR=.328333/.338333、P95=21.87/23.08ms；主指标差95%CI[0,.075]不满足下界>0，统计门槛4/5通过，statistical_gate_pass=false，promote_default=false，quality_pass=false。两臂无答案FPR均1，不能当绝对质量通过。20可回答题只有1改善、0退化；仍非独立留出集。Ragas只复用240正式观察，输入错误0，无新增检索/外部模型调用。总7456.83ms、业务/持久化读回观察差14.79ms；无后台线程所以异常字段null。

产物strict-ab-ea1zf1jl含失败原始记录、有效计划hash/向量/预热/逐查询/报告/audit/Ragas/两库和最终pytest日志。新增脚本+测试2文件、文档3文件，生产检索/default修改0；compileall/diff-check通过。详细见STRICT-AB.md；独立查询/真实Flash、Standalone/容量、远程Worker、答案评测仍待验收。


### 2026-09-10 用户授权自行构造新问题：冻结准备

用户答复“你自己想吧”后构造24条新问题（20可回答/4无答案），不冒充真实日志或独立业务留出。目标含5数据容器、4整类概览、10辅助方法语义、1嵌套函数；显式类概览标类定义，不标子函数。与旧24题规范化文本重叠0，gold(path,symbol)交集0；共享源码且作者已知策略，因此held_out仍false，label_status仍proposed_for_user_review。

冻结manifest位于tests/fixtures/code_retrieval/synthetic-v2/manifest.json，SHA256=5c280c10a43b882c2623c8edd0e7a46520bbceac7704ef1793ba230d4ce8bc03；freeze.json保存策略hash与preflight，82文档/50833bytes/24唯一题，输入错误0，P@5上限.20，尚未读取新题检索结果。engine/graph hash与先前策略一致。新增CLI --repository/--manifest入口，--help退出0；不改默认策略、不外发源码。旧企业语料临时目录缺失，已记录，不擅自改用其他公司仓。后续执行一次新集配对A/B。

新构造题正式验证：targeted5 passed/13.63s，--help成功，policy/default修改0。一次A/B共48预热+240正式调用，重试0，执行检查6/6通过，A/B P@5=.13/.12、Recall=.65/.60、MRR=.435/.425、P95=28.77/33.28ms；Recall差CI[-.15,0]，统计门槛2/5，默认保留A。s06整类概览请求中，B把CodeMapQuery类替换成get_snapshot方法，相关类定义退出top5；0题提升、1题下降，未改题/标签/策略去补反例。

产物synthetic-ab-53o054d8保存冻结输入、计划、240原始观察、预热、两库、audit和Ragas；输入82文档/50833bytes，两库82/82，错误0，总8448.43ms、业务/读回观察差21.89ms，无外部模型调用。新题并非业务独立留出，标签proposed且现已曝光；旧企业临时语料缺失未绕过授权改选。新增CLI接入指定manifest，数据与文档8文件；没有检索代码变更，不重跑全量，保留前轮693项为历史基线。详见SYNTHETIC-AB.md。


### 2026-09-10 用户暂停图优化并核对剩余项

用户明确图候选选择优化pending；保持默认hybrid，不继续该实验。核对PRD、模型/协调器和历史验收，新增OPEN-ITEMS.md，修正PRD仍写未开始实现的过期状态。剩余：真实服务常驻完整链路、Standalone部署/容量、长chunk及byte ranges、查询端到端预算、运维/用量配额、默认hybrid真实质量及答案验收、最终review文档。Ragas已接入/环境已恢复不再列为未实现，但质量仍未通过。此次仅文档4文件，新测试0/Case0/模型调用0/默认修改0；历史全量693通过，不冒充新跑结果。


### 2026-09-10 Standalone准备与连接适配

图策略保持pending，转向Standalone。查阅Milvus官方v3.0.1 Compose及资源/鉴权说明，下载原始配置与hash存Case目录。Docker初始socket缺失；启动Docker Desktop后Engine29.4.1可用，8CPU、8321994752bytes VM内存。独立Compose固定Milvus3.0.1/etcd3.5.25/MinIO2024-12-18，项目/卷隔离、仅127.0.0.1端口、启用鉴权、对象存储随机凭证存0600文件；最终保留8g服务内存上限，不宣称满足生产容量或50GiB SSD指标。

适配器新增token和连接timeout透传，配置读取ANTISENTINEL_CODE_RETRIEVAL_MILVUS_TOKEN；SDK日志在带凭证的Case中禁用，报告不输出凭证。生命周期Case支持显式server URI和两次启动之间hook；Standalone runner将验证鉴权/停服/重启及原应用Evidence恢复。针对性40通过/8.24s，后续生命周期+Milvus25通过/6.63s；全量694 passed、0 skipped、0 failed、7 warnings、55.55s（新增1测试）。Compose config --quiet退出0。真实Standalone尚未运行，镜像下载进行中，预算600秒；业务Case120秒。产物根milvus-standalone-_8k570ob。

Standalone本轮结束记录：真实业务Case未启动，镜像下载尝试2次。首次600秒超时；重试300秒日志显示Milvus层commit/containerd sync I/O错误，写摘要ENOSPC；df一度601MiB空闲，后约2.17GiB，Docker Engine unable to start。下载前漏查磁盘，已补只读预检（本地余量10GiB+Engine/CPU/内存），低空间不访问Docker；3测试通过/0.10s，实际preflight退出1，证据保存preflight-final.json。未执行global prune、重置Docker磁盘、删除其他项目；daemon不可用，未能安全清理未完成镜像层。

配置和脚本已准备，官方上游Compose/hash留存；只有etcd/MinIO镜像完成，Milvus未完成，未固定实际digest、不伪造服务版本/认证/重启通过。业务Case0、外部模型调用0、默认策略修改0；最后全量694通过在补预检前，后续仅3预检测试，不声称697全量通过。产物milvus-standalone-_8k570ob含私有env不可分享。详细状态见STANDALONE-PREPARATION.md，待磁盘与Docker恢复后再继续真实验收。


### 2026-09-10 释放空间后恢复Standalone验收

用户确认已有40G，预检40405377024bytes空闲/Engine29.4.1就绪。原生pull仍在原损坏层digest失败两次，未跳过校验/全局清理。临时独立BuildKit遭Docker Hub鉴权网络超时，builder已移除；宿主机直接获取官方arm64镜像，10层压缩及解压hash全部通过，最后空层401后刷新匿名凭证、复核9已完成层再导入。官方源manifest d0f1645d...、config f78faf2b...，本地导入描述符dbc112bf...，运行Config/10 RootFS diff ID完全一致。

三服务健康启动。v3.0.1官方tag commit658cbd16899bb715a17d4d6f727531a376678ca6与RPC构建号3.0-20260902-658cbd1689对应；修正脚本错误semver字符串断言。SDK将UNAUTHENTICATED包在通用连接异常中，增加异常链识别；两次bootstrap技术失败均保留，--resume-bootstrap复用已有密码，不重置。成功Standalone外层6+Runtime12检查：停服探测10.90ms、重启后Worker修复前2个point及字段保留、1Evidence恢复、编码2→0、后台异常0，业务核心15484.70ms，观察存储差1.92ms。

审计原Case持久attempt发现1次index_reconciliation重试，而原runner常量写0。原报告保留，original-retry-audit.json纠正；get/reconcile补Strong、runner从attempt计重试。新独立collection真实Case12检查通过，2任务各attempt1、重试0、编码2→0、1Evidence，5003.64ms/存储观察差2.33ms；该补充Case只重开客户端，不与含server重启的首Case作时延对比。

最终相关44 passed/7.12s；最终全量701 passed、0 skipped、0 failed、7 warnings、40.03s。本轮新增4测试；相对此前完整694增加7个实际通过项还包含前轮3项预检。源码/脚本/测试5文件、文档6文件。两个有效服务端Case（首次业务bootstrap技术重试2，Strong补充非挑选重试结果），外部模型调用0。测试栈最终运行容器0、卷和数据库保留，仅删除本轮6196828160bytes临时tar，最终约25.4GB空闲。图/engine策略hash与冻结前一致，图优化继续pending、默认hybrid不变。最小权限/TLS、容量/生产部署、真实Flash/答案链路仍未验收，详细见STANDALONE-VERIFIED.md。


### 2026-09-10 查询共享deadline：实现与本地验证

增加上下文隔离的单调时钟deadline，search/expand_graph从锁等待开始共享默认10秒，配置ANTISENTINEL_CODE_RETRIEVAL_QUERY_TIMEOUT。每次Milvus RPC取剩余预算，SQLite busy timeout/进度回调受预算约束，Graph循环及阶段边界检查截止。工具注册不再等待索引客户端锁。预算耗尽返回query_deadline_exceeded，迟到结果不接受。read_evidence仍沿用独立范围/字节预算，未混入可能已提交的存证事务。

Qwen预算内查询用独立AsyncClient与取消计时，预留20%剩余时间供hybrid keyword降级；不修改/关闭后台Worker的同步客户端，不在此路径做自动重试。自定义sync client不能被静默替换成真实网络，需显式async_client_factory。401/403与schema错误仍传播，不当作关键词降级。同步CPU/自定义非协作调用仅能在阶段边界拒绝迟到结果；非可抢占DNS/OS调用不声称实时硬截止，后续真实服务验收仍需覆盖。

已有相关43 passed/15.23s；新增deadline相关组合26 passed/13.96s。首次本地真实HTTPS/SQLite/Milvus Case9检查全部通过，1文件52bytes/2文档/2任务、本机HTTPS调用2、外部模型0、后台异常0；慢响应取消后keyword降级341.42ms（预算400ms），锁等待85.15ms（预算80ms，调度容差门槛300ms），超时锁请求未发HTTP，后续正常查询成功，存证副作用0、线程关闭。产物query-deadline-r1vo9xqu/deadline。补报告输出数与Case回归后，全量及最终累计验证继续运行。

查询预算最终：全量711 passed/0 skipped/0 failed/7 warnings/47.42s（新增10用例）。随后将本地TLS Case升级为每40ms持续分段响应，针对性10 passed/3.37s；最终9累计Case100检查全通过，重试0。deadline-final在6个分段后取消请求，降级333.91ms（400ms配置），锁失败90.11ms（80ms配置/300ms调度容差），2/2输出、2文档/2任务、0Evidence副作用、2本机HTTPS/0外部模型、0后台异常/未关闭线程，总1835.32ms；业务到关闭/存储核验差272.66ms非异步写入延迟。

源码/脚本/测试10文件、文档3文件，compileall/diff-check通过。共享预算与迟到结果拒绝已接入，但不承诺非协作同步回调、C解析、DNS/OS或PyMilvus同步重试下实时硬截止；后者查读固定3.0.1 SDK重试实现确认存在独立退避计时，因此OPEN-ITEMS保留真实故障验收。read_evidence及整体Session未套此次deadline，防止存证后超时隐藏副作用。默认hybrid不变，图仅加预算检查，不做新排序实验。


### 2026-09-10 长源码切片与范围去重

实现 utf8-slices-8192-v1 显式投影：原始chunk关联、绝对范围/父hash入SQLite与Milvus、切片hash权威校验、Evidence存证/rehydrate、Graph投影ID及范围去重。旧记录NULL兼容/manifest原文保留，新版完整发布前旧pointer保持，旧collection不覆盖。发现默认Code Map字节切块可能切断UTF-8，改派生视图相邻边界前进至字符起点，原始归档不改；非UTF8显式拒绝。实现涉及15个源码模块、1脚本、3测试文件；文档4文件。

新增17项测试，针对性14通过/5.17s；最终全量728 passed、0 skipped、0 failed、7 warnings、61.92s，基线711→728（+17/+2.39%）。过程中修正旧SQL测试14列→17列、UTF8 fixture原先偶然对齐、测试误把publish拒绝当返回值；首轮全量727通过1失败的原因是期望异常补丁前的测试，最终新进程回归全部通过。随后仅补Case报告实际attempt/retry字段，最终真实Case覆盖该报告改动。

真实Lite与Standalone各2文件150121bytes、18父块（3个原块不能独立UTF8解码）→38文档/38向量/38任务；hybrid输出5、Evidence4且全部恢复，38→0次重开编码、attempt38、重试0、后台异常0、外部模型0；每个15/15检查。Lite总2557.28ms/业务2427.98ms/核验差129.30ms；Standalone总28217.38ms/业务28014.49ms/核验差202.89ms。空间预检17983459328bytes后复用既有独立栈/镜像，新collection隔离，完成stop退出0、数据保留。

最终累计10本地Case/115检查全通过，命令墙钟23.75s、退出失败0、Case重新运行次数0；Worker故障注入的7 attempts单列，不能宣称业务重试0。另1个Standalone切片Case/15检查通过。11份最终report.json；全量原始日志、每条命令、报告和数据库位于 /Users/xuewentao/.local/share/antisentinel/cases/source-slicing-3ki805yl。可复现命令及阈值见 docs/validation/prd005-stage5/SOURCE-SLICING.md。

限定状态为真实运行通过、回归通过，用户review待完成。默认hybrid与图优化pending保持。剩余至少6类边界：真实Flash+LLM完整链路、权限/TLS、容量并发、故障硬时限、运维用量、业务质量；此外其他编码/Graph完整分页不在本轮保证内。下一轮优先真实模型完整链路，不写生产就绪或RAG质量通过。


### 2026-09-10 按用户要求合入主分支并暂停剩余验收

用户要求先合入 master、剩余列为 pending。仓库实际默认主分支为 main（origin/HEAD→origin/main），按主分支意图执行本地合入，不创建平行 master；fetch 后 main 与 origin/main 差异0/0。PRD-005及剩余清单明确生产/质量验收、图优化全部 pending，暂停后续实施，不标生产就绪。合入范围130个文件，包括RAG源码/脚本/测试/阶段证据及共享开发日志；其他阶段独立草稿和运行产物留在工作区。合入前全量pytest退出0、728项通过，原始日志 source-slicing-3ki805yl/pytest-premerge.log；git diff --cached --check通过。合入后再运行全量回归，结果作为Git主分支验证交付。此次仅本地合入，不推送远端。
