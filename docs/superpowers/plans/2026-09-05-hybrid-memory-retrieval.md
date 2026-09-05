# 混合记忆检索实施计划

> **供执行型 Agent 使用：** 使用 `superpowers:executing-plans` 逐任务执行。每个任务必须先写失败测试，再写最小实现；任务完成后暂停等待用户 review。

**目标：** 将 FTS5、精确标识符和 Qwen Flash 向量候选通过 RRF 融合后接入 Trusted Recall，并在固定数据集上量化验证。

**架构：** 三个候选通道只返回 memory ID、通道、排名和原始分数；`HybridMemoryRetriever` 用固定 `rrf_k=60` 去重排序；随后回源 canonical `MemoryRecord` 并交给 `TrustedMemoryRecall`。索引失败只产生 `bypass`，不改变事实记录。

**技术栈：** Python、SQLite FTS5、SQLite memory_vectors、Qwen `qwen3.7-text-embedding-flash`、pytest、LongMemEval-S。  
**设计：** `docs/superpowers/specs/2026-09-05-hybrid-memory-retrieval-design.md`

## 全局约束

- 所有候选先下推 operator、incident、status、模型、维度、content_version 过滤；最终仍必须经过 Trusted Recall。
- 默认候选仅来自 active LLM projection；原始 Session/Turn/Evidence 只用于 provenance 回读，不作为线上候选 fallback。failed/pending 投影必须可统计、可重试，不能退回原始全文。
- RRF 固定 `k=60`，不在评测中按查询调权。
- `vector_bypass`、`lexical_bypass` 与 `identifier_bypass` 不是 hit；每条查询必须记录通道状态。
- 不重建正式索引；真实 Qwen 调用只在用户确认的隔离 Case 中发生，凭据只从环境读取。
- 固定数据 SHA、query 集、top-k=5、时钟、模型和索引版本；少于 10 条查询不能得出检索优化结论。
- 通过条件：P@5>=0.80、R@5>=0.60、MRR@5>=0.80、P95<=词法基线1.20x、EvidenceRef完整率100%、泄漏/悬挂引用/后台异常为0。
- 每完成任一任务，必须运行下方“累计测试集”，并立刻把命令、总数、通过数、失败数、耗时、修改文件数、当前参数、剩余风险写入 `docs/memory-development-log.md`；没有这份记录不得进入下一任务。
- 参数调优只能在任务 1–4 的接口与行为冻结、累计测试集持续通过后开始。调优时一次只改变一个参数，固定数据 SHA、查询集、相关性标注、top-k、时钟、模型、索引版本，并记录前后全部指标。

## 每任务累计测试集

```sh
python3 -m pytest -q tests/test_qwen_embedding.py tests/test_memory_models.py tests/test_memory*.py tests/test_local_memory.py tests/test_local_vector_memory.py tests/test_sqlite_memory_search.py tests/test_operator_graph.py tests/test_worker_redis.py tests/test_redis_bootstrap.py tests/test_persistence_cutover.py tests/test_runtime_context.py tests/test_trusted_memory_case.py
```

任务新增测试必须附加到该命令。若累计测试失败，停止调参和后续任务，先修复同一失败输入；同一输入连续失败两次时记录根因后暂停扩展。

## 每任务检索评测集

每个影响候选、融合、过滤、排序、预算或线上 Recall 的任务完成后，除累计测试集外，还必须运行固定 LongMemEval-S 评测：500 Case 输入、470 non-abstention Case 计分、30 abstention Case 单列。固定 SHA256 为 `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442`，top-k=5，使用同一投影规则、时钟、相关性标注、模型与索引版本。

每次记录以下实际值：

| 指标 | 公式 | 硬门槛 |
|---|---|---:|
| Recall@5 | 前5条命中的相关记忆数 / 该查询全部相关记忆数 | >=0.60 |
| Precision@5 | 前5条相关记忆数 / 5 | >=0.80 |
| MRR@5 | 首条相关记忆排名的倒数；无命中为0 | >=0.80 |
| P95 | 470 条查询延迟的第95百分位 | <= 词法基线1.20x |

同时报告宏平均、每条查询最小值、评测错误数、bypass 数、EvidenceRef 完整率、跨 scope/悬挂引用数。评测 runner 在任务 1 中先实现；任务 2、3、4 完成后都用同一命令重跑。真实 Qwen 尚未获 Case 确认时，向量通道明确记录 `vector_bypass`，仍输出 FTS5/identifier/RRF 指标，不能把该结果称为完整混合检索成绩。

## 任务 1：固定评测基线、生产候选契约与 RRF

**文件：** 新增 `memory/retrieval.py`、`memory/hybrid_ranker.py`、`scripts/run_hybrid_memory_evaluation.py`、`tests/test_hybrid_ranker.py`、`tests/test_hybrid_memory_evaluation.py`。  
**接口：** `RetrievalCandidate(memory_id, channel, rank, raw_score)`；`HybridMemoryRetriever.retrieve(query, scope, limit)`；`ReciprocalRankFusion.combine(channels, limit)`。

