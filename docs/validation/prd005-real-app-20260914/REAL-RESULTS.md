# 真实 R1/R2 运行结果（2026-09-14）

状态：两个真实Case运行通过，最终773项回归通过；用户后续回复“继续”，本阶段用户review通过，转入现有24题开发评测。此次不标PRD Done、业务质量通过或生产就绪。图优化仍pending，默认hybrid未改。

## 输入与执行

用户在完整Case提案后回复“继续”，确认固定本项目语料、既有模型服务和独立Milvus。输入manifest SHA-256始终为 `6473b9ae5f596fb3dc2287a747ad24f97da15165ce7fc77f7a1cd97d2adab3d0`，查询始终q01/q21，未删题、改标签或选新仓库。

真实服务：原有Milvus Standalone 3.0.1独立栈，镜像ID `sha256:dbc112bfcf4353721b4c70c2db7c2e2c25a9d007f5a43a8b32f01af5f9bb0544`；Flash `qwen3.7-text-embedding-flash` 1024维，回答模型请求名 `deepseek-chat`。R1走正常应用工厂、ASGI lifespan及HTTP Session路由，未启动额外公网Web服务。R2使用真实子进程退出和新Worker恢复。

新产物根：`/Users/xuewentao/.local/share/antisentinel/cases/prd005-real-application-20260914`。父进程仅加载指定配置字段到子进程环境，日志私有；没有把密钥放进参数、Git或报告。R1 Case预算600秒/看门狗630秒，R2 120/150秒。

## R1 三轮结果与修复

| 轮次 | 结果 | 文档/任务/向量 | Evidence | Embedding请求/响应 | 回答模型请求 | 业务重试 | runner耗时 |
|---|---|---|---|---|---|---|---|
| 首次r1 | 失败，4检查不满足 | 82/82/82 | 0 | 82/82 | 4 | 0 | 264.13s |
| r1-retry1 | 失败，5检查不满足 | 82/82/82 | 0 | 85/83 | 5 | 2次transport重试 | 279.30s |
| r1-retry2 | 23/23检查满足 | 82/82/82 | 2 | 84/84 | 5 | 0 | 282.11s |

技术重试共2次，未超过批准上限。前两轮完整保留：[首次失败](real-r1-first-failed.json)、[第一次重试失败](real-r1-retry1-failed.json)；最终[通过报告](real-r1-passed.json)及[执行凭据](real-r1-final-execution.json)。

首轮模型传top_k10、猜未绑定repo ID，被后端正确拒绝。修复在manifest中公开现有1..5上限、候选上限30，以及当前Incident且allowlist内的可选repo/snapshot；权限仍在每次调用时重新检查。

第一次重试的合法search已成功，但JSON tasks中的provider工具别名没有还原为运行时注册名，触发unknown_tool。修复适配器仅映射本次已声明工具的别名，native和JSON两条响应路径保持一致；同时给Evidence candidates提供完整source_identity结构说明。没有调整检索排序、容错为越权放行或修改样本。

该轮另有2次Embedding传输失败后队列恢复。审计现在在真实异常边界记录“已发请求无响应”，保留失败次数而不要求所有请求必然有HTTP响应。未解释的差额仍不能通过审计。旧85/83差额由持久attempt的2条transport记录独立证实，旧报告不改写。

## 最终量化验收

