# SQLite 持久化与 Memory Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 AntiSentinel 增加 SQLite 本地持久化、legacy 幂等迁移、Redis cache-aside 恢复和真实 Memory 成对评测，并在中文 Dashboard 展示严格的 85%/30%/90% gate。

**Architecture:** SQLite 是事务持久化与查询事实源，JSONL 是不可变审计副本，Redis 仅负责热缓存、ZSET 索引、Memory Job Queue 与幂等。所有数据库差异封装在现有 Persistence Port seam 后；Evaluation Harness 对相同输入执行 baseline/optimized paired replay，并把事实级指标写入 SQLite。

**Tech Stack:** Python 3.13、标准库 `sqlite3`、FastAPI、Redis、OpenTelemetry、Prometheus、Vanilla HTML/CSS/JavaScript、pytest。

**Spec:** `docs/superpowers/specs/2026-09-04-sqlite-memory-evaluation-design.md`

## Global Constraints

- 不引入 MongoDB、PostgreSQL、LibreChat、Phoenix、ORM 或新常驻容器。
- SQLite 默认路径固定为 `${ANTISENTINEL_STORAGE_ROOT}/antisentinel.db`。
- SQLite 使用 WAL、`foreign_keys=ON`、`busy_timeout=5000`。
- SQLite commit 是持久化成功依据；JSONL 失败必须记录 `audit_degraded`。
- Redis 不可用时回退 SQLite，计 `cache_bypass`，不计 `cache_miss`。
- API Token 不得进入 SQLite、Redis、JSONL、Trace 或 Dashboard。
- 大型 Evidence 原文保持文件存储，SQLite 只存引用、hash、长度和 preview。
- 每阶段验收顺序固定为：针对性测试 → 全量回归 → 已确认的累计真实 Case → 用户 review。
- 每一个 Task/Step 都必须运行对应的真实测试集子集；不能等到大阶段结束才首次运行数据集。
- 数据集步骤固定为：LongMemEval fixture Smoke → LongMemEval-S 分层 30 Case → LongMemEval-S 全量 500 Case（只在该步骤确实需要全量时执行）。
- 每个数据集结果必须记录 dataset SHA256、Case 数、跳过数、异常数、核心指标和真实产物路径。
- 每完成一个 Step，立即追加 `docs/memory-development-log.md`：变更、命令、数字、产物、失败/修复、结论、下一步。
- 当前目录没有 Git 元数据；各任务记录验证结果，但不得伪造 commit。

---

## 文件结构

新增文件：

- `src/antisentinel/persistence/sqlite_database.py`：connection、transaction、schema migration。
- `src/antisentinel/persistence/sqlite_stores.py`：Application、Conversation、Event、Evidence、State、Memory adapter。
- `src/antisentinel/persistence/audited_stores.py`：SQLite 主写与 JSONL audit 组合写入。
- `src/antisentinel/persistence/legacy_import.py`：legacy 扫描、fingerprint、幂等导入和对账。
- `scripts/import_legacy_storage.py`：迁移命令入口。
- `src/antisentinel/evaluation/models.py`：Case、Run、Result、Ground Truth 类型。
- `src/antisentinel/evaluation/metrics.py`：纯函数指标计算与 gate。
- `src/antisentinel/evaluation/harness.py`：baseline/optimized paired replay。
- `scripts/run_memory_evaluation.py`：真实评测入口。
- `tests/test_sqlite_database.py`
- `tests/test_sqlite_stores.py`
- `tests/test_legacy_import.py`
- `tests/test_persistence_cutover.py`
- `tests/test_memory_evaluation.py`
- `tests/test_memory_evaluation_api.py`

修改文件：

- `src/antisentinel/ports/event_store.py`
- `src/antisentinel/ports/evidence_store.py`
- `src/antisentinel/ports/state_store.py`
- `src/antisentinel/persistence/application_store.py`
- `src/antisentinel/entry/conversation_store.py`
- `src/antisentinel/persistence/memory_store.py`
- `src/antisentinel/memory/rollout.py`
- `src/antisentinel/memory/operator_graph.py`
- `src/antisentinel/memory/recorder.py`
- `src/antisentinel/memory/jobs.py`
- `src/antisentinel/memory/worker.py`
- `src/antisentinel/entry/application.py`
- `src/antisentinel/tracing/telemetry.py`
- `src/antisentinel/tracing/metrics.py`
- `src/antisentinel/api/app.py`
- `frontend/dashboard.html`
- `frontend/dashboard.js`
- `frontend/dashboard.css`
- `scripts/start_local.sh`
- `README.md`

