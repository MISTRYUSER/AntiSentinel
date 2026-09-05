# PRD-001 Domain Kernel Foundation

## 基本信息

- **状态**：Ready
- **提出角色**：AntiSentinel PM
- **目标**：建立能够支撑第一条诊断链路的最小领域模型
- **优先级**：P0
- **依赖**：当前项目骨架
- **后续依赖**：PRD-002 Model Runtime Loop

## 1. 背景与问题

当前 `domain` 目录只有模块骨架，但协议、Worker Runtime、编排和持久化都会依赖统一的领域对象。如果没有明确的领域状态和转换规则，大模型 Loop 很容易把会话、任务、工具调用和执行结果混在一起，导致无法恢复、无法审计，也无法判断一次诊断是否完成。

本 PRD 只建立最小领域内核，不追求一次完成完整的事件溯源、DAG 编排或所有外部适配器。

## 2. 领域层级

```text
Incident（一个 Bug / 错误）
└── Session（围绕该 Bug 的一次对话）
    └── Turn（对话中的一个阶段 / 模型回合）
        └── Task（这个阶段要完成的具体事情）
            └── ToolCall（任务调用工具）
                └── Attempt（工具的一次实际执行尝试）
```

- `Incident` 是要解决的问题，同一 Incident 可以有多个 Session。
- `Session` 是围绕 Incident 的一次用户或 Agent 对话。
- `Turn` 是 Session 中的一次对话阶段。
- `Task` 是 Turn 中拆出的具体工作，一个 Turn 可以包含多个 Task。
- `ToolCall` 是 Task 发起的一次工具调用，一个 Task 可以调用多个工具。
- `Attempt` 是 ToolCall 的一次实际执行尝试，用于记录重试和执行结果。

## 3. 用户与使用场景

### 场景 A：创建一次诊断

```text
Incident
→ 创建 Session
→ Session 创建 Turn
→ Turn 拆分多个 Task
→ Task 产生一个或多个 ToolCall
→ ToolCall 形成 Attempt
→ Attempt 关联 Evidence
→ 产生 Event
```

### 场景 B：推进任务状态

系统能够区分任务处于 `pending`、`running`、`waiting_approval`、`succeeded`、`failed`、`cancelled` 等状态，并拒绝非法状态转换。

### 场景 C：审计一次诊断

给定 `incident_id` 或 `session_id`，可以通过稳定 ID 将 Turn、Task、ToolCall、Attempt、Evidence 和 Event 关联起来。

## 3. 目标

- 实现稳定、可序列化的领域对象：`Incident`、`Session`、`Turn`、`Task`、`ToolCall`、`Attempt`、`Event`、`Evidence`。
- 明确对象之间的关联关系和生命周期。
- 为任务、步骤、回合和工具调用定义最小状态机。
- 所有领域对象使用项目已有的稳定 ID 类型。
- 领域对象不依赖 LLM SDK、Worker、数据库或具体 Web 框架。
- 领域对象可以被 PRD-002 的 Runtime Loop 直接使用。
- 对非法状态转换、缺失关联和无效输入提供明确异常。

## 4. 非目标

- 不实现真实数据库或 Event Store。
- 不实现完整 Event Sourcing 重放。
- 不实现 DAG 调度和并发策略。
- 不实现 LLM Provider Adapter。
- 不实现审批 UI、通知渠道或远程 Worker。
- 不引入复杂 ORM、领域事件总线或通用工作流框架。

## 5. 最小领域模型

### 5.1 核心关系

```text
Incident 1 ── N Session
Session 1 ── N Turn
Turn 1 ── N Task
Task 1 ── N ToolCall
ToolCall 1 ── N Attempt
Attempt 1 ── N EvidenceRef
所有状态变化 ── N Event
```

### 5.2 对象要求

- `Incident`：故障或诊断入口，至少包含 ID、标题/摘要、来源、状态、创建时间。
- `Session`：围绕 Incident 的一次对话运行，至少包含 ID、`incident_id`、参与者和状态。
- `Turn`：Session 中的一次对话阶段，至少包含 ID、`session_id`、上下文摘要、输出摘要和状态。
- `Task`：Turn 中需要完成的具体工作，至少包含 ID、`turn_id`、目标、状态和结果摘要。
- `ToolCall`：Task 请求执行的工具名称、参数、目标和状态；参数必须是可序列化数据。
- `Attempt`：一次实际执行尝试，记录重试序号、开始/结束时间、结果状态和错误。
- `Evidence`：不可变证据元数据；原始内容通过 `EvidenceRef` 引用，不直接嵌入状态对象。
- `Event`：追加式事实记录，至少包含事件 ID、类型、聚合 ID、关联 ID、时间和 payload。

### 5.3 状态规则

第一版至少支持：

```text
Session: active → waiting → completed/failed
Turn: pending → running → waiting_tool → completed/failed
Task: pending → running → waiting_tool → succeeded/failed/cancelled
ToolCall: requested → running → succeeded/failed/denied/timed_out
Attempt: created → running → succeeded/failed/timed_out
```

每个状态对象必须通过显式方法或统一状态转换函数变更，不允许调用方随意写入任意状态。

## 6. Event 与 Evidence

至少定义以下事件类型：

```text
incident.created
session.created
session.completed
task.created
task.started
turn.started
turn.completed
tool_call.requested
tool_call.started
tool_call.succeeded
tool_call.failed
attempt.started
attempt.completed
evidence.recorded
```

事件 payload 必须可 JSON 序列化。Evidence 只保存不可变元数据和内容引用，不要求本 PRD 实现内容存储。

## 7. 错误处理

- 非法状态转换：抛出领域异常，不产生成功事件。
- 缺少必需关联：创建对象时拒绝。
- 空工具名、空 ID、不可序列化参数：校验失败。
- 重复处理：领域对象通过 ID 和状态保证不会重复推进；幂等存储留给后续 PRD。
- 时间字段统一使用 timezone-aware UTC 时间。

## 8. 验收标准

- [ ] 所有核心领域对象可构造、校验和 JSON round-trip。
- [ ] Incident 到 Evidence 的关联链路可以完整表达。
- [ ] Session、Turn、Task、ToolCall、Attempt 的合法状态转换可执行。
- [ ] 非法状态转换会失败且不会伪造事件。
- [ ] 事件包含稳定聚合/关联 ID，并且 payload 可序列化。
- [ ] 领域层不导入 LLM、Worker、数据库或 Web 框架依赖。
- [ ] 可以用领域对象表达一次成功工具调用和一次失败工具调用。

## 9. 推荐测试用例

```text
test_incident_creates_session
test_incident_has_multiple_sessions
test_session_contains_multiple_turns
test_turn_contains_multiple_tasks
test_task_contains_multiple_tool_calls
test_task_state_transitions
test_illegal_task_transition_is_rejected
test_turn_records_model_output_kind
test_tool_call_requires_serializable_arguments
test_attempt_records_failure
test_evidence_is_immutable
test_event_contains_correlation_ids
test_domain_objects_json_round_trip
test_domain_has_no_infrastructure_dependency
```

## 10. 完成定义

领域内核对象、状态规则和测试全部完成，并且能够支撑 PRD-002 建立以下最小链路：

```text
Incident → Session → Turn → Task → ToolCall → Attempt → Evidence → Event
```
