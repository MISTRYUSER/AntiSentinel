# Memory & Evidence Foundation 外部实现研究

日期：2026-09-04

## 研究目的

为 PRD-003 的分层记忆、后台异步 LLM 抽取、Token-aware digest 和 tracing 设计提供一手实现依据。本文只记录参考项目事实与对 AntiSentinel 的启示，不把外部项目实现当作本项目必须照搬的方案。

## Codex 官方实现事实

参考：

- [OpenAI Codex memories README](https://github.com/openai/codex/blob/main/codex-rs/core/src/memories/README.md)
- [OpenAI Codex memories read path](https://github.com/openai/codex/blob/main/codex-rs/ext/memories/templates/memories/read_path.md)
- [OpenAI Codex memories consolidation template](https://github.com/openai/codex/blob/main/codex-rs/memories/write/templates/memories/consolidation.md)

从官方仓库可确认的设计事实：

1. Memory pipeline 在 root session 启动时运行，要求 session 非 ephemeral、功能开启、不是 sub-agent session，并且 state DB 可用。
2. pipeline 在后台异步执行，至少分为按 rollout 的抽取阶段和后续 consolidation 阶段，两个阶段有明确先后关系。
3. read path 不是无条件注入所有记忆，而是先根据当前请求判断是否需要 memory，再按关键词查找相关条目。
4. consolidation 输出面向检索和复用的 durable memory，并强调在没有有效信号时保持最小输出、保留来源/可追溯结构。

对 AntiSentinel 的启示：

- 将主诊断链路与 memory extraction/consolidation 解耦，用可重试的后台任务承载 LLM 工作。
- Memory read 必须是有范围、有条件、有预算的 Context View；不能把存储中的全部内容拼入 ModelRequest。
- 应把原始 Event/Evidence 与可重建的 summary、alias、preference 明确分层。

## Agno 官方实现事实

参考：

- [Agno Team implementation](https://github.com/agno-agi/agno/blob/main/libs/agno/agno/team/team.py)
- [Agno Agent run events](https://github.com/agno-agi/agno/blob/main/libs/agno/agno/run/agent.py)
- [Agno session summary token-aware gating issue](https://github.com/agno-agi/agno/issues/8342)

从官方仓库与 issue 可确认的设计事实：

1. Team/Agent 将 `session_id`、session state、历史读取、session summary、user memory、tool-result compression 作为不同的能力开关。
2. Agno 支持将 session summary 放入下一次上下文，也支持独立的 user memory 更新；两者不是同一个存储概念。
3. run event 类型中有 memory update、session summary、model request、tool call、compression 等阶段事件，说明记忆操作适合纳入统一运行事件/观测链路。
4. Agno issue #8342 指出：摘要不应每轮无条件创建，而应在 Token 使用超过阈值时创建，并在摘要后只保留最近若干轮；当前 compression manager 主要针对 tool-result，不能代替完整 session history compaction。

对 AntiSentinel 的启示：

- Session timeline、session digest、operator preference、tool output compression 要保持独立 Port 和独立指标。
- `SessionDigest` 应按预算/阈值触发，默认只输出“摘要 + 最近窗口”，并且丢失后可由事实源重建。
- memory update、summary、lookup、fallback、compression 都应成为同一 trace 下可关联的 span/event，而不是只看 Redis 的单项监控。

## Codex Rollout Trace 与 OTel 观测目录

参考：

- [Codex Rollout Trace README](https://github.com/openai/codex/blob/main/codex-rs/rollout-trace/README.md)
- [Codex raw trace event schema](https://github.com/openai/codex/blob/main/codex-rs/rollout-trace/src/raw_event.rs)
- [Codex OTel event catalog](https://github.com/openai/codex/blob/main/codex-rs/otel/README.md)
- [Codex MCP tool telemetry](https://github.com/openai/codex/blob/main/codex-rs/core/src/mcp_tool_call/telemetry.rs)

Codex 官方 Rollout Trace 的事实：

1. Rollout tracing 是可选的本地诊断 bundle，不等同于 telemetry；启用后会写 `manifest.json`、有序 append-only `trace.jsonl`、大 payload 引用目录和可选的 reducer `state.json`。
2. Trace writer 先写原始观察，再由离线 reducer 解释成语义图；运行热路径不在当场构建最终图。
3. Raw event envelope 包含 schema version、writer sequence、wall time、rollout_id、thread_id、turn_id 和 typed payload。
4. 观测边界包括 rollout/thread/turn 生命周期、inference started/completed/cancelled、tool call started/runtime started/runtime ended/ended、code cell、terminal operation、compaction、子 Agent spawn/task/result/close，以及它们之间的信息流边。
5. Raw payload 与 reduced graph 分离：模型可见 ConversationItem、ToolCall、InferenceCall、Compaction、InteractionEdge 各自表达不同事实，原始请求/响应/工具输出通过 RawPayloadRef 追溯。
6. Codex 明确区分 rollout_id 与 trace_id；父子 Agent 可以共享一个 root writer，但独立顶层线程可以有独立 bundle。
7. Codex 的 OTel 事件 catalog 包含 conversation start、API request、SSE event、user prompt、tool decision/result、token counts 等；公共字段包括 conversation.id、model、app.version、terminal.type 等。用户 Prompt 默认脱敏。
8. MCP telemetry 将工具失败区分为请求/传输层错误和工具返回 `isError=true`，并记录 duration、tool/server/connector 等低敏字段。

对 AntiSentinel 的建议（本项目推断）：

```text
incident.run
  ├── session.started / session.completed
  ├── turn.started / turn.completed
  ├── model.request / model.response / model.parse
  ├── task.created / task.completed
  ├── tool_call.requested / tool_call.completed
  ├── attempt.started / attempt.completed
  ├── memory.lookup / memory.write / memory.classify
  ├── context.build / context.pruned
  └── worker.job.claim / retry / ack / dead_letter
```

每个事件建议统一包含：`schema_version`、`seq`、`wall_time`、`trace_id`、`request_id`、`parent_request_id`、`causation_id`、`incident_id`、`session_id`、`turn_id`，并按需要添加 `task_id`、`tool_call_id`、`attempt_id`、`memory_id`。大请求/响应/工具原文只保存脱敏后的 `payload_ref`，不直接写入 OTel attributes。

不能照搬的部分：Codex 的 thread/rollout/terminal/code-mode/子 Agent 对象属于 Codex 自身领域；AntiSentinel 应保留自己的 Incident/Session/Turn/Task/ToolCall/Attempt/Evidence 语义，不为了模拟 Codex 而引入无关对象。

## 本项目建议与外部事实的边界

外部项目没有替 AntiSentinel 决定 Redis key、Evidence hash 幂等、Incident/Operator 物理隔离或准确率标注集。因此以下内容属于本项目建议：

- Event/Evidence 是唯一事实源；Redis 只作为长期结构化记忆的热缓存和 summary cache。
- PreferenceGraph 的 durable adapter 默认采用本地 append-only JSONL；Redis 通过版本号和 TTL 做 cache-aside。
- 实体消解先返回 canonical candidate，再由确定性规则与冲突合并器决定是否生效；低置信度结果只进入候选/审计记录。
- 85% 命中率、30% Token 节省、90%+ 记忆准确率必须以固定时间窗、memory type、cache layer 和离线标注集分别验收，不能仅凭平均 Redis hit rate 宣称达标。
