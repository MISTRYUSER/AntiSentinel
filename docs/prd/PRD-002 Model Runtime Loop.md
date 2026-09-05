# PRD-002 Model Runtime Loop

## 基本信息

- **状态**：Ready
- **提出角色**：AntiSentinel PM
- **目标**：跑通大模型驱动的一次诊断回合闭环
- **优先级**：P0
- **依赖**：PRD-001 Domain Kernel Foundation（已实现并通过当前 58 个测试）

## 0. 当前代码基线

PRD-001 的领域链路已经落地：

```text
Incident → Session → Turn → Task → ToolCall → Attempt → EvidenceRef
```

当前已经具备：

- `Session`、`Turn`、`Task`、`ToolCall`、`Attempt`、`Evidence`、`EvidenceRef`、`Event` 领域对象。
- 各对象的创建校验、状态转换、JSON round-trip 和 pending Event。
- `Attempt` 对 `EvidenceRef` 的引用、工具结果摘要/外部结果地址和重试序号。
- `WorkerGateway` 协议接口。

当前尚未实现、正是本 PRD 的工作范围：

- `ports/model.py` 中的 Model Port。
- `worker/runtime/context.py`、`messages.py`、`loop.py`、`engine.py`、`checkpoint.py`。
- Worker Handler、ToolExecutor 和真实的模型→Task→ToolCall→结果回灌流程。

## 1. 背景与问题

AntiSentinel 的核心价值不是单独调用模型，而是让模型基于 Incident 上下文进行诊断，在需要时请求工具，并把工具结果继续带回模型，最终形成可审计的诊断结果。

当前 Worker Runtime 目录已经预留 `context`、`messages`、`loop`、`engine` 和 `checkpoint` 模块，但实现仍为空壳。本 PRD 只实现单 Session、单 Worker、有限 Turn 的最小 Loop，验证产品主路径。

## 2. 用户与使用场景

```text
用户提交 Incident
→ 系统创建或恢复 Session
→ Session 开始一个 Turn
→ 模型读取 Incident / Session 上下文
→ 模型将当前阶段拆分为一个或多个 Task
→ 每个 Task 执行零个或多个 ToolCall
→ 汇总 Task 结果并回传模型
→ 模型进入下一 Turn，或输出诊断结论
→ Session 完成并返回结果
```

## 3. 目标

- 定义 `ModelPort` 的最小调用接口和结构化模型输出，不绑定具体 LLM SDK。
- 构建只包含必要信息的模型上下文：Incident、Session、历史 Turn、当前 Turn 的 Task、工具清单和 Evidence 摘要。
- 支持模型输出最终结论或一个 Task 列表；Task 列表中的 ToolCall 参数必须可校验、可序列化。
- 单个 Session 支持配置最大 Turn 数，防止无限 Loop。
- 一个 Turn 可以创建多个 Task；一个 Task 可以执行多个 ToolCall。
- Task 结果汇总后回灌模型并进入下一 Turn；Evidence 本体不直接注入模型上下文。
- 每一轮都写入 Turn、Task、ToolCall、Attempt 和 Event，并关联 `incident_id/session_id/turn_id/task_id/tool_call_id`。
- Session 完成时返回诊断结论、Evidence 引用和 Task 执行摘要。
- 在超时、模型错误、工具错误和超过最大回合时安全结束。

## 4. 非目标

- 不实现自主多任务并发；一个 Turn 内的多个 Task 第一版顺序执行。
- 不实现 DAG、复杂规划和自动重试策略。
- 不接入真实日志、指标、SSH 或 Kubernetes 工具。
- 不实现流式输出、长期记忆和 Embedding 检索。
- 不实现审批 UI；遇到需审批的 ToolCall 只返回等待状态。
- 不实现真实 LLM Provider；使用 `FakeModel` 验证 Loop，Provider Adapter 留给后续工作。

## 5. Loop 行为

### 5.1 模型输出协议

模型输出必须解析为结构化结果：

```text
final:
  summary
  diagnosis
  confidence
  evidence_refs

tasks:
  - task_id
    objective
    tool_calls:
      - tool_name
        arguments
        target_ref
```

无法解析的模型输出视为 `failed`，不得猜测工具名或参数。

### 5.2 主循环

