# Runtime incident 检索绑定验证（2026-09-10）

本步骤完成正常 `DiagnosisApplicationService.start_session` 的可注入检索工具绑定。默认 `retrieval_tools=None`，尚未由环境配置构造客户端，也没有启动常驻投影调度。不能据此标记生产接入全部完成或 RAG 质量通过。

服务端每次工具调用重读 incident binding，仅接受白名单内的仓库；snapshot 可选，多个不同绑定时拒绝歧义。generation/commit 不由模型指定，且与归档核验。生产工具 schema 移除 query_vector，hybrid/vector 查询通过服务端 encoder 生成向量。非法 Scope、模式、预算和空查询在编码前拒绝；keyword/graph 不调用 encoder。读取 Evidence 继续核验当前绑定、hash 和预算。

## 验证命令与结果

Python 为 `/Users/xuewentao/miniconda3/bin/python`。

- `python -m pytest tests/test_incident_retrieval_tools.py tests/test_graph_case_runner.py tests/test_retrieval_runtime_sources.py tests/test_graph_review_contracts.py -q`：28 passed，2.40 秒。
- `python -m pytest -q`：652 passed、2 skipped、0 failed，7 warnings，45.37 秒。两项 Ragas 因主环境未安装依赖跳过，未报告为通过。
- `git diff --check` 与本步骤 Python 文件 `compileall`：退出 0。

持久产物根目录：`/Users/xuewentao/.local/share/antisentinel/cases/runtime-binding-ij1rzwsv`。每个 Case 独立新建目录、父进程超时 120 秒，本次重试 0；每个目录 `report.json`，根目录保存 CLI 输出和 `commands.json`。调用格式：

```sh
python scripts/run_embedding_worker_case.py --output <new-root>/worker
python scripts/run_lexical_publication_case.py --output <new-root>/publication
python scripts/run_code_retrieval_case.py --case keyword --output <new-root>/keyword --timeout 120
python scripts/run_code_retrieval_case.py --case vector --output <new-root>/vector --timeout 120
python scripts/run_code_retrieval_case.py --case hybrid --output <new-root>/hybrid --timeout 120
python scripts/run_code_retrieval_case.py --case graph --output <new-root>/graph --timeout 120
```

| 指标 | 基线 | 实际值 | 阈值 | 变化 | 证据 | 结果 |
|---|---:|---:|---:|---:|---|---|
| 回归通过项 | 638 | 652 | 原有项无失败 | +14 | pytest | 通过 |
| 全量耗时（秒） | 54.49 | 45.37 | ≤57.21 | -9.12 | pytest | 通过 |
| 默认检索客户端 | 0 | 0 | 0 | 0 | default 测试 | 通过 |
| 非法请求编码调用 | N/A | 0 | 0 | N/A | 10 个参数化边界测试 | 通过 |
| 正常应用检索→Evidence 引用 | 未覆盖 | 1 | 1 | 新增覆盖 | graph/report.json | 通过 |
| Graph 完整性检查 | 15 | 17 | 17 | +2 | graph/report.json | 通过 |
| 累计 Case | 6 | 6 | 全部通过 | 0 | 六份 report.json | 通过 |

六个 Case worker/publication/keyword/vector/hybrid/graph 检查分别 17/9/4/6/8/17，全部通过；内部耗时分别 6600.55/77.52/50.52/796.50/1183.33/456.49 ms。Graph 现在包含额外一次完整应用 Session，不能与旧单 Runtime Case 作等工作量延迟比较。单次耗时也不构成 P95 性能证明。

Graph 输入 1 文件、220 bytes、6 个文档；恢复运行和正常应用运行各引用 1 条 Evidence，数据库读回 2 条，精确等于两次引用并集，两次源码 hash 均校验。Scope 泄漏 0、负向检查 11、后台异常 0；业务完成与读回完成差 5.92 ms。真实 SQLite、归档源码、工具执行器和 Runtime 参与运行；协议模型为 scripted，外部模型调用 0，未验证远程 Embedding 或 LLM 质量。

## 尚未完成

1. 环境配置工厂、显式仓库授权配置、客户端生命周期、常驻投影入队与消费。
2. Standalone 部署及资源验收、四路检索冻结质量评测。
3. 完整 Ragas 依赖环境复测。当前正常应用 Case 覆盖 graph；服务端 vector/hybrid 编码边界由测试覆盖，尚未完成正常应用的真实 Milvus/远程编码 Case。

当前子步骤具有本地 `真实运行通过`、`回归通过` 证据；未标记 `用户 review 通过`。