---

## 阶段 1：SQLite Schema、Adapter 与 Legacy Import

### Task 1：SQLite Database 深模块

**Files:**
- Create: `src/antisentinel/persistence/sqlite_database.py`
- Test: `tests/test_sqlite_database.py`

**Interfaces:**
- Produces: `SQLiteDatabase(path: str | Path)`
- Produces: `initialize() -> None`
- Produces: `transaction() -> ContextManager[sqlite3.Connection]`
- Produces: `query(sql: str, params: Sequence[object] = ()) -> list[sqlite3.Row]`
- Produces: `close() -> None`
- Produces: schema version `1`

- [x] **Step 1: 写失败测试，锁定连接配置、迁移幂等和回滚**

```python
def test_sqlite_database_initializes_schema_idempotently_and_rolls_back(tmp_path):
    db = SQLiteDatabase(tmp_path / "antisentinel.db")
    db.initialize(); db.initialize()
    assert db.query("PRAGMA journal_mode")[0][0].lower() == "wal"
    assert db.query("PRAGMA foreign_keys")[0][0] == 1
    assert db.query("SELECT version FROM schema_migrations")[-1][0] == 1
    with pytest.raises(RuntimeError):
        with db.transaction() as connection:
            connection.execute("INSERT INTO incidents(incident_id,title,source,status,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("i-1","x","operator","open","{}","2026-09-04T00:00:00Z","2026-09-04T00:00:00Z"))
            raise RuntimeError("rollback")
    assert db.query("SELECT incident_id FROM incidents") == []
```

- [x] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `pytest -q tests/test_sqlite_database.py`

- [x] **Step 3: 实现 schema v1 与 transaction**

Schema 必须创建设计文档列出的 20 张表；所有 stable ID 使用 `TEXT PRIMARY KEY`，payload 使用 `TEXT NOT NULL` JSON，时间使用 UTC ISO-8601 TEXT，关系表启用 foreign key。`events.event_id`、`evidence.evidence_id`、`memory_records.memory_id`、`spans.span_id` 不允许覆盖。

```python
class SQLiteDatabase:
    def __init__(self, path: str | Path): ...
    def initialize(self) -> None: ...
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]: ...
    def query(self, sql: str, params: Sequence[object] = ()) -> list[sqlite3.Row]: ...
    def close(self) -> None: ...
```

- [x] **Step 4: 运行针对性测试**

Run: `pytest -q tests/test_sqlite_database.py`
Expected: PASS。

### Task 2：SQLite Persistence Adapters

**Files:**
- Create: `src/antisentinel/persistence/sqlite_stores.py`
- Modify: `src/antisentinel/memory/rollout.py`
- Modify: `src/antisentinel/memory/operator_graph.py`
- Test: `tests/test_sqlite_stores.py`

**Interfaces:**
- Consumes: `SQLiteDatabase`
- Produces: `SQLiteApplicationStore.save_incident/save_session/save_result/load_incidents/load_sessions/load_result`
- Produces: `SQLiteConversationStore.append/load`
- Produces: `SQLiteEventStore.append/list_by_aggregate/list_by_correlation`
- Produces: `SQLiteEvidenceStore.put_once/get`
- Produces: `SQLiteStateStore.save_projection/load_projection/mark_projection_lag`
- Produces: `SQLiteMemoryStore.append/replace/list_by_operator/get/list_by_type`

- [x] **Step 1: 写失败测试覆盖 adapter 契约**

```python
def test_sqlite_stores_preserve_ids_order_and_immutability(sqlite_db):
    events = SQLiteEventStore(sqlite_db)
    events.append(first_event); events.append(first_event)
    assert events.list_by_correlation("corr-1") == [first_event]
    with pytest.raises(DomainError):
        events.append(conflicting_event_with_same_id)
    messages = SQLiteConversationStore(sqlite_db)
    messages.append("session-1", "user", "先看日志")
    messages.append("session-1", "assistant", "收到")
    assert [item["role"] for item in messages.load("session-1")] == ["user", "assistant"]
```