- [x] 写失败测试：三个通道同一 memory ID 只保留一次；rank 1 的两通道候选高于单通道 rank 1；空通道不影响其他通道。
- [x] 运行 `python3 -m pytest -q tests/test_hybrid_ranker.py`，预期模块不存在而失败。
- [x] 最小实现：对每个 channel 的 rank 从 1 开始累加 `1/(60+rank)`，按 `(-rrf_score, memory_id)` 排序；输出 channels 和每个通道排名。
- [x] 实现评测 runner：固定数据 SHA、470 条可计分查询、top-k=5；输出 `recall_at_5`、`precision_at_5`、`mrr_at_5`、P50/P95、逐查询结果、错误数、bypass、EvidenceRef 完整率和 scope/悬挂引用数。
- [x] 重跑单测与固定470条评测，记录 RRF 去重数、候选解释字段、Recall@5、Precision@5、MRR@5、P95；这是后续所有任务的 baseline。
- [x] 运行“每任务累计测试集”并记录实际测试总数、通过数、失败数、耗时、RRF k=60、四项检索指标、修改文件数和风险；通过后才进入任务2。

## 任务 2：SQLite FTS5 与精确标识符通道

**文件：** 修改 `persistence/sqlite_stores.py`；新增 `tests/test_memory_candidate_retrieval.py`。

- [ ] 写失败测试：错误码 `ERR_TIMEOUT_504`、Redis key `cache:tenant:1` 可精确候选；错误 operator/incident、inactive record 不返回；FTS5 失败返回 lexical bypass 而非异常。
- [ ] 运行 `python3 -m pytest -q tests/test_memory_candidate_retrieval.py`，预期候选 API 缺失而失败。
- [ ] 实现 `SQLiteMemoryCandidateStore.lexical_candidates()` 和 `identifier_candidates()`；每项返回 `RetrievalCandidate`，identifier 使用受控正则提取错误码、key、URL、IP、trace ID，不扫描 Evidence 原文。
- [ ] 运行 `python3 -m pytest -q tests/test_memory_candidate_retrieval.py tests/test_sqlite_memory_search.py`；要求 scope泄漏=0、候选通道字段完整。
- [ ] 重跑固定470条评测；记录 FTS5/identifier 候选数、Recall@5、Precision@5、MRR@5、P95、bypass、scope泄漏数、失败数、耗时和风险；通过后才进入任务3。
- [ ] 运行“每任务累计测试集”并记录同一组数字。

## 任务 3：Qwen 向量候选适配器与 bypass

**文件：** 修改 `persistence/local_vector_memory.py`；新增 `tests/test_vector_candidates.py`。

- [ ] 写失败测试：向量候选继承 model/dimension/content_version/scope 过滤；Embedder 异常返回 `vector_bypass`；不允许返回半批结果。
- [ ] 运行 `python3 -m pytest -q tests/test_vector_candidates.py tests/test_qwen_embedding.py`，预期候选 API 缺失而失败。
- [ ] 实现 `LocalVectorMemory.vector_candidates(query, scope, limit)`，成功返回 vector 通道候选；仅捕获可恢复 embedding 异常并返回 bypass 状态，其他数据契约错误继续抛出。
- [ ] 重跑命令，要求模型混用、维度混用、旧 content_version 命中均为0。
- [ ] 重跑固定470条评测；记录 vector hit/miss/bypass、模型/维度/version、Recall@5、Precision@5、MRR@5、P95、失败数、耗时和风险；通过后才进入任务4。
- [ ] 运行“每任务累计测试集”并记录同一组数字。

### 任务 3A：增量记忆投影与行业基准对齐

- [ ] 新增 `memory/projection.py` 与测试；从 Session/Turn 生成 `session_digest`、`keyphrase`、`user_fact`、`diagnosis_fact`、`decision`、`failure_barrier`，每条均带来源、scope、时间、版本和抽取状态。
- [ ] 写失败测试：投影不复制原始 Evidence；每个投影至少有一个 Session/Turn/Evidence 来源；冲突/抽取失败保留状态而不产生 active fact；同一输入幂等。
- [ ] LongMemEval adapter 增加官方可比较 corpus：turn/session raw、session-summ、session-keyphrase/userfact、raw+projection；输出 Session/Turn Recall 与最终 QA 输入集。
- [ ] 默认线上 corpus 固定为 LLM projection-only；raw 与 raw+projection 仅保留为 benchmark 对照。增加 failed/pending projection 计数、retry 状态和缺失投影 telemetry。
- [ ] 真实 Qwen Case 改为对投影记录批量向量化，不对完整 Session 原文批量向量化；记录投影数、提取失败数、文档/查询批次、来源完整率、P95、恢复结果和费用/请求量。
- [ ] 首个超长Case与全量470评测只有在投影版本固定、单Case预算满足后运行；原“每Session最多8个字符块”实验保留为失败诊断，不作为默认索引策略。

