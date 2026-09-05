# AntiSentinel PRD Index

所有产品需求先在这里登记，再进入技术设计和开发。

## PRD 状态

- `Draft`：需求正在讨论，尚未确认。
- `Planned`：已排入路线图，尚未开始详细设计。
- `Ready`：需求已确认，可以进入技术设计。
- `In Progress`：正在开发。
- `Review`：等待代码 Review 或产品验收。
- `Done`：已完成并通过验收。
- `Rejected`：明确不再推进。

## PRD 列表（按实现顺序）

| 编号 | 名称 | 状态 | 优先级 | 依赖 | 目标模块 |
|---|---|---|---|---|---|
| [[PRD-001 Domain Kernel Foundation]] | Domain 最小领域内核 | Done | P0 | - | domain |
| [[PRD-002 Model Runtime Loop]] | 大模型 Runtime Loop | Done | P0 | PRD-001 | worker/runtime / domain |
| [[PRD-002A Runtime Integration Smoke Test]] | 前端与大模型接入验证 | Done | P0 | PRD-001、PRD-002 | api / adapters/llm / frontend |
| [[PRD-003 Memory and Evidence Foundation]] | Memory 与 Evidence 基础 | Ready | P0 | PRD-001、PRD-002 | memory / persistence |

## 后续规划

| 编号 | 名称 | 状态 | 优先级 | 依赖 | 目标模块 |
|---|---|---|---|---|---|
| PRD-004 | Code Map Foundation | Planned | P0 | PRD-003 | retrieval / code_map |
| PRD-005 | RAG Retrieval | Planned | P0 | PRD-003、PRD-004 | retrieval / memory |
| PRD-006 | Diagnostic & Execution Plan | Planned | P0 | PRD-004、PRD-005 | domain / orchestration / control |
| [[PRD-007 Tool Registry and Policy Guardian]] | Tool Registry & Policy Guardian | Planned | P0 | PRD-003、PRD-006 | tools / security |
| PRD-008 | Sandbox & HostGateway | Planned | P0 | PRD-007 | security / adapters/ssh |
| PRD-009 | Runtime Protocol & Worker Boundary | Planned | P1 | PRD-007、PRD-008 | protocol / worker |
| PRD-010 | DAG Orchestration | Planned | P1 | PRD-006、PRD-007、PRD-009 | orchestration / control |
| PRD-011 | Multi-Agent Task Delegation | Planned | P1 | PRD-010 | orchestration / worker |
| PRD-012 | Collector & Runbook Workflow | Planned | P1 | PRD-010、PRD-011 | entry / capabilities/runbook |
| PRD-013 | Remote Worker Adapter | Planned | P2 | PRD-009、PRD-012 | adapters/worker |

## 横向能力

| 能力 | 规划方式 | 推荐时机 |
|---|---|---|
| Tracing | 接入 OpenTelemetry + 选择第三方 Trace/Log Backend，不自建日志平台 | 从 PRD-003 起逐阶段接入 |
| Eval / Benchmark | 接入 benchmark/eval 工具，固定数据集、程序化指标和 LLM Judge | PRD-004 起建立基线，PRD-005/006/010 持续回归 |

## 每个 PRD 必须回答

1. 谁遇到什么问题？
2. 希望系统产生什么行为？
3. 哪些内容明确不做？
4. 如何验证需求完成？
5. 失败、重试、超时和权限场景怎么处理？
6. 需要沉淀哪些 Event、Evidence 和指标？
