# PRD-005 Real Application Case Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立两个可复现的真实验收 Case：R1 通过正常应用入口验证真实 Embedding、正常 Session、Evidence 和完成后重开；R2 验证向量已写但 SQLite 未确认时的进程中断恢复。

**Architecture:** 不改现有 SQLite + Milvus、默认 hybrid 或图策略。R1 复用 `DiagnosisApplicationService.from_environment()`、FastAPI lifespan、`RetrievalCoordinator` 和 HTTP Session API；R2 在持久队列边界注入一次可控进程退出，使用稳定 `point_id`、SQLite 缓存向量和 fenced lease 恢复。真实服务调用只在用户确认 Case 后执行，准备阶段使用相同接口的本地替身。

**Tech Stack:** Python 3.13、pytest、FastAPI TestClient、SQLite、PyMilvus 3.0.1 / Milvus、Qwen Flash Embedding、OpenAI-compatible model adapter。

**Spec:** `docs/superpowers/specs/2026-09-14-prd005-resumption-design.md`

## 本轮执行状态（2026-09-14）

下文保留设计时的步骤和示例，实际实现/差异/命令以 [R1/R2 Case 文档](../../validation/prd005-real-app-20260914/REAL-APPLICATION-CASE.md) 为准，不用示例代码替代已验证代码。

| 单元 | 当前证据 |
|---|---|
| collection前置 | 49d6595；默认/显式配置测试及相关33项通过 |
| Task 1冻结/审计 | 9ad65ab及后续加固；包含非法usage/Milvus token保护 |
| Task 2 R1 | c20e274及后续验收加固；全量中的23个本地检查满足，远程Case未运行 |
| Task 3 R2 | 真实子进程exit86、本地13个检查满足；旧exit17路径17检查保持 |
| Task 4准备 | 766/766回归通过；输入、既有模型配置和600/120秒预算已列入Case文档，待用户确认 |
| Task 5真实运行 | R1最后一次技术重试23/23、R2首轮13/13检查满足，独立审计通过；最终773/773回归通过。用户review待完成，详见REAL-RESULTS.md |

实现调整：R2支持代码拆入 `scripts/prd005_embedding_recovery.py`；离线R1轮询20ms、真实工厂保持5秒；真实R1申请600秒预算；collection以输出路径摘要隔离而非复用同manifest基名。以上均在Case文档披露。

## Global Constraints

- 固定存储边界：SQLite 是文档、任务、attempt、manifest、Session 和 Evidence 的权威来源；Milvus 是可重建向量投影。
- 固定检索入口：默认 `hybrid`；图候选策略继续 pending，不在本计划修改。
- 固定投影：R1 显式使用 `utf8-slices-8192-v1`；R2 使用冻结单文档自身的 projection revision 并逐项记录。不得依赖应用的 `path-symbol-source-v1` 默认值，也不得对已有 active projection 做隐式迁移。
- 固定身份：`repository_id/snapshot_id/published_generation/commit_sha` 与 `model_revision/dimension/template_revision/projection_revision` 全部进入核对。
- R1 只验已完成 Session 的重开读取；R2 只验 Embedding 中断恢复。运行中 Session 的持久 `RuntimeSnapshot` 接线不在本计划内。
- R1/R2 使用单活动 Coordinator；现有 task lease 不等于 projection run-level lease，多 Coordinator 并发 reconcile 继续列为生产硬化项。
- 单个真实 Case 最长 120 秒、最多重试 2 次；同一输入连续失败 2 次后停止扩展并定位根因。
- 外部执行前必须冻结 repository、manifest SHA-256、query IDs、模型身份、维度、Milvus URI 身份和输出目录，并得到用户对 Case 的确认。
- 凭证只从环境读取；命令、JSON、Markdown、异常和日志不得保存 API key、Milvus token、Authorization header、请求正文或完整源码正文。
- `case_pass=true` 仅在所有硬检查通过、意外后台异常为 0、产物齐全且重开核对完成时允许出现；`execution_pass` 与 `quality_pass` 分开。
- 每完成实现、针对性测试、真实 Case 或回归 Step，立即追加 `docs/memory-development-log.md`，包含命令、实际数字、产物和状态。
- 当前工作区有用户的既有文档改动；每次提交只暂存本 Task 列出的文件，不使用全量 `git add`。

---

## File Map

| 文件 | 责任 |
|---|---|
| `src/antisentinel/retrieval/config.py` | 增加向后兼容且受校验的 Milvus collection 基名配置，使共享 Standalone Case 可隔离 |
| `tests/test_retrieval_coordinator.py` | 验证 collection 配置默认值、显式值和非法值 |
| `scripts/prd005_real_case_support.py` | 冻结输入、审计 HTTP 请求数量/usage、临时 Case 环境、脱敏报告校验；不执行业务流程 |
| `tests/test_prd005_real_case_support.py` | 验证输入冻结、answerable/no-answer 选择、审计脱敏和环境恢复 |
| `scripts/run_prd005_real_session_case.py` | R1：发布冻结 Code Map、正常应用启动、两个真实 Session、停止/重开、Evidence/双库核对和报告 |
| `tests/test_prd005_real_session_case.py` | 用本地 Encoder/Model 走相同 R1 编排，验证正常 HTTP/lifespan 接线与失败判定 |
| `scripts/run_embedding_worker_case.py` | 保留已有本地恢复 Case，并扩展一个单文档真实向量写后退出/恢复模式 |
| `tests/test_prd005_embedding_recovery_case.py` | 验证既有故障路径的可复用包装器、退出标记、旧租约 fencing、缓存向量复用和报告门槛 |
| `docs/validation/prd005-resumption-20260914/REAL-APPLICATION-CASE.md` | Case 输入、命令、实际数字、R1/R2 结果和剩余风险 |
| `docs/validation/prd005-stage5/OPEN-ITEMS.md` | 只在真实 Case 后更新对应 pending 项，不提前关闭质量/生产项 |
| `docs/memory-development-log.md` | 每个 Step 的量化开发记录 |

