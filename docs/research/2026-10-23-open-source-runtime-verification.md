# 开源实现核验与五项验收标准

核验日期：2026-10-23。AntiSentinel 基线：`d1ef53a9eef9a730da5d4cea23545dc12244d028`。
核验方式：读取官方源码并固定提交，不把 issue 中的建议当作已有功能。下列方案是本项目的工程选择，不是对上游性能的复述。

## 已核实的可借鉴机制

| 来源与固定版本 | 核验位置 | 借鉴点 | 本项目落点及边界 |
|---|---|---|---|
| [LangGraph](https://github.com/langchain-ai/langgraph/blob/9d68e5b3eb1a63027b05e9f0152149ea0284dcdb/libs/langgraph/langgraph/pregel/_runner.py) | `PregelRunner.tick`、`commit` | 并发任务完成后分别保存结果，恢复不必重跑整个批次 | DAG 节点级持久化、成功结果复用；工具外部副作用和落盘之间仍有窗口，不能承诺 exactly-once |
| [OpenCode](https://github.com/anomalyco/opencode/blob/2a381f895a79ac55a5a1256aab055582990f70c4/packages/core/src/session/compaction.ts) | `Settings.keep`、`buffer`、压缩触发与结果类型 | 为输出预留空间，保留近期原文，以摘要替代早期历史，压缩失败显式返回 | 复用 Working Set/ContextBudget；32K 必须包含工具定义、预留输出和消息封装，不能只数字符 |
| [Mem0](https://github.com/mem0ai/mem0/blob/2c81af3bbe27d9bccb09c772d583df9e9be72ee5/mem0/memory/main.py) | `_strip_identity_keys`、`_update_memory`、`add_history` | 更新内容时保持身份范围，保存更新前后历史 | 复用异步 Memory Pipeline、版本与 scope 校验；不照搬删除历史的冲突处理方式 |
| [Aider](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/repomap.py) | `get_tags`、`get_ranked_tags`、`get_ranked_tags_map` | 缓存语法标签，依据依赖关系和预算选择代码地图片段 | 复用 Git 快照和 AST Code Map；图扩展作为可选策略，必须通过固定集对照后才能替换默认检索 |
| [DeepAgents](https://github.com/langchain-ai/deepagents/blob/c89da392fbfdea1df7b6ff1ea8bad030efab090d/libs/deepagents/deepagents/middleware/skills.py) | `SkillsMiddleware`、元数据解析、工具披露状态 | 先披露目录元数据，再读取指令；工具执行独立校验披露状态 | 复用两阶段 SkillRuntime 和不可变 Release，恢复时保持原版本；加载技能不能扩大执行权限 |

这些机制无须引入五套框架。保留 Python 自研 Runtime，通过现有接口接入，避免并存多套任务状态和恢复逻辑。

## 开源模型适配核验

- [Qwen 官方工具调用说明](https://qwen.readthedocs.io/en/stable/framework/function_call.html)：Qwen3 推荐 Hermes 风格工具模板，推理模型不宜用依赖停止词的 ReAct 模板截断输出。本项目使用结构化工具调用并在服务端校验名称、参数和权限，不依据自然语言推理文本调度。
- [DeepSeek 官方工具调用说明](https://api-docs.deepseek.com/guides/thinking_mode/)：思考模式的工具链要求按接口保留 `reasoning_content`。当前适配器显式关闭 DeepSeek 思考模式，本轮保持该行为，不声称已经支持思考模式的完整工具协议。
- 模型选型不替代 Runtime、记忆隔离、幂等或恢复；质量比较需要同一数据、相同预算和独立输出记录。本轮不把离线模拟当作真实模型收益。

## 基线核查

- Runtime 已有模型循环、工具执行、Working Set、ContextBudget、Checkpoint、源码引用和 OpenTelemetry；应增加组合验收，避免重复实现。
- Memory 已有会话树、偏好图、异步任务、scope 过滤、Redis 与 RRF；`docs/memory-development-log.md` 的 B任务2记录了470条评测的 Recall@5=91.48%、MRR@5=91.56%、P95=4.28ms，但对应 lexical/identifier 通道，vector=bypass。后续一次重复结果P95为5.08ms。历史数值有日志依据，不能归因于真实向量召回或当作本轮重测。完整数据与逐查询原始产物不在当前 checkout，需另行复测。
- Skill 已有 manifest、package、loader、Release、runtime state 及回归测试；30s→15s 必须明确冷启动和首轮规划分别计时，不能拿加载指令耗时替代模型延迟。
- 代码检索已有快照、AST、FTS/Milvus、RRF、Evidence、Ragas 入口。`docs/validation/prd005-stage5/README.md` 明确质量未通过，不能将诊断执行通过等价为 Recall 达标。
- `orchestration/{dag,plan_validator,planner,scheduler,state_machine}.py` 为骨架。现有 PlanningService 只生成草案，没有依赖边或执行调度。
- `docs/validation/prd006a-effectiveness-acceptance.md` 的 macro-F1=0.96643，但路由仅27/32；零危险误放行限于该固定集，不是任意请求的保证。

## 验收原则

五项标准拆为功能验收与效果验收。功能通过须有真实组件测试和持久化恢复证据；效果通过须有基线、数据指纹、逐样本结果及环境信息。
保留全部失败和未评测项，不修改标签、删除困难样本或硬编码期望数值。原简历数值仅作为目标，不自动成为当前仓库的实测结论。
