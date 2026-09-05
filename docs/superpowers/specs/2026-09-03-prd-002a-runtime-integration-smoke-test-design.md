# PRD-002A Runtime Integration Smoke Test 设计

## 背景与范围

PRD-002A 在已有 Domain 和 Runtime Loop 之上增加最小 HTTP API、前端、真实 LLM Adapter 和集成 smoke test，验证从 HTTP 输入到最终诊断展示的完整路径。

本阶段采用进程内存储，不实现登录、生产数据库、真正的模型 token 流、真实诊断工具或 Policy Guardian；增量设计实现 Runtime Event SSE。

## 目标与非目标

目标：

- HTTP 创建 Incident、启动 Session、查询 Session。
- 真实 Provider 通过 `ModelPort` 接入 RuntimeEngine。
- 无凭证时由 FakeModel 独立完成 smoke test。
- 前端能提交 Bug 并展示 Session、Turn、Task、ToolCall、最终诊断和 EvidenceRef。
- API Key 只存在服务端配置中。
- 增加一条可重复运行的 API → RuntimeEngine → 前端数据结构的集成测试。

非目标：登录、权限中心、多租户、生产数据库、SSE/WebSocket、完整聊天历史、真实日志/指标/SSH/Kubernetes 工具、PRD-003 Policy Guardian。

## 方案比较

### 方案 A：FastAPI + OpenAI-compatible HTTP Adapter（推荐）

使用 FastAPI 暴露 API，httpx 调用兼容 OpenAI Chat Completions 的 endpoint。Adapter 将标准 `ModelRequest` 映射为 `messages` 与 `tools`，强制要求结构化 JSON 输出，再调用现有 `parse_model_response`。优点是依赖少、支持真实服务和本地兼容模型、Provider 边界清晰；缺点是需要自己处理 Provider 格式差异。

### 方案 B：直接引入 OpenAI SDK

使用官方 SDK 处理请求和结构化输出。优点是 SDK 类型和错误更完整；缺点是当前环境未安装 SDK，绑定单一 Provider，且后续仍需为其他兼容 Provider 做 Adapter。

### 方案 C：使用 Agno Agent 作为 Runtime

让 Agno Agent 接管消息、工具和模型循环。优点是集成快；缺点是绕过当前已验证的 RuntimeEngine、领域事件和 checkpoint 语义，难以保持 AntiSentinel 的审计链路。本阶段不采用。

## 推荐架构

```text
Browser
  → FastAPI Router
    → DiagnosisApplicationService
      → RuntimeEngine
        → ModelPort
          → OpenAICompatibleModelAdapter / FakeModel
        → ToolRegistry
          → ToolExecutor
            → Read-only Mock Tool
```

### LLM Adapter（`src/antisentinel/adapters/llm/`）

定义 `OpenAICompatibleModelAdapter`，配置 `base_url`、`api_key`、`model`、`timeout`。它将 `ModelRequest.messages` 和 `ModelRequest.tools` 转为 Provider 请求，使用服务端鉴权，解析 Provider JSON/text content，调用现有 `parse_model_response`，并将网络超时、429、401、5xx 和非法响应映射为 `ModelTimeoutError` 或 `ModelError`。

真实 Adapter 不记录完整 prompt、API Key 或 raw tool output；日志只包含 model、状态码、错误码和耗时。

定义 `FakeProviderModel`/`FakeModel` 作为相同 `ModelPort` 的本地实现，按固定脚本返回 tasks 和 final，确保 smoke test 无外网依赖。

### 应用服务（`src/antisentinel/entry/`）

`DiagnosisApplicationService` 持有进程内 Incident/Session/Result 状态：

- `create_incident(title, summary, source) -> Incident`。
- `start_session(incident_id, participant_ids, model_mode) -> RuntimeResult`。
- `get_session(session_id) -> SessionView`。

它负责 ID 查找、重复启动保护和 RuntimeEngine 组装；不直接调用 LLM SDK。一次 Session 运行期间用 session-level lock 或状态检查拒绝重复启动。

### API（`src/antisentinel/api/`）

提供：

- `POST /api/incidents`：校验请求并返回 `incident_id`、状态、创建时间。
- `POST /api/incidents/{incident_id}/sessions`：创建 Session、同步运行 RuntimeEngine，并返回结构化 `RuntimeResult`。
- `GET /api/sessions/{session_id}`：返回 Session 状态、Turn/Task/ToolCall/Attempt 摘要、最终诊断、EvidenceRef 和错误。

路由只做 HTTP DTO 校验和错误映射。领域错误映射为 4xx，模型/运行时错误映射为结构化 5xx/业务错误；响应中不包含 API Key、隐藏思考或原始工具输出。

### 前端（`frontend/`）

