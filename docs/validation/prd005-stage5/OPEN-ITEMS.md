# PRD-005 剩余工作（2026-09-14更新）

历史用户决定（2026-09-10）：先合入主分支，剩余项pending。2026-09-14用户恢复方案A并确认真实R1/R2；这两个Case和回归现已通过，用户review待完成。其余生产/质量项仍pending；不标生产就绪。

用户决定：**图候选选择策略优化 pending**。保持默认hybrid，不继续调整策略或扩大该实验；保留已有A/B报告，不标优化通过。

## 已有证据

keyword/FTS、Milvus Lite向量、hybrid、contains图与hybrid_graph实验入口、Evidence/context/checkpoint、持久embedding队列及恢复、常驻协调器和应用生命周期已有本地Case。持久Ragas环境已恢复，ID指标真实执行；真实Flash检索与DeepSeek/Ragas答案曾以独立Case运行，不等于当前生产入口验收。

最新全量773 passed、0 skipped、0 failed、7 warnings、70.59s。真实R1在修复参数manifest及JSON工具别名后23/23检查满足，82向量/2Session/2Evidence重开；真实R2写后进程退出13/13检查满足、1向量/2attempt/恢复编码0，独立复核通过。两轮失败与最后成功均保留，详见[真实结果](../prd005-real-app-20260914/REAL-RESULTS.md)。质量和生产就绪仍未通过。

## 剩余清单与本轮已验证范围

| 项目 | 当前状态 | 缺少的交付/验收 |
|---|---|---|
| 图选择优化 | 用户指定pending | 等待用户恢复；默认hybrid不变 |
| 真实服务完整链路 | R1/R2真实运行通过、回归通过；用户review待完成 | 本项目固定语料→常驻Worker真实Flash→正常HTTP Session真实LLM→Evidence→服务对象重开，以及独立Worker进程中断恢复已验；不等于运行中Session跨进程续跑或业务质量通过 |
| Milvus Standalone | v3.0.1基础正确性已通过 | 鉴权、干净停启、持久数据与Evidence已验；仍需最小权限/TLS、生产平台镜像部署、容量/并发/P95及更广故障验证，见STANDALONE-VERIFIED.md |
| 长源码与范围去重 | UTF-8 限定实现及 Lite/Standalone 正确性通过 | 8192字节切片、父chunk/hash关联、两库范围持久化、Evidence恢复已有证据；其他编码、大语料容量/效果与Graph完整分页仍未验，见SOURCE-SLICING.md |
| 查询总预算 | 共享deadline及本地IO取消/降级已验证 | 仍需真实Flash/Standalone故障下的硬时限；同步DNS/CPU和SDK内部重试可能延迟返回。Evidence/整体Session时限未纳入，见QUERY-DEADLINE.md |
| 运维与用量 | 后端队列/状态已有 | 管理员重试、停用、重建的完整操作入口与验收；模型Token/调用用量和配额触限暂停闭环。租约token不等于模型用量 |
| 默认hybrid质量与答案验收 | 有基线与工具，验收未通过 | 真实业务问题/参考标签review、真实Flash固定评测、无答案处理、生产Session的Ragas答案指标 |
| 文档与最终review | 需收尾 | 统一历史状态、部署/恢复操作说明、验收证据与逐项review；不将整个PRD标Done |
| 运行中Session续跑 | pending | 正常应用尚未接入持久CheckpointStore/resume；R1仅验已完成结果重开 |
| 多Coordinator与周期续租 | pending | task lease不等于projection run-level fencing；长调用周期heartbeat与并发reconcile发布仍待设计/验收 |
| Evidence投影身份 | pending | 当前靠scope/hash/范围回连active文档；Evidence自身尚未持久document/projection字段 |

本阶段用户review后按已选A→B→C推进：业务查询/标签和质量验收，再补生产边界。既有源码外发授权不扩大；旧企业语料临时目录缺失，本轮只使用明确确认的本项目冻结语料。

本次真实执行累计包含失败轮次：266外部请求、108436已报告tokens；2次技术重试全部用于R1，最终R1/R2通过。3个隔离容器已停止，数据/日志保留；修复尚在独立分支，未合入main。范围见REAL-RESULTS.md；质量尚未达标验收。
