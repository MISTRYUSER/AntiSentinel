# PRD-002A Runtime Integration Smoke Test

## 基本信息

- **状态**：Ready
- **提出角色**：AntiSentinel PM
- **目标**：通过最小前端和真实大模型接入，验证 Runtime Loop 可以正常运行
- **优先级**：P0
- **依赖**：PRD-001 Domain Kernel Foundation、PRD-002 Model Runtime Loop
- **后续依赖**：PRD-003 Memory & Evidence Foundation

## 0. 当前代码基线

当前已经具备：

- Incident、Session、Turn、Task、ToolCall、Attempt、EvidenceRef、Event 领域链路。
- `ModelPort`、结构化 `ModelRequest/ModelResponse` 和模型输出解析。
- `RuntimeEngine` / `RuntimeLoop`，支持多 Turn、多 Task、多 ToolCall。
- ContextBuilder、MessageBuilder、内存 checkpoint。
- ToolRegistry 和 ToolExecutor。
- 99 个测试全部通过。

当前缺少：

- 真实 LLM Provider Adapter。
- 对外 API。
- 可操作的最小前端页面。
- 一条从 HTTP 输入到 RuntimeEngine 再到前端输出的集成测试。

## 1. 背景与问题

领域模型和 Runtime Loop 已经通过单元测试，但还没有验证真实使用路径。需要尽快确认：用户输入一个 Bug 后，系统能否创建 Session，调用真实模型，生成 Task 和 ToolCall，执行工具，再把结果回传模型并展示诊断结论。

本阶段是 smoke test，不追求生产级前端、完整登录、持久化、流式输出或复杂运维能力。

## 2. 用户场景

```text
用户打开诊断页面
→ 输入 Bug 描述
→ 点击开始诊断
→ 页面展示 Session / Turn / Task / ToolCall 状态
→ 模型调用只读 Mock Tool
→ 工具结果回灌模型
→ 页面展示最终诊断、置信度和 EvidenceRef
```

## 3. 目标

- 提供最小 HTTP API：创建 Incident、启动诊断、查询 Session 结果。
- 实现一个符合 `ModelPort` 的真实 LLM Adapter。
- API 层不得直接调用 LLM SDK，必须经过 `ModelPort` 和 `RuntimeEngine`。
- 提供一个最小可用前端页面，支持输入 Bug 和展示结果。
- 支持配置模型名称、API 地址、超时和最大 Turn 数。
- 保留现有 `FakeModel` 测试，不让真实模型成为单元测试依赖。
- 增加一条可重复运行的集成 smoke test；无真实凭证时使用 Fake Provider。
- 前端展示结构化运行结果，不展示模型隐藏思考内容。

## 4. 非目标

- 不实现用户登录、权限中心和多租户。
- 不实现生产数据库；第一版使用进程内存储即可。
- 不实现 SSE/WebSocket 流式输出。
- 不实现完整聊天历史管理和 Session 恢复 UI。
- 不接入真实日志、指标、SSH、Kubernetes 或写操作工具。
- 不实现 PRD-003 的 Policy Guardian；当前只允许一个明确的只读 Mock Tool。
- 不在浏览器端保存或发送模型 API Key。

## 5. 推荐模块边界

```text
Frontend
  → HTTP API
    → Incident/Session Application Service
      → RuntimeEngine
        → ModelPort → LLM Adapter
        → ToolRegistry → ToolExecutor → Mock Read-only Tool
```

建议实现位置：

- `src/antisentinel/adapters/llm/`：真实模型适配器。
- `src/antisentinel/api/`：HTTP 路由、请求/响应 DTO、错误映射。
- `src/antisentinel/entry/`：Incident/Session 应用服务编排。
- `frontend/` 或项目约定的静态资源目录：最小诊断页面。
- `tests/test_integration_smoke.py`：API 到 RuntimeEngine 的集成测试。

如果项目尚未选定 Web 框架，第一版使用轻量 ASGI API 和原生 HTML/CSS/JavaScript 页面，避免引入完整前端工程体系。

## 6. API 需求

### 6.1 创建 Incident