生产代码只计划修改 `retrieval/config.py` 的 collection 基名配置，默认行为保持不变；不修改排名、Evidence DTO 或生产 API。若 acceptance harness 还需要其他生产接口，停止实施并回到设计 review；不得为了让 Case 通过而在脚本中改 SQLite 状态。

---

### Prerequisite Task: 隔离 Milvus collection 基名

**Files:**
- Modify: `src/antisentinel/retrieval/config.py`
- Modify: `tests/test_retrieval_coordinator.py`
- Modify: `docs/memory-development-log.md`

**Interfaces:**
- Consumes: `ANTISENTINEL_CODE_RETRIEVAL_MILVUS_COLLECTION`，缺省为 `antisentinel_code`
- Produces: `collection_base: str`，作为 `MilvusAdapter` 的第二个位置参数

- [ ] **Step 1: 写显式 collection 配置失败测试**

```python
def test_enabled_config_uses_isolated_collection_base(monkeypatch):
    monkeypatch.setenv("ANTISENTINEL_CODE_RETRIEVAL_ENABLED", "true")
    monkeypatch.setenv("ANTISENTINEL_CODE_RETRIEVAL_REPOSITORIES", "repo-a")
    monkeypatch.setenv("ANTISENTINEL_CODE_RETRIEVAL_MILVUS_URI", "case.db")
    monkeypatch.setenv("ANTISENTINEL_CODE_RETRIEVAL_MILVUS_COLLECTION", "prd005_case_a1b2")
    captured = {}

    class Adapter:
        def __init__(self, uri, collection_name, **kwargs):
            captured.update(uri=str(uri), collection_name=collection_name, **kwargs)

    monkeypatch.setattr("antisentinel.retrieval.milvus_adapter.MilvusAdapter", Adapter)
    service = SimpleNamespace(code_map_store=object(), code_map_evidence_store=object())
    coordinator = coordinator_from_environment(service)
    coordinator.index_factory(SimpleNamespace(model_name="m", dimension=3))
    assert captured["collection_name"] == "prd005_case_a1b2"
```

- [ ] **Step 2: 运行测试，确认红灯**

Run: `/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python -m pytest tests/test_retrieval_coordinator.py::test_enabled_config_uses_isolated_collection_base -q`

Expected: FAIL，实际 collection 基名仍为 `antisentinel_code`。

- [ ] **Step 3: 实现受限配置并保持默认值**

```python
collection_base = os.getenv(
    "ANTISENTINEL_CODE_RETRIEVAL_MILVUS_COLLECTION", "antisentinel_code"
).strip()
if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", collection_base) is None:
    raise ValueError("invalid Milvus collection base")
```

把 `collection_base` 传给现有 `MilvusAdapter`；正则和 80 字符是应用层的保守命名约束，version hash 后缀另由 `versioned_collection_name()` 添加。

- [ ] **Step 4: 补默认值和非法值测试**

参数化验证缺省值等于 `antisentinel_code`，以及空字符串、数字开头、包含 `-`、`.`、空格和长度 81 都抛出 `ValueError("invalid Milvus collection base")`。非法值必须在构造 Milvus 客户端前失败。

- [ ] **Step 5: 运行相关测试并追加开发记录**

Run: `/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python -m pytest tests/test_retrieval_coordinator.py tests/test_milvus_adapter.py -q`

Expected: 全部 PASS、0 failed。记录实际测试数、耗时、修改文件数、外部调用 0。

- [ ] **Step 6: 提交 prerequisite**

```bash
git add src/antisentinel/retrieval/config.py tests/test_retrieval_coordinator.py docs/memory-development-log.md
git commit -m "feat: isolate retrieval collections by configuration"
```

---

### Task 1: 冻结输入与脱敏调用审计

**Files:**
- Create: `scripts/prd005_real_case_support.py`
- Create: `tests/test_prd005_real_case_support.py`
- Modify: `docs/memory-development-log.md`

**Interfaces:**
- Consumes: `antisentinel.evaluation.code_corpus.load_corpus(repository: Path, manifest_path: Path) -> EvaluationCorpus`
- Produces: `freeze_case_input(repository: Path, manifest: Path, answerable_query_id: str, no_answer_query_id: str) -> FrozenCaseInput`
- Produces: `HttpUsageAudit.request(request)`, `HttpUsageAudit.response(response)`, `HttpUsageAudit.snapshot() -> dict`
- Produces: `case_environment(values: dict[str, str]) -> ContextManager[None]`
- Produces: `assert_report_safe(report: dict) -> None`

- [ ] **Step 1: 写输入冻结失败测试**

