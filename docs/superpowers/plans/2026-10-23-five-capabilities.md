# Five Capabilities Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 补齐计划 DAG 的执行闭环，验证其余四项已有能力，并明确效果目标与实测差距。
**Architecture:** 复用 Runtime 和工具执行边界，计划先冻结校验，再通过有界调度执行；节点结果持久化后才唤醒后继。
**Tech Stack:** Python 3.11+、SQLite、现有 ToolExecutor、OpenTelemetry、pytest。
**Spec:** `docs/superpowers/specs/2026-10-23-five-capabilities-design.md`

## Global Constraints

- 保留 Domain 与外部系统隔离，模型只产出计划，代码控制权限和调度。
- 不覆盖原始评测记录；无真实数据或模型时不声称效果目标通过。
- 不自动重放执行结果不确定的写工具。

## Review Focus

- 执行期间计划内容或工具权限发生变化：执行前重新校验，恢复校验哈希。
- 节点成功但进程退出：已落盘结果重用，未确认写入停止自动执行。
- 同一运行重复提交：独占锁防止重复副作用。
- 依赖失败与用户取消：后继不执行，无关分支可完成，取消后不提交新任务。
- 用户/会话越权或跨版本恢复：执行入口拒绝，不读取他人的结果。

## Task 1: DAG 定义与 PlanningService

Files: `orchestration/dag.py`、`orchestration/plan_validator.py`、`control/planning.py`，测试 `tests/test_plan_dag.py`。
Interfaces: `ExecutionDAG.from_steps(steps)`、`to_dict()`、`content_hash`、`topological_order()`；节点包含 step_id/tool_name/arguments/depends_on。
- [x] 测试先覆盖重复ID、缺失依赖、环、输入变异隔离、旧计划顺序和显式并行。
- [x] 运行并确认失败，然后实现定义、拓扑校验和计划生成兼容。
- [x] 运行目标回归，提交。

## Task 2: 持久化调度与工具运行

Files: `orchestration/scheduler.py`、`orchestration/state_machine.py`、`orchestration/store.py`、`orchestration/runtime.py`，测试 `tests/test_dag_runtime.py`。
Interfaces: `PlanRuntime.run(plan, *, run_id, scope, cancel_requested=None)` 返回含节点结果的报告；`SQLitePlanStore` 保存并独占锁定 run_id。
- [x] 先用 Barrier/Event 测试并发上限、依赖、失败传播、恢复、写权限、重入锁、取消与Evidence归档。
- [x] 确认失败，实现调度与持久化，复用 ToolExecutor；加入 OTel。
- [x] 运行目标回归，提交。

## Task 3: 真实入口接线

Files: `control/live_intent_routes.py`、`api/app.py`，测试 `tests/test_plan_execution_api.py`。
Interfaces: `LiveIntentRoutes.execute_plan(actor, session, plan_id, expected_version)`，显式 POST 执行已保存草案，只读权限由服务端构造。
- [x] 测试正常链路、同计划重放、版本冲突、跨用户拒绝和工具目录变更。
- [x] 确认失败，接入执行与查询结果，不更改规划默认不执行的行为。
- [x] 运行入口回归，提交。

## Task 4: 五项统一验收

Files: `scripts/validate_five_capabilities.py`、`docs/validation/five-capabilities/`、README，测试 `tests/test_five_capabilities_validation.py`。
Interfaces: 验收脚本逐项运行真实回归并写 JSON；功能通过与目标指标未评测独立记录。
- [x] 测试失败、跳过和缺失结果不能被报告为全通过，基线/数据规模不得虚构。
- [x] 确认失败，实现报告与实际运行，修复核实的缺口。
- [x] 完整 pytest、diff 检查、独立代码审查；保存真实结果并提交。
