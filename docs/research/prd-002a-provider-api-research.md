# PRD-002A Provider、API 与 Agent Runtime 调研

## Agno 的参考实现

Agno 将工具定义为包含名称、描述、JSON Schema 参数、entrypoint、确认和外部执行等元数据的 `Function`，由 Toolkit 在初始化时注册；这与 AntiSentinel 当前的 `ToolDefinition`/`ToolRegistry` 边界相近。[Agno Function 源码](https://github.com/agno-agi/agno/blob/main/libs/agno/agno/tools/function.py)、[Agno Toolkit 源码](https://github.com/agno-agi/agno/blob/main/libs/agno/agno/tools/toolkit.py)

Agno 的 Provider Adapter 会把标准化的 messages、tools、response format 转成具体 Provider 请求，并将 Provider 响应解析为统一 `ModelResponse`；以 Claude Adapter 为例，它在请求中传递 messages/tools，收到响应后再解析结构化 JSON。[Agno Claude Adapter 源码](https://github.com/agno-agi/agno/blob/main/libs/agno/agno/models/anthropic/claude.py)

Agno 也提供了独立的 FastAPI Agent API 示例，说明 Agent Runtime 与 HTTP API 可以通过应用服务组合，而不让路由直接管理模型细节。[Agno Agent API](https://github.com/agno-agi/agent-api)

## Codex 的参考实现

Codex 使用显式 ToolRegistry 保存受信任和外部工具，按照稳定工具名索引，并在分发前处理未知工具、工具类型、hooks、权限和 telemetry。[Codex ToolRegistry 源码](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/registry.rs)

Codex 的启示是：API/Runtime 不应直接依赖具体工具实现；工具调用要经过 registry 和 dispatch 边界，错误应转化为模型可识别结果，同时记录调用生命周期。

## 对本项目的决策

- ModelPort 继续作为 Runtime 与真实 Provider 之间的唯一接口。
- 真实 Adapter 使用 OpenAI-compatible HTTP 协议和 `httpx`，不引入 OpenAI SDK；这样可以支持 OpenAI 以及兼容接口，并能在当前环境直接运行。
- Adapter 只负责 HTTP 请求、鉴权、超时、Provider 错误映射和结构化响应解析，不负责 Incident/Session/Task。
- API 路由只调用应用服务；应用服务创建 Incident/Session 并调用 RuntimeEngine。
- API 使用进程内的 ApplicationState 保存 Incident/Session/RuntimeResult，后续替换 StateStore 不改变路由契约。
- FakeModel 作为 ModelPort 测试替身；无凭证时 API 通过配置选择 Fake Provider。
- 前端只展示结构化状态和结论，不展示隐藏思考、API Key 或 raw tool output。
- smoke test 默认不访问外网；真实 Provider 测试通过显式环境变量启用。

## 流式事件参考

Codex 的客户端以 turn stream 持续消费生命周期通知，并区分 turn 开始、item 开始/完成、turn 完成和失败；Python SDK 的 `TurnHandle.stream()` 会持续读取通知，直到收到当前 turn 的完成事件。[Codex Protocol v1](https://github.com/openai/codex/blob/main/codex-rs/docs/protocol_v1.md)、[Codex Python SDK](https://github.com/openai/codex/blob/main/sdk/python/src/openai_codex/api.py)

Agno 提供 `stream=True` 和 `stream_events=True`，可消费 run、content delta、tool call started/completed、pause 和 error 等事件。[Agno streaming events](https://github.com/agno-agi/agno/blob/main/cookbook/03_teams/02_modes/tasks/11_streaming_events.py)、[Agno run events](https://github.com/agno-agi/agno/blob/main/libs/agno/agno/run/agent.py)

Agno 的公开安全问题显示，如果把完整 `tool_args` 和 raw result 直接放进 SSE，会泄漏敏感数据；AntiSentinel 的 SSE 只发送工具名、状态、摘要和 EvidenceRef，不发送参数原文、工具原始结果、prompt 或隐藏思考。[Agno SSE 数据暴露问题](https://github.com/agno-agi/agno/issues/7745)

## 对 AntiSentinel 的流式决策

- 第一版采用“提交 Session 返回 session_id + GET SSE 事件流”，不让 POST 长连接阻塞浏览器。
- RuntimeLoop 通过事件发布器广播脱敏 RuntimeEvent；同步 `RuntimeResult` 仍保留，便于 API 查询和兼容现有调用方。
- SSE 事件使用稳定 `event`、`id`、`data` 字段，客户端按 event id 处理重连和去重。
- 第一版先保证首个 Runtime Event 快速到达；真正 token TTFT 需要后续 `ModelPort.stream()`，不把隐藏思考推给前端。
