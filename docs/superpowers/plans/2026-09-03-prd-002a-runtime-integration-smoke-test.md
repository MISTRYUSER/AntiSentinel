# PRD-002A Runtime Integration Smoke Test 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 建立从 HTTP 输入到 RuntimeEngine、LLM/Fake Provider、只读 Mock Tool、最终诊断和前端展示的可重复 smoke test。

**架构：** 采用 FastAPI + httpx 的 OpenAI-compatible Adapter，并用进程内 RuntimeEventBus + SSE 暴露诊断进度。API 只调用 DiagnosisApplicationService，应用服务负责 Incident/Session 状态和 RuntimeEngine 组装；RuntimeEngine 只依赖 ModelPort 与 ToolRegistry。前端使用原生 HTML/CSS/JavaScript，不接触模型密钥或隐藏思考。

**技术栈：** Python 3.11+、FastAPI、Uvicorn、httpx、原生 HTML/CSS/JavaScript、pytest；不引入 OpenAI SDK 或 Agno Runtime。

**设计文档：** `docs/superpowers/specs/2026-09-03-prd-002a-runtime-integration-smoke-test-design.md`

## 全局约束

- 保持 Python 要求 `>=3.11,<4`。
- API 层不得直接调用 LLM SDK，必须经过 `ModelPort` 和 `RuntimeEngine`。
- 默认 smoke test 不访问外网、不要求真实凭证。
- API Key 只能从服务端环境变量/配置读取，不进入响应、页面源码、日志或 ModelRequest。
- 不实现登录、生产数据库、真正的 token 流、真实诊断工具或 PRD-003 Policy Guardian；本阶段实现 Runtime Event SSE。
- 不新增隐藏思考字段；模型和工具只向 API/前端暴露结构化摘要、状态和 EvidenceRef。
- Session 启动 API 快速返回 `session_id`，运行进度通过 SSE 事件流观察；同步查询接口保留。
- SSE 只发送脱敏事件，不发送 API Key、完整 prompt、隐藏思考、原始工具参数或 raw tool output。
- 除现有 PRD-001/PRD-002 测试外，本阶段以跨边界集成测试为主，不新增孤立单元测试套件。

## 文件地图

- Create `src/antisentinel/adapters/llm/openai_compatible.py`: OpenAI-compatible ModelPort Adapter。
- Modify `src/antisentinel/adapters/llm/__init__.py`: 导出 Adapter 和配置。
- Create `src/antisentinel/entry/application.py`: Incident/Session 应用服务和进程内状态。
- Modify `src/antisentinel/entry/__init__.py`: 导出应用服务。
- Create `src/antisentinel/api/app.py`: FastAPI app、路由、DTO、错误映射。
- Modify `src/antisentinel/api/__init__.py`: 导出 app factory。
- Modify `src/antisentinel/bootstrap.py`: 组装 Fake/Real Model、ToolRegistry、Engine、Application Service。
- Create `src/antisentinel/runtime/events.py`: 脱敏 RuntimeEvent、Session 级事件缓冲和订阅。
- Create `frontend/index.html`: 诊断页面结构。
- Create `frontend/app.js`: 提交、查询和结果渲染。
- Create `frontend/styles.css`: 最小可用页面样式。
- Create `tests/test_integration_smoke.py`: HTTP → Application Service → RuntimeEngine → Provider/Tool 的跨边界回归。
- Modify `pyproject.toml`: 增加 FastAPI、Uvicorn、httpx 的运行依赖和测试配置。

## 第一阶段：LLM Adapter

**产出：** 一个符合 `ModelPort` 的真实 OpenAI-compatible HTTP Adapter，以及无凭证可运行的 Fake Provider。

**接口：**

```python
class OpenAICompatibleModelAdapter:
    def __init__(self, *, base_url: str, api_key: str, model: str, timeout: float = 30.0): ...
    def complete(self, request: ModelRequest) -> ModelResponse: ...

class FakeProviderModel:
    def complete(self, request: ModelRequest) -> ModelResponse: ...
```

- [ ] 在 `tests/test_integration_smoke.py` 增加 mock HTTP provider 响应，验证 Request 的 messages/tools 被发送、final JSON 被解析成 `ModelResponse`。
- [ ] 增加 timeout、401/429/5xx、非法 JSON/非法结构响应的跨边界用例，确认映射为 `ModelTimeoutError`/`ModelError`。
- [ ] 实现 Adapter：服务端添加 Bearer header，读取配置，限制日志字段，只调用现有 `parse_model_response`，不猜测 Provider 输出。
- [ ] 实现 FakeProviderModel：固定返回至少一轮 tasks 和一轮 final，带只读 Mock Tool 的 EvidenceRef。
- [ ] 运行 `pytest tests/test_integration_smoke.py -q`，确认 Adapter 阶段用例通过。

## 第二阶段：Application Service 与 HTTP API

**产出：** 三个 API 端点和统一的进程内应用状态。

**接口：**

```python
class DiagnosisApplicationService:
    def create_incident(self, *, title: str, summary: str | None, source: str) -> Incident: ...
    def start_session(self, *, incident_id: str, participant_ids: list[str], model_mode: str) -> RuntimeResult: ...
    def get_session(self, session_id: str) -> dict[str, object]: ...
```

