# 查询共享预算（2026-09-10）

search/expand_graph工具入口已接入共享单调时钟deadline。默认10秒，可由服务端ANTISENTINEL_CODE_RETRIEVAL_QUERY_TIMEOUT配置；模型不能通过参数扩大预算。默认hybrid和图候选选择策略没有改变，本轮未重启排序实验。

## 行为

- 锁等待计入预算；到期返回failed/query_deadline_exceeded，不调用编码器。工具注册不再等待索引客户端锁。
- SQLite连接忙等待取剩余预算，长SQL通过progress handler中断；每次Milvus调用动态取得剩余时间，不修改后台Worker的固定RPC设置。
- Graph在入口、归档读取后、遍历边/chunk及返回前检查截止时间；工具入口不接受超期结果。
- Qwen预算内查询使用独立AsyncClient与外层取消计时，最多使用当前剩余预算的80%，留出keyword降级余量；此路径无自动HTTP重试。超时或传输失败且仍有余量时hybrid降级；401/403、schema等错误继续传播。
- query客户端不会关闭/修改后台Worker的同步HTTP客户端。自定义sync client不能被静默替换为真实网络，需显式async_client_factory。
- read_evidence仍使用既有Scope/hash/字节预算；本轮没有给可能已经提交的Evidence存证事务套上事后超时失败。

## 重要边界

这是共享预算传播、受测IO取消与迟到结果拒绝，**不是任意阻塞情况下10秒内硬返回的最终验收**。同步自定义回调、JSON解析/C扩展、DNS/操作系统调用不能被Python阶段检查强行抢占；asyncio.run退出时的底层解析器清理也可能等待。PyMilvus3.0.1同步重试包装会使用其自身时钟/退避，不会在每次内部重试前重新获取本项目deadline，因此故障条件仍可能延迟返回，入口会拒绝超期结果。

这些情况需要进一步的真实Flash/Standalone故障验证及必要的异步RPC/隔离方案；没有用超时线程遗留后台模型请求来伪装硬截止。取消HTTP客户端等待也不承诺外部提供方停止计算或计费。本轮全部请求只到本机TLS替身。

## 验证

Python：`/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python`。

- 既有相关43 passed、15.23s；新增预算相关组合26 passed、13.96s。
- 全量`python -m pytest -q`：711 passed、0 skipped、0 failed、7 warnings、47.42s，基线701 passed。随后只将Case升级成持续分段响应，`python -m pytest tests/test_query_deadline.py -q`：10 passed、3.37s。没有把第一次全量说成包含后加的分段响应细节。
- 测试覆盖锁超时无后续调用、嵌套deadline不延长、上下文恢复、RPC预算递减、SQLite递归查询中断、HTTP取消/客户端关闭、401/403不降级、自定义传输不被静默替换，以及真实本地TLS Case。
- compileall和git diff --check退出0。

真实命令：`python scripts/run_query_deadline_case.py --output <new-output>`，父进程timeout120秒。产物根`/Users/xuewentao/.local/share/antisentinel/cases/query-deadline-r1vo9xqu`；最终deadline-final/report.json、facts.sqlite及Milvus Lite文件保存，TLS私钥只用于本机测试且权限0600，不应分享。

本地TLS服务每40ms发送一个响应字节，持续时间长于查询预算，防止把socket空闲超时误当整请求deadline。最终在收到6个分段后取消请求并降级；SSL证书由Case生成并显式信任，没有verify=False。另一请求立即正常返回，证明取消不影响后续查询和Worker客户端。

| 指标 | 基线 | 实际 | 门槛 | 变化 | 证据 | 结果 |
|---|---|---:|---|---|---|---|
| 共享预算覆盖 | 无统一deadline | 锁/SQL/HTTP/RPC/Graph | 受测阶段均有控制 | 新增 | 测试/实现 | 通过受测范围 |
| 持续响应降级耗时 | 未测 | 333.91ms | 配置400ms；Case容差650ms且必须成功降级 | N/A | deadline-final/report.json | 通过 |
| 锁超时观察耗时 | 无锁等待上限 | 90.11ms | 配置80ms；调度容差300ms | N/A | 同上 | 通过 |
| 锁超时后的HTTP请求 | 未测 | 0 | 0 | N/A | 同上 | 通过 |
| 降级/正常输出 | 未测 | 2/2 | 有结果/完整2条 | N/A | 同上 | 通过 |
| 持久文档/任务 | 0/0 | 2/2 | 2/2 | +2/+2 | SQLite读回 | 通过 |
| 新Evidence | 0 | 0 | 0 | 0 | Case查询 | 通过 |
| 本机HTTP/外部模型调用 | 未测/0 | 2/0 | 2/0 | N/A | Case计数 | 通过 |
| 后台异常/未关闭线程 | 0/0 | 0/0 | 0/0 | 0/0 | Case检查 | 通过 |

输入1文件、52bytes、2chunks，最终10/10检查通过，总1835.32ms。业务完成观察1562.65ms、关闭与存储读回核验1835.31ms，差272.66ms；该差包含服务关闭，不是异步持久化延迟。

全量后累计9 Case deadline/worker/publication/keyword/vector/hybrid/graph/lifecycle/shutdown共100检查全部通过，重试0；内部耗时1835.32/6204.32/63.29/43.34/761.88/1153.53/366.83/967.81/956.21ms。命令、父进程耗时和日志在commands-final.json及同级log文件。

没有把本轮全量47.42s与较小基线40.03s作同负载性能通过结论。本轮具有本地功能回归和Case证据；真实服务故障下的硬时限、整体Session/Evidence时限、容量与最终质量仍未验收。
