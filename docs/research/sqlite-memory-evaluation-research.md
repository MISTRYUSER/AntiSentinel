# SQLite 持久化与 Memory Evaluation 调研

## 调研范围

本记录只回答两个问题：

1. Codex 与 Agno 如何组织本地 Session、Memory、Trace 和回放数据。
2. 哪些做法适合 AntiSentinel，哪些只作为参考而不照搬。

调研时间：2026-09-04。

## Codex：一手实现事实

### 官方源码

来源：[openai/codex `thread-store/src/local/mod.rs`](https://github.com/openai/codex/blob/main/codex-rs/thread-store/src/local/mod.rs)

Codex 的 `LocalThreadStore` 注释明确说明：

- 本地实现同时使用文件系统和 SQLite。
- Rollout JSONL 是耐久回放格式，即使没有 SQLite 也应可读。
- SQLite state DB 是列表、读取等路径使用的可查询元数据索引。
- 活跃写入仍把 canonical history 追加到 JSONL，同时把派生元数据更新到 SQLite。
- SQLite 不可用时，部分分页历史能力会明确返回 unsupported，而不是假装成功。

### 本机只读验证

当前本机 `codex-cli 0.144.4` 的 `$CODEX_HOME` 中存在：

- `state_5.sqlite`：`projects`、`threads`、`thread_sections` 等查询状态。
- `thread_history_1.sqlite`：`thread_turns`、`thread_items`、`thread_realtime_items`。
- `memories_1.sqlite`：`jobs`、`stage1_outputs`。
- `logs_2.sqlite`：结构化日志。
- `sessions/YYYY/MM/DD/rollout-*.jsonl`：按 Session 保存的 rollout 追加记录。

这些本机文件用于验证公开源码描述，但它们不是 OpenAI 承诺的稳定外部接口。

### 官方 issue 暴露的风险

来源：

- [异步持久化未 flush 导致历史丢失](https://github.com/openai/codex/issues/16644)
- [SQLite 元数据与缺失 JSONL 形成孤儿记录](https://github.com/openai/codex/issues/21196)
- [超大 rollout 导致资源问题](https://github.com/openai/codex/issues/29510)

可归纳出的工程风险：

- SQLite 与 JSONL 双层写入必须定义成功顺序和 shutdown barrier。
- SQLite 索引与 JSONL canonical payload 之间必须有一致性校验和修复路径。
- JSONL 不能无限增长，需要轮转、上限或归档策略。
- 不应在主查询路径频繁全量扫描 JSONL。

## Agno：一手文档与源码事实

### 统一 Db interface

来源：[Agno Database](https://docs.agno.com/database/overview)

Agno 把 Session、对话历史、状态、Memory、Knowledge、Trace 和 Evaluation 收口到统一数据库能力中。Agent、Team、Workflow 都接收同一个 `db` 参数，调用方不依赖具体数据库。

官方说明 SQLite 用于本地开发；部署场景选择网络数据库。

### SQLite adapter

来源：[Agno SqliteDb](https://docs.agno.com/reference/storage/sqlite)

`SqliteDb` 实现统一 Db interface，支持 Session、Memory、Metrics、Knowledge、Evaluation、Trace、JSON 数据、schema versioning 和批量 upsert。

### 持久化裁剪

来源：[Agno Storage Control](https://docs.agno.com/sessions/persisting-sessions/storage-control)

Agno 不要求保存所有大对象：媒体可只保存外部引用；Tool message 和历史消息可按需持久化；即使裁剪大型 payload，真实 token metrics 仍可保留。

### 官方源码

来源：[agno-agi/agno database adapters](https://github.com/agno-agi/agno/tree/main/libs/agno/agno/db)

Agno 为不同数据库提供 adapter，但共享 Session、Memory、Metrics、Eval、Knowledge、Trace、Span 等逻辑概念。数据库差异被限制在 adapter 实现内部。

## 参考事实与本项目建议的边界

### 可以吸收

- Codex：canonical append-only replay 与 query index 分层。
- Codex：SQLite 缺失时行为必须显式降级或失败。
- Agno：稳定 Persistence interface 下挂 SQLite adapter。
- Agno：大型 Tool/Evidence 内容文件化，数据库只保存引用、hash 和摘要。
- Session、Memory、Trace 都应按稳定 ID 查询，不依赖扫描日志。

### 不直接照搬

- 不复制 Codex 的多个独立 SQLite 文件；当前规模使用一个数据库和清晰表前缀。
- 不照搬 Agno 的全部通用表和配置面；只实现 PRD-003 需要的 interface。
- 不把 JSONL 与 SQLite 都定义为可独立修改的事实源。
- 不因未来 PostgreSQL 提前引入 ORM；当前使用标准库 `sqlite3`，数据库差异留在 adapter 内。

## 对 AntiSentinel 的结论

```text
SQLite：本地查询与事务持久化
Redis：短时缓存、排序索引、异步队列、幂等
JSONL：不可变审计与恢复输入
文件：大型 Evidence 原文
```

SQLite 写入是业务持久化成功的判定依据；JSONL 是审计副本。两者必须在同一逻辑操作中可观测，后台异步写入必须有 drain/shutdown barrier。

Evaluation 必须使用相同输入做 baseline/optimized 成对回放，并分别计算缓存命中率、Token 降幅和事实级准确率。