```text
while turn_count < max_turns:
    build_context()
    model_output = model.complete(context)

    if final:
        record_turn_completed()
        complete_session()
        return result

    if tasks:
        for task in tasks:
            create_task(task)
            start_task(task)
            for tool_call in task.tool_calls:
                validate_tool_call()
                execute_tool()
                record_attempt_and_evidence()
            complete_task_with_results()
        append_task_results_to_context()
        continue

fail(max_turns_exceeded)
```

第一版默认 `max_turns=8`，每个 Turn 默认最多创建 `max_tasks=8` 个 Task，每个 Task 默认最多执行 `max_tool_calls=8` 次 ToolCall。第一版按顺序执行，不要求并发。调用方可以配置更小值，但不能绕过上限校验。

### 5.3 工具结果

工具成功时，模型只能看到结构化结果和 EvidenceRef 对应的摘要；`Evidence` 本体及其原始内容不直接注入上下文。一个 Turn 中多个 Task 的工具结果先聚合为 Task Result，再回灌模型。工具失败时，模型收到可识别的失败信息，并由 Loop 决定是否结束，不在本 PRD 中自动重试。

## 6. Checkpoint 与恢复

第一版 checkpoint 使用内存实现或最小 Port，不要求接入数据库。每次模型调用前后至少保存：

- 当前 Session/Turn/Task 状态
- 当前 Turn 产生的 Task、已执行 ToolCall 和 Attempt 引用
- 上下文版本或消息列表
- 当前回合数
- 最近一次错误

进程中断后允许从最近一个 checkpoint 恢复；恢复不得重复执行已经标记成功的 ToolCall。

## 7. 错误、超时和权限

- 模型调用超时：Session/Turn 标记 `failed`，记录错误 Event。
- 模型返回非法结构：Session/Turn 标记 `failed`，保留原始模型响应的 Evidence 摘要。
- ToolCall 未通过工具注册或策略校验：不执行，记录拒绝结果。
- ToolCall 需要审批：Task/Turn 进入 `waiting_approval`，不继续调用模型。
- 达到最大回合：Session 标记 `failed`，错误码为 `max_turns_exceeded`。
- 任意异常都必须保留关联 ID，不能只写普通日志而丢失诊断链路。

## 8. Event、Evidence 与指标

至少记录：

```text
turn.started
model.called
model.responded
model.failed
tool_call.requested
tool_call.result_received
task.completed
task.failed
```

至少提供以下指标：

- 模型调用次数、成功率和延迟
- 每个 Session 的 Turn 数和每个 Turn 的 Task 数
- ToolCall 数、成功率和延迟
- 达到最大回合的 Session 数
- 最终结论是否包含 Evidence 引用

## 9. 验收标准

- [ ] `FakeModel` 可以返回最终结论并完成 Session。
- [ ] `FakeModel` 可以在一个 Turn 中返回多个 Task。
- [ ] 一个 Task 可以顺序执行多个 ToolCall，Loop 能汇总结果后继续下一 Turn。
- [ ] 每轮都能正确关联 Domain ID 和 Event。
- [ ] 工具结果以 Evidence 摘要形式回灌模型。
- [ ] 非法模型输出不会触发工具执行。
- [ ] 模型超时、工具失败、需审批和最大回合均有明确结果。
- [ ] checkpoint 恢复不会重复执行已成功工具调用。
- [ ] 端到端测试覆盖“模型 → 多 Task → 多 ToolCall → EvidenceRef → 模型 → 结论”。

## 10. 推荐测试用例

```text
test_model_returns_final_and_completes_session
test_model_creates_multiple_tasks_in_one_turn
test_task_executes_multiple_tool_calls
test_model_tool_calls_are_executed_then_loop_continues
test_invalid_model_output_does_not_execute_tool
test_tool_failure_is_returned_to_model
test_approval_required_pauses_loop
test_max_turns_stops_session
test_model_timeout_fails_session
test_checkpoint_resume_does_not_duplicate_successful_tool_call
test_end_to_end_model_tool_model_final
```

## 11. 完成定义

使用 Mock Model 和一个 Mock Read-only Tool，可以稳定跑通：

```text
Incident → Session → Turn → Model → Task → ToolCall → Attempt → Evidence → Model → Final Diagnosis
```

并且所有成功、失败、等待审批和终止路径都能通过 Event 与关联 ID 复盘。
