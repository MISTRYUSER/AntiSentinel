# Embedding Worker：租约、版本通道与恢复

2026-09-09。本轮范围为持久化任务消费处理器和本地恢复Case；未调用付费模型，未声明常驻服务或Standalone生产验收完成。

## 实现

EmbeddingTaskStore.queue提供EmbeddingQueue。enqueue只能接受当前已发布的词法projection，并把实际源码记录与manifest expected集合比较；Scope、model、dimension、template和projection共同确定run_id及expected point IDs。相同身份的任务集合和attempt预算不可悄悄改变。非空投影才可入队，空仓库向量通道另行处理。

claim在SQLite事务中领取一条到期任务，写入owner、随机lease_token、lease_expires_at并增加attempt。过期running任务可回收，同owner重启也必须获得新token。renew/cache/complete/fail都核验owner、token、running状态和未过期租约。最多3次尝试（首次+2次重试），包括过期恢复与索引修复；retry_wait和指数退避next_attempt_at持久化，重开不丢失。耗尽后failed，不自动清零预算。

Worker使用固定path-symbol-source-v1输入模板，验证source hash、input hash、模型与索引版本；拒绝错维、NaN与零向量。向量必须先持久化为不可改写的vector_json，随后才upsert Milvus。若进程写入后退出，新owner复用缓存，避免重复Embedding。无法撤回已发出的旧RPC，但它也只能写相同缓存和canonical point ID；过期owner无法提交队列状态。

任务ready与通道ready分离：所有任务完成后，对Milvus实际ID/字段进行对账，且Scope仍为活动projection，才能发布该版本通道。任务已ready但进程尚未发布通道时，重启可只做对账；reconcile会先确保collection加载。缺失/字段漂移可将对应任务退回retry_wait并用缓存修复，仍共用attempt上限；额外未知向量不自动删除，通道保持degraded，需明确处置。

认证/权限/配置错误blocked，对应版本停止新领取，unblock必须显式调用且不重置attempt。不同版本的run互不借用ready或blocked。识别Qwen适配器HTTP401/403、408/429/常见5xx及transport错误；Milvus gRPC不可用/超时/资源耗尽可重试，权限拒绝阻塞，未知错误保守阻塞。底层原始错误正文不写入任务错误字段。

Worker要求Embedding适配器关闭内部重试（Qwen实例max_retries=0），并要求MilvusAdapter显式配置rpc_timeout。PyMilvus自身可能在这个RPC时间窗口内重试，所以worker attempt不是底层RPC次数；不声称3个attempt等于3个网络请求。默认工作租约120秒、Case RPC窗口10秒。没有硬中断任意Python模型函数；超期返回由租约拒绝，进程级supervisor仍需集成。

code_embedding_attempts保存每次领取、过期、完成、重试或阻塞结果；正常结束的任务记录duration，崩溃检测时间不冒充精确计算耗时。完成后清除当前错误，历史错误留在attempt记录。状态变更更新时间维护，避免沿用上一attempt的完成时间。

## 兼容边界

生产检索只读完整版本的code_embedding_projection_runs，不再把Scope级ready当作任意模型可用。旧set/get_channel_status不带版本的形式保留给诊断兼容，但不再作为检索权威。旧Case的mark_channel_ready现在必须同时满足完整当前文档集、canonical IDs、任务ready和版本/输入hash；空或伪造的“consistent报告”不能独自标ready。

Worker管理的任务禁止旧set_task_status接口修改，必须走租约。已有手工任务不会自动转为Worker任务；重复身份入队要求显式迁移或重建。已有历史索引还需上一阶段的词法对账发布，本轮未修改私有模型评测历史库。

处理器提供enqueue/run_once/reconcile/unblock，队列与Milvus部署/collection配置须固定对应。尚未接入生产常驻调度、配置热更新、多主机时钟/并发压测、批量Embedding或真实付费模型Worker Case；未知SDK错误和额外索引记录仍需操作员处理。整体生产就绪不能据此勾选。

## 测试与真实Case