- [x] **Step 2: 运行并确认失败**

Run: `pytest -q tests/test_sqlite_stores.py`

- [x] **Step 3: 实现六个 adapter**

实现不得调用 file adapter 私有方法。为 `SQLiteMemoryStore` 增加公开 `get(memory_id)` 和 `list_by_type(memory_type)`，并把 `RolloutMemory.get()` 从 `durable._read()` 改为 `durable.get("rollout:" + rollout_id)`。

- [x] **Step 4: 运行 adapter 与旧存储回归**

Run: `pytest -q tests/test_sqlite_stores.py tests/test_persistence.py tests/test_memory_store.py tests/test_operator_graph.py`
Expected: PASS。

### Task 3：Legacy Importer 与完整性报告

**Files:**
- Create: `src/antisentinel/persistence/legacy_import.py`
- Create: `scripts/import_legacy_storage.py`
- Test: `tests/test_legacy_import.py`

**Interfaces:**
- Produces: `ImportReport(scanned: int, inserted: int, skipped: int, failed: int, errors: tuple[str, ...])`
- Produces: `LegacyImporter(source_root: Path, database: SQLiteDatabase).run() -> ImportReport`
- CLI: `python scripts/import_legacy_storage.py --source <storage> --database <db>`

- [x] **Step 1: 写失败测试验证重复导入第二次新增 0 行**

```python
def test_legacy_import_is_idempotent_and_preserves_relationships(legacy_fixture, tmp_path):
    db = SQLiteDatabase(tmp_path / "antisentinel.db"); db.initialize()
    importer = LegacyImporter(legacy_fixture, db)
    first = importer.run(); second = importer.run()
    assert first.failed == 0 and first.inserted > 0
    assert second.failed == 0 and second.inserted == 0
    assert db.query("PRAGMA foreign_key_check") == []
```

- [x] **Step 2: 运行并确认失败**

Run: `pytest -q tests/test_legacy_import.py`

- [x] **Step 3: 实现 fingerprint、import ledger 与对账**

Fingerprint 使用 `sha256(relative_path + newline + canonical_json_or_raw_line)`；单条失败写入 report 并使 CLI exit code 为 `1`。Importer 支持 application JSON、message/event/memory/trace JSONL、Incident state JSON 和 Evidence JSON。

- [x] **Step 4: 运行阶段 1 针对性与全量回归**

Run: `pytest -q tests/test_sqlite_database.py tests/test_sqlite_stores.py tests/test_legacy_import.py`

Run: `pytest -q`

- [x] **Step 5: 执行已确认真实 Case 的阶段 1 范围**

复制当前 `storage/` 到 `mktemp -d` 创建的隔离目录；运行 importer 两次；读取 SQLite 表数量、`import_ledger`、`PRAGMA foreign_key_check` 和失败报告。不得修改当前 `storage/`。

完成标准：迁移两次均无后台异常，第二次 `inserted=0`，关系与 hash 校验成功。随后暂停等待用户 review。

每个后续 Step 同样要求在变更后立即跑对应 LongMemEval/真实数据子集；如果该 Step 改变了 Memory Record、Rank、Filter、ContextView 或 Evaluation 口径，至少运行 30 Case 分层 Smoke，并在阶段收尾再跑全量 500 Case。

---

## 阶段 2：Runtime、Memory、Tracing 累计接入

### Task 4：应用装配切换与 JSONL Audit

**Files:**
- Create: `src/antisentinel/persistence/audited_stores.py`
- Modify: `src/antisentinel/entry/application.py`
- Modify: `src/antisentinel/memory/recorder.py`
- Modify: `src/antisentinel/tracing/telemetry.py`
- Modify: `scripts/start_local.sh`
- Test: `tests/test_persistence_cutover.py`

**Interfaces:**
- Produces: `AuditedEventStore(primary, audit, on_degraded)`
- Produces: `AuditedEvidenceStore(primary, audit, on_degraded)`
- Produces env: `ANTISENTINEL_PERSISTENCE_MODE=sqlite|legacy`, default `sqlite`
- Produces env: `ANTISENTINEL_SQLITE_PATH`, default `${ANTISENTINEL_STORAGE_ROOT}/antisentinel.db`