```python
def test_freeze_case_input_requires_one_answerable_and_one_no_answer_query():
    root = Path(__file__).resolve().parents[1]
    frozen = freeze_case_input(
        root,
        root / "tests/fixtures/code_retrieval/v1/manifest.json",
        "q01",
        "q21",
    )
    assert frozen.answerable_query["relevant_ids"]
    assert frozen.no_answer_query["relevant_ids"] == []
    assert frozen.answerable_query["scope"] == frozen.no_answer_query["scope"]
    assert len(frozen.manifest_sha256) == 64

    with pytest.raises(ValueError, match="answerable query"):
        freeze_case_input(root, root / "tests/fixtures/code_retrieval/v1/manifest.json", "q21", "q22")
```

- [ ] **Step 2: 运行输入冻结测试，确认红灯**

Run: `/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python -m pytest tests/test_prd005_real_case_support.py::test_freeze_case_input_requires_one_answerable_and_one_no_answer_query -q`

Expected: FAIL，错误为 `ModuleNotFoundError: No module named 'scripts.prd005_real_case_support'`。

- [ ] **Step 3: 实现不可变 Case 输入**

```python
@dataclass(frozen=True)
class FrozenCaseInput:
    repository: Path
    manifest: Path
    manifest_sha256: str
    corpus: EvaluationCorpus
    answerable_query: dict
    no_answer_query: dict


def freeze_case_input(repository, manifest, answerable_query_id, no_answer_query_id):
    repository, manifest = Path(repository).resolve(), Path(manifest).resolve()
    corpus = load_corpus(repository, manifest)
    selected = {item["id"]: item for item in corpus.queries}
    answerable = selected.get(answerable_query_id)
    no_answer = selected.get(no_answer_query_id)
    if answerable is None or not answerable["relevant_ids"]:
        raise ValueError("answerable query must exist and have relevant IDs")
    if no_answer is None or no_answer["relevant_ids"]:
        raise ValueError("no-answer query must exist and have zero relevant IDs")
    if answerable["scope"] != no_answer["scope"]:
        raise ValueError("pilot queries must share one fixed scope")
    return FrozenCaseInput(
        repository, manifest, hashlib.sha256(manifest.read_bytes()).hexdigest(),
        corpus, answerable, no_answer,
    )
```

- [ ] **Step 4: 写 HTTP 审计脱敏失败测试**

```python
def test_http_usage_audit_counts_usage_without_secrets_or_bodies():
    audit = HttpUsageAudit("model")
    request = httpx.Request(
        "POST", "https://model.example/v1/chat/completions",
        headers={"Authorization": "Bearer secret-value"},
        json={"private_source": "do not persist"},
    )
    response = httpx.Response(
        200, request=request,
        json={"usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}},
    )
    audit.request(request)
    audit.response(response)
    snapshot = audit.snapshot()
    assert snapshot == {
        "kind": "model", "requests": 1, "responses": 1,
        "input_tokens": 11, "output_tokens": 7, "total_tokens": 18,
        "usage_responses": 1,
    }
    assert "secret-value" not in json.dumps(snapshot)
    assert "private_source" not in json.dumps(snapshot)
```

- [ ] **Step 5: 实现只保存计数的调用审计**

```python
class HttpUsageAudit:
    def __init__(self, kind):
        self.kind = kind
        self._lock = Lock()
        self.requests = self.responses = self.input_tokens = 0
        self.output_tokens = self.total_tokens = self.usage_responses = 0

    def request(self, _request):
        with self._lock:
            self.requests += 1

    def response(self, response):
        response.read()
        try:
            usage = response.json().get("usage", {})
        except (ValueError, AttributeError):
            usage = {}
        with self._lock:
            self.responses += 1
            if isinstance(usage, dict) and type(usage.get("total_tokens")) is int:
                self.input_tokens += int(usage.get("prompt_tokens", usage.get("input_tokens", 0)))
                self.output_tokens += int(usage.get("completion_tokens", usage.get("output_tokens", 0)))
                self.total_tokens += usage["total_tokens"]
                self.usage_responses += 1

    def snapshot(self):
        with self._lock:
            return {name: getattr(self, name) for name in (
                "kind", "requests", "responses", "input_tokens", "output_tokens",
                "total_tokens", "usage_responses",
            )}
```

- [ ] **Step 6: 实现环境恢复与报告泄密扫描**

`case_environment()` 只修改显式键，并在 `finally` 中逐键恢复原值或删除新增键。`assert_report_safe()` 对序列化报告拒绝 `DASHSCOPE_API_KEY`、`ANTISENTINEL_MODEL_API_KEY`、`Authorization`、`Bearer `、当前环境中两个 API key 的实际值，以及字段名 `source_text`、`request_body`、`response_body`。

```python
@contextmanager
def case_environment(values):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def assert_report_safe(report):
    encoded = json.dumps(report, ensure_ascii=False)
    forbidden = ["DASHSCOPE_API_KEY", "ANTISENTINEL_MODEL_API_KEY", "Authorization", "Bearer ",
                 "source_text", "request_body", "response_body"]
    forbidden.extend(value for value in (
        os.getenv("DASHSCOPE_API_KEY"), os.getenv("ANTISENTINEL_MODEL_API_KEY")
    ) if value)
    if any(value in encoded for value in forbidden):
        raise ValueError("case report contains sensitive material")
```

- [ ] **Step 7: 运行 Task 1 测试并追加开发记录**

Run: `/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python -m pytest tests/test_prd005_real_case_support.py -q`

Expected: 全部 PASS，0 failed。把测试数、耗时、修改文件数、外部调用 0 和产物路径追加到 `docs/memory-development-log.md`。

- [ ] **Step 8: 提交 Task 1**

