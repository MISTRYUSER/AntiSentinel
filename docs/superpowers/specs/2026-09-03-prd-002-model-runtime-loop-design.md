# PRD-002 模型运行时循环设计

## 背景与范围

PRD-001 已提供 `Incident`、`Session`、`Turn`、`Task`、`ToolCall`、`Attempt`、`Evidence`、`EvidenceRef` 和 `Event` 等领域对象及其状态转换。PRD-002 增加一个最小化、与模型供应商无关的运行时，使单个 Session 能够完成模型规划、顺序执行工具、回灌 Evidence 摘要并输出最终诊断结论。

本设计限定在进程内顺序执行。不实现真实模型 Provider、持久化 checkpoint 后端、并行执行、自动重试、流式输出、检索或审批 UI。

## 设计目标

- 模型边界不依赖任何 LLM SDK。
- 所有模型和工具 payload 都是 JSON 安全的，并且在产生副作用前完成结构校验。
- 保持现有领域聚合 API 不变，使用聚合对象的 pending events 作为运行时事件来源。
- 模型只能看到 Evidence 引用和摘要，不能直接看到 Evidence 原始内容。
- 让限制、失败、审批等待和恢复行为明确且可测试。

## 方案比较

### 方案 A：运行时直接耦合 Provider SDK

实现单个 Provider 会比较快，但会把 Provider 的消息和工具调用类型泄漏到领域层，也会增加 FakeModel 测试的复杂度。不采用。

### 方案 B：事件溯源式运行时状态机

该方案可以提供更强的 replay 能力，但 PRD-001 已经负责聚合状态转换，而 PRD-002 只要求内存 checkpoint。现在引入会在闭环验证前增加基础设施复杂度，延后处理。

### 方案 C：Provider 无关的 Port + 内存适配器（推荐）

在 `ports/model.py`、`worker/runtime/*` 和 `tools/*` 中定义小型 dataclass 与 Protocol。Engine 负责编排现有领域对象、内存事件/Evidence 视图和 checkpoint store。使用 FakeModel 和测试工具验证完整链路，不依赖外部服务。

## 组件与职责

### Model Port（`src/antisentinel/ports/model.py`）

定义以下接口和数据结构：

- `ModelPort.complete(request: ModelRequest) -> ModelResponse`。
- `ModelRequest`：包含 Session/Incident ID、类型化的消息/上下文 payload 和可用工具清单。
- `ModelResponse`：包含经过校验的 `FinalDiagnosis` 或 `TaskPlan`，以及用于失败留痕的原始响应摘要。
- `ModelTimeoutError` 和 `ModelError`：与具体 Provider 无关的异常类型。

解析器只接受明确的 `final` 或 `tasks` 结构。缺失分支、无效字段、未知或无效 Task 字段、非 JSON 参数、空工具名、同一响应中的重复 Task ID，以及超过 Task/ToolCall 限制的结果，都必须拒绝。不得猜测工具名或参数。

### 消息与上下文（`worker/runtime/messages.py`、`context.py`）

模型可见数据使用 JSON 可序列化的字典或 dataclass。`ContextBuilder` 从以下内容构建请求：

- Incident 的 ID、标题、来源、状态和摘要。
- Session 的 ID、状态和历史 Turn 摘要。
- 当前 Turn 的 ID，以及此前的 Task 结果摘要。
- 已注册且可用的工具清单。
- Evidence 引用及其 `evidence_id`、role、类型/来源元数据和结果摘要。

原始结果 payload 和 Evidence 本体不得进入模型上下文。`messages.py` 负责稳定地构造消息，以便 checkpoint 能保存模型实际收到的完整输入。

### 工具注册与执行（`tools/*`、`worker/execution/tool_executor.py`）

定义 `ToolDefinition`，包含工具名、描述、JSON 参数 schema、是否需要审批以及可调用的 handler。`ToolRegistry` 按精确名称解析工具。`ToolExecutor` 执行以下流程：

1. 解析工具名，并依据定义校验参数。
2. 工具不存在或策略校验失败时，不调用 handler，返回结构化拒绝结果。
3. 工具需要审批时，不调用 handler，返回结构化的审批等待结果。
4. 对允许执行的调用调用 handler，并将成功/失败统一为 `ToolExecutionResult`。

Executor 不负责领域 ID 或重试。Runtime 创建 `ToolCall` 和 `Attempt`，记录 Evidence 及其引用，并将所有生成的 ID 关联到 Incident、Session、Turn、Task 和 ToolCall。

### Runtime Engine 与 Loop（`worker/runtime/loop.py`、`engine.py`）

`RuntimeEngine.run(incident, session, model, ...)` 是公开入口，负责配置校验，并为每一次模型调用创建一个 Turn。主循环如下：

