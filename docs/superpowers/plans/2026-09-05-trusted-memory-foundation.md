# 可信记忆基础实施计划

> **供执行型 Agent 使用：** 逐任务执行，使用复选框记录；每个任务都遵循“失败测试 → 最小实现 → 针对性回归 → 用户确认真实 Case → review”。

**目标：** 让进入模型上下文的每条记忆都能证明其作用域、来源、有效期、生命周期和预算正确。

**架构：** 在 Memory 边界引入不可变 `MemorySourceRef`、`MemoryCandidate`、`MemoryRecord`、`AuthorizedMemoryScope`。Recorder 写入 typed record；Trusted Recall 在排序前校验 scope、时间、状态与 Evidence；Worker 对失败任务受限重试。Qwen `qwen3.7-text-embedding-flash` 仅保留为派生索引，本阶段不接线上向量检索。

**技术栈：** Python 3.11+、dataclasses、SQLite、JSONL 审计、Redis 队列适配器、pytest、OpenTelemetry。  
**设计依据：** `docs/superpowers/specs/2026-09-05-memory-evolution-design.md`

## 全局约束

- Event 和不可变 Evidence 是事实源；派生 Memory 不得覆盖它们。
- 默认拒绝跨 operator、跨 incident；共享记忆不在本阶段。
- 当前时间从可注入 UTC clock 获取，测试使用固定时间。
- Memory 生命周期仅允许 `active`、`superseded`、`retracted`、`conflict`、`pending`、`failed`；诊断完成状态写入独立 `outcome`。
- 来源仅允许 `event`、`evidence`、`session`、`turn`、`unknown`；`unknown` 不能变成 EvidenceRef，也不能注入为事实。
- Qwen 向量仅在模型、维度、`content_version` 均匹配主记录时使用。
- 真实 Case 最多 120 秒、同一输入最多重试 2 次；用户确认前不运行外部服务或写入正式 storage。
- 仓库无 Git 元数据；每个实现、测试、Case Step 都向 `docs/memory-development-log.md` 追加命令与真实数字。

## 文件职责

| 文件 | 职责 |
|---|---|
| `memory/models.py` | typed source、生命周期、候选、记忆记录、受权作用域。 |
| `memory/candidates.py` | 基于完整来源身份生成稳定 candidate ID。 |
| `memory/recorder.py` | 将 Runtime 事实写成 typed record/job，不猜测 Evidence。 |
| `memory/jobs.py`、`memory/worker.py` | retry、终态失败、drain、后台异常。 |
| `memory/trusted_recall.py` | 可信度门槛、来源验证、统一预算。 |
| `memory/recall.py` | 兼容入口，改调 Trusted Recall。 |
| `persistence/sqlite_stores.py` | typed record 持久化和来源回查。 |
| `tests/test_memory_models.py` | 契约与 ID 唯一性。 |
| `tests/test_memory_jobs.py` | 受限重试与终态失败。 |
| `tests/test_trusted_memory_recall.py` | scope、时间、来源、状态、可信度、预算。 |
| `scripts/run_trusted_memory_case.py` | 隔离全链路 Case。 |
| `docs/validation/trusted-memory-case.md` | 可确认真实 Case 的输入、阈值与清理。 |

## 任务 1：定义版本化记忆与来源契约

**修改：** `src/antisentinel/memory/models.py`、`src/antisentinel/memory/candidates.py`。  
**新增：** `tests/test_memory_models.py`。

**接口：**

```python
MemorySourceRef(ref_type, ref_id, role=None, content_hash=None, content_version=None)
AuthorizedMemoryScope(operator_id, incident_id, session_id)
MemoryCandidate.create(operator_id, incident_id, session_id, turn_id, text, source_kind, source_version)
MemoryRecord.create(..., extraction_confidence, source_refs, valid_from, status="active")
```

- [x] 写失败测试：相同文本但不同 operator/incident/session/turn 必须生成不同 `candidate_id`；Evidence 缺 hash 或版本时 `MemoryRecord.create()` 必须报错。
- [x] 运行 `python3 -m pytest -q tests/test_memory_models.py`，预期因接口不存在而失败。
- [x] 最小实现 frozen dataclass。Candidate ID 对完整来源 identity 的 canonical JSON 求 SHA-256；MemoryRecord 固定 `schema_version=2`、`content_version=1`，`extraction_confidence` 限制在 `[0,1]`。
- [x] 新增序列化 round-trip、非法 lifecycle、非法 confidence 测试；重跑命令，预期全部通过。
- [x] 开发日志记录：用例数、通过/失败数、唯一 ID 数、耗时、修改文件数、遗留风险。