```bash
git add scripts/prd005_real_case_support.py tests/test_prd005_real_case_support.py docs/memory-development-log.md
git commit -m "test: add PRD005 real case contracts"
```

---

### Task 2: R1 正常应用入口、真实 Session 与完成后重开

**Files:**
- Create: `scripts/run_prd005_real_session_case.py`
- Create: `tests/test_prd005_real_session_case.py`
- Modify: `docs/memory-development-log.md`

**Interfaces:**
- Consumes: `FrozenCaseInput`, `HttpUsageAudit`, `case_environment()`, `assert_report_safe()` from Task 1
- Consumes: `publish_graph_corpus(corpus, database) -> SQLiteCodeMapStore`
- Produces: `CaseFactories(embedder_factory: Callable, model_factory: Callable)` for offline tests
- Produces: `run(repository: Path, manifest: Path, output: Path, milvus_uri: str, answerable_query_id: str, no_answer_query_id: str, timeout: float = 120, factories: CaseFactories | None = None) -> dict`
- Produces CLI: `--preflight`, `--repository`, `--manifest`, `--output`, `--milvus-uri`, `--answerable-query-id`, `--no-answer-query-id`, `--timeout`

- [ ] **Step 1: 写正常入口端到端失败测试**

测试使用本地 `Encoder` 和每个 Session 独立的脚本化 `Model`，但仍必须调用 `DiagnosisApplicationService.from_environment()`、`create_app()`、`POST /api/incidents` 和 `POST /api/incidents/{id}/sessions`。脚本化 answerable 模型依次调用 `code_retrieval.search`、`code_retrieval.read_evidence`、返回带 Evidence ID 的 final；no-answer 模型调用 search 后返回 `insufficient_evidence` 且引用为空。

```python
def test_r1_uses_normal_application_entry_and_reopens_without_document_embedding(tmp_path):
    root = Path(__file__).resolve().parents[1]
    factories = local_case_factories()
    report = run(
        root,
        root / "tests/fixtures/code_retrieval/v1/manifest.json",
        tmp_path / "r1",
        str(tmp_path / "vectors.db"),
        "q01", "q21", 120,
        factories=factories,
    )
    assert report["case_pass"]
    assert report["counts"]["sessions"] == 2
    assert report["counts"]["persisted_results"] == 2
    assert report["counts"]["rehydrated_evidence"] >= 1
    assert report["embedding"]["document_inputs_per_start"][1] == 0
    assert report["checks"]["normal_http_entry"]
    assert report["checks"]["projection_revision_matches"]
    assert report["unexpected_background_exceptions"] == 0
```

- [ ] **Step 2: 运行 R1 测试，确认红灯**

Run: `/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python -m pytest tests/test_prd005_real_session_case.py::test_r1_uses_normal_application_entry_and_reopens_without_document_embedding -q`

Expected: FAIL，错误为 `ModuleNotFoundError: No module named 'scripts.run_prd005_real_session_case'`。

- [ ] **Step 3: 实现真实 adapter 工厂**

CLI 模式构造共享的 `HttpUsageAudit`。Embedding 工厂创建 `QwenFlashEmbedder`，显式 `max_retries=0`，让持久 Worker 负责重试；Model 工厂创建 `OpenAICompatibleModelAdapter`。两者给各自 `httpx.Client` 安装审计 hooks，禁止保存正文和 header。

```python
@dataclass(frozen=True)
class CaseFactories:
    embedder_factory: Callable[[], object]
    model_factory: Callable[[], object]
    embedding_audit: HttpUsageAudit
    model_audit: HttpUsageAudit
    close: Callable[[], None]


def real_case_factories():
    embedding_audit, model_audit = HttpUsageAudit("embedding"), HttpUsageAudit("model")
    model_clients = []

    def embedder_factory():
        embedder = QwenFlashEmbedder(
            base_url=os.environ["ANTISENTINEL_EMBEDDING_BASE_URL"],
            api_key=os.environ["DASHSCOPE_API_KEY"],
            dimension=int(os.getenv("ANTISENTINEL_EMBEDDING_DIMENSION", "1024")),
            max_retries=0,
        )
        embedder.client.event_hooks["request"].append(embedding_audit.request)
        embedder.client.event_hooks["response"].append(embedding_audit.response)
        return embedder

    def model_factory():
        client = httpx.Client(event_hooks={
            "request": [model_audit.request], "response": [model_audit.response],
        })
        model_clients.append(client)
        return OpenAICompatibleModelAdapter(
            base_url=os.environ["ANTISENTINEL_MODEL_BASE_URL"],
            api_key=os.environ["ANTISENTINEL_MODEL_API_KEY"],
            model=os.environ["ANTISENTINEL_MODEL_NAME"],
            timeout=float(os.getenv("ANTISENTINEL_MODEL_TIMEOUT_SECONDS", "30")),
            client=client,
        )

    def close():
        for client in model_clients:
            client.close()

    return CaseFactories(embedder_factory, model_factory, embedding_audit, model_audit, close)
```

R1 的 `finally` 必须调用 `factories.close()`；测试断言所有 Model HTTP client 已关闭。

- [ ] **Step 4: 实现固定 Code Map 与 Case 环境**

先在 `output/facts.sqlite` 调用 `publish_graph_corpus()`；然后设置以下非秘密环境并构造 `DiagnosisApplicationService.from_environment()`：