1. 启动 Turn，保存当前 checkpoint，并构建模型请求。
2. 记录/保存 `model.called`，调用 ModelPort，并标准化模型响应。
3. 如果是最终输出，则完成 Turn 和 Session，并返回 `RuntimeResult`。
4. 如果是 Task 计划，则按顺序创建并启动 Task。对每个声明的 ToolCall，创建/启动 ToolCall，创建/启动 Attempt，执行一次，结果有 Evidence 时附加 `EvidenceRef`，然后完成 Attempt 和 ToolCall。
5. 完成每个 Task 并生成紧凑的结构化结果，将所有 Task 结果加入下一轮上下文后继续。
6. 每次模型响应和工具结果之后保存 checkpoint。发生暂停或失败时立即停止，并返回明确的 `RuntimeResult`。

Engine 使用现有领域对象的状态转换方法。当前领域层的 `TurnStatus.WAITING_TOOL` 用于表示 Turn 级别的运行时暂停；返回结果使用独立的 `waiting_approval` 状态和错误码表达审批原因，因此不需要修改 PRD-001 的状态枚举。

### Checkpoint（`worker/runtime/checkpoint.py`）

定义 `CheckpointStore` Protocol，包含 `save(snapshot)`、`load(session_id)` 和 `clear(session_id)`，并提供 `InMemoryCheckpointStore`。Snapshot 保存以下内容：

- Incident、Session、Turn、Task、ToolCall、Attempt 的序列化状态。
- 精确的模型消息/上下文。
- 当前回合数。
- 当前 Task/ToolCall 执行位置。
- 已成功执行的 ToolCall ID。
- 最近一次错误。

恢复时，只有持久化 Attempt 已成功的 ToolCall 才可以跳过。已经创建但尚未成功完成的调用不得直接盲目重放；应将其表示为失败/中断工作，并以执行错误返回模型，从而避免隐式重复副作用。

## Runtime 结果与错误语义

`RuntimeResult` 始终包含 Session ID、Incident ID、状态（`completed`、`failed` 或 `waiting_approval`）、可选的最终诊断、Evidence 引用、Task 执行摘要、Turn 数量和错误详情。

- 模型超时或 Provider 异常：当前 Turn 和 Session 标记为失败，记录 `model.failed` 和错误信息。
- 模型响应非法：当前 Turn 和 Session 标记为失败，记录 `model.failed`，保留有长度限制的原始响应摘要，但不执行任何工具。
- 工具不存在或参数无效：不调用 handler，记录被拒绝的 ToolCall/Attempt 结果，并把失败信息继续返回给模型。
- 工具异常或超时：将 Attempt 完成为失败/超时，记录 `tool_call.result_received` 和 `task.failed`，再由模型决定是否结束或继续。
- 工具需要审批：调用前停止，Turn/Session 进入等待状态，保存 checkpoint，并返回 `waiting_approval`。
- 超过限制：使用稳定错误码 `max_turns_exceeded`、`max_tasks_exceeded` 或 `max_tool_calls_exceeded` 标记失败。

所有运行时事件都必须通过 `related_ids` 或 payload 携带相关 ID：在适用时包括 `incident_id`、`session_id`、`turn_id`、`task_id` 和 `tool_call_id`。至少需要记录以下事件：`turn.started`、`model.called`、`model.responded`、`model.failed`、`tool_call.requested`、`tool_call.result_received`、`task.completed`、`task.failed`。

## 指标

提供内存指标收集器，记录以下计数和耗时：模型调用、模型成功率、工具调用、工具成功率、Session Turn 数、每个 Turn 的 Task 数、达到最大回合的 Session 数，以及最终答案是否包含 Evidence 引用。指标只负责运行时观测，不改变领域行为。

## 测试策略

增加以下单元测试：模型输出 JSON 解析、上下文脱敏、工具注册与 schema 校验、工具结果标准化、checkpoint round-trip。

使用脚本化 `FakeModel` 和确定性的只读 Mock Tool 增加集成测试，覆盖：

- 模型直接返回最终结果并完成 Session。
- 一个 Turn 返回多个 Task。
- 一个 Task 顺序执行多个 ToolCall。
- 工具结果回灌后模型继续并返回最终结果。
- 非法模型输出且工具调用次数为零。
- 工具失败结果回灌模型。
- 审批等待。
- 最大回合数限制。
- 模型超时。
- checkpoint 恢复且不重复执行已成功工具。
- 完整的“模型 → 多 Task → 多 ToolCall → EvidenceRef → 模型 → 最终结果”链路。

现有 PRD-001 的 58 个测试必须继续通过。整个测试过程不依赖外部服务或 SDK。

## 自审结果

- P0 路径没有未解决的占位项或待定决策。
- 设计保持 PRD-001 状态枚举不变，通过 RuntimeResult 表达审批等待，并使用现有 `waiting_tool` Turn 转换。
- Provider 输出会在任何工具副作用发生前完成校验。
- Evidence 脱敏逻辑集中在 `ContextBuilder` 中。
- 恢复逻辑区分成功 Attempt 与中断工作，不会静默重复执行成功工具。
- 所有组件都位于已有预留模块或对应测试文件中，范围可由一个实现计划完成。