```http
POST /api/incidents
Content-Type: application/json

{
  "title": "checkout API returns 502",
  "summary": "production checkout requests fail intermittently",
  "source": "operator"
}
```

响应至少包含 `incident_id`、状态和创建时间。

### 6.2 启动诊断

```http
POST /api/incidents/{incident_id}/sessions
Content-Type: application/json

{
  "participant_ids": ["operator-1"]
}
```

响应至少包含 `session_id`、状态和诊断运行结果。

### 6.3 查询 Session

```http
GET /api/sessions/{session_id}
```

响应至少包含：

- Session 状态
- Turn 列表或摘要
- Task 列表或摘要
- ToolCall/Attempt 状态
- 最终诊断
- EvidenceRef
- 错误信息（如果失败）

## 7. LLM Adapter 需求

- 实现 `ModelPort.complete(ModelRequest) -> ModelResponse`。
- 将 Provider 错误、超时、限流映射为统一的 `ModelError` / `ModelTimeoutError`。
- 严格使用现有 `parse_model_response` 解析结构化输出。
- Provider 返回非法 JSON 或非法 Task/ToolCall 时不得猜测修复。
- API Key 只从服务端环境变量或本地配置读取，绝不返回给前端。
- 记录模型调用延迟、模型名称和错误码，不记录完整敏感 prompt 或 API Key。

## 8. 前端需求

页面至少包含：

- Bug 标题输入框。
- Bug 描述输入框。
- 开始诊断按钮。
- 当前 Session 状态。
- Turn / Task / ToolCall 的结构化结果。
- 最终诊断、置信度和 EvidenceRef。
- 失败时显示用户可理解的错误信息。

前端不展示：

- API Key。
- 模型隐藏思考过程。
- 未脱敏的工具原始输出。

## 9. 运行与错误语义

- 未配置模型凭证：API 返回明确配置错误；本地 smoke test 可切换 Fake Provider。
- 模型超时：Session/Turn 进入失败状态，API 返回可识别错误码。
- 模型输出非法：不执行 ToolCall，保留失败事件。
- ToolCall 失败：把结构化失败结果回灌模型，由 Loop 决定是否结束。
- API 重复请求：同一请求 ID 或 Session 运行中的重复启动不得并发创建重复运行。
- 前端网络失败：允许重新查询 Session，不要求重新执行诊断。

## 10. 验收标准

- [ ] 可以通过 API 创建 Incident。
- [ ] 可以通过 API 创建 Session 并启动 RuntimeEngine。
- [ ] 真实 LLM Adapter 可以完成至少一次 final response。
- [ ] FakeModel 可以在无凭证环境完成同样的 smoke test。
- [ ] 一个 Turn 可以生成多个 Task。
- [ ] 一个 Task 可以执行多个只读 Mock ToolCall。
- [ ] 工具结果以摘要和 EvidenceRef 形式回灌模型。
- [ ] 前端可以展示 Session、Turn、Task、ToolCall 和最终诊断。
- [ ] 模型超时、非法输出和工具失败在 API 与页面上有明确反馈。
- [ ] API Key 不出现在前端响应、页面源码或日志中。
- [ ] 全量测试保持通过，并新增端到端 smoke test。

## 11. 推荐测试用例

```text
test_create_incident_api
test_start_session_runs_runtime_engine
test_real_provider_response_maps_to_model_response
test_fake_provider_smoke_test_without_credentials
test_api_returns_structured_runtime_result
test_frontend_does_not_receive_api_key
test_model_timeout_maps_to_session_failure
test_invalid_model_output_does_not_execute_tool
test_tool_failure_is_visible_in_session_result
test_duplicate_session_start_is_safe
```

## 12. 完成定义

在本地启动服务，打开诊断页面，输入一个 Bug 描述，可以稳定完成：

```text
HTTP Input
→ Incident
→ Session
→ Turn
→ LLM
→ Task
→ Mock ToolCall
→ Attempt
→ EvidenceRef
→ LLM
→ Final Diagnosis
→ Frontend Display
```

真实模型不可用时，Fake Provider 必须能够独立验证整条链路。
