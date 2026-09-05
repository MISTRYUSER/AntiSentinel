# AntiSentinel SQLite 持久化与 Memory Evaluation 设计

## 1. 状态与范围

- 日期：2026-09-04
- 所属范围：PRD-003 Memory & Evidence Foundation
- 当前状态：设计完成、用户 review 通过、真实 Case 已确认
- 基线：Memory、Redis、Tracing、Event/Evidence/State JSON/JSONL 骨架已存在；SQLite 尚未接入；85%/30%/90% 尚无真实评测结论。

本设计包含两个子系统：SQLite Persistence Adapter 与基于真实 Session 的 Memory Evaluation Harness。

## 2. 目标与非目标

目标：

- 在现有 Persistence Port 后增加 SQLite adapter，不让领域层和 Runtime 感知 SQL。
- 将 Incident、Session、Message、Event、Evidence、Memory、Preference、Trace 变成可事务查询的本地数据。
- 保留 JSONL 作为不可变审计与恢复输入，不再用于主查询。
- 使用相同输入执行 baseline/optimized 成对回放。
- 持续测量缓存命中率、上下文 Token 降幅和 Memory 事实准确率。
- 在中文 Dashboard 展示全局、Incident、Session 和失败 Case。

非目标：

- 不引入 MongoDB、PostgreSQL、LibreChat、Phoenix 或新常驻容器。
- 不实现向量数据库和完整 PRD-005 RAG。
- 不为未来横向扩展提前引入 ORM。
- 不把 Redis 作为长期事实源。
- 不把未人工确认的 LLM Judge 结果当作发布 Ground Truth。
- 不在迁移阶段删除现有 JSON/JSONL 或 Docker volume。

## 3. 候选方案

### A. SQLite 为唯一持久化，删除 JSONL

写入链路最简单，但失去易读的 append-only 回放与独立恢复材料。

### B. JSONL 为 canonical，SQLite 只做可重建索引

接近 Codex ThreadStore，但双写顺序、shutdown flush、一致性修复更复杂；当前项目缺少成熟 repair 工具。

### C. SQLite 为持久化事实源，JSONL 为审计副本（已选）

业务写入和查询以 SQLite transaction 为准；JSONL 用于审计、迁移输入和离线恢复。它保留 Codex 双层存储的优点，同时比 canonical JSONL 更适合当前实现基础。

参考事实与本项目推断已分开记录在 [调研文档](../../research/sqlite-memory-evaluation-research.md)。

## 4. 总体架构

```text
FastAPI / Runtime / Memory Worker
              |
       Persistence Ports
              |
   +----------+-----------+
   |                      |
SQLite Adapter       JSONL Audit Sink
持久化事实源          不可变审计副本
   |
storage/antisentinel.db

Redis
  |- Session Tree 热缓存
  |- Memory Recall 缓存
  |- ZSET 排序索引
  |- Memory Job Queue
  `- 锁与幂等键

File Evidence Store
  `- 大型 Evidence 原文
```

## 5. 模块职责与 Interface

### 5.1 SQLite Adapter

位于现有 Persistence Port seam 后，负责 connection lifecycle、schema migration、transaction、foreign key、JSON 序列化、幂等 upsert、Event/Evidence 不可变约束、索引、WAL、busy timeout 和 shutdown flush。

领域层、Runtime、Memory Worker 不接收 connection，也不感知表名。

### 5.2 Legacy Importer

Importer 只读当前 `storage/`：计算 fingerprint、写入 `import_ledger`、保持 stable ID、校验数量/时序/hash/关系。重复执行不产生重复行，也不改写 legacy 文件。

### 5.3 Evaluation Harness

每个 Case 以相同 provider、model、prompt revision、Evidence cutoff 和采样参数运行两次：

- `baseline`：完整合格历史，禁用 Memory Recall，强制 cache bypass。
- `optimized`：Scoped ContextView + Digest + Memory Recall + Redis cache。

### 5.4 Dashboard