## 任务 4：接入线上 MemoryRecall 与可信过滤

**文件：** 修改 `memory/recall.py`、`memory/trusted_recall.py`、`memory/recorder.py`；新增 `tests/test_hybrid_memory_recall.py`。

- [ ] 写失败测试：RRF 候选顺序传入 Trusted Recall；无可信候选仅返回 digest；向量 bypass 仍允许词法候选；最终 EvidenceRef 不悬挂。
- [ ] 运行 `python3 -m pytest -q tests/test_hybrid_memory_recall.py tests/test_trusted_memory_recall.py`，预期线上 Recall 没有 retriever 参数而失败。
- [ ] 实现注入式 retriever；先候选、再按 memory ID 回源、最后 Trusted Recall。Telemetry 记录每通道候选数、bypass、RRF 去重、最终注入、拒绝原因和延迟。
- [ ] 运行 `python3 -m pytest -q tests/test_hybrid_memory_recall.py tests/test_memory_recall.py tests/test_runtime_context.py`；要求跨 scope/无效来源注入均为0。
- [ ] 重跑固定470条评测；记录每通道最终注入数、拒绝原因、Recall@5、Precision@5、MRR@5、P95、EvidenceRef完整率、失败数、耗时和风险；通过后才进入任务5。
- [ ] 运行“每任务累计测试集”并记录同一组数字。

## 任务 5：固定评测与隔离真实 Case

**文件：** 新增 `scripts/run_hybrid_memory_evaluation.py`、`scripts/run_hybrid_memory_case.py`、`docs/validation/hybrid-memory-case.md`；修改 `evaluation/` 与相关测试。

- [ ] 写失败测试：评测输出每 query 的通道候选、RRF 分数、最终结果、P50/P95、P@5/R@5/MRR 和理论 Precision@5 上限；Case 报告包含索引版本、scope、来源、恢复和 bypass。
- [ ] 先运行 10 条固定离线查询并保存 baseline；少于 10 条时报告 `INSUFFICIENT_DATA`。
- [ ] 实现 470 non-abstention LongMemEval-S 评测，记录 SHA256、模型 `qwen3.7-text-embedding-flash`、1024维、RRF k=60、时钟、top-k=5。
- [ ] 向用户提交真实 Case：至少12条隔离 typed records、10条固定查询、临时 SQLite/向量表、最大120秒、最多2次重试。用户确认后才调用真实 Qwen。
- [ ] Case 后写七列表格与至少10个数字字段到开发日志；只有全部硬门槛满足才报告 `真实运行通过`，随后等待用户 review。

## 参数微调阶段（功能冻结后）

- [ ] 先保存 `baseline.json`：数据 SHA、470 queries、top-k=5、Qwen 模型/1024维、RRF k=60、每通道 limit、P@5、R@5、MRR@5、P50/P95、EvidenceRef完整率、bypass 数。
- [ ] 一次只调整一个参数，顺序固定为：每通道 candidate limit → RRF k → identifier 通道是否保留低分候选。每次运行同一 470 queries，保存 `trial-<name>.json`。
- [ ] 仅在 P@5、R@5、MRR 三项均不下降，且 P95 不超过基线 1.20 倍、EvidenceRef完整率100%、泄漏/悬挂引用/后台异常均为0 时接受新参数；否则恢复上一组参数。
- [ ] 将最终参数、基线、每次试验、接受或拒绝原因和全部数值写入开发日志；不得因单项提升宣称整体优化成功。

## 交叉评测阶段（参数冻结后）

- [ ] 从 LongMemEval-S 主评测数据按任务类型生成固定留出集；保存 split SHA，留出集不参与参数选择。
- [ ] 接入 LoCoMo adapter，运行其 QA 与事件摘要原生指标，同时记录统一安全/性能指标。
- [ ] 接入 MemoryAgentBench adapter，记录任务成功率、更新/冲突/遗忘正确率和统一安全指标。
- [ ] 接入 LongMemEval-V2 adapter，记录准确率-延迟前沿、工作流/状态/环境陷阱能力和统一安全指标。
- [ ] 任一外部基准显著退化时，保留主评测参数不变，记录退化任务类别并进入设计 review；不得在外部基准上反向调参后再宣称独立验证。

## 自审

任务1覆盖融合稳定性；任务2覆盖可解释词法/标识符；任务3覆盖 Qwen 版本与降级；任务4覆盖 Runtime；任务5覆盖固定评测和真实 Case。ANN、正式索引重建、跨 scope 共享、consolidation 不在本计划。
