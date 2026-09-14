# PRD005 下一阶段：代码 RAG 质量验收一手资料调研

调研日期：2026-09-14。范围：指标口径、标注冻结、真实 Session 与评估证据一致性。本文没有调用外部生成模型、运行 Ragas 评估或修改服务；仅读取公开官方资料。以下将一手事实与项目建议分开。

## 当前边界

任务给定现状：SQLite + Milvus 的 default hybrid 保持不变；图优化仍 pending；本地 773 项测试、真实 R1 两个 pilotSession 和 R2 崩溃恢复通过。这些事实由主任务提供，本调研未复跑，不能据此宣称业务质量达标。旧 24 题标签仍为 proposed；不能直接作为冻结金标或独立留出集。

## 实际读取的一手来源

固定 GitHub 提交来自调研时官方仓库 main 的 GitHub API 查询，引用使用完整 SHA，不使用会移动的 main 链接。

| 来源 | 固定链接 / 官方页 | 本次读取内容 |
|---|---|---|
| OpenAI Codex | [agents_md.rs @ 3abbf9f](https://github.com/openai/codex/blob/3abbf9fe2c6b6910e9de61f6a0c5bb468f74b5c8/codex-rs/core/src/agents_md.rs#L1-L115) | 项目指令发现、拼装、预算和错误处理源码 |
| Agno | [agent.py @ 44219f8](https://github.com/agno-agi/agno/blob/44219f8532e2fe6ce936850455f4b9a2567e4194/libs/agno/agno/agent/agent.py#L148-L219)；[Sessions 官方文档](https://docs.agno.com/sessions/overview) | history / knowledge 配置源码；持久化与模型上下文的区别 |
| Ragas | [Context Precision 官方文档](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/) | 排序 precision 与 ID precision 两种定义 |
| Ragas | [Context Recall @ 298b682](https://github.com/vibrantlabsai/ragas/blob/298b68274234c060deacab3cf5fb52aa3a20e885/docs/concepts/metrics/available_metrics/context_recall.md) | 声明级、文本级、ID 级 recall 定义 |
| Ragas | [Faithfulness @ 298b682](https://github.com/vibrantlabsai/ragas/blob/298b68274234c060deacab3cf5fb52aa3a20e885/docs/concepts/metrics/available_metrics/faithfulness.md) | 回答声明被实际上下文支持的比例 |
| Ragas | [Answer Relevancy @ 298b682](https://github.com/vibrantlabsai/ragas/blob/298b68274234c060deacab3cf5fb52aa3a20e885/docs/concepts/metrics/available_metrics/answer_relevance.md) | 反向问题生成与 embedding 余弦相似度 |

## 一手事实及可推导的限制

### Codex：上下文是有边界的拼装结果

上述 Codex 源码沿项目根到当前目录收集项目指令，使用 `project_doc_max_bytes` 剩余预算，并区分未找到文档与读取错误；不受信任项目的加载路径另有分支。因此“文件在仓库里”不等于“文件内容进入了当前模型请求”。这是上下文组装的实现事实，不是 Codex 提供了代码 RAG 质量阈值。来源：[Codex 源码](https://github.com/openai/codex/blob/3abbf9fe2c6b6910e9de61f6a0c5bb468f74b5c8/codex-rs/core/src/agents_md.rs#L1-L115)。

### Agno：Session 持久化与历史注入是不同开关

Agno 官方文档区分 `run_id`、`session_id`、`user_id`，并说明数据库保存记录，`add_history_to_context` 才把历史加入模型请求。源码该开关默认 False；`add_knowledge_to_context` 默认 False，`search_knowledge` 默认 True，但后者是具备 knowledge 时提供检索工具，并不保证实际调用。来源：[Sessions](https://docs.agno.com/sessions/overview)、[配置源码](https://github.com/agno-agi/agno/blob/44219f8532e2fe6ce936850455f4b9a2567e4194/libs/agno/agno/agent/agent.py#L148-L219)。

项目推论：数据库存在 Session、检索器返回文档、Agent 注册工具，这三个事实都不能单独证明回答使用了那些证据。

### Ragas：四个指标不能互相替代

| 指标 | 一手定义摘要 | 对代码验收的限制 |
|---|---|---|
| 排序 Context Precision | 对相关位置的 Precision@k 加权求和，再除以 top-K 中相关项数 | 排首的唯一相关项可以获得接近 1；这不是“5 个槽位中相关项占比” |
| ID Context Precision | 检索 ID 命中参考 ID 的数量 / 检索 ID 数量 | 分母跟返回数变化，不自动等于固定分母 P@5 |
| Context Recall | 参考答案声明被检索上下文支持的比例；ID 版以参考 ID 总数为分母 | 参考不完整时不能解释为全代码库穷尽召回 |
| Faithfulness | 回答声明被 retrieved context 支持的比例 | 不能证明答案覆盖需求，也不能用事后补充上下文追认生成时的依据 |
| Answer/Response Relevancy | 根据回答反推若干问题，计算其与原问题 embedding 的平均余弦相似度 | 不评事实准确性；需要生成与 embedding，不能用手工打分冒充该 Ragas 输出 |

前两行来源：[Context Precision](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/)；其余分别见上表固定版本 Recall、Faithfulness、Relevancy 源文件。Relevancy 文档还明确提示余弦相似度理论范围可低于 0，因此不能假设它天然严格落在 [0,1]。

这些资料没有给出适用于本项目的统一 `.8` 上线线；项目阈值必须独立校准。

## 项目建议：先冻结协议，再跑真实质量批次

以下均为项目设计建议，不是官方标准或已达成结果。

### 1. 明确定义严格分母与可达上限

- 固定版本、固定粒度的相关证据集合记为 Gq，去重后的前五个检索结果记为 Rq；`strict_P@5 = |Rq ∩ Gq| / 5`。少返结果的空槽不计命中，重复项不能扩大分子。分母不能改成 `min(5, |Gq|)` 或实际返回数再继续称 P@5。
- `gold_recall@5 = |Rq ∩ Gq| / |Gq|`，仅适用于非空且已审核的 Gq。它表示冻结金标的覆盖率；金标未穷尽时不声称全库 recall。答案的必要证据可以另建 must-have 子集，避免可替代证据全部被强制要求。
- 固定分母的单题理论上限为 `min(5, |Gq|) / 5`。若题目只有 1 个金标，满召回也只有 P@5=.2；24 题宏平均上限是逐题上限均值。主任务必须对真实标签算出上限后再决定旧 .8 是否可达，不能为了使阈值可达而补充虚假相关标签。
- 可保留严格 P@5 为诊断指标，将候选主门槛设为 gold_recall@5、must-have coverage、Hit@5 与回答正确性。若另报上限归一化分数，明确单独命名，保留原始值。没有任何文献支持盲目将所有指标一律设为 .8。
- 对无答案题，空 Gq 是有效类别，不计算普通 recall；单独考察正确拒答、无虚构路径/符号。对尚未标注的空集合，状态必须是 invalid/unlabeled，不能与无答案题混合。

可实现的首轮候选线：已冻结、有效、可回答题的宏平均 gold_recall@5 ≥ .8；回答关键需求覆盖 ≥ .8；逐项审核的代码事实不得有严重错误；无答案题不得虚构代码位置。这些是供校准的 proposed 值，需结合 dev 分布、题数与逐题失败复核决定；不得在看过留出结果后为求通过而改线。Faithfulness 和 Relevancy 暂以报告项建立人工一致性基线，未校准 judge 前不设生产硬线。

### 2. 金标冻结与留出隔离

每题至少冻结：question_id、意图/类别、repo commit、corpus manifest hash、evidence ID 粒度、相关 ID、must-have 事实与证据、允许替代证据、无答案标记、标注理由、审核者、审核时间、标签版本和状态。路径加符号名未必唯一；真实 ID 必须在冻结索引中可解析，并验证 hash/范围一致。proposed → reviewed → frozen 需要真实审核依据，不能只修改状态字段。

旧 24 题已经用于开发或人工挑选时，定位为 dev/calibration；同一道题改写几个词不构成独立 holdout。按功能/符号族/调用链拆分并检查近重复。留出题可检索其所问代码，但题目、答案、rubric 和评估报告不得进入生成检索库、Session 历史、提示或调参输入。冻结后发现标签错误，登记原因、生成新版本并重算整批；不得悄悄删掉失败题。

### 3. 真实 Session 的生成证据必须与评估披露一致

真实应用入口生成回答后，记录 `session_id/run_id/turn_id` 与 question_id 的一一映射、运行配置、模型标识、代码/索引快照、实际有序检索结果、预算截断后的注入片段、历史/摘要、工具输入输出、最终回答与错误。保存足够的内容快照与 hash；仅保存数据库引用不足以防止后续内容漂移。

把 retrieved 候选集与最终注入集分开记录。检索指标评候选排名；回答 Faithfulness 评生成时实际可见证据。若回答还看了历史、代码文件工具结果或摘要，报告须披露这些来源并保存审计链；不能给 judge 更多未见证据后标成原始 Ragas retrieved-context 口径。可同时报告“仅检索片段支持率”和“完整实际生成证据支持率”，清晰命名。

独立题使用新 Session 或已冻结的初始状态；多轮题则冻结前序轮次并作为独立类别。不得把上一题答案带入下一题提升得分。人工参考答案只进入评估，不进入生成。没有真实回答、注入快照或可追溯 run 的离线合成 fixture 只证明评估管线可运行。

### 4. 失败与缺失不可在聚合时消失

冻结批次 N 题后，逐题报告 complete / execution_failed / evaluation_failed / invalid_label。执行成功率分母为 N；质量指标报告有效评分数、N、缺失原因和 coverage。保留有效题条件均值，但另报全批次通过数/N，任何未评分题不能算已通过。NaN、judge 超时、空回答、解析错误必须保留状态；不得默认忽略后声称整批质量合格。Faithfulness 的零声明答案不能靠空分母获得成功；应按拒答/任务完成规则判定。

### 5. 三种状态独立记录

| 层次 | 能证明什么 | 当前可写状态 |
|---|---|---|
| 执行验收 | 测试、真实 Session、持久化、恢复链路完成 | 主任务给定本地与 R1/R2 已通过，注明命令/证据来源 |
| 业务质量 | 冻结真实题、可追溯回答、严格分母、人工/指标复核达标 | pending；本次无业务质量结论 |
| 生产就绪 | 质量加上线所需运行证据与部署决定 | pending，不从前两项自动推导 |

下一交付应是可审查的标签/ID 审计、冻结协议和真实批次报告结构；default hybrid 维持基线，图优化维持 pending。待基线质量证据完整后再决定哪种检索优化值得实验，避免用更换架构代替验收。
