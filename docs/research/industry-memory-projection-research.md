# 超长 Agent/Chat 历史的 Memory Projection：官方实现与基准调研

调研日期：2026-09-05。范围仅限项目维护方发布的源码、README 与官方文档；不把博客转述、第三方复现或社区 issue 的结论当作事实依据。

## 结论先行

这些一手实现和基准共同表明，**“把无限长完整会话直接嵌入为一个长期 Memory”不是主流的默认路径**。常见的分工是：保留可追溯的原始 Session/Turn（或 episode）作为证据层；在写入时按 Session、Turn、rollout 或 episode 做有界处理；再抽取可检索的事实、摘要、实体或关系。完整历史直送模型是 LongMemEval 的可比较 baseline，而不是其推荐的可扩展索引方式。

| 对象 | 默认长期记忆单元 | 是否直接嵌入完整会话 | 有界/增量机制 | 已公开评测方式 |
|---|---|---|---|---|
| OpenAI Codex | 每个 rollout 的 `raw_memory` 与 `rollout_summary`，再全局 consolidation | 未见公开主链路使用向量 embedding；Phase 1 输入是过滤后的 rollout，输出是提取物/摘要 | startup claim 上限、并发上限、保留窗口、top-N consolidation、watermark | 公开实现说明 usage telemetry/citation；本次查到的核心 README 未提供端到端 QA benchmark runner |
| LongMemEval | 原始 Turn 或 Session 索引；可添加 summary/keyphrase/user-fact 作为 index key | 支持把全历史直接送 reader 作为 baseline；索引 baseline 是 turn/session 粒度 | S 约 115k token/~40 Session，M 约 500 Session；检索 `top-k` | 500 QA；Session/Turn recall 与 LLM-judge QA；30 个 abstention 不进入 retrieval recall |
| LoCoMo | 原始 dialog、自动 observation、Session summary 三种可选 RAG database | 官方代码比较三种 database，并未规定“整段会话一个向量” | 10 条多 Session 对话；Session 内含 dialog/turn；提供 Session summary/observation 生成 | QA 的答案与 evidence dialog IDs；QA 脚本保存逐题 F1；另有 event summarization 标注 |
| Mem0 | 默认由消息抽取的可持久事实 | 默认不存逐字 transcript；`infer=False` 才按原样存入 | 写入前查同 scope 旧记忆，逐事实 ADD/UPDATE/DELETE；`add` 接收消息列表，未在所查官方文档中声明统一 token/batch 上限 | 官方 benchmark：Ingest → Search → Answer → LLM judge；可测 LoCoMo、LongMemEval、BEAM 与多组 top-k |
| Zep/Graphiti | episode（原始输入及 provenance）+ entity/fact 图边、有效时间 | 原始 episode 可保存；检索核心同时有全文、向量和图搜索，facts 由抽取而来 | 新 episode 立即增量入图、保留旧事实的时间历史；`add_episode_bulk` 与 `max_coroutines` | 所查核心 README/代码描述能力与 tracing，未给出可直接运行的统一 QA benchmark protocol |

## 一手事实

### 1. OpenAI Codex：先按 rollout 提取，再做受限全局合并

