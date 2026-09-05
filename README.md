# AntiSentinel

AntiSentinel is a code-aware incident diagnosis and runbook execution platform.

## 一键启动

需要安装并启动 Docker Desktop 或 Docker Engine（包含 Compose v2）。

```bash
git clone https://github.com/MISTRYUSER/AntiSentinel.git
cd AntiSentinel
./scripts/start.sh
```

启动后访问：

- Web 页面：http://localhost:8765/
- 可观测性看板：http://localhost:8765/dashboard

默认使用内置 Fake Provider，无需配置 API Key，可直接体验完整的 Agent
Runtime 和 Redis 工具调用链路。AntiSentinel、Redis 均运行在容器中，SQLite、
审计记录和 Redis 数据保存在 Docker Volume 中；服务仅监听本机地址。

也可以不使用脚本，直接运行：

```bash
docker compose up --build -d --wait
```

### 常用命令

```bash
docker compose ps
docker compose logs -f antisentinel
docker compose down
```

`docker compose down` 会保留数据。只有执行 `docker compose down -v` 才会删除
该 Compose 项目的数据卷。Docker 部署使用独立存储，不会导入宿主机已有的
`storage/` 目录。

### 接入真实模型

复制环境变量示例并填写模型配置：

```bash
cp .env.example .env
```

将 `.env` 中的 `ANTISENTINEL_MODEL_MODE` 改为 `real`，并填写模型地址、名称和
API Key，然后重新运行 `./scripts/start.sh`。`.env` 和 `.env.local` 不会提交到
Git，也不会复制进镜像。启用真实模型后，任务内容会发送给你配置的模型服务商。

The legacy `docker-compose.oss.yml` runs optional LibreChat/Phoenix services
for the host-based setup; it is separate from this core Compose deployment.
See [deployment notes](deploy/README.md).

## Architecture

- Runtime: model turns, tool execution, state transitions and evidence.
- Memory: session tree, preferences, asynchronous extraction and trusted recall.
- Persistence: SQLite primary storage, JSONL audit and Redis cache/job queue.
- Observability: runtime events and OpenTelemetry instrumentation.
- RAG strategy modules and DAG orchestration are currently scaffolding.
- `vendor/agno_infra` is an isolated upstream dependency candidate with its own license.

## 本地开发启动

不使用 Docker 开发应用代码时，可在本机启动 API 和前端：

```bash
pip install -e .
PYTHONPATH=src uvicorn antisentinel.api.app:app --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/` and submit an incident. The default mode uses the in-process Fake Provider and read-only mock tools. To use an OpenAI-compatible provider, set `ANTISENTINEL_MODEL_MODE=real`, `ANTISENTINEL_MODEL_BASE_URL`, `ANTISENTINEL_MODEL_NAME`, and `ANTISENTINEL_MODEL_API_KEY` on the server only.

## Memory embedding: Qwen Flash

The embedding adapter now uses Alibaba Cloud Bailian
`qwen3.7-text-embedding-flash`, replacing the local E5 adapter previously
exported under the `BGE_M3Embedder` alias. It requires no local model weights,
PyTorch, or sentence-transformers. Text passed to the adapter is sent to
Bailian; vectors remain in SQLite.

Configure the server environment:

- `DASHSCOPE_API_KEY`: the API key for your Bailian workspace.
- `ANTISENTINEL_EMBEDDING_BASE_URL`: the **OpenAI-compatible base URL** shown
  in that workspace's invocation example. For example,
  `https://<workspace-id>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`.
  A complete URL ending in `/embeddings` is also accepted.
- `ANTISENTINEL_EMBEDDING_DIMENSION`: optional, defaults to `1024`;
  Flash supports `256`, `512`, `768`, and `1024`.

These variables are separate from the diagnosis model configuration.
`from_env()` reads process environment variables; it does not load `.env.local`.
Keep API keys out of source files and logs.

```python
from antisentinel.persistence.local_vector_memory import LocalVectorMemory, QwenFlashEmbedder
from antisentinel.persistence.sqlite_database import SQLiteDatabase

database = SQLiteDatabase("/path/to/memory.db")
database.initialize()
with QwenFlashEmbedder.from_env() as embedder:
    memory = LocalVectorMemory(database, embedder)
    # Pass only authorized text. upsert() persists the supplied memory record.
    memory.upsert({
        "memory_id": "example-log-order", "operator_id": "operator-1",
        "content": "排查时先看日志，再看指标", "content_version": 1,
    })
    matches = memory.search("排查顺序", operator_id="operator-1", limit=5)
```

Requests are split into batches of at most 20 inputs, and response vectors
are restored to input order. Queries and documents use plain text without
the old E5 `query:`/`passage:` prefixes. Transient HTTP/transport failures
retry at most twice; authentication failures do not retry.

Existing vectors must be regenerated for the new model, even if their
dimensions match. Search excludes rows with a different model, dimension,
or content version. Changing this adapter does **not** automatically rebuild
the database. The runtime enables the optional vector candidate channel with
`ANTISENTINEL_MEMORY_VECTOR_ENABLED=1`; it requires separately indexed records.
Lexical and exact-identifier retrieval remain available without embedding credentials.

See the [Bailian embedding API reference](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api)
and the [isolated smoke case](docs/validation/qwen-flash-embedding-smoke.md).