## 任务 2：持久化 typed record，并安全处理旧数据

**修改：** `persistence/sqlite_stores.py`、`persistence/memory_store.py`。  
**测试：** `tests/test_memory_store.py`、`tests/test_memory_resolution.py`。

**接口：**

```python
SQLiteMemoryStore.append_record(record: MemoryRecord) -> None
SQLiteMemoryStore.replace_record(record: MemoryRecord) -> None
SQLiteMemoryStore.get_record(memory_id: str) -> MemoryRecord | None
```

- [x] 写失败测试：typed EvidenceRef round-trip 保留 hash/version；旧 `source_ids=["session-1"]` 必须映射为 `MemorySourceRef("unknown", "session-1")`，不得映射为 Evidence。
- [x] 运行 `python3 -m pytest -q tests/test_memory_store.py tests/test_memory_resolution.py`，预期新 API 缺失而失败。
- [x] 实现 typed API，旧 `append`/`replace` 保留兼容；尚未切换的 producer 在任务 3 改为调用 typed API。
- [x] 写幂等和冲突测试：同 ID 同内容重复写入后 SQLite 记录数为 1；同 ID 不同内容抛 `DomainError`。
- [x] 运行 `python3 -m pytest -q tests/test_memory_store.py tests/test_memory_resolution.py tests/test_local_vector_memory.py`，预期全部通过；记录 Evidence 关联数、unknown 数和版本隔离结果。

## 任务 3：让 Recorder 写入来源正确且 ID 不冲突的候选

**修改：** `memory/recorder.py`、`memory/candidates.py`、`memory/llm_classifier.py`、`memory/operator_graph.py`、`memory/rollout.py`。  
**测试：** `tests/test_memory_pipeline.py`、`tests/test_memory_recorder.py`。

- [x] 写失败测试：同一 operator 下的两个 session 即使 diagnosis 相同，也必须有两条 episodic memory；用户偏好来源类型为 `session`，不是 `evidence`。
- [x] 运行 `python3 -m pytest -q tests/test_memory_pipeline.py tests/test_memory_recorder.py`，预期现有常量 ID/source_ids 语义失败。
- [x] 用 `MemoryCandidate.create()` 构造候选。LLM 结果写入 `extractor_revision`、`reason_code`、`extraction_confidence`，但不把它等同来源可信度；读取不到 Evidence 时写 `unknown` 且状态设为 `pending`。
- [x] 运行 `python3 -m pytest -q tests/test_memory_pipeline.py tests/test_memory_recorder.py tests/test_memory_llm_classifier.py tests/test_memory_store.py`，预期 ID 冲突数=0、Session→Evidence 误转数=0。
- [x] 日志记录 job 数、唯一 candidate 数、来源类型分布、unknown 数、重复副作用数、耗时。

## 任务 4：使 Memory Job 可重试、可终止、可观测

**修改：** `memory/jobs.py`、`memory/worker.py`、`memory/recorder.py`。  
**新增：** `tests/test_memory_jobs.py`。

**接口：**

```python
MemoryJob(job_id, source, candidates, attempts, next_attempt_at, status, last_error)
queue.claim(now) -> MemoryJob | None
queue.retry(job_id, error, next_attempt_at) -> None
queue.fail(job_id, error) -> None
```

- [x] 写失败测试：第一次分类异常后，在退避时间后可重新 claim；第三次失败为 `failed`，pending/processing 均移除。
- [x] 运行 `python3 -m pytest -q tests/test_memory_jobs.py tests/test_memory_pipeline.py`，预期当前 claim 后异常会导致任务无法正确 retry。
- [x] 实现总尝试最多 3 次（首次+最多两次重试），退避 `2 ** attempts` 秒；错误中只保存异常类别和受限摘要。新增 `memory.job.retry`、`memory.job.failed` telemetry。
- [x] 运行 `python3 -m pytest -q tests/test_memory_jobs.py tests/test_memory_pipeline.py tests/test_redis_bootstrap.py`，预期最多 2 次 retry、终态作业的 pending/processing 数均为 0。
- [x] 日志记录首失败数、重试数、终态失败数、队列残留、worker 异常、测试数和耗时。

## 任务 5：在 Recall 生产边界落实可信度门槛

**新增：** `memory/trusted_recall.py`、`tests/test_trusted_memory_recall.py`。  
**修改：** `memory/recall.py`、`worker/runtime/context.py`、`tests/test_memory_recall.py`。

