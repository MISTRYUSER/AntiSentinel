# PRD-005 剩余工作（2026-09-10）

用户决定（2026-09-10）：**先合入主分支，以下未完成项统一 pending，暂停继续实施；合入不代表生产就绪或 RAG 质量通过。**

用户决定：**图候选选择策略优化 pending**。保持默认hybrid，不继续调整策略或扩大该实验；保留已有A/B报告，不标优化通过。

## 已有证据

keyword/FTS、Milvus Lite向量、hybrid、contains图与hybrid_graph实验入口、Evidence/context/checkpoint、持久embedding队列及恢复、常驻协调器和应用生命周期已有本地Case。持久Ragas环境已恢复，ID指标真实执行；真实Flash检索与DeepSeek/Ragas答案曾以独立Case运行，不等于当前生产入口验收。

最近全量728 passed、0 skipped、0 failed（SOURCE-SLICING），10个累计本地Case及新切片Standalone Case通过。Standalone基础正确性及Strong读取补充验证已有证据；质量和生产就绪仍未通过。

## 未完成清单（全部 pending）

| 项目 | 当前状态 | 缺少的交付/验收 |
|---|---|---|
| 图选择优化 | 用户指定pending | 等待用户恢复；默认hybrid不变 |
| 真实服务完整链路 | 实现已有，本地替身验证通过 | 授权语料→常驻Worker真实Flash→正常Session真实LLM→Evidence→重启恢复的一体化Case。真实模型旧证据来自独立评测脚本 |
| Milvus Standalone | v3.0.1基础正确性已通过 | 鉴权、干净停启、持久数据与Evidence已验；仍需最小权限/TLS、生产平台镜像部署、容量/并发/P95及更广故障验证，见STANDALONE-VERIFIED.md |
| 长源码与范围去重 | UTF-8 限定实现及 Lite/Standalone 正确性通过 | 8192字节切片、父chunk/hash关联、两库范围持久化、Evidence恢复已有证据；其他编码、大语料容量/效果与Graph完整分页仍未验，见SOURCE-SLICING.md |
| 查询总预算 | 共享deadline及本地IO取消/降级已验证 | 仍需真实Flash/Standalone故障下的硬时限；同步DNS/CPU和SDK内部重试可能延迟返回。Evidence/整体Session时限未纳入，见QUERY-DEADLINE.md |
| 运维与用量 | 后端队列/状态已有 | 管理员重试、停用、重建的完整操作入口与验收；模型Token/调用用量和配额触限暂停闭环。租约token不等于模型用量 |
| 默认hybrid质量与答案验收 | 有基线与工具，验收未通过 | 真实业务问题/参考标签review、真实Flash固定评测、无答案处理、生产Session的Ragas答案指标 |
| 文档与最终review | 需收尾 | 统一历史状态、部署/恢复操作说明、验收证据与逐项review；不将整个PRD标Done |

用户恢复本阶段后，优先真实服务完整链路及Standalone验收，随后补大规模切片验收和故障硬时限。既有源码外发授权不扩大；旧企业语料临时目录缺失，需要恢复原语料。

最新本轮完成切片实现及验证：728项回归、10个累计本地Case和1个Standalone切片Case通过；外部模型调用0、默认模式修改0。范围见SOURCE-SLICING.md，质量尚未达标验收。
