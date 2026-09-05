# PRD-007 Tool Registry & Policy Guardian

## 基本信息

- **状态**：Planned
- **提出角色**：AntiSentinel PM
- **目标**：让每一次 ToolCall 都经过可审计、可拒绝、可审批的安全执行边界
- **优先级**：P0
- **依赖**：PRD-003 Memory & Evidence Foundation、PRD-006 Plan

## 0. 当前代码基线

PRD-002 已经完成并通过当前测试。现有代码已经具备一部分 Tool 基础能力：

- `ToolDefinition`：工具名称、描述、参数 schema、handler、审批标记、只读标记。
- `ToolRegistry`：精确名称解析、重复注册拒绝、manifest 输出、基础参数校验。
- `ToolDiscovery`：显式模块发现，支持 `TOOL` / `TOOLS` 导出，不扫描任意函数。
- `ToolExecutor`：未知工具拒绝、参数错误拒绝、审批等待、handler 异常标准化。
- `ToolExecutionResult`：成功、失败、拒绝、等待审批四种结果。

当前尚未实现、正是本 PRD 的工作范围：

- `security/policy.py` 的策略接口和策略决策模型。
- `security/guardian.py` 的统一 ToolCall 安全检查入口。
- `security/approval.py` 的审批请求、批准、拒绝和恢复语义。
- `security/sandbox.py` 的最小执行边界。
- 工具执行结果与 Domain `ToolCall` / `Attempt` / `EvidenceRef` / `Event` 的完整连接。

## 1. 背景与问题

模型可以提出任意工具调用，但模型输出本身不具备权限。AntiSentinel 面对的是生产 Bug，工具可能读取敏感数据、访问不同环境，甚至执行有副作用的操作。因此工具调用必须在真正执行前经过统一的注册、参数、目标、风险、权限和审批判断。

本 PRD 不扩展真实 SSH、Docker 或 Kubernetes 能力，只把安全执行 seam 建好，并使用 Mock Tool 验证行为。

## 2. 产品定义

```text
Model 输出 ToolCall
→ ToolRegistry 解析工具
→ 参数 Schema 校验
→ Policy 决策
→ Guardian 统一判定
→ denied / waiting_approval / allowed
→ 允许后进入 ToolExecutor
→ 生成 Attempt / EvidenceRef / Event
```

安全原则：

- 未注册工具默认拒绝。
- 参数无法校验默认拒绝。
- 目标环境无法识别默认拒绝。
- 权限信息不足默认拒绝或等待人工审批，不默认放行。
- 审批只针对具体 `tool_call_id` 和参数快照，参数变化后必须重新审批。
- 工具集合在一个 Session 内保持稳定，不允许模型动态增加工具。

## 3. 目标

- 定义 provider-neutral 的 `PolicyPort` 和 `PolicyDecision`。
- 定义 `Guardian`，为每次 ToolCall 返回 `allowed`、`denied` 或 `waiting_approval`。
- 支持按工具风险、`actor`、目标环境、Incident/Session 上下文做策略判断。
- 支持审批请求的创建、批准、拒绝、过期和恢复。
- 审批前后都重新执行参数和策略校验。
- 将安全结果映射到 Domain 状态：ToolCall、Task、Turn、Session。
- 允许通过配置注入不同策略实现，不把策略写死在 ToolExecutor 中。
- 将每次决策记录为可审计 Event，并包含决策原因和关联 ID。
- 保持现有 Registry/Executor API 可复用，避免重复实现工具发现和 handler 调用。

## 4. 非目标

- 不实现真实用户/组织权限中心。
- 不实现 OPA/Rego、RBAC 服务或策略 DSL。
- 不实现真实 SSH、Docker、Kubernetes 沙箱。
- 不实现审批 UI 或通知渠道；第一版提供内存审批 Port。
- 不实现工具并发、自动重试和 DAG 调度。
- 不允许通过字符串黑名单假装完成命令安全。

## 5. 工具风险与策略

`ToolDefinition` 需要扩展为可供策略使用的最小风险信息：

```text
tool_name
description
argument_schema
read_only
requires_approval
risk_level: low | medium | high | critical
allowed_targets
```

第一版默认策略：

| 条件 | 决策 |
|---|---|
| 未注册工具 | denied |
| 参数不符合 schema | denied |
| 只读工具 + 允许目标 | allowed |
| `requires_approval=true` | waiting_approval |
| 写操作或 high/critical 风险 | waiting_approval |
| 目标不在允许范围 | denied |
| actor 缺失或无权限 | denied |

策略决策至少包含：

```text
decision
reason_code
reason_message
policy_version
requires_approval
expires_at
```

## 6. 审批流程