Dashboard 只消费 Observability API，不直接读取 SQLite/Redis。它支持从聚合指标下钻到 Incident、Session、Turn、Span、Memory、冲突记录和失败 Case。

## 6. SQLite 数据模型

核心表：

- `incidents`、`sessions`、`messages`
- `turns`、`tasks`、`tool_calls`、`attempts`
- `events`、`evidence`、`evidence_refs`
- `memory_records`、`preference_nodes`、`preference_edges`、`memory_conflicts`、`memory_jobs`
- `traces`、`spans`
- `evaluation_runs`、`evaluation_cases`、`evaluation_results`
- `schema_migrations`、`import_ledger`

大型 Evidence 不复制进 SQLite；只保存 URI/path、hash、media type、byte count 与有界 preview。

关键索引覆盖 Incident→Session、Session→Message/Turn 时间序、Event aggregate/correlation/time、Evidence hash/source、Memory owner/kind/entity/validity/confidence/time、Trace/Session/Turn/parent span 和 Evaluation run/case/mode/gate。

## 7. 数据流

### 7.1 写入

```text
Runtime Result
  → Persistence Port
  → SQLite transaction commit
  → JSONL audit append
  → Redis cache/index refresh or invalidate
  → persistence completed
```

SQLite 失败即业务持久化失败。JSONL 失败时 SQLite 仍有效，但记录 `audit_degraded`，真实 Case 判失败直至补写完成。

### 7.2 Recall

```text
Query → stable cache key
  → Redis hit：取得 Memory IDs，批量读取 SQLite
  → Redis miss：SQLite 查询，回填 Redis
  → Rank/Filter → Scoped ContextView
```

Redis 只缓存 ID、score、version 和短摘要，不保存唯一的大型 Memory/Evidence 本体。

### 7.3 异步 Memory Job

```text
Candidate → Redis Queue → lease/idempotency
  → Worker classify/normalize/merge
  → SQLite transaction → Redis index refresh
  → job completed
```

业务完成与 Memory 持久化完成分别记录。进程退出前必须 drain，或保留可恢复 job。

## 8. 指标口径

缓存命中率：

```text
eligible_memory_cache_hits / eligible_memory_cache_lookups
```

冷启动、主动 invalidation、管理查询和强制 bypass 单独统计，不进入目标分母。

Token 降幅：

```text
1 - sum(optimized_input_tokens) / sum(baseline_input_tokens)
```

只统计相同配置且两侧都成功的 paired cases。

Memory 准确率：

```text
TP / (TP + FP + FN)
```

使用带 provenance 的事实标签计算 micro average，同时展示 precision、recall、过期记忆注入率、冲突合并错误率和答案采用率。只有人工标签和确定性规则标签进入发布 gate。

Gate：命中率 >=85%，Token 降幅 >=30%，准确率 >=90%。状态只有 `PASS`、`FAIL`、`INSUFFICIENT_DATA`。最低样本为 30 个标注 Case、5 个 Session、3 个 Incident、10 次 cache-eligible 重复查询。

## 9. 错误语义

- SQLite constraint：记录表、操作、stable ID，不输出 Secret 或大型 Evidence。
- SQLite busy：bounded retry；超限返回 persistence error。
- JSONL audit 失败：记录 `audit_degraded`，不得报告完整成功。
- Redis 不可用：回退 SQLite Recall，记录 `cache_bypass`，不误计为 miss。
- Import 损坏：保存失败输入并停止 cutover。
- Evaluation provider 失败：计 execution error，不计准确率失败。
- Ground Truth 不足：报告 `INSUFFICIENT_DATA`。

## 10. 恢复与安全边界

- Migration 不删除、移动或覆盖 legacy 文件。
- Cutover 前执行 foreign-key check、row count reconciliation、Event order 和 Evidence hash 校验。
- Rollback 通过配置切回 legacy adapter，SQLite 与 JSONL 均保留。
- API Token 不进入 SQLite、Redis、Trace、JSONL 或 Dashboard。
- 大型 Tool/Evidence 原文只保存引用，避免数据库膨胀。
- 删除 LibreChat/Phoenix 容器、volume 和部署文件需要后续单独授权。

