# PRD-005 R1/R2：离线验证结果与真实 Case 提案

日期：2026-09-14。状态：真实R1/R2、最终773项回归及用户review通过，已转入现有24题开发评测。实际运行与两次失败后的修复见[真实结果](REAL-RESULTS.md)；下文保留提案与运行前基线。默认 hybrid，图优化 pending。工作分支 `codex/prd005-real-application-case`，原 main 的未提交文档保持原样。

## 1. 本轮实际结果

本轮验证使用本地模型替身与真实 SQLite/Milvus Lite；没有向外部服务发送源码。R1 的 MODEL_MODE=fake 仅用于本地工厂装配，Session 仍通过 HTTP `model_mode=real` 选择测试注入工厂；远程执行使用 MODEL_MODE=real 和生产 HTTP adapter，不能把二者的结果混报。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据命令/产物 | 结果 |
|---|---|---|---|---|---|---|
| 全量测试 | 728 | 766 | 既有+新增全部通过 | +38/+5.22% | `python -m pytest -q --tb=short --show-capture=no`；[日志](pytest-full-review.txt) | 766/766 |
| 失败/跳过/警告 | 0/0/7 | 0/0/7 | 失败/跳过0，无新增警告 | 0 | 同上 | 满足 |
| 累计相关测试 | 新Case未接入 | 71 | 全部通过 | N/A | [日志](pytest-focused-review.txt) | 71/71，17.70s |
| 全量耗时 | 历史48.65s | 66.13s | 性能须5轮同配置中位比较 | +17.48s/+35.93%（样本/测试数不同） | pytest-full-review.txt | 不作性能通过结论 |
| R1 文档/任务/向量 | 新目录0/0/0 | 82/82/82 | 三者一致 | 各+82 | [R1报告](r1-local-review-report.json) | 满足 |
| R1 Session/持久结果 | 0/0 | 2/2 | 2/2 | 各+2 | 同上 | 满足 |
| R1 Evidence/恢复 | 0/0 | 1/1 | 全部恢复/引用披露正确 | 各+1 | 同上 | 满足 |
| R1 重开文档编码 | 首次82 | 重开0 | 重开0 | −82/−100% | 同上 | 满足 |
| R1 检查 | 0 | 23/23 | 全部满足 | +23 | 同上 | 本地满足 |
| R2 任务/向量/attempt | 0/0/0 | 1/1/2 | 1/1/2 | +1/+1/+2 | [R2报告](r2-local-review-report.json) | 满足 |
| R2 编码次数 | 首次1 | 恢复0 | 恢复0 | −1/−100% | 同上 | 满足 |
| R2 stale lease拒绝 | 0 | 4/4 | 4/4 | +4 | 同上 | 满足 |
| R2 检查 | 0 | 13/13 | 全部满足 | +13 | 同上 | 本地满足 |
| 旧Worker Case | 历史17检查 | 17/17 | 旧行为保持 | 0 | [日志](legacy-worker.txt) | 满足 |
| 外部模型调用 | 0 | 0 | 本轮0 | 0 | R1/R2/旧Case报告 | 未调用 |

R1 3741.10ms，业务观察3630.06ms、持久结果观察3631.03ms，观察差0.97ms；重开核验差103.35ms。R2 3179.57ms，ready返回3165.78ms、存储核验3179.50ms，观察差13.72ms。差值含轮询/读回开销，不是数据库内精确异步写入延迟。

R2 预期子进程退出1次（exit86），业务重试1次（租约到期后attempt2）；R1重试0。意外后台异常/协调器错误为0。本地R2 lease为2秒，拟用远程lease为40秒（覆盖默认30秒Embedding请求），没有宣称Worker拥有周期heartbeat。旧Case exit17路径保留，3模型投影/3向量/7attempt/4编码，6460.87ms；其历史business时间口径只兼容保留，不作为新R2持久延迟。

最终原始数据库由pytest保留在 `/private/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/pytest-of-xuewentao/pytest-17/` 下 R1/R2 对应目录；JSON报告已复制到本目录便于review。临时数据库可能被未来pytest清理，报告不会因此变成新的运行证据。旧Case目录 `/tmp/antisentinel-prd005-legacy.EXBIF7/case`。首次761项日志及首次R1/R2报告保留；[独立review](REVIEW.md)发现的重开结果完整性漏洞已增加5项测试并修正，最终以review后报告为准。

## 2. 超时根因与本轮修正

原失败数据库有48 ready、34 pending、0失败attempt；任务每5秒推进2个。两个快照各41文档，纯轮询等待下界为 `(41−1)×5=200秒`。因此120秒Case不可能满足完整投影。

本地工厂使用20ms轮询以缩短测试；真实工厂保持默认5秒，报告记录实际值。`AllocTimestamp` 日志不是投影停止的证据，不能把这次预算错配描述成Lite兼容性失败。

其他修正：异步HTTP hook使用async函数；真实文档编码计数与query编码分离；坏usage不影响业务响应；CLI从其他目录可启动；blocked、failed及时终止；伪造引用、keyword替代hybrid、隐式降级、跨scope结果均不通过验收；collection基名按输出路径摘要隔离。

## 3. 真实 R1 提案（已确认）

输入仓库：`/Users/xuewentao/.codex/worktrees/antisentinel-prd005-real-app`。读取冻结Git blobs，不读取当前工作区源码。

