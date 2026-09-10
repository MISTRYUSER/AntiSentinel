# 常驻检索与应用生命周期（2026-09-10）

后续修正见[关闭边界专项](SHUTDOWN-BOUNDARIES.md)：增加stopping状态；停止超时不强关在途客户端，真实异常和关闭失败会报告failed并向调用者报错。

应用现在可通过 FastAPI lifespan 创建检索客户端并启动常驻协调器。协调器只扫描显式白名单中的 ready Code Map snapshot，按归档 generation/hash 构造文档，发布 lexical manifest，入持久队列，再由已有带租约 Worker 编码、写 Milvus、对账发布。正常 Session 使用上一轮 incident-bound 工具注册；查询、写索引和关闭共用锁。

默认功能关闭，构造应用不发起 Embedding 请求。此轮未修改用户环境、未启动远程服务、未调用外部模型。

## 配置和运行边界

| 配置 | 行为 |
|---|---|
| `ANTISENTINEL_CODE_RETRIEVAL_ENABLED` | 默认 `false`，只接受 `true` / `false` |
| `ANTISENTINEL_CODE_RETRIEVAL_REPOSITORIES` | 逗号分隔的显式仓库 ID；启用时必填，意味着索引这些仓库已经发布的源码 |
| `ANTISENTINEL_CODE_RETRIEVAL_MILVUS_URI` | 必填；本轮验证本地 Lite 路径，远程 Standalone 尚未验收 |
| `ANTISENTINEL_CODE_RETRIEVAL_PROJECTION` | 默认 `path-symbol-source-v1`；不同已激活 revision 要求显式迁移，协调器不会覆盖 |
| `ANTISENTINEL_STORAGE_ROOT` / `ANTISENTINEL_SQLITE_PATH` | 必须启用 SQLite 持久化，并与 Code Map 发布进程使用同一数据库 |
| 既有 Embedding 配置 | `DASHSCOPE_API_KEY`、`ANTISENTINEL_EMBEDDING_BASE_URL`、`ANTISENTINEL_EMBEDDING_DIMENSION`，继续使用现有 Qwen Flash 适配器；不打印密钥 |

启动入口保持 README 中的 `PYTHONPATH=src uvicorn antisentinel.api.app:app --host 127.0.0.1 --port 8765`。本轮 Lite 使用单个应用进程；不要将本轮结果理解为已验证多 worker 共享 Lite。

`GET /api/retrieval/status` 返回 disabled/new/running/stopped/failed、线程状态、tick 数、各 run 状态数量和投影错误数。不返回凭证或源码。blocked 任务沿用队列显式 unblock 规则；损坏归档及 projection 冲突在当前进程内隔离，需修复后重启，不每轮重复上传。ready run 每 60 秒重新对账；poll 默认 5 秒，每轮每个 run 消费最多一个任务，吞吐未定标。

关闭先禁止后续工具访问，等待在途工作，再由调度线程释放两个客户端。默认 join 上限 40 秒，超时明确抛错；不会为了立即结束而在 RPC 执行中强行关闭客户端。租约和任务缓存继续负责进程退出后的恢复。源码解析发布仍由既有 Code Map daemon 负责。

## 本地验证

基线：652 项测试通过、2 项跳过、45.37 秒；正常生命周期自动投影 Case 尚无覆盖。

- 针对性：`python -m pytest tests/test_retrieval_coordinator.py tests/test_incident_retrieval_tools.py tests/test_embedding_worker.py -q` → 34 passed，4.43 秒。
- 追加启动失败清理和独立 Case 测试后，`/Users/xuewentao/miniconda3/bin/python -m pytest -q` → **659 passed、2 skipped、0 failed、7 warnings，41.87 秒**。新增 7 项；2 项 Ragas 因未安装依赖跳过。
- Case：`/Users/xuewentao/miniconda3/bin/python scripts/run_retrieval_coordinator_case.py --output <new-directory>`，父进程 timeout 120 秒。
- 产物根：`/Users/xuewentao/.local/share/antisentinel/cases/retrieval-lifecycle-b0c94kz6`；包含真实 SQLite、Milvus Lite 文件、source refs、report 与 CLI 日志。

| 指标 | 基线 | 本次首次成功 Case | 阈值 | 差值 | 证据 | 结果 |
|---|---:|---:|---:|---:|---|---|
| 生命周期检查 | 未覆盖 | 12/12 | 全通过 | 新增12 | lifecycle/report.json | 通过 |
| 初次编码 chunk | N/A | 2 | 2 | N/A | report | 通过 |
| 重启编码 chunk | N/A | 0 | 0 | N/A | report | 通过 |
| 持久任务 | 0 | 2 | 2 | +2 | SQLite读回 | 通过 |
| 恢复 Evidence | 0 | 1 | 1 | +1 | source-refs及hash校验 | 通过 |
| 后台异常 | 0 | 0 | 0 | 0 | health及Case检查 | 通过 |
| 外部模型调用 | 0 | 0 | 0 | 0 | 本地替身 | 通过 |
| 全量测试通过数 | 652 | 659 | 原用例无失败 | +7 | pytest | 通过 |

输入 1 文件、52 bytes、2 chunks；正常应用 Session 执行 hybrid 搜索→Evidence→上下文→最终引用；关闭后新建 SQLite/Milvus 客户端，校验已有 ready 任务、搜索及 Evidence 恢复。首次成功总耗时 1297.74 ms，业务/持久化观察差 1.49 ms。该数字是轮询观察值，不是精确写入时刻或生产 P95。

首次 CLI 在业务开始前因脚本缺少 src 导入路径失败，修复后重试 1 次；原始日志保留。Case report 的 retries=0 指其内部业务重试，不代表 CLI 首次就成功。同进程客户端重建不能替代进程崩溃恢复；后者由累计 Worker Case 检验。

全量通过后累计重跑 7 个 Case（worker/publication/keyword/vector/hybrid/graph/lifecycle-final），全部退出0且case_pass=true。检查分别17/9/4/6/8/17/12；内部耗时6568.13/64.26/44.58/792.11/1374.62/499.91/991.04ms，总10334.65ms。最终生命周期Case重建后编码仍为0。命令状态和父进程耗时见根目录cumulative-commands.json；每个Case report和CLI日志分别保存，累计运行重试0。

## 未完成项

本轮仅具有本地 `真实运行通过`、`回归通过` 证据，未标记用户 review 通过。仍需远程 Flash 经常驻 Worker 的授权 Case、Standalone 版本/资源与权限部署、吞吐/容量/多进程验证、四路冻结质量验收及完整 Ragas 环境复测。本轮不证明检索质量提升或生产就绪。