以下只描述最后的成功R1与首轮成功R2。baseline为对应隔离Case执行前状态；零基线的百分比不计算。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据 | 结果 |
|---|---|---|---|---|---|---|
| R1输入 | 固定10份文件版本/50833bytes/82文档 | 相同 | manifest不变 | 0 | real-r1-passed.json | 满足 |
| R1双库及任务 | 0/0/0 | 82文档/82任务/82向量 | 三者一致且逐字段匹配 | 各+82 | [独立审计](real-r1-independent-audit.json) | 82/82匹配 |
| R1结果持久化 | 0 | 2个Session/2结果 | 2/2 | 各+2 | 同上 | 满足 |
| R1最终引用恢复 | 0 | 2条引用/2恢复 | 100% | +2 | 同上 | 2/2 |
| R1重开文档编码 | 首次82 | 重开0 | 0 | −82/−100% | real-r1-passed.json | 满足 |
| R1运行时间 | N/A：首次真实成功 | 282.11s | ≤600s | 余量317.89s | 同上 | 满足 |
| R1业务/持久结果观察 | N/A | 281858.65/281859.63ms | 均在Case预算内 | 差0.98ms | 同上 | 满足 |
| R1重开核验差 | N/A | 247.93ms | 独立记录不混作写入延迟 | N/A | 同上 | 已记录 |
| R1核心检查 | 0 | 23/23 | 全满足 | +23 | 同上 | 满足 |
| R2输入 | 固定1文档/3518bytes | 相同 | hash不变 | 0 | real-r2-passed.json | 满足 |
| R2任务/向量 | 0/0 | 1/1 | 1/1 | 各+1 | [独立审计](real-r2-independent-audit.json) | 满足 |
| R2进程退出 | 0 | 1次exit86 | 写后退出1次 | +1 | real-r2-execution.json | 满足 |
| R2attempt | 0 | 2 | lease_expired→ready | +2 | real-r2-passed.json | 满足 |
| R2恢复编码 | 首次1 | 恢复0 | 0 | −1/−100% | 同上 | 满足 |
| R2旧lease写入拒绝 | 0 | 4/4 | 4/4 | +4 | 同上 | 满足 |
| R2运行时间 | N/A | 42.19s | ≤120s | 余量77.81s | 同上 | 满足 |
| R2业务/存储观察 | N/A | 42158.54/42189.54ms | 均在Case预算内 | 差31.00ms | 同上 | 满足 |
| R2核心检查 | 0 | 13/13 | 全满足 | +13 | 同上 | 满足 |
| 意外后台异常 | 0目标 | 0 | 0 | 0 | 两份成功报告 | 满足 |
| 最终业务重试 | 0 | R1=0、R2=1 | R2预期lease恢复1次 | +1 | 两份成功报告 | 满足 |

成功R1调用真实Embedding84次/24091tokens，真实回答模型5次/16795tokens；成功R2调用Embedding1次/814tokens，回答模型0次。包含失败轮次的整次执行累计：Embedding252请求/250响应/73010已报告tokens，回答模型14请求/14响应/35426tokens；合计266请求、108436已报告tokens，费用N/A（无账单）。缺少响应的2次请求不伪造usage为0，金额不能由已报告tokens估计为完整账单。

四轮runner总时间867.73s（首轮失败、第一次重试失败、最终R1、R2），不含环境启动/修复/回归时间。时间差均为轮询与读回观察，不声称数据库内精确异步延迟。

## 独立核对、清理与剩余项

最终R1独立读取全部82个point逐字段匹配、最终2条引用重开hash校验；R2独立读取单point及两次attempt。独立审计共11检查全部满足；加核心36检查为47检查。使用的独立审计不会通过重新Embedding修复数据。

Case后仅停止原 `antisentinel-milvus-_8k570ob` 项目的3个容器，`docker compose ... ps`返回运行容器0。卷、4轮数据库/向量/日志均保留，Docker Desktop未全局关闭或重置；未清理其他项目。未合入main或推送。

仍待验收：真实业务质量/标签review、生产容量与权限/TLS、运行中Session持久续跑、多Coordinator reconciliation fencing、Worker周期续租、Evidence独立投影字段、运维与用量配额。默认hybrid不变，图优化pending。两个pilot场景不足以判断业务问答准确率，quality_pass始终false。

最终相关回归106/106、20.32s；全量773/773、0失败/0跳过、7warnings、70.59s，相对真实运行前766基线新增7项（+0.91%），相对最初728新增45项（+6.18%）。最新日志为 [相关回归](pytest-real-focused.txt) 和 [全量回归](pytest-real-full.txt)。新增/修改运行与测试文件本轮7个，真实Case共4轮（2失败+2成功）、技术重试2、成功报告2、意外后台异常0、运行容器0、未解决边界8类（见剩余项段落）。用户已确认继续，本阶段review通过；下一步为现有24题开发评测设计review。