- Manifest：`tests/fixtures/code_retrieval/v1/manifest.json`
- SHA-256：`6473b9ae5f596fb3dc2287a747ad24f97da15165ce7fc77f7a1cd97d2adab3d0`
- 两个commit：`c89b679db7080b25590ef3cc99e4cb4116f41f95`、`21ad8faf354c97c83fdc29d7525ba24e81106077`
- 数据：10份文件版本、50,833字节、82文档；两个Scope各41文档。
- 问题：q01有答案、q21无答案；pilot只有2题，标签仍proposed，不能当业务质量集。
- 发送范围：文档的路径、符号和源码发送给已有阿里云MaaS Embedding端点；查询文本、模型上下文和预算内披露源码片段发送给已有DeepSeek端点。没有新增企业仓库。
- 模型：`qwen3.7-text-embedding-flash`，1024维；回答模型请求名 `deepseek-chat`，沿用既有 `.env.local` 配置，不宣称实际响应别名与请求名相同。
- 存储：已有独立Standalone `http://127.0.0.1:29530`，使用本Case唯一collection；事实库使用下方新目录。
- 预算：申请600秒（轮询下界200秒，另含82次逐任务编码、两次Session、存证和重开）。最多2次技术重试；同输入连续失败2次停止扩展并定位。费用N/A，provider usage如实记录。
- 初始82次文档编码；query/LLM调用由实际工具回合决定，按HTTP审计计数，不预填固定总次数。当前预算是Case时间，不冒充已实现token配额暂停。

运行前安全读取 `/Users/xuewentao/agentCode/antisentinel/.env.local` 的模型/Embedding字段至子进程，不打印密钥。Standalone token来自已有私有Case配置，不加入命令行。只读检查已确认所需模型/Embedding字段存在；本轮没有启动Standalone，服务就绪状态待检查。

```sh
cd /Users/xuewentao/.codex/worktrees/antisentinel-prd005-real-app
/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python scripts/run_prd005_real_session_case.py \
  --repository /Users/xuewentao/.codex/worktrees/antisentinel-prd005-real-app \
  --manifest tests/fixtures/code_retrieval/v1/manifest.json \
  --output /Users/xuewentao/.local/share/antisentinel/cases/prd005-real-application-20260914/r1 \
  --milvus-uri http://127.0.0.1:29530 \
  --answerable-query-id q01 --no-answer-query-id q21 --timeout 600
```

上述命令的独立进程由执行者加630秒父进程看门狗；Case自身600秒预算含业务检查，额外30秒只用于失败记录/清理。不要把HTTP读超时声称为硬wall-clock终止。看门狗退出必须记录失败；不能遗留report=true。确认Case后再执行。

观测：两个Session API结果、SQLite sessions/result/evidence、manifest及tasks、Milvus Strong读回、引用披露、进程与Coordinator退出状态、脱敏HTTP usage。成功指标：双库82/82且完整匹配；Session2/2持久化；所有引用披露并恢复100%；重开文档编码0。失败指标：越域/坏hash/坏版本/意外后台错误/未声明降级均0。回归：766既有项和后续修复测试全部通过。

清理仅本Case创建的进程/collection/临时文件；保留事实库与报告。若现有Standalone未运行，先用既有Case控制文件完成空间/Engine预检，再启动已核验镜像；不下载新镜像、不重置Docker、不清理其他项目。旧Standalone控制目录见 `docs/validation/prd005-stage5/STANDALONE-VERIFIED.md`。

## 4. 真实 R2 提案（已确认）

输入同一manifest的q01首个按ID排序的相关文档：`src/antisentinel/code_map/worker.py` 的3518字节片段；保持其 `evaluation-projection-v1`，不假装使用R1切片版本。input_hash=`6a6ea5c47ad34237b5a4c423cad2774b35f062b1a43af7cbecbc586b929132f1`。

只调用一次远程Embedding，无回答模型。第一次Worker在向量upsert后fsync marker并exit86，父进程核对1个已写point及未确认任务，等待40秒租约到期后重新领取；新Worker复用SQLite缓存向量，不重新Embedding。成功门槛：task/vector=1/1、attempt2、旧/新token不同、4种旧lease操作拒绝、HTTP请求1、恢复编码0、全字段核对通过。意外异常0；预期exit86单列。

```sh
cd /Users/xuewentao/.codex/worktrees/antisentinel-prd005-real-app
/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python scripts/run_embedding_worker_case.py \
  --real-recovery \
  --repository /Users/xuewentao/.codex/worktrees/antisentinel-prd005-real-app \
  --manifest tests/fixtures/code_retrieval/v1/manifest.json \
  --query-id q01 \
  --output /Users/xuewentao/.local/share/antisentinel/cases/prd005-real-application-20260914/r2 \
  --milvus-uri http://127.0.0.1:29530 --timeout 120
```

R2业务预算120秒、父进程看门狗150秒；最多2次技术重试。正常恢复产生1次业务重试，与技术重跑次数分开。产物config.json、facts.sqlite、upsert-complete.json、embedding-inputs.jsonl、http-audit.jsonl、私有child.log、report.json；不把进程成功退出当作DB/向量成功。

## 5. 实现偏差与继续条件

相对最初计划，R2专属支持代码拆到 `scripts/prd005_embedding_recovery.py`，CLI保留原runner；不增加生产依赖。R1/R2的预期真实预算分别600/120秒，需本轮确认。

本轮没有新增生产API、Evidence schema或多实例租约。仍未解决：运行中Session的持久checkpoint/续跑、Evidence独立保存projection字段、多Coordinator reconcile fencing、Worker周期续租、容量/TLS/权限、硬deadline、token配额闭环、真实业务质量与图优化。R1只验已完成结果重开，R2只验单Worker交接。

用户确认上述输入、既有模型端点、存储与预算后，执行R1→核对/记录→R2→核对/记录→回归→用户review。未确认前不把整个PRD标为完成。