来源：[memory pipeline README](https://github.com/openai/codex/blob/main/codex-rs/memories/README.md)、[Phase 1 prompt](https://github.com/openai/codex/blob/main/codex-rs/memories/write/templates/memories/stage_one_system.md)、[Phase 2 prompt](https://github.com/openai/codex/blob/main/codex-rs/memories/write/templates/memories/consolidation.md)、[配置类型](https://github.com/openai/codex/blob/main/codex-rs/config/src/types.rs)。

- Phase 1 领取一组**有界** rollout job，先过滤为 memory-relevant response items，再并发调用模型；结构化产物为详细 `raw_memory`、紧凑 `rollout_summary` 和可选 `rollout_slug`，写回 state DB。该步骤不是把整条 rollout 当作单个向量直接建索引。
- Phase 2 串行领取一个全局 job，从 Stage-1 产物中按 `usage_count`、`last_usage`/`generated_at` 和 `max_unused_days` 选择有界输入；把它们同步为 `raw_memories.md` 与单 rollout summary 文件后，再由 consolidation agent 更新 `MEMORY.md`、`memory_summary.md` 等文件化 artifact。
- Phase 2 prompt 明确把 `memory_summary.md` 设为始终加载的稠密导航层，将 `MEMORY.md` 用于关键词检索，并要求“避免复制大型 tool output”，优先紧凑摘要、错误片段和指针。它同时以 workspace diff 支持 INIT 与 incremental update，并在删除输入时清理不再有证据支持的记忆。
- 公开配置暴露 `max_raw_memories_for_consolidation`、`max_unused_days`、`max_rollout_age_days`、`max_rollouts_per_startup`、`min_rollout_idle_hours`、`extract_model` 与 `consolidation_model`；README 还明确 Phase 1 有 concurrency cap、DB lease/retry backoff。这是批次受限、可重试和分阶段处理，而非每次全量重算。
- 当前公开核心 memory 路径的明示存储为 SQLite stage output 加文件 artifact；本次核对的源码/README 没有证明该主链路用 embedding KNN 作长期记忆检索。因此不应把 Codex 当作“整会话 embedding”或“官方已采用向量库”的证据。
- 读路径的 README 说明会注入 memory usage instructions、解析 memory citation 并记录 usage telemetry。就本次查到的核心目录而言，未发现与 LongMemEval 同类的公开端到端记忆正确率脚本；不能据此声称 Codex 的 memory 准确率。

### 2. LongMemEval：把“整史直送”与“按 Session/Turn 索引”分开评测

来源：[官方 README](https://github.com/xiaowu0162/LongMemEval/blob/main/README.md)、[官方检索实现目录](https://github.com/xiaowu0162/LongMemEval/tree/main/src/retrieval)、[官方 QA evaluator](https://github.com/xiaowu0162/LongMemEval/blob/main/src/evaluation/evaluate_qa.py)。

- 数据包含 500 个实例。`longmemeval_s` 的全历史约 115k token、约 40 个历史 Session；`longmemeval_m` 每例约 500 Session；`oracle` 仅保留证据 Session。每个 Session 是 Turn 列表，Turn 有 user/assistant role 与 content。
- gold 标注同时给出 `answer_session_ids`（Session-level recall）和 evidence Turn 的 `has_answer`（Turn-level recall），因此系统能区分“答案碰巧正确”与“是否召回实际证据”。
- 官方提供 full-history-session 长上下文 baseline：将全部历史给 reader，并建议足够大的 `TOPK`（例如 1000）确保包含全史。S/oracle 设计为可装入 128k context，M 明确过长而不适于该测试。这是**直接读完整历史的对照组**。
- 官方 retrieval baseline 则把原始内容按 `turn` 或 `session` 建 flat BM25/Contriever/Stella/GTE 索引；这不是把多 Session 整体压成一个 embedding。索引扩展可另行生成 `session-summ`、`session-keyphrase`、`session-userfact`、`turn-keyphrase`、`turn-userfact`，可作为独立 key、和原 key 合并，或替换原 key。
- 时间感知扩展会从 Session 抽取带时间戳事件，从 query 推断时间范围，再缩小搜索空间；论文实验使用 Session granularity。
- QA 评测要求输出 `question_id` 与 `hypothesis` JSONL，由官方 evaluator 写 `autoeval_label` 并汇总。检索评测跳过 30 个 abstention，因为它们没有答案位置；这避免把“本应无证据”的题强行计入证据召回。

### 3. LoCoMo：原始 dialog、Session observation 与 Session summary 是可比较的三种 RAG 库

来源：[官方 README](https://github.com/snap-research/locomo/blob/main/README.MD)、[官方数据](https://github.com/snap-research/locomo/blob/main/data/locomo10.json)、[官方 QA evaluator](https://github.com/snap-research/locomo/blob/main/task_eval/evaluate_qa.py)。

- 官方发布 10 条超长会话；每例含按时间排序的多个 Session、两位 speaker、Session 内 turn 的 `dia_id` 与 `text`。QA 标注提供 question、answer、category 和包含答案的 `evidence` dialog IDs。
- 数据同时提供按 Session 生成的 `observation` 与 `session_summary`；而 `event_summary` 是按 speaker、跨 Session 因果/时间关系的人工标注，README 明确提示它不是 Session summary。
- 官方 RAG 脚本可分别把 dialogs、observations、session summaries 当作 database 来评测 `gpt-3.5-turbo`。所以 LoCoMo 官方材料支持“保留原始颗粒度并比较摘要投影”的实验设计，但没有把全会话单向量规定为标准做法。
- 原始对话直送模型的官方脚本使用 **truncated conversation as context**；这也是对照方式而不是对无限历史的长期存储策略。
- QA evaluator 为每题写入模型答案的 F1 字段；数据的 dialog-level evidence 还能独立用于检查检索是否命中原始证据。官方 README 列出 QA、event summarization 和多模态 dialog generation 三类任务。

### 4. Mem0：默认抽取、去重、嵌入“记忆事实”；原文存储是显式开关

来源：[官方 how-it-works 文档](https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/how-it-works.mdx)、[官方 memory-types 文档](https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/memory-types.mdx)、[官方 API reference](https://github.com/mem0ai/mem0/blob/main/LLM.md)、[官方 benchmark repository](https://github.com/mem0ai/memory-benchmarks)。

- Mem0 文档明确：调用方传入 messages，但默认保存的是 extracted memories，**不是逐字 transcript**；只有 `infer=False` 才原样存储内容。默认抽取目标包括 preference、decision、plan 等持久事实。
- `infer=True`（默认）写入路径包含同一 `user_id`/`agent_id`/`run_id` scope 的 context gathering、对旧记忆的向量检索、用一次 LLM 针对每个候选事实决定 ADD/UPDATE/DELETE/no-op，并抽取实体。普通读路径按 semantic vector、keyword、entity 和 temporal 信号排序（具体可用信号随 OSS/Platform 配置不同）。
- `run_id` 用于把记忆绑定到同一次 session/task；`user_id` 可跨 session。官方资料把 `add(messages, …)` 作为入口，故调用方可以提交一段完整历史；但官方默认语义仍是从它抽取/去重事实，而非将整段历史做一个 embedding。
- 本次查到的官方 API/文档没有给出通用的单条消息 token 上限或固定 ingestion batch-size；实现选型应实际测量自己的 provider 与 extractor，不能把未声明的数字当作 Mem0 约束。
- Mem0 官方 benchmark 的流水线是 Ingest → Search → Evaluate：conversation 分块并 `add`，抽取事实并建实体链接；查询用 semantic similarity + BM25 + entity boost；answerer 基于 retrieved memories 生成，再由 judge LLM 对照 gold 评分。README 支持 LoCoMo、LongMemEval 和 BEAM，接受多个 retrieval top-k cutoff，且有 `predict-only`、`evaluate-only`、`resume`。

### 5. Zep/Graphiti：episode 是原始可追溯层，事实图是增量投影层

来源：[Graphiti 官方 README](https://github.com/getzep/graphiti/blob/main/README.md)、[核心 `Graphiti` 实现](https://github.com/getzep/graphiti/blob/main/graphiti_core/graphiti.py)、[节点/边抽取 prompt](https://github.com/getzep/graphiti/blob/main/graphiti_core/prompts/extract_nodes_and_edges.py)、[检索实现](https://github.com/getzep/graphiti/blob/main/graphiti_core/search/search_utils.py)、[官方 adding-episodes 文档](https://help.getzep.com/graphiti/core-concepts/adding-episodes)。

- Graphiti 把 episode 定义为产生派生知识的原始数据；README 将它列为 provenance/ground-truth stream，并说明 entity/relationship 均能追溯到 episode。默认构造参数 `store_raw_episode_content=True`，所以它不是只保留压缩事实、完全丢弃原文的设计。
- 每个 `add_episode` 接收一个 `episode_body`、reference time、source 与 group。抽取 prompt 要求在一次处理里提取 entity nodes 与 relationship facts，并要求每个 fact 标注来自哪个 episode；这给事实投影保留了来源。
- README 将新数据的图构建描述为即时 incremental update，而不是静态文档的离线全量重摘要；当关系变化时，事实有 validity window，旧事实会被 invalidated 但保留时间历史。核心 API 同时提供 `add_episode_bulk`，并以 `max_coroutines`/`SEMAPHORE_LIMIT` 控制并行 ingestion。
- 检索代码提供 edge/node 的 full-text、similarity 与 BFS graph search；README 把读法概括为 hybrid semantic、keyword、graph-based search，并带 reranking。这意味着被嵌入/索引的是实体、事实与 episode 等多类节点，不是把整段多 Session 聊天压成单一向量。
- 官方 repo 的这些入口着重 ingestion、检索、provenance 与时间更新；本次核对到的材料没有给出如 LongMemEval 官方 runner 一样的统一端到端 QA 指标协议。因此如果采用 Graphiti，需要自行以 LongMemEval/LoCoMo 对其事实更新、时态过滤和 retrieval 进行外部验收。

## 对 AntiSentinel 的建议（本节是设计判断，不是上述项目的官方主张）

1. 保留 `Session/Turn/Evidence` 作为不可覆盖的原始证据层；只对可追溯的投影建立 embedding。投影记录至少应保存 `source_session_id`、`source_turn_ids`/EvidenceRef、提取版本、时间区间、scope/owner 与状态。
2. 写入采用两层：每个完成或稳定的 Session 先抽取少量原子事实、决定/偏好/失败屏障和 Session digest；后台再对**有界**的近期/高使用投影做 consolidation。将“原文入库”和“事实可检索”分为两条数据路径，避免摘要错误成为唯一证据。
3. 索引粒度同时保留原始 Turn/Session chunk 与事实投影。优先对事实、摘要和有大小上限的原始 chunk 做向量/词法 hybrid retrieval；不要给完整多 Session 历史只生成一个向量。对 error code、路径、ID 等精确对象保留词法通道。
4. 增量入库要有 lease、幂等键、失败重试、吞吐/并发上限与版本 watermark；事实冲突用新旧并存的 validity/state 表达，并让回答阶段根据 question time 与有效状态选择，不能静默覆盖证据。
5. 评测分三层并固定版本：
   - LongMemEval：Session Recall@K、Turn Recall@K、MRR、abstention 的 false-positive retrieval；
   - LoCoMo：dialog evidence hit、每类 QA F1，以及 raw-dialog/observation/session-summary 三个 corpus 的成对比较；
   - 端到端：同一 reader/judge、同一 top-k、同一 token budget 下，记录答案正确性、实际输入 token、P95 检索与写入延迟、投影到 EvidenceRef 的完整率。
6. 以“全文直送”作为容量与准确率的基线，只在能够完整装入 context 的样本上运行；超限时应显式记为 overflow，不能静默截断后与 memory 方案作不公平比较。

## 证据边界

- “未见/未提供”只指本报告列出的、调研日可访问的官方入口；它不是对任何项目私有实现或未来版本的否定。
- LongMemEval 与 LoCoMo 是 benchmark，不是要求产品照抄其 baseline；其价值在于提供了原始 Session/Turn/Dialog 的 gold evidence，可把投影检索和最终 QA 分开验收。