**可信度计算：**

```text
source_trust：验证过的 Evidence = 1.0；Event/Session/Turn = 0.7；unknown = 0.0
trust_score = 0.6 × source_trust + 0.4 × extraction_confidence
verified：trust_score >= 0.80
hypothesis：0.50 <= trust_score < 0.80
blocked：trust_score < 0.50
```

文本或向量相似度只做相关性排序，不能抬高 `trust_score`。

- [ ] 写失败测试：未来记录不召回；Evidence 缺失或 hash/version 不符不注入；cross-scope 注入数为 0；digest+memory 的估算 token 不超过一个预算。
- [ ] 运行 `python3 -m pytest -q tests/test_memory_recall.py tests/test_trusted_memory_recall.py`，预期当前远未来时间、source_ids 推断、双预算行为失败。
- [ ] 实现 `TrustedMemoryRecall.recall(scope, query, token_budget, now, records, evidence_lookup, digest)`：精确过滤 operator/incident、lifecycle、有效期；校验 Evidence ID/hash/version；unknown 标记 `provenance_unknown`。`verified` 作为事实注入，`hypothesis` 带标签注入，`blocked` 不注入。
- [ ] 先从同一总预算中分配 digest，剩余预算才供长期记忆；给 `MemoryContextView` 增加 `estimated_tokens` 供 ContextBuilder telemetry 使用。
- [ ] 运行 `python3 -m pytest -q tests/test_trusted_memory_recall.py tests/test_memory_recall.py tests/test_runtime_context.py tests/test_memory_retrieval.py`；预期未来/无效/cross-scope 注入均为 0，任一 ContextView 不超预算。
- [ ] 日志记录：拒绝原因分布、可信度层级注入数、最大 token/预算、异常数、耗时。

## 任务 6：准备隔离真实 Case，并等待确认后执行

**新增：** `scripts/run_trusted_memory_case.py`、`docs/validation/trusted-memory-case.md`。  
**修改：** `tests/test_memory_recorder.py`、`docs/memory-development-log.md`。

- [x] 写失败测试：`run_case()` 报告必须含 sessions、unique_candidate_ids、persisted_records、verified_source_refs、cross_scope_injected、future_injected、provenance_invalid_injected、background_exceptions、duration_ms、case_pass。
- [x] 运行 `python3 -m pytest -q tests/test_memory_recorder.py -k trusted_memory_case`，预期 runner 未实现而失败。
- [x] 实现 runner：使用 `tempfile.mkdtemp(prefix="antisentinel-trusted-memory-")` 和独立 SQLite，绝不使用默认 `storage/`。输入为 2 operator、2 incident、4 completed session、一次偏好纠错、未来记录、hash 不匹配 Evidence、一次失败后成功的分类任务；drain worker 后重开 SQLite。
- [x] `case_pass=true` 的硬条件：sessions=4、unique_candidate_ids=4、persisted_records>=4、verified_source_refs>=4、cross-scope/future/provenance-invalid 注入均为 0、恢复记录数一致、后台异常=0、耗时<=120000ms。
- [x] 先运行累计离线回归：

```sh
python3 -m pytest -q tests/test_memory_models.py tests/test_memory_jobs.py tests/test_trusted_memory_recall.py tests/test_memory*.py tests/test_local_memory.py tests/test_local_vector_memory.py tests/test_sqlite_memory_search.py tests/test_operator_graph.py
```

- [x] 向用户提交 `trusted-memory-case.md` 并等待确认。确认后运行真实 Case，记录七列表格和至少 10 个数值字段；用户 review 前不得开始 B。

## 覆盖映射

| 设计要求 | 任务 |
|---|---|
| 版本化 MemoryRecord、typed provenance | 任务 1 |
| 幂等 SQLite、旧数据安全解释 | 任务 2 |
| Candidate ID 与 Recorder 来源正确性 | 任务 3 |
| claim/retry/fail/drain | 任务 4 |
| scope、状态、时间、Evidence、可信度、预算 | 任务 5 |
| 隔离真实 Case、恢复、量化报告、review gate | 任务 6 |
| Qwen 向量版本隔离 | 任务 2 回归 |
| 混合检索、ANN、470 条评测 | B 阶段，不在本计划 |
| consolidation、遗忘、反馈闭环 | C 阶段，不在本计划 |

计划包含 6 个任务。开始实现后，每个任务都在针对性测试和用户确认的真实 Case 后暂停，等待 review。
