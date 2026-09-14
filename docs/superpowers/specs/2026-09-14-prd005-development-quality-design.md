# PRD-005 阶段B：现有24题开发评测设计

日期：2026-09-14。类型：architectural（评测、运行时投影、模型上下文三处边界）。状态：设计草案待review；用户已选择现有24题开发评测，正式业务验收保持待定。本稿不更改默认hybrid、图策略、向量库或原PRD质量门槛。

来源：[本轮输入/关键词/Ragas基线](../../validation/prd005-quality-dev-20260914/BASELINE.md)、[一手调研](../../research/prd005-quality-gates-research.md)、[上一阶段真实结果](../../validation/prd005-real-app-20260914/REAL-RESULTS.md)。

## 1. 目标与当前事实

目标是让现有24题能够正确评价当前运行时投影的检索结果，并为正常Session的回答建立可复查的开发指标。交付能回答“哪道题在哪个环节失败”，不把开发题分数解释成业务准确率。

本轮基线：源码Python文件204、测试文件130；上一阶段773测试通过，本轮运行时代码变更0；新业务留出题0；离线关键词120观测、Ragas ID120观测、评测执行错误0、外部模型调用0。关键词P@5=.14、Recall@5=.55、MRR=.485、无答案FPR=.25、P95=8.92ms；这与历史企业语料FLASH.md不是同一数据集。

24题中20有答案/4无答案，金标数量16题1项、3题2项、1题3项。固定分母P@5宏上限=.25，原.80门槛在当前标签上不可达。旧评测projection为evaluation-projection-v1，运行时为utf8-slices-8192-v1；82/82 document_id不同，但完整来源身份82/82唯一对应。

非目标：补造标签凑足5项、测后更换分母、引入reranker或新图策略、把新造问题冒充业务留出、生产部署硬化、模型训练或微调。本阶段不将标签状态改为reviewed，不修改QUALITY_THRESHOLDS。

## 2. 候选方案

| 方案 | 范围 | 可测门槛 | 取舍 |
|---|---|---|---|
| A：来源映射＋当前运行时开发评测（推荐） | 把冻结标签映射到active投影，固定query评检索，正常Session评答案 | 82/82当前来源唯一映射；24题完整保留；所有结果可追溯 | 最能覆盖当前代码，需新增评测适配和上下文记录 |
| B：仅延用旧离线投影 | 继续evaluate_code_retrieval.py及独立答案脚本 | 24题×5轮完整；现有命令可复现 | 最少改动，但不能把结果写成当前切片/正常Session质量 |
| C：先重标注或修改质量门槛 | 完整人工审核、冻结新标签/协议后再评 | 至少20条独立审核题；标签状态有审核依据 | 适合正式验收，目前用户选择先做开发评测，暂不采用 |

推荐A；既有离线B作为对照保留。源码证据支持的当前事实与官方实现启示分别见调研，不因Agno/Codex有相应机制就要求照搬其运行时。

## 3. B1：冻结开发标签和运行时身份映射

新建评测适配模块（建议 `evaluation/runtime_corpus.py`），只读取冻结语料及明确active、ready的运行时投影，不修改Code Map、索引或Evidence。

输入：原EvaluationCorpus、明确允许的Scope集合、指定projection_revision、SQLite文档快照。输出RuntimeCorpusBinding：源manifest/query/label hash、目标projection hash、旧ID→新ID映射、映射状态、面向现有scorer的运行时EvaluationCorpus视图。

映射键必须包括 repository_id、snapshot_id、published_generation、commit_sha、node_id、chunk_id、path、source_hash。当前样本可按这一完整键82/82唯一映射；还需验证text bytes/source_hash一致与范围合法，记录目标byte_start/byte_end/parent_source_hash。不能只按path、symbol或hash复用不同仓库/版本的来源。

任意缺失或多对多映射都返回 `projection_label_unmapped` / `projection_label_ambiguous`，停止本批；不能直接给0分再解释为检索失败。大chunk拆成多个不同hash片段时，本阶段不自动把父标签扩散到所有片段，必须另行标注相关byte ranges；当前82文档没有这个分歧。

标注文件保持原样。派生binding单独写入评测目录，fingerprint包含原manifest hash、实际projection revision、排序后的文档与映射hash。旧Ragas samples_from_report只能消费与报告fingerprint一致的派生corpus，不能混入旧ID。

成功门槛：完整匹配82/82、24题解析24/24、原manifest/query/label hash变化0；失败指标缺失/歧义/跨scope匹配0；回归用既有源身份/切片/ID scorer测试与新增错误映射测试。尚无实现，当前审计只是设计依据。

## 4. B2：固定query的关键词/hybrid对照

使用相同冻结query文本、Scope、目标投影、top_k=5、candidate_limit=30。测keyword与默认hybrid两组，每组24题×5轮=120观测，共240；重复轮次用于稳定性与耗时，独立样本数仍24。

检索指标由固定query直接调用受Scope约束的检索服务获得，不让模型先改写query再把成绩记为原query固定检索。正常Session中模型选择的查询另记录在B3，不与固定query排名混为一组。

每条输出：question_id、round、mode、原始query hash、Scope/投影版本、前5有序document IDs、source_identity、通道贡献、P@5、标签Recall@5、MRR、命中数、返回数、重复数、错误/降级与耗时。报告宏平均及逐题最小值；无答案FPR单独分母4，不能把执行错误算作正确拒答。