- [x] **Step 1: 写失败测试验证默认 SQLite、legacy 回退与 audit degraded**

```python
def test_environment_uses_sqlite_and_recovers_after_reload(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTISENTINEL_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("ANTISENTINEL_PERSISTENCE_MODE", "sqlite")
    first = DiagnosisApplicationService.from_environment()
    incident = first.create_incident(title="SQLite flow", summary=None, source="operator")
    second = DiagnosisApplicationService.from_environment()
    assert str(incident.incident_id) in second.incidents
```

- [x] **Step 2: 运行并确认失败**

Run: `pytest -q tests/test_persistence_cutover.py`

- [x] **Step 3: 注入 adapter，删除 `MemoryRecorder` 内部 concrete store 创建**

`MemoryRecorder.__init__` 改为接收 `event_store`、`evidence_store`、`state_store`、`memory_store`、`preference_store`、`job_queue` 和 `cache`；`from_environment()` 是唯一装配位置。

- [x] **Step 4: 将 Trace span 同步写入 SQLite，JSONL 保持 audit**

新增 `SQLiteSpanExporter`，与现有 `JsonlSpanExporter` 同时挂载。Span attributes 继续执行 secret key 过滤。

- [x] **Step 5: 运行针对性回归**

Run: `pytest -q tests/test_persistence_cutover.py tests/test_application_persistence.py tests/test_conversation_persistence.py tests/test_observability_logging.py tests/test_tracing_metrics.py`

### Task 5：Redis Queue、Cache-aside 与 Worker 恢复

**Files:**
- Modify: `src/antisentinel/memory/jobs.py`
- Modify: `src/antisentinel/memory/worker.py`
- Modify: `src/antisentinel/adapters/cache/redis.py`
- Modify: `src/antisentinel/tracing/metrics.py`
- Test: `tests/test_worker_redis.py`

**Interfaces:**
- Produces: `MemoryJobQueue` protocol with `enqueue/claim/ack/retry`
- Produces: `RedisMemoryJobQueue(client, prefix, lease_seconds=30)`
- Produces: `MemoryCacheLookup(status: Literal["hit","miss","bypass"], value: object | None)`
- Produces: `MemoryWorker.stop(timeout: float = 5.0) -> bool`

- [x] **Step 1: 写失败测试覆盖 lease、retry、幂等和 drain**

```python
def test_redis_job_is_recovered_after_expired_lease(redis_client):
    queue = RedisMemoryJobQueue(redis_client, "case:memory", lease_seconds=1)
    queue.enqueue(memory_job); assert queue.claim().job_id == memory_job.job_id
    advance_redis_clock_or_expire_lease(redis_client, memory_job.job_id)
    assert queue.claim().job_id == memory_job.job_id
    queue.ack(memory_job.job_id)
    assert queue.claim() is None
```

- [x] **Step 2: 运行并确认失败**

Run: `pytest -q tests/test_worker_redis.py`

- [x] **Step 3: 实现 Redis Queue 与 cache outcome**

Redis key 使用 `{prefix}:pending` ZSET、`{prefix}:processing` ZSET、`{prefix}:job:{job_id}` HASH。score 为可领取时间；claim 使用 transaction/Lua 保证 pending→processing 原子移动。Redis exception 返回 bypass，由 SQLite durable query 提供结果。

- [x] **Step 4: 增加 worker drain 与后台异常状态**

`stop()` 在 timeout 内等待 processing 归零；超时返回 `False`。`errors` 非空或 stop 返回 `False` 时真实 Case 失败。

- [x] **Step 5: 运行阶段 2 累计验收**

Run: `pytest -q tests/test_persistence_cutover.py tests/test_worker_redis.py tests/test_memory_pipeline.py tests/test_memory_recall.py tests/test_tracing_metrics.py`

Run: `pytest -q`

真实 Case 累计运行阶段 1–2：创建 Incident/Session、多轮消息、真实 Redis health ToolCall，等待 Memory persisted，停止并重启服务，校验 SQLite/JSONL/Redis/API/Trace/Dashboard 和 worker errors。随后暂停等待用户 review。

---

## 阶段 3：Memory Evaluation Harness

### Task 6：Evaluation 类型与纯指标计算