使用一页原生 HTML/CSS/JavaScript，由 FastAPI 静态托管。页面包含标题、描述、开始诊断按钮、Session 状态、执行链路摘要、最终诊断、置信度和 EvidenceRef。提交后展示 API 返回结果；网络失败时允许使用 Session ID 重新查询，不自动重复启动。

## 数据流与安全边界

```text
HTTP JSON
→ 请求 DTO
→ Incident/Session
→ ModelRequest（messages + tools）
→ Provider/Fake Provider
→ parse_model_response
→ RuntimeEngine
→ RuntimeResult
→ response DTO
→ Browser
```

API Key 从环境变量或服务端配置读取，不能进入 DTO、HTML、JavaScript、日志或 ModelRequest。模型隐藏思考不进入 `RuntimeResult`。工具原始结果只留在服务端运行态，前端和模型都只接收摘要/EvidenceRef。

## 配置

使用环境变量或构造配置对象：

- `ANTISENTINEL_MODEL_MODE=fake|real`
- `ANTISENTINEL_MODEL_NAME`
- `ANTISENTINEL_MODEL_BASE_URL`
- `ANTISENTINEL_MODEL_API_KEY`
- `ANTISENTINEL_MODEL_TIMEOUT_SECONDS`
- `ANTISENTINEL_MAX_TURNS`

未配置 real 模式的 API Key 时，API 明确返回配置错误；fake 模式不读取或返回 API Key。

## 错误与幂等

- 未知 Incident/Session：404 和稳定错误码。
- 缺少真实模型配置：配置错误，不启动模型调用。
- 模型超时/Provider 错误：复用 RuntimeEngine 的失败状态和错误码。
- 非法模型输出：不执行 ToolCall，保留 `model.failed` 事件。
- 工具失败：由 RuntimeLoop 回灌结构化失败结果。
- Session 已运行或已完成：重复启动返回现有运行结果或明确冲突，不并发创建第二个 Session 运行。
- 前端网络失败：GET 查询可恢复展示，不重新执行诊断。

## 测试策略与验收映射

以 `tests/test_integration_smoke.py` 为主，只增加跨边界测试：

- 创建 Incident API。
- 启动 Session 经过 Application Service 和 RuntimeEngine。
- Fake Provider 无凭证完成多 Task、多 ToolCall、EvidenceRef、Final。
- OpenAI-compatible Adapter 的 HTTP 响应映射和错误映射使用 mock transport，不访问外网。
- API 返回结构化 RuntimeResult。
- API/前端响应和页面源码不包含 API Key 或隐藏思考。
- 模型超时、非法输出、工具失败、重复启动。
- GET Session 能返回完整执行摘要。

真实 Provider smoke test 只有在显式凭证存在时运行；默认 CI/local 回归使用 Fake Provider。

## 完成标准

本地启动服务，打开页面，输入 Bug，选择 fake 模式，可以稳定完成：

```text
HTTP Input
→ Incident
→ Session
→ Turn
→ Fake/Real LLM
→ Task
→ Mock ToolCall
→ Attempt
→ EvidenceRef
→ LLM
→ Final Diagnosis
→ API Response
→ Frontend Display
```

所有现有 PRD-001/PRD-002 测试继续通过，并新增 API 到 RuntimeEngine 的可重复 smoke test。

## 增量设计：实时 Runtime Event 与可视化

同步 POST 等待完整 RuntimeLoop 才返回，无法满足真实诊断观察需求。本增量将 Session 运行改为可观察的事件流：创建 Session 后尽快返回 `session_id`，Runtime 在后台执行，前端通过 SSE 订阅脱敏事件。

```text
POST /api/incidents/{id}/sessions
  → 202 {session_id, status: "running"}

GET /api/sessions/{session_id}/events
  → text/event-stream
     session.started
     turn.started
     model.started
     tool.started
     tool.completed
     task.completed
     diagnosis.completed / runtime.failed
```

SSE 只包含事件 ID、事件类型、发生时间、关联领域 ID、状态、工具名、结果摘要、EvidenceRef 和错误码；禁止包含 API Key、完整 messages、隐藏思考、原始 ToolCall 参数和 raw tool output。

第一版使用进程内 Session 级事件缓冲和后台执行，支持 `Last-Event-ID` 重连补发。同步 GET Session 结果接口继续保留。真正模型 token 的 TTFT 需要后续新增 `ModelPort.stream(request) -> Iterable[ModelDelta]`；本增量先保证首个 Runtime Event 快速到达。

## 自审结果

- 真实 Provider、Fake Provider 和 RuntimeEngine 的职责边界明确。
- API 路由没有直接调用 LLM SDK。
- 默认 smoke test 不依赖外部网络或凭证。
- 前端不接触 API Key、隐藏思考和 raw tool output。
- 当前范围不包含 PRD-003 Policy Guardian。
- 设计可拆为四个大标题阶段：LLM Adapter、Application/API、前端、全链路 Smoke Test。