继续使用标准P@5固定分母5；同时报告ceiling=.25，不新增冒名的归一化precision。原阈值.80/.60/.80仅展示满足情况，不因开发profile改写原quality_pass。

建议新DevelopmentRunReport单独提供 `pipeline_pass`：冻结输入一致、映射完整、所有240观测完成、无执行错误/跨域/重复/未解释降级、审计与产物齐全。字段名明确它是管线完整性，`business_quality_pass=false`、`label_status=proposed_for_user_review`、`held_out=false`固定披露。原evaluate()的四模式all()和硬编码blockers不是这次二模式开发profile的全局完成定义；保留原报告，用适配层展示选择的模式和已有证据范围，不能删除旧失败结论。

复用R1真实索引前必须核对两个Scope及全部point字段，不重新编码来掩盖丢失。不具备完整旧产物则明确失败或另行批准重建，不能悄悄建立新基线。旧keyword离线投影与新runtime投影不可作为“同配置”性能比较；本次建立目标profile的5轮基线，后续同profile中位数劣化≤5%、P95≤1.20倍才可声称性能回归满足。

## 5. B3：正常Session答案与实际披露上下文

每题使用独立Incident/Session及唯一评测participant_id，固定初始化状态，避免上一题答案或Memory候选进入下一题。记录24题各自的Session、模型工具调用、已执行查询、最后一次生成请求中的实际source_context、最终答案和错误。

现有R1 ModelObserver只保存披露ID，不足以支持新的答案评分；不得事后拿更多源码重新拼上下文去评分旧R1答案。需新增Case级请求观察器，在发送前保存预算裁剪后的实际上下文快照（包括路径、scope、范围和Evidence ID），同时保存digest。评估必须使用这份快照，不能拿数据库所有候选或完整文件替代。

建议 AnswerObservation 字段：question_id、session_id、生成配置hash、final_request_context_hash、disclosed_contexts、response、final_evidence_refs、retrieval_calls、status、usage和timing。私有目录保存必要内容，不保存header/凭证；公开报告仅引用文件/hash与数量。生成时不得看到gold答案或rubric。

复用Ragas0.4.3（不升级依赖）对真实回答评Faithfulness与Answer Relevancy，ID precision/recall继续独立列名。生成与评估上下文digest必须24/24一致，最终引用必须全部来自披露内容并可恢复。非检索工具额外提供事实时，明确记录来源；无法还原实际证据集的样本标 `context_capture_incomplete`，不评分。

有答案20题与无答案4题分组。无答案需要人工/规则辅助检查是否拒答、是否虚构路径/符号；候选非空不等于模型误答，候选为空也不等于模型正确拒答。Runtime final没有独立answer_status枚举，不仅凭关键词“证据不足”声称正确拒答；保留答案供review。

历史Faithfulness≥.90、Relevancy≥.80只能作为筛查线。judge同源偏差、未审核gold、20/4小样本必须披露；本阶段不升级为生产硬门槛。NaN/超时/无评分保留null及原因；显示有效均值、有效数量、总数量与覆盖率，不能丢掉失败样本。

## 6. B4：开发报告、错误语义和恢复

总报告按“冻结输入→固定检索→实际上下文→答案→裁判”分层，不把SQLite/Milvus可用等同于回答质量。输出逐题failure ledger，类别至少包含 `label_mapping_error`、`retrieval_error`、`context_capture_incomplete`、`generation_error`、`judge_error`、`unsupported_citation`，保留原始失败轮次和修复版本。

长批次每题完成即持久化，重启时只复用同时匹配query、模型/配置、上下文digest、标签版本的已完成项。生成答案与judge评分分别恢复；重评必须给出原因与新run版本，不挑选最好分数。每个Step追加memory-development-log。

成功指标：冻结24题完整保留、至少240条检索观测、24份生成上下文/结果对齐或显式失败项；失败指标越域/坏hash/未解释缺失0；回归773项及相关新增项全部满足。质量仍为诊断结果，不使用 `pipeline_pass` 替换quality_pass。

## 7. 预算与后续Case确认

本轮已执行的都是离线读取/关键词/Ragas ID，外部模型调用0。以下是待实施和待确认的真实批次，不在本轮运行：

- QD1固定检索：复用ready索引，120次hybrid query编码＋120次keyword查询；建议120秒业务预算/150秒父进程看门狗，启动服务不计业务性能。若计划请求数和实际延迟预检不支持预算，应在运行前调整并确认。
- QD2答案：24个独立Session分4批，每批6题；每批生成＋Ragas裁判建议600秒，理由是每题有多轮模型/工具调用和额外裁判/反向问题编码。不是把R1两个pilot成绩外推到24题。
- 每个Case最多2次技术重试；同输入连续失败2次先定位，不扩大题集。文档编码预期0（复用索引），发生重建必须单独审批Case；实际API请求/token全部记录，费用无账单写N/A。

设计确认后，先实现B1和相应离线测试，再准备QD1精确命令/冻结hash和预算供用户确认。B3/QD2在固定检索与数据契约review后进入，不同时扩大到生产部署或图优化。

## 8. 自审与待决策

已给出3个方案、4个阶段、数据身份/上下文契约、成功/失败/回归门槛；无未定义的生产接口变更。本次设计引入的是评测适配与观察产物，不改变运行时权限或存储权威。

用户已经选择现有24题开发诊断，因此不再次询问数据来源。待review的设计选择为推荐A：完整来源ID映射＋固定检索/正常Session两类评测；原门槛和标签状态保持，正式业务验收继续pending。