```python
values = {
    "ANTISENTINEL_STORAGE_ROOT": str(output / "storage"),
    "ANTISENTINEL_SQLITE_PATH": str(output / "facts.sqlite"),
    "ANTISENTINEL_PERSISTENCE_MODE": "sqlite",
    "ANTISENTINEL_MEMORY_VECTOR_ENABLED": "0",
    "ANTISENTINEL_MODEL_MODE": "real",
    "ANTISENTINEL_CODE_RETRIEVAL_ENABLED": "true",
    "ANTISENTINEL_CODE_RETRIEVAL_REPOSITORIES": frozen.answerable_query["scope"]["repository_id"],
    "ANTISENTINEL_CODE_RETRIEVAL_MILVUS_URI": milvus_uri,
    "ANTISENTINEL_CODE_RETRIEVAL_MILVUS_COLLECTION": collection_base,
    "ANTISENTINEL_CODE_RETRIEVAL_PROJECTION": SLICE_REVISION,
    "ANTISENTINEL_CODE_RETRIEVAL_QUERY_TIMEOUT": "10",
}
```

构造 service 后、进入 lifespan 前，把 `service.retrieval_coordinator.embedder_factory` 设为审计工厂，把 `service.model_factories["real"]` 设为审计模型工厂。该替换只增加 Case 调用计数，使用的仍是生产 adapter 和正常 runtime/tool 接线。

- [ ] **Step 5: 实现两个 HTTP Session 和 R1 硬检查**

对 answerable/no-answer 各调用一次：

```python
incident = client.post("/api/incidents", json={
    "title": query["query"],
    "summary": "请使用代码检索工具核对固定版本源码后给出结论；证据不足时明确拒答。",
    "source": "prd005-r1-case",
}).json()
service.code_map_store.bind_incident(
    incident["incident_id"], query["scope"]["repository_id"], query["scope"]["snapshot_id"]
)
started = client.post(
    f"/api/incidents/{incident['incident_id']}/sessions",
    json={"participant_ids": ["prd005-case"], "model_mode": "real"},
)
```

等待条件必须同时检查 HTTP session 终态和 SQLite `load_result(session_id)` 非空。answerable Session 要求成功执行 search/read_evidence、至少 1 条 final Evidence 引用且每条都属于披露切片；no-answer Session 要求运行完成、引用只能来自实际披露 Evidence，不强制引用数为 0，也不把答案内容计入质量通过。

- [ ] **Step 6: 实现完整停止与重开核对**

退出第一个 `TestClient` 后重新调用 `DiagnosisApplicationService.from_environment()`，安装同样的审计工厂并进入新的 lifespan。核对：

```python
assert client.get("/api/retrieval/status").json()["runs"] == {"ready": scope_count}
assert client.get(f"/api/sessions/{answerable_session_id}").json()["status"] == "completed"
references = [{"evidence_id": item["evidence_id"]} for item in persisted_result["evidence_refs"]]
restored = SourceEvidenceService(
    None, service.code_map_store, service.code_map_evidence_store
).rehydrate(references)
assert len(restored) == len(references)
```

同时从 SQLite 读取 manifest、tasks、attempts、sessions、result_json、evidence；调用已启动 coordinator 的 reconciliation 结果核对 Milvus。第二次启动的文档 Embedding 输入数必须为 0。不要把 query embedding 与文档 embedding 合并计数。

Evidence metadata 当前没有 `document_id/projection_revision`。对每条恢复 Evidence，必须用 `repository_id/snapshot_id/generation/commit_sha/path/node_id/chunk_id/source_hash/byte range` 在 active SQLite projection 唯一匹配 1 个 document，再用该 document 的 point_id 读取 Milvus 并核对 `document_id/input_hash/model_revision/dimension/template_revision/projection_revision`。报告 `evidence_to_active_document_matches == evidence_count`，并明确 `evidence_projection_identity_persisted=false`；不得声称 Evidence 自身已保存投影版本。

- [ ] **Step 7: 实现报告与失败产物**

`report.json` 至少包含：冻结输入 hash、模型/维度/投影、文件/字节/chunk/document 数、两个 Session 状态、工具 attempt 数、输出数、SQLite/Milvus/Evidence 数、身份核对数、首次/重开文档与查询 Embedding 数、模型 HTTP 请求与 token、任务 attempt/retry、后台异常、`t_business`、`t_persisted`、`t_verified` 和两个差值。异常路径写 `failure.json`，保留已产生的 DB/向量，不覆盖已有输出。

`case_pass` 的布尔表达式必须等价于：

```python
case_pass = (
    all(checks.values())
    and unexpected_background_exceptions == 0
    and missing_artifacts == 0
    and elapsed_ms <= timeout * 1000
)
```

`quality_pass` 固定为 `False`，原因写 `two-query integration pilot is not a reviewed quality set`。

- [ ] **Step 8: 运行 R1 离线测试与 CLI 合约**

Run: `/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python -m pytest tests/test_prd005_real_session_case.py tests/test_prd005_real_case_support.py -q`

Run: `/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python scripts/run_prd005_real_session_case.py --help`

Expected: 全部测试 PASS；`--help` 退出 0；外部请求 0。

- [ ] **Step 9: 追加开发记录并提交 Task 2**

记录实际测试数、耗时、文件数和外部调用 0，然后仅提交本 Task 文件：

```bash
git add scripts/run_prd005_real_session_case.py tests/test_prd005_real_session_case.py docs/memory-development-log.md
git commit -m "test: add real application retrieval case"
```

---

### Task 3: R2 向量写后进程退出与 fenced lease 恢复

