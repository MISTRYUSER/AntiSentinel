# Core Docker deployment

From the project root, run `docker compose up --build -d`.
This starts AntiSentinel (including the built-in frontend) and Redis.
See [Docker quick start](../README.md#docker-quick-start) for configuration and storage.

The integration below is the optional, legacy host-runtime setup; it is not
started by the core Compose command.

# LibreChat + Phoenix 集成

AntiSentinel 提供 OpenAI-compatible Chat Completions：

```text
http://127.0.0.1:8765/v1/chat/completions
```

LibreChat 使用官方 Compose 部署；将 `librechat.yaml` 挂载到 LibreChat API 容器，并将 `host.docker.internal` 指向宿主机上的 AntiSentinel。

```yaml
services:
  api:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ./deploy/librechat.yaml:/app/librechat.yaml
```

Phoenix 使用官方 Compose 方式部署，默认界面为：

```text
http://127.0.0.1:6006
```

启动前先运行 AntiSentinel：

```bash
./scripts/start_local.sh
```

如果使用 LibreChat 作为 AntiSentinel 项目前端，直接在 AntiSentinel 项目根目录启动：

```bash
ANTISENTINEL_START_LIBRECHAT=1 ./scripts/start_local.sh
```

当前 AntiSentinel 的 `/v1/chat/completions` 会复用服务端模型 Token；LibreChat 不需要保存 DeepSeek Token。Phoenix OTLP 接收配置将在下一步通过 `OTEL_EXPORTER_OTLP_ENDPOINT` 接入。

## 可观测性界面

项目的中文主看板是 `http://127.0.0.1:8765/dashboard`，按项目、会话、Turn 展示记忆、RAG、模型、工具调用、worker 和 token tracing。

Phoenix 保留为原始 trace 的深度审计入口：`http://127.0.0.1:6006`。其内置 UI 暂无官方中文 locale，因此启动配置只保留 DeepSeek provider，并将默认 trace 保留期设为 30 天，减少无关内容和存储压力。

访问 LibreChat：`http://127.0.0.1:3080`。LibreChat 的 AntiSentinel 自定义 Endpoint 指向宿主机 `http://host.docker.internal:8765/v1`；项目和对话历史由 LibreChat 管理，AntiSentinel 接收每次 Chat Completion 并负责运行时观测与长期记忆。