新增15个测试：并发领取、同owner旧token、重开退避、attempt耗尽、缓存不可变、跨模型blocked隔离、实际Worker写入后中断、缺失向量缓存修复、手工setter拒绝、续租、3个SDK错误码、ready checkpoint恢复、3个无效向量。新模块缺失红灯后逐步通过。针对累计68 passed、0 failed、11.35s；全量先617 passed/52.81s，再624 passed/50.29s及36.48s。最后诊断字段收尾后结果另记下方。

```sh
python scripts/run_embedding_worker_case.py --output <不存在的新目录>
```

首个真实Case使用os._exit(17)在Milvus确认upsert之后结束子进程，父进程等待真实租约过期并重开SQLite/Milvus。16/16检查通过，6.72秒；这不是只抛Python异常模拟进程退出。后续增加ready checkpoint重开验证到17项，并运行publication/keyword/vector/hybrid/graph累计Case。

累计Case产物根：`/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-worker-final-1sb_19e9`。

| 指标 | 原Worker基线 | 本次实际 | 阈值 | 差值 | 证据 | 结果 |
|---|---:|---:|---:|---|---|---|
| Worker恢复检查 | 0（此前无Worker Case） | 17/17 | 17 | +17 | worker/report.json | 通过 |
| 真实进程退出 | 0 | 1 | 1 | +1 | child退出码17 | 通过 |
| ready任务/版本通道 | N/A | 3/3 | 3/3 | N/A | facts.sqlite | 通过 |
| 持久化向量/搜索输出 | N/A | 3/3 | 3/3 | N/A | worker/report.json | 通过 |
| 缓存复用模型调用 | N/A | 4次本地替身调用 | 4 | N/A | embedding-calls.jsonl | 通过 |
| 外部模型调用 | 0 | 0 | 0 | 0 | Case实现/报告 | 通过 |

输入1源文件27字节，3个测试模型版本身份（不是3个真实模型）；3任务共7个attempt，额外4次尝试来自受控故障/显式恢复，没有隐瞒为0重试。业务提交44.92ms，全部持久化6415.31ms，滞后6370.39ms，总6418.40ms，包含刻意等待租约和退避，不能作为正常生产吞吐数据。

同期publication9检查通过/62.01ms；keyword45.71ms、vector802.07ms、hybrid1250.19ms、graph122.90ms，五个既有Case均case_pass=true。Worker与其累计共6Case，全由父进程120秒上限保护，子进程单独30秒保护。新Worker Case使用真实SQLite/Milvus Lite、本地Embedding替身，无网络付费模型。

共涉及9实现/脚本/测试文件：新增embedding_jobs.py、embedding_worker.py、test_embedding_worker.py、run_embedding_worker_case.py，修改vector.py、milvus_adapter.py及3个兼容测试。已知质量、Fusion/Graph到Evidence收尾和Runtime生产集成仍未完成。

## 最终收尾验证

任务诊断字段收尾后，全量命令 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-ragas-clean-wxav1le_/bin/python -m pytest -q`：624 passed、0 failed、6 warnings、38.73s；609→624新增15项，通过率100%。compileall与git diff --check通过。

再次运行Worker Case，产物 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-worker-check-qe47kod_`：17/17、case_pass=true，6336.55ms；提交49.62ms、持久化6333.33ms、滞后6283.71ms，3任务/3通道/3向量/3输出，7attempt、4次本地替身调用。独立SQL检查ready通道无残留error_code、ready任务retryable=0、3份缓存向量、integrity=ok。

独立Milvus读回与SQLite缓存逐元素比较，3/3向量一致（容差1e-6）。首条检查命令漏配src导入路径而在连接前失败，修正后通过，未追加Embedding调用。源文本hash按UTF8验证，非UTF8原始字节投影尚需另外适配；不得伪造hash绕过。

当前本阶段真实运行通过、回归通过；仍需用户review、常驻调度集成、真实模型Worker/Standalone与生产容量验证。没有据本地替身Case宣称整体RAG生产就绪。