**Files:**
- Modify: `scripts/run_embedding_worker_case.py`
- Create: `tests/test_prd005_embedding_recovery_case.py`
- Modify: `docs/memory-development-log.md`

**Interfaces:**
- Consumes: `FrozenCaseInput`, `HttpUsageAudit`, `assert_report_safe()` from Task 1
- Consumes: `EmbeddingQueue`, `EmbeddingWorker`, `MilvusAdapter`, `QwenFlashEmbedder`
- Produces: `CrashAfterFirstUpsert(index, marker: Path, crash: Callable[[int], NoReturn])`
- Produces: `run_real_recovery(repository: Path, manifest: Path, output: Path, milvus_uri: str, query_id: str, timeout: float = 120) -> dict`
- Preserves: 现有 `run(output: Path)` 本地多故障 Case 和 `--child` 模式
- Produces child CLI phase: `--real-recovery --child`; expected exit code `86`

- [ ] **Step 1: 写故障边界失败测试**

```python
def test_crash_wrapper_marks_successful_upsert_before_exit(tmp_path):
    index = FakeIndex()
    exits = []

    class InjectedCrash(BaseException):
        pass

    def crash(code):
        exits.append(code)
        raise InjectedCrash()

    wrapper = CrashAfterFirstUpsert(index, tmp_path / "upsert-complete.json", crash)
    with pytest.raises(InjectedCrash):
        wrapper.upsert([POINT])
    assert exits == [86]
    marker = json.loads((tmp_path / "upsert-complete.json").read_text())
    assert marker == {"point_ids": [POINT.point_id], "upserted": 1}
    assert index.points == {POINT.point_id: POINT}
```

- [ ] **Step 2: 运行故障边界测试，确认红灯**

Run: `/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python -m pytest tests/test_prd005_embedding_recovery_case.py::test_crash_wrapper_marks_successful_upsert_before_exit -q`

Expected: FAIL，错误为 `ImportError: cannot import name 'CrashAfterFirstUpsert' from 'scripts.run_embedding_worker_case'`。

- [ ] **Step 3: 实现可审计的一次性退出包装器**

```python
class CrashAfterFirstUpsert:
    def __init__(self, index, marker, crash=os._exit):
        self.index, self.marker, self.crash = index, Path(marker), crash

    def __getattr__(self, name):
        return getattr(self.index, name)

    def upsert(self, points):
        count = self.index.upsert(points)
        payload = json.dumps({"point_ids": [p.point_id for p in points], "upserted": count}).encode()
        with self.marker.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        self.crash(86)
        raise RuntimeError("crash callback returned")
```

生产路径不会引用该类。现有本地 `CrashAfterWrite` 改用此公共 Case 包装器且保持退出码 17；新 `--real-recovery --child` 使用退出码 86。output 必须是本 Case 新目录。

- [ ] **Step 4: 实现单文档冻结与子进程阶段**

从指定 answerable query 的 `relevant_ids` 选择按 ID 排序后的第一个文档，只把这一条写入新的 `SQLiteCodeSearchStore` manifest，再调用：

```python
run_id = EmbeddingQueue(database).enqueue(
    scope,
    model_revision=embedder.model_name,
    dimension=embedder.dimension,
    template_revision="path-symbol-source-v1",
    projection_revision=document.projection_revision,
    max_attempts=3,
)
worker = EmbeddingWorker(
    queue,
    CrashAfterFirstUpsert(index, output / "upsert-complete.json"),
    embedder,
    owner="crash-child",
    lease_seconds=1,
)
worker.run_once(run_id)
```

子进程预期在 Milvus upsert 成功且 SQLite `vector_json` 已缓存后以 86 退出；任何其他退出码都是失败。调用审计每次 response 后用 append + flush + `os.fsync()` 写脱敏计数 JSONL，确保 `os._exit` 前可读取。

- [ ] **Step 5: 实现父进程中断点核对和恢复**

父进程在恢复前必须观察到：marker 存在、子进程退出 86、SQLite 任务 `status=running`、`attempt=1`、`vector_json IS NOT NULL`、Milvus 中相同 point_id 恰好 1 条。等待 1 秒租约到期后，用 owner `recovery-worker`、相同模型/维度/版本和同一 collection 构造新 Worker。

循环调用 `run_once(run_id)`，直到 `ready` 或总预算耗尽。恢复后的硬断言：

```python
assert task["status"] == "ready"
assert task["attempt"] == 2
assert [row["status"] for row in attempts] == ["lease_expired", "ready"]
assert embedding_http_requests == 1
assert len(index.get([task["point_id"]])) == 1
assert duplicate_logical_vectors == 0
```

`embedding_http_requests == 1` 证明恢复轮次复用了 SQLite 缓存向量；第二次 Milvus 幂等 upsert 不算重复逻辑向量。

- [ ] **Step 6: 增加旧租约 CAS 回归测试**

复用真实 `EmbeddingQueue`，让旧 lease 过期、新 owner 领取后，验证旧 lease 的 `complete()`、`cache_vector()` 和 `fail()` 都抛出 `LeaseLost`，且新任务状态不变。断言条件覆盖 `task_id/status/lease_owner/lease_token/lease_expires_at`，不只比较 owner。

- [ ] **Step 7: 实现 R2 报告门槛**

报告记录 input documents=1、input bytes、运行耗时、Milvus 输出=1、SQLite tasks=1、attempts=2、完整性检查数、预期故障注入=1、意外后台异常=0、业务完成与持久化完成时刻、差值、HTTP 请求=1、重试=1（lease expiry 导致 attempt 2）。预期故障不得记为后台异常，也不得把重试写成 0。