**Files:**
- Create: `src/antisentinel/evaluation/models.py`
- Create: `src/antisentinel/evaluation/metrics.py`
- Test: `tests/test_memory_evaluation.py`

**Interfaces:**
- Produces: `EvaluationCase`, `GroundTruthFact`, `PairedResult`, `EvaluationSummary`
- Produces: `calculate_cache_hit_rate(outcomes) -> RateMetric`
- Produces: `calculate_token_reduction(pairs) -> RateMetric`
- Produces: `calculate_fact_accuracy(results) -> AccuracyMetric`
- Produces: `evaluate_gates(summary, sample_counts) -> dict[str, GateState]`

- [x] **Step 1: 写失败测试锁定分母、排除项和 gate**

```python
def test_metrics_exclude_bypass_and_require_minimum_samples():
    cache = calculate_cache_hit_rate(["hit"] * 17 + ["miss"] * 3 + ["bypass"] * 5)
    assert cache.value == pytest.approx(0.85)
    tokens = calculate_token_reduction([(100, 60), (200, 140)])
    assert tokens.value == pytest.approx(1 - 200 / 300)
    accuracy = calculate_fact_accuracy(tp=18, fp=1, fn=1)
    assert accuracy.value == pytest.approx(0.90)
    assert evaluate_gates(summary(cache, tokens, accuracy), insufficient_counts())["accuracy"] == "INSUFFICIENT_DATA"
```

- [x] **Step 2: 运行并确认失败**

Run: `pytest -q tests/test_memory_evaluation.py`

- [x] **Step 3: 实现 frozen dataclass 与纯函数**

所有 ratio 使用 `Decimal` 内部计算并输出 float；零分母返回 `value=None` 和 `INSUFFICIENT_DATA`，不得返回 0。

- [x] **Step 4: 运行指标测试**

Run: `pytest -q tests/test_memory_evaluation.py`

### Task 7：Paired Replay 与 SQLite Evaluation Store

**Files:**
- Create: `src/antisentinel/evaluation/harness.py`
- Create: `scripts/run_memory_evaluation.py`
- Modify: `src/antisentinel/persistence/sqlite_stores.py`
- Test: `tests/test_memory_evaluation.py`

**Interfaces:**
- Produces: `EvaluationStore.save_run/save_case/save_result/load_run`
- Produces: `MemoryEvaluationHarness(baseline_runner, optimized_runner, store)`
- Produces: `run(cases: Sequence[EvaluationCase], config: EvaluationConfig) -> EvaluationSummary`
- CLI: `python scripts/run_memory_evaluation.py --database <db> --cases <jsonl> --model <name> --prompt-revision <revision>`

- [x] **Step 1: 写失败测试验证相同配置、paired 排除和 provenance**

```python
def test_harness_persists_only_valid_pairs_for_token_aggregate(sqlite_db):
    harness = MemoryEvaluationHarness(fake_baseline, fake_optimized, SQLiteEvaluationStore(sqlite_db))
    summary = harness.run([human_labeled_case, failed_provider_case], fixed_config)
    assert summary.paired_success_count == 1
    assert summary.execution_error_count == 1
    assert summary.accuracy.ground_truth_sources == ("human",)
```

- [x] **Step 2: 运行并确认失败**

Run: `pytest -q tests/test_memory_evaluation.py`

- [x] **Step 3: 实现 replay、持久化与 CLI**

每次运行保存 config snapshot、Case cutoff、selected memory IDs、answer facts、token usage、cache outcome、latency 和 error。未 review 的 `llm_draft` Ground Truth 不进入 gate。

- [x] **Step 4: 运行阶段 3 累计验收**

Run: `pytest -q tests/test_memory_evaluation.py tests/test_memory_recall.py tests/test_token_usage.py`

Run: `pytest -q`

真实 Case 累计运行阶段 1–3：使用已确认 Case 数据执行 baseline/optimized，直接查询 SQLite 验证 Pair 数、token、TP/FP/FN、cache denominator 和 gate。样本不足时预期 `INSUFFICIENT_DATA`。随后暂停等待用户 review。

---

## 阶段 4：中文 Dashboard 与依赖收敛

### Task 8：Evaluation API