- [ ] 在集成测试中验证 `POST /api/incidents` 返回 incident_id、状态和创建时间。
- [ ] 验证 `POST /api/incidents/{id}/sessions` 经过应用服务和 RuntimeEngine，返回结构化 RuntimeResult。
- [ ] 验证 `GET /api/sessions/{id}` 返回 Session 状态、Turn、Task、ToolCall、Attempt、Final 和 EvidenceRef。
- [ ] 增加 unknown ID、缺少字段、real 模式缺少 API Key、重复启动的 HTTP 用例。
- [ ] 实现 Application Service：维护 Incident/Session/Result 字典，用 session lock 或状态检查阻止重复运行，不直接 import Provider SDK。
- [ ] 实现 FastAPI DTO、路由和错误映射；确保响应不含 API Key、隐藏思考或 raw tool output。
- [ ] 运行阶段集成测试和已有全量测试。

## 第三阶段：实时 Runtime Event SSE

**产出：** Session 创建快速返回，前端能通过 SSE 实时看到 Runtime 当前阶段。

- [ ] 在 `tests/test_integration_smoke.py` 中验证启动接口返回 `202`、`session_id` 和 `running`，并验证 SSE 首事件快速到达。
- [ ] 验证 SSE 事件包含稳定 event id 和 Incident/Session/Turn/Task/ToolCall 关联 ID。
- [ ] 验证 SSE 数据不包含 API Key、完整模型消息、隐藏思考、原始参数和 raw tool output。
- [ ] 实现 `RuntimeEventBus`：按 Session 保存有界事件缓冲，支持订阅、Last-Event-ID 补发和终态关闭。
- [ ] 将 RuntimeEngine 放入后台执行，Application Service 立即返回 Session；保留 GET Session 最终结果。
- [ ] 在 RuntimeLoop 关键边界发布 `session.started`、`turn.started`、`model.started`、`tool.started`、`tool.completed`、`task.completed`、`diagnosis.completed` 和 `runtime.failed`。
- [ ] 实现 `GET /api/sessions/{session_id}/events`，使用 `StreamingResponse(media_type="text/event-stream")` 输出安全 DTO。
- [ ] 运行集成 smoke 和全量测试。

## 第四阶段：Codex 风格最小前端

**产出：** 可由服务托管的一页诊断页面。

- [ ] 创建标题、Bug 描述、来源、参与者、模式选择和开始诊断控件。
- [ ] 创建 Session/Turn/Task/ToolCall 状态展示、最终诊断、置信度、EvidenceRef 和错误区域。
- [ ] 使用 `fetch` 调用创建 Incident、启动 Session 和查询 Session；网络失败只提示重新查询，不自动重复启动。
- [ ] 使用 `EventSource` 订阅 `/api/sessions/{session_id}/events`，按事件实时更新时间线和右侧 Inspector。
- [ ] 在集成 smoke test 中读取页面源码，确认不包含 API Key、隐藏思考和 raw tool output。
- [ ] 通过 FastAPI 静态目录托管前端，并验证页面可以访问。
- [ ] 运行 API + frontend smoke test；不引入前端构建系统。

## 第五阶段：全链路 Smoke Test 与运行入口

**产出：** 一条默认 Fake Provider、可重复、无外网的完整验收链路；真实 Provider 只在显式配置时启用。

- [ ] 使用 FastAPI TestClient 执行：创建 Incident → 创建 Session → RuntimeEngine → FakeModel → 多 Task/多 ToolCall → EvidenceRef → Final。
- [ ] 断言第二次模型请求包含工具结果摘要和 EvidenceRef，不包含原始工具结果或 Evidence 内容。
- [ ] 断言 API 返回的所有状态和错误字段可序列化，前端可以直接渲染。
- [ ] 断言模型超时、非法输出、工具失败、重复 Session 启动均有明确错误结果。
- [ ] 完成 `build_application()`，默认按 `ANTISENTINEL_MODEL_MODE` 选择 Fake/Real Provider，并读取最大 Turn 和 timeout 配置。
- [ ] 增加 `uvicorn antisentinel.api.app:app --factory` 或等价本地启动说明，并验证服务可启动。
- [ ] 最终运行 `pytest -q`、Python 编译检查和完整 smoke test，确认原有测试继续通过。

## 验收映射

- Incident API：第二阶段。
- Session + RuntimeEngine：第二、四阶段。
- Real LLM Adapter：第一阶段。
- Fake Provider：第一、四阶段。
- 多 Task、多 ToolCall、EvidenceRef 回灌：第四阶段。
- API 结构化结果：第二、四阶段。
- 前端展示：第四、五阶段。
- Runtime Event SSE：第三、四、五阶段。
- Timeout、非法输出、工具失败、重复启动：第一、二、五阶段。
- API Key 不泄漏：第二、三、四阶段。
- 全量回归和端到端 smoke test：第五阶段。

## 计划自审

- 五个阶段均可独立验证，且每阶段对应一个 PRD 大标题。
- Adapter 不绕过 ModelPort；API 不绕过 RuntimeEngine。
- Fake Provider 默认不依赖网络，Real Provider 必须显式配置。
- 前端只消费 RuntimeResult/SessionView，不接触内部对象和密钥。
- 未纳入 PRD-003 Policy Guardian、生产持久化和真正的模型 token 流。