- [ ] **Step 8: 运行 R2 离线测试与 CLI 合约**

Run: `/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python -m pytest tests/test_prd005_embedding_recovery_case.py tests/test_embedding_worker.py -q`

Run: `/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python scripts/run_embedding_worker_case.py --help`

Expected: 全部测试 PASS；`--help` 退出 0；外部请求 0；没有遗留子进程。

- [ ] **Step 9: 追加开发记录并提交 Task 3**

```bash
git add scripts/run_embedding_worker_case.py tests/test_prd005_embedding_recovery_case.py docs/memory-development-log.md
git commit -m "test: add embedding crash recovery case"
```

---

### Task 4: 准备可供用户确认的真实 Case

**Files:**
- Create: `docs/validation/prd005-resumption-20260914/REAL-APPLICATION-CASE.md`
- Modify: `docs/memory-development-log.md`

**Interfaces:**
- Consumes: R1/R2 CLI from Tasks 2–3
- Produces: 一份包含真实冻结 hash、范围、阈值、命令和清理边界的 Case 提案

- [ ] **Step 1: 运行累计离线回归**

Run: `/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python -m pytest tests/test_prd005_real_case_support.py tests/test_prd005_real_session_case.py tests/test_prd005_embedding_recovery_case.py tests/test_embedding_worker.py tests/test_retrieval_coordinator.py tests/test_retrieval_runtime_sources.py -q`

Expected: 0 failed、0 skipped。记录实际测试数与耗时，不预填预计数量。

- [ ] **Step 2: 运行全量回归**

Run: `/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python -m pytest -q`

Expected: 当前 728 项加本计划新增测试全部通过，0 failed、0 skipped；警告数与基线 7 比较。单轮耗时只作描述，不能声称满足 5 轮性能门槛。

- [ ] **Step 3: 运行文档和代码静态检查**

Run: `/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python -m compileall -q scripts/prd005_real_case_support.py scripts/run_prd005_real_session_case.py scripts/run_embedding_worker_case.py`

Run: `git diff --check`

Expected: 两条命令退出 0。

- [ ] **Step 4: 冻结真实 Case 的非执行 preflight**

执行者先设置四个本地变量；变量只包含路径/URI，不包含 API key。下列值是本轮可只读预检的候选输入，尚未授权把该仓库源码发送给外部服务：

```bash
PRD005_REPOSITORY=/Users/xuewentao/agentCode/antisentinel
PRD005_MANIFEST=/Users/xuewentao/agentCode/antisentinel/tests/fixtures/code_retrieval/v1/manifest.json
PRD005_CASE_ROOT=/Users/xuewentao/.local/share/antisentinel/cases/prd005-real-application-20260914
PRD005_MILVUS_URI=http://127.0.0.1:29530
```

Run:

```bash
/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python scripts/run_prd005_real_session_case.py \
  --preflight \
  --repository "$PRD005_REPOSITORY" \
  --manifest "$PRD005_MANIFEST" \
  --output "$PRD005_CASE_ROOT/r1" \
  --milvus-uri "$PRD005_MILVUS_URI" \
  --answerable-query-id q01 \
  --no-answer-query-id q21 \
  --timeout 120
```

Preflight 只读取本地 Git/manifest 和环境变量是否存在，不连接模型或 Milvus，不创建 R1 输出目录。输出包括实际 query IDs、scope、manifest/source/query hash、文件/字节/document 数、模型名、Embedding 名/维度、URI host/port、外部发送范围和 hard gates；不输出凭证。

- [ ] **Step 5: 写 REAL-APPLICATION-CASE.md 并暂停**

文档必须含 R1/R2 的输入、启动方式、观测点、预期、失败判定、清理、基线、至少 3 个成功指标、1 个失败指标、1 个回归指标、120 秒预算和最多 2 次重试。填入 Step 4 的实际 hash/数量，不留待填占位符或估算值；未知费用写 `N/A：provider 未提供账单`。

此处是强制 review gate。不得在同一 Step 启动外部服务、发送源码或调用模型；等待用户确认该 Case。

- [ ] **Step 6: 追加记录并提交 Case 提案**

```bash
git add docs/validation/prd005-resumption-20260914/REAL-APPLICATION-CASE.md docs/memory-development-log.md
git commit -m "docs: propose PRD005 real application case"
```

---

### Task 5: 用户确认后执行 R1 与 R2

**Gate:** 只有用户明确确认 Task 4 中冻结的 repository、模型服务、Milvus 实例、两个 query IDs 和源码外发范围后执行。确认不自动授权其他仓库、模型或生产数据库。

**Files:**
- Modify: `docs/validation/prd005-resumption-20260914/REAL-APPLICATION-CASE.md`
- Modify: `docs/validation/prd005-stage5/OPEN-ITEMS.md`
- Modify: `docs/memory-development-log.md`

**Interfaces:**
- Consumes: 已确认的 `PRD005_REPOSITORY`、`PRD005_MANIFEST`、`PRD005_CASE_ROOT`、`PRD005_MILVUS_URI`
- Produces: `$PRD005_CASE_ROOT/r1/report.json`、`$PRD005_CASE_ROOT/r2/report.json` 和失败轮次产物

- [ ] **Step 1: 执行 R1，一次失败只允许同输入重跑**