## 11. 实现阶段与 Review 粒度

### 阶段 1：SQLite Schema、Adapter 与 Legacy Import

完成 schema migration、Persistence Port adapter、幂等 importer 和完整性报告。

### 阶段 2：Runtime/Memory/Tracing 累计接入

切换主流程读写，接入异步 job completion、Redis cache-aside、restart recovery 和 JSONL audit 状态。

### 阶段 3：Memory Evaluation Harness

实现 Case 定义、paired replay、指标、Ground Truth provenance 和 gate 状态。

### 阶段 4：中文 Dashboard 与依赖收敛

展示全局/Incident/Session/失败 Case；默认启动只保留 AntiSentinel + Redis；LibreChat/Phoenix 保持暂停，待单独授权后清理。

## 12. 测试策略

每阶段固定顺序：失败测试与针对性回归 → 全量回归 → 用户确认的累计真实 Case → 用户 review。

重点覆盖 schema migration、Event/Evidence 不可覆盖、importer 幂等、transaction rollback、Redis hit/miss/bypass、paired replay 排除、TP/FP/FN、worker crash/restart，以及服务重启后的项目/对话/Memory/Trace 恢复。

## 13. 累计全链路真实 Case（已确认）

```text
Case: SQLite + Redis Memory 全链路与成对评测
范围: PRD-003 SQLite Persistence + Memory Evaluation
输入:
  1. 在隔离目录导入一份当前真实 storage 副本。
  2. 创建 1 个真实 Incident、2 个 Session。
  3. 每个 Session 至少 3 轮中文对话，包含稳定偏好、偏好变更、重复查询和一次 Redis health ToolCall。
  4. 为这些事实创建人工确认 Ground Truth。
运行:
  ANTISENTINEL_STORAGE_ROOT=<隔离目录> 启动 AntiSentinel。
  Redis 使用专用 key prefix。
  先运行 baseline，再运行 optimized，随后重启并恢复查询。
观测:
  SQLite schema/rows/foreign_key_check/import_ledger。
  JSONL audit、Evidence 文件、Redis keys/ZSET/Queue。
  API、SSE、Trace、Token、Dashboard、worker 日志。
预期:
  Import 重跑无重复；领域关联完整。
  业务 completed 与 Memory persisted 分开可见，最终都完成。
  重启后项目、对话、Memory、Trace、Evaluation Run 可恢复。
  Dashboard 显示真实数值及 PASS/FAIL/INSUFFICIENT_DATA，不伪造达标。
失败判定:
  后台未捕获异常、丢失记录、重复导入、hash/外键错误、错误计入指标、重启不可恢复、Dashboard 与数据库不一致。
清理:
  仅停止本 Case 进程并删除隔离目录及专用 Redis key；不触碰当前 storage 和已有 Docker volume。
```

## 14. 验收映射

| 需求 | 可观察产物 | 验收 |
|---|---|---|
| SQLite Adapter | `antisentinel.db`、schema version | 事务测试 + 真实流程关系校验 |
| Legacy Import | `import_ledger`、完整性报告 | 同一输入运行两次，第二次新增 0 行 |
| JSONL Audit | audit record 与 stable ID | 双侧对账，失败状态可见 |
| Redis Cache | hit/miss/bypass、ZSET/Hash | 真实 key/score/member 与回退 |
| 异步 Memory | job lease/status、Memory/Conflict rows | worker 完成、无异常、重启恢复 |
| 85% 命中率 | Evaluation aggregate | 满足样本门槛后判定 |
| 30% Token 降幅 | paired token records | 相同配置 Pair 聚合 |
| 90% 准确率 | TP/FP/FN 与 provenance | 人工/规则 Ground Truth |
| Dashboard | 全局/Incident/Session/Case | UI 与 SQLite 数值一致 |
