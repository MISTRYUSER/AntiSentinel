# 五项能力验收

本目录保存新增执行能力与现有组件回归的证据。`regression_pass` 只表示列出的离线回归通过；`acceptance_pass` 还要求真实模型和固定数据的质量、延迟对照。本轮不把后者自动置为通过。

| 标准 | 本轮实现或复用范围 | 效果验收仍缺什么 |
|---|---|---|
| Runtime 与上下文 | 复用模型→工具→状态→Evidence、Working Set、Checkpoint；补完成状态发布顺序和原子文件保存；32K本地压力测试 | 真实服务商计数校准和相同任务下的质量对照 |
| 分层记忆 | 复用会话树、偏好图、异步抽取、冲突处理、scope过滤与RRF | 完整470条重测；91.48%/91.56%是历史词法+标识符通道结果，本轮未冒充向量召回收益 |
| Skill 与追踪 | 复用两阶段加载、不可变Release、多版本恢复、执行权限和OTel；新增计划/节点父子追踪 | 同环境冷启动与首轮规划各自的真实计时，验证30s→15s |
| 代码 RAG | 复用Git/AST增量快照、FTS/Milvus融合、来源Evidence及Ragas ID评测 | 人工确认标签与真实语义向量的24题对照；不能将原诊断Case作为92.5%/0.82达标证据 |
| 多意图 DAG | 新增不可变依赖图、校验、有界并发、失败阻断、重试、SQLite恢复、鉴权执行接口 | 50条复合自然语言指令的冻结标注、模型质量和串行/并发P90对照；现有F1来自32条历史集 |

可复现入口：

```bash
OTEL_TRACES_SAMPLER=always_on python -m pytest -q
python scripts/validate_five_capabilities.py --output /tmp/antisentinel-validation-new
python scripts/run_plan_recovery_case.py --output /tmp/antisentinel-recovery-new
```

输出目录必须是新目录。Ragas依赖通过 `pip install -e '.[ragas-evaluation]' pytest` 安装。
统一验收显式排除 `integration` 标记，不使用环境凭据触发真实模型请求；真实模型缺口保留为 `not_evaluated`。

`2026-10-23/` 包含逐组 pytest XML/日志、代码指纹、真实进程恢复结果和32K压力结果。压力测试使用本地保守估算器，包含消息与工具定义；它不证明任意服务商 tokenizer 或任务质量。

独立代码审查发现并修复：恢复未绑定工具代码版本、未知Checkpoint schema未拒绝、审批等待与取消状态冲突。每项均有先失败再通过的回归；额外补了已持久化审批等待的取消。工具远程配置变化须由作者更新 `implementation_version`。

最后全量验证发现现有应用先发布完成、再落盘的竞态。阻塞存储测试复现后，修复为先保存再发布；文件保存使用fsync与同目录原子替换。等待测试后台任务真正结束，避免将线程遗留给后续多进程测试。

接口限制和恢复语义见 [执行说明](../../guides/plan-dag-execution.md)。HTTP执行入口保持只读；通用Runtime仅记录审批等待，尚无审批决策恢复协议。单机文件锁不能替代跨主机分布式锁。

本轮结果：离线五组 294 项全部通过；完整测试946通过、0失败、1项真实模型测试跳过。32K五个压力场景零超限（本地估算器）；真实进程退出及跨进程锁验证通过。真实模型调用0。