```bash
/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python scripts/run_prd005_real_session_case.py \
  --repository "$PRD005_REPOSITORY" \
  --manifest "$PRD005_MANIFEST" \
  --output "$PRD005_CASE_ROOT/r1" \
  --milvus-uri "$PRD005_MILVUS_URI" \
  --answerable-query-id q01 \
  --no-answer-query-id q21 \
  --timeout 120
```

Expected: exit 0、`case_pass=true`、两个 Session 2/2 持久化、answerable 引用至少 1、所有引用身份/hash/范围 100% 通过、第二次启动文档 Embedding 0、后台异常 0。失败时保留目录，第二次使用 `$PRD005_CASE_ROOT/r1-retry1`；不得覆盖或删除首次结果。

- [ ] **Step 2: 校验 R1 产物并立即追加开发记录**

用 Python 读取 `report.json`，重新查询 `facts.sqlite` 的 documents/tasks/attempts/sessions/evidence 数，并从 Milvus 用固定 IDs 读回。文档报告实际值、阈值、差值/比例、证据命令和结果。任何字段缺失都把 `case_pass` 判为 false。

- [ ] **Step 3: 执行 R2**

```bash
/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python scripts/run_embedding_worker_case.py \
  --real-recovery \
  --repository "$PRD005_REPOSITORY" \
  --manifest "$PRD005_MANIFEST" \
  --output "$PRD005_CASE_ROOT/r2" \
  --milvus-uri "$PRD005_MILVUS_URI" \
  --query-id q01 \
  --timeout 120
```

Expected: 父进程 exit 0、预期子进程 exit 86、`case_pass=true`、1 文档/1 任务/1 逻辑向量、attempt 2、HTTP Embedding 请求 1、旧 attempt=`lease_expired`、新 attempt=`ready`、后台意外异常 0。失败重跑使用 `$PRD005_CASE_ROOT/r2-retry1`。

- [ ] **Step 4: 校验 R2 产物并立即追加开发记录**

独立核对 SQLite task/attempt、Milvus point 字段、HTTP audit、marker 和进程退出码。报告预期故障 1 与意外异常 0，不能合并为“异常 1”。

- [ ] **Step 5: 真实 Case 后再跑针对性和全量回归**

Run: `/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python -m pytest tests/test_prd005_real_case_support.py tests/test_prd005_real_session_case.py tests/test_prd005_embedding_recovery_case.py tests/test_embedding_worker.py tests/test_retrieval_coordinator.py -q`

Run: `/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python -m pytest -q`

Expected: 两次均 0 failed、0 skipped。若外部 Case 通过但回归失败，阶段状态仍不能推进。

- [ ] **Step 6: 更新状态文档并请求用户 review**

在 `REAL-APPLICATION-CASE.md` 写 7 列量化表；阶段汇报至少包含新增/修改文件数、测试总数/通过数、失败数、Case 数、总耗时、重试次数、产物数量、后台异常数、未解决风险数。只在 R1/R2 和回归均满足门槛时，把本阶段状态报告为 `回归通过`；`用户 review 通过` 仍等待用户确认。

`OPEN-ITEMS.md` 只能把“真实服务完整链路”缩小为已实际覆盖的范围；Runtime checkpoint 持久接线、projection run-level lease/多 Coordinator reconcile、Evidence 投影字段、质量、TLS/权限、容量、硬 deadline、运维用量和图优化继续 pending。

- [ ] **Step 7: 提交真实报告，不提交私有运行产物或凭证**

```bash
git add docs/validation/prd005-resumption-20260914/REAL-APPLICATION-CASE.md docs/validation/prd005-stage5/OPEN-ITEMS.md docs/memory-development-log.md
git commit -m "docs: record PRD005 real application verification"
```

提交前运行 `git diff --cached --check`，并核对 staged 文件不包含 case DB、向量文件、源码副本、token 或调用正文。

---

## Self-Review Mapping

| 设计要求 | 计划任务 |
|---|---|
| 可隔离的正常应用 Milvus collection | Prerequisite Task |
| 正常应用入口、真实 Embedding、正常 Session LLM | Task 2 |
| 完成后重开、Evidence rehydrate、双库对账 | Task 2 Steps 5–7 |
| 向量已写/SQLite 未确认的中断恢复 | Task 3 |
| point identity、hash/版本字段和 lease fencing | Task 3 Steps 4–6 |
| 输入/耗时/输出/持久化/关联/后台异常六类数字 | Tasks 2–5 报告契约 |
| 120 秒、最多 2 次重试、失败产物保留 | Global Constraints、Task 5 |
| 真实 Case 前用户确认 | Task 4 Step 5、Task 5 Gate |
| 累计回归和开发日志 | Tasks 1–5 的末尾 Step |
| default hybrid、图优化 pending | Global Constraints |
| 不混淆 Runtime checkpoint 与 Embedding 恢复 | Global Constraints、设计 3.0、Task 5 状态边界 |
| Evidence 缺少投影字段的当前边界 | Task 2 Step 6、真实报告剩余风险 |
| 单活动 Coordinator 假设与 run-level lease 缺口 | Global Constraints、真实报告剩余风险 |

自审命令：

```bash
rg -n '\b(T[O]DO|T[B]D|F[I]XME)\b|[s]imilar to|write tests for the abov[e]' docs/superpowers/plans/2026-09-14-prd005-real-application-case.md
git diff --check
```

第一条命令必须无输出，第二条必须退出 0。接口名在 Task 1 定义后由 Tasks 2–3 一致消费；R1/R2 共用的冻结、审计和脱敏逻辑只定义一次。