```text
ToolCall requested
→ Guardian 发现需要审批
→ 创建 ApprovalRequest
→ Task/Turn/Session waiting
→ operator approve / reject / expire
→ approve 后重新校验参数与策略
→ allowed 才能执行
→ reject/expire 则 ToolCall denied，Task 按策略失败或结束
```

审批请求必须绑定：

- `incident_id`
- `session_id`
- `turn_id`
- `task_id`
- `tool_call_id`
- 工具名和参数快照
- 目标环境
- 请求人和审批人
- 创建、决定、过期时间

审批不能只绑定工具名。相同工具但参数、目标或 actor 变化时，必须创建新的审批请求。

## 7. Sandbox 边界

第一版 Sandbox 只提供抽象 Port 和 Mock 实现，至少表达：

- 允许访问的目标引用。
- 允许的环境标签，如 `test` / `staging` / `prod`。
- 超时和资源限制。
- 是否允许网络、文件或主机副作用。

实际执行必须通过 Guardian/Sandbox 给出的上下文进入 handler；handler 不得自行绕过策略对象获取未授权目标。

## 8. Domain 与 Event

每次 ToolCall 安全决策至少产生一个 Event：

```text
tool_call.policy_checked
tool_call.denied
tool_call.approval_requested
tool_call.approved
tool_call.rejected_after_approval
tool_call.execution_allowed
```

Event 必须携带或可关联：

```text
incident_id
session_id
turn_id
task_id
tool_call_id
approval_request_id
policy_version
```

执行成功后仍由 Attempt 记录结果；工具产生的原始内容存为 `Evidence`，Domain 对象和模型上下文只持有 `EvidenceRef` 及摘要。

## 9. 错误与安全语义

- 任何策略异常都 fail closed：不得因为策略服务异常而放行。
- 参数校验失败不得调用 handler。
- 未审批不得调用 handler。
- 审批过期不得调用 handler。
- handler 抛错由 ToolExecutor 标准化为失败 Attempt，不吞掉错误关联信息。
- Guardian 不负责重试；重试由后续调度层决定，每次重试都要重新检查策略。
- 不能通过模型返回的字段覆盖服务端 actor、target 或权限上下文。

## 10. 验收标准

- [ ] 现有工具 Registry/Discovery/Executor 测试保持通过。
- [ ] 已注册只读工具在合法 actor 和目标下被允许执行。
- [ ] 未注册工具、非法参数、未知目标和缺失 actor 被拒绝。
- [ ] 高风险或需审批工具生成绑定具体 ToolCall 的审批请求。
- [ ] 未审批、拒绝或过期的 ToolCall 不会调用 handler。
- [ ] 批准后会重新进行参数和策略校验。
- [ ] 修改参数或目标后不能复用旧审批。
- [ ] 每个决策都生成包含关联 ID 和原因的 Event。
- [ ] ToolCall/Task/Turn/Session 状态与安全决策一致。
- [ ] 工具成功结果可关联 Attempt、EvidenceRef 和摘要。
- [ ] 策略异常时执行 fail closed。

## 11. 推荐测试用例

```text
test_allowed_read_only_tool_executes
test_unknown_tool_is_denied
test_invalid_arguments_are_denied_before_handler
test_unknown_target_is_denied
test_missing_actor_is_denied
test_high_risk_tool_creates_approval_request
test_unapproved_tool_never_executes
test_rejected_approval_never_executes
test_expired_approval_never_executes
test_approved_call_is_revalidated_before_execution
test_changed_arguments_cannot_reuse_approval
test_policy_exception_fails_closed
test_security_decision_emits_auditable_event
test_execution_links_attempt_evidence_ref_and_summary
```

## 12. 完成定义

使用 Mock Policy、Mock Approval Store、Mock Sandbox 和至少三个工具：只读工具、需审批工具、非法目标工具，稳定验证：

```text
Model ToolCall
→ Registry
→ Schema
→ Policy Guardian
→ Approval/Sandbox
→ ToolExecutor
→ Attempt
→ EvidenceRef
→ Event
```

并证明任何未明确允许的调用都不会进入 handler。

## 13. 参考资料

以下资料只作为设计参考，不要求照搬外部 SDK：

- [OpenAI Agents SDK Guardrails](https://github.com/openai/openai-agents-python/blob/main/docs/guardrails.md)：工具级 guardrail 应在每次工具调用前后执行。
- [OpenAI Agents SDK Human-in-the-loop](https://github.com/openai/openai-agents-python/blob/main/docs/human_in_the_loop.md)：审批绑定具体 ToolCall，批准后可以恢复运行。
- [OpenAI Agents SDK Running Agents](https://github.com/openai/openai-agents-python/blob/main/docs/running_agents.md)：工具找不到时可将错误回传模型，而不是隐式执行。