**Files:**
- Modify: `src/antisentinel/api/app.py`
- Test: `tests/test_memory_evaluation_api.py`

**Interfaces:**
- Produces: `GET /api/evaluations`
- Produces: `GET /api/evaluations/{run_id}`
- Produces: `GET /api/evaluations/{run_id}/cases?gate=&incident_id=&session_id=`

- [ ] **Step 1: 写失败 API 测试**

```python
def test_evaluation_api_returns_gate_and_failing_cases(client_with_evaluation):
    run = client_with_evaluation.get("/api/evaluations/run-1").json()
    assert run["gates"] == {"cache_hit_rate": "PASS", "token_reduction": "FAIL", "accuracy": "INSUFFICIENT_DATA"}
    cases = client_with_evaluation.get("/api/evaluations/run-1/cases?gate=FAIL").json()
    assert all(case["gate"] == "FAIL" for case in cases)
```

- [ ] **Step 2: 运行并确认失败**

Run: `pytest -q tests/test_memory_evaluation_api.py`

- [ ] **Step 3: 实现只读 API 与 404/422 错误语义**

API 返回已持久化 aggregate，不在请求时重新计算或调用模型。未知 run 返回 `evaluation_run_not_found`；非法 gate 返回 `invalid_gate_filter`。

- [ ] **Step 4: 运行 API 测试**

Run: `pytest -q tests/test_memory_evaluation_api.py tests/test_observability_api.py`

### Task 9：中文 Dashboard 指标与下钻

**Files:**
- Modify: `frontend/dashboard.html`
- Modify: `frontend/dashboard.js`
- Modify: `frontend/dashboard.css`
- Modify: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: Task 8 Evaluation API
- Produces: 三张 KPI 卡、run/Incident/Session filter、baseline/optimized token 对比、cache breakdown、失败事实列表。

- [ ] **Step 1: 写失败测试锁定中文文案与数据入口**

```python
def test_dashboard_contains_memory_evaluation_sections(client):
    html = client.get("/dashboard").text
    assert "缓存命中率" in html
    assert "Token 降幅" in html
    assert "记忆准确率" in html
    assert "/api/evaluations" in client.get("/dashboard.js").text
```

- [ ] **Step 2: 运行并确认失败**

Run: `pytest -q tests/test_dashboard.py`

- [ ] **Step 3: 实现 Dashboard**

`PASS` 使用绿色、`FAIL` 红色、`INSUFFICIENT_DATA` 灰色；任何 `value=None` 显示“样本不足”，不能显示 `0%`。失败 Case 点击后展示 Session、Memory IDs、Evidence refs、FP/FN 和对应 Span。

- [ ] **Step 4: 运行 Dashboard 测试**

Run: `pytest -q tests/test_dashboard.py tests/test_memory_evaluation_api.py`

### Task 10：启动路径与最终累计真实 Case

**Files:**
- Modify: `scripts/start_local.sh`
- Modify: `README.md`
- Modify: `deploy/README.md`

**Interfaces:**
- Produces: 默认启动仅 AntiSentinel + Redis。
- Preserves: `docker-compose.oss.yml` 与 volumes，不执行删除。

- [ ] **Step 1: 更新启动文档和运行信息**

启动输出必须包含 SQLite path、Redis URL、模型、Dashboard URL，并明确 LibreChat/Phoenix 为 paused legacy integrations。

- [ ] **Step 2: 运行脚本语法与全量回归**

Run: `bash -n scripts/start_local.sh`

Run: `pytest -q`

- [ ] **Step 3: 执行完整累计真实 Case**

按设计文档第 13 节运行全部输入，验收报告必须列出：

```text
运行状态
持久化状态
真实产物路径
关键数量与关联校验
后台异常
恢复校验
cache_hit_rate / token_reduction / accuracy
gate 状态
case_pass
```

- [ ] **Step 4: 使用浏览器检查中文 Dashboard**

确认 Incident→Session→Turn→Span 下钻、三项 KPI、失败 Case、Token 对比和 Memory/Evidence 引用与 SQLite 查询一致。

- [ ] **Step 5: 暂停等待最终用户 review**

只有针对性测试、全量回归、完整真实 Case 和 UI 检查都有刚刚产生的证据，才能报告阶段 4 完成。不得删除暂停中的 Docker containers 或 volumes。
