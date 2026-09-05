# AntiSentinel 开源 Memory 测试集调研

## 1. 调研目标

为 PRD-003 的三个目标寻找可重复、可审计的开源评测数据：

- Memory 事实准确率 >= 90%；
- 上下文 Token 降幅 >= 30%；
- Redis Memory Cache 命中率 >= 85%。

调研只使用数据集官方仓库、官方数据卡、论文和官方评测实现。时间：2026-09-04。

## 2. 结论

推荐组合：

```text
主 Gate：LongMemEval-S cleaned（MIT）
偏好专项：PrefEval classification（CC BY-NC 4.0，仅内部研究）
长会话补充：LoCoMo10（CC BY-NC 4.0，仅内部研究）
框架方法参考：supermemory MemoryBench（MIT，不引入其运行时）
超长压力测试：InfiniteBench 的 longdialogue/kv/passkey 子集（非 Memory 主 Gate）
```

当前先接 LongMemEval-S。它与 AntiSentinel 的 Session、Memory Recall、知识更新和 EvidenceRef 最匹配，许可证也允许在项目中构建 adapter。

## 3. 候选测试集

### 3.1 LongMemEval-S cleaned：主 Gate

一手来源：

- [官方仓库](https://github.com/xiaowu0162/LongMemEval)
- [官方 Hugging Face 数据](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned)
- [论文](https://arxiv.org/abs/2410.10813)

官方事实：

- ICLR 2025 benchmark，500 个问题。
- 测试 information extraction、multi-session reasoning、knowledge updates、temporal reasoning 和 abstention。
- `longmemeval_s_cleaned.json` 约 277 MB；每个问题的完整历史约 115k tokens、约 40 个 Session。
- 提供 `answer_session_ids`，可评估 Session 级召回。
- Evidence turn 标有 `has_answer: true`，可评估 Turn 级召回。
- 提供 `question_type`、标准答案、问题时间、Session 时间戳和排序后的历史。
- cleaned 数据卡标注 MIT License。
- 官方评测支持把系统回答写成 `question_id + hypothesis` JSONL，再用 evaluator 评分。
- 官方 retrieval evaluation 对 30 个 abstention Case 不计算 Recall，因为它们没有 Evidence 位置。

对 AntiSentinel 的映射：

| LongMemEval | AntiSentinel |
|---|---|
| history sessions | Incident 下的多个 Session |
| session timestamp | Session/Turn 时间 |
| answer_session_ids | 应召回的 Session Memory/Evidence |
| has_answer turn | 应命中的 Turn/事实 |
| knowledge-update | Preference/semantic Memory 覆盖与冲突合并 |
| temporal reasoning | validity interval 与时效排序 |
| abstention | 无证据时不注入错误 Memory |

适用指标：

- Session Recall@K、Turn Recall@K；
- Evidence precision/recall；
- 端到端 QA accuracy；
- baseline full-history 与 optimized scoped-context 的 Token 差；
- warm replay 的 cache hit rate。

限制：

- 全量 full-history baseline 约为数千万输入 tokens，直接全部调用商业模型成本很高。
- 官方 QA evaluator 使用 LLM Judge；其结果不能取代人工抽检。
- Cache 命中率不是原生指标，必须由 AntiSentinel 增加固定 warm replay 协议。

### 3.2 PrefEval：Preference Graph 专项

一手来源：

- [官方仓库](https://github.com/amazon-science/PrefEval)
- [官方数据集](https://huggingface.co/datasets/siyanzhao/prefeval_explicit)
- [项目页](https://prefeval.github.io/)
- [论文](https://arxiv.org/abs/2502.09597)

官方事实：

- ICLR 2025 Oral。
- 1,000 个独特 preference-query pair，三种表达形式共 3,000 条：显式偏好、隐式选择、隐式 persona。
- 覆盖 20 个主题，可构造最长 100k tokens 的多 Session 对话。
- 同时提供 generation 与 classification；classification 可自动计算准确率。
- 官方实验包含 conflicting preferences 和 multiple preferences。
- 仓库 License 是 Creative Commons Attribution-NonCommercial 4.0。

对 AntiSentinel 的价值：

- Candidate 提取是否识别显式/隐式 Preference；
- Entity Normalize 是否归一同义偏好；
- 新偏好覆盖旧偏好；
- Preference Recall 后是否真正影响答案；
- classification 可避免所有 Case 都依赖 LLM Judge。

限制：

- CC BY-NC 4.0，不作为商业发布 Gate 或产品内置数据；只允许内部研究评测并保留 attribution。
- 原项目执行脚本偏 AWS Bedrock，不直接复用；只写 AntiSentinel dataset adapter。

### 3.3 LoCoMo10：超长会话补充

一手来源：

- [官方仓库](https://github.com/snap-research/locomo)
- [论文](https://arxiv.org/abs/2402.17753)

官方事实：

- 发布 10 条高质量超长对话。
- 每条包含多个带时间戳 Session、Turn ID、Session observation、Session summary、人工 event summary。
- QA 标注包含 question、answer、category 和 Evidence dialog IDs。
- 支持 QA、event summarization 和多模态对话生成；图片本体不发布，只提供 URL/caption/query。
- License 为 CC BY-NC 4.0。

对 AntiSentinel 的价值：

- Session Tree digest；
- Event summary 与事实时间线；
- 跨 Session QA、temporal 和 adversarial retrieval；
- EvidenceRef 可下钻到 dialog ID。

限制：

- 只有 10 个 conversation，不足以单独支撑 30 Case / 5 Session / 3 Incident 的统计 Gate。
- 非商业许可。
- 多模态部分不属于当前 PRD-003 范围。

### 3.4 LongMemEval-V2：未来 Agent 经验记忆

一手来源：

- [官方仓库](https://github.com/xiaowu0162/LongMemEval-V2)
- [论文](https://arxiv.org/abs/2605.12493)

官方事实：451 个人工问题、5 种 Agent Memory 能力、每个 haystack 最多 500 条 trajectory、最大约 115M tokens，覆盖 web 和 enterprise。

它测试 workflow knowledge、environment gotchas、动态状态和 premise awareness，与未来 Tool/Runbook Memory 很匹配。但当前规模和 trajectory adapter 成本过高，留到 PRD-005/Runbook 阶段，不进入本轮 Gate。

### 3.5 InfiniteBench / LongBench：压力测试，不是主 Memory Gate

一手来源：

- [InfiniteBench 官方仓库](https://github.com/OpenBMB/InfiniteBench)
- [LongBench 官方仓库](https://github.com/THUDM/LongBench)

InfiniteBench 包含 100k+ token 的 `longdialogue_qa`、`kv_retrieval`、`passkey` 等任务；LongBench 包含中英文长上下文任务。它们适合验证 Context Digest 不丢 needle、超长输入裁剪和中文 long-context，但不包含持续写入、Memory 更新和用户偏好生命周期，因此不用于 90% Memory accuracy 主 Gate。

## 4. Benchmark Framework 调研

### supermemory MemoryBench

一手来源：[官方仓库](https://github.com/supermemoryai/memorybench)

值得吸收：

```text
INGEST → INDEX → SEARCH → ANSWER → EVALUATE → REPORT
```

- 每阶段独立 checkpoint，可从失败点恢复。
- Provider、Benchmark、Judge 分开。
- 报告把 accuracy、latency、context tokens 保持为三个独立维度，不压成一个不可解释分数。
- MIT License。

不直接引入：

- 项目使用 Bun/TypeScript，并自带 Web UI；直接引入会再次重量化。
- AntiSentinel 已有 SQLite Evaluation Run/Case/Result、Trace 和中文 Dashboard，应实现轻量 Python adapter。

### THUIR MemoryBench

一手来源：[官方仓库](https://github.com/THUIR/MemoryBench)

它覆盖 memory 与 continual learning，MIT License，但依赖多类上游数据、baseline 和 critic，安装面较大。可用于未来综合对比，不适合作为本轮最快闭环。

## 5. 推荐评测协议

### 5.1 第一层：无模型 Retrieval Gate

数据：LongMemEval-S cleaned 全 500 Case。

流程：

1. 按时间顺序 ingest history Session。
2. 运行 Candidate/Normalize/Merge/Persist。
3. 对 question 做 Memory Recall。
4. 以 `answer_session_ids` 和 `has_answer` 计算 Session/Turn Recall@K、Precision@K、MRR。
5. abstention 单独计算 false-positive retrieval rate。

该层不调用答案模型，成本低，先验证 Memory 层本身。

### 5.2 第二层：端到端 QA

数据分层：

- Smoke：按 question type 分层抽样 30 Case。
- Regression：固定 100 Case。
- Release：500 Case。

每个 Case 成对运行：

- Baseline：完整可容纳历史；超限时记录 `baseline_context_overflow`，不偷偷截断。
- Optimized：Recall + Rank + Digest + scoped ContextView。

记录：实际 provider input/output/cached tokens、检索耗时、answer、Evidence IDs、Memory IDs、prompt revision、数据集 SHA256。

### 5.3 Cache Gate

LongMemEval 本身不产生自然重复流量，因此定义三轮固定协议：

```text
Pass 1：cold，全部 bypass/miss，不进入目标分母
Pass 2：warm，相同 query + 相同 Memory version，进入分母
Pass 3：mixed，80% 重复 query + 20% version invalidation
```

85% Gate 使用 Pass 2 + Pass 3 中 `eligible hit / eligible lookup`。不得把 cold population 排除规则用于隐藏正常 miss。

### 5.4 Preference 专项

内部研究使用 PrefEval classification：

- 先跑 explicit 100 Case；
- 再跑 implicit choice 100 Case；
- 最后跑 conflict/update 子集；
- 评估 Candidate extraction、Memory update accuracy、retrieval accuracy 和 answer adherence。

由于许可证为非商业，数据不提交仓库、不打包、不在产品运行时下载。

## 6. 目标与测试集的关系

| 目标 | 数据集 | 直接证据 |
|---|---|---|
| 90% Memory accuracy | LongMemEval-S + PrefEval classification | Evidence Recall、QA、Preference adherence |
| 30% Token 降幅 | LongMemEval-S paired replay | provider input token 差 |
| 85% Cache 命中率 | LongMemEval-S warm/mixed replay | Redis eligible hit/miss/bypass |
| 冲突合并 | knowledge-update + PrefEval conflict | 新事实覆盖、旧事实不注入 |
| 时效性 | temporal reasoning + LoCoMo timestamps | 时间过滤与排序 |
| 不乱答 | LongMemEval abstention | false-positive recall / abstention accuracy |

## 7. 数据与许可策略

- 数据集不提交仓库，统一放到 `storage/benchmarks/<dataset>/<version>/`，由下载脚本获取。
- 保存上游 URL、license、revision/commit、SHA256、下载时间。
- LongMemEval 可作为主 CI/Release benchmark adapter。
- PrefEval 与 LoCoMo 标记 `research_only=true`，运行前显示 CC BY-NC attribution。
- benchmark 输出进入 SQLite，原始数据保持只读。
- 日常单测使用手写最小 fixture，不把数百 MB 数据塞入测试包。

## 8. 下一步建议

先实现一个窄阶段：

```text
LongMemEval-S Adapter
  → 官方数据下载/校验
  → 30 Case 分层 Smoke
  → Retrieval-only 全 500
  → 真实 DeepSeek 30 Case paired QA
  → warm/mixed cache replay
```

这一阶段完成后再决定是否接 PrefEval。不要同时接三套数据集，否则会把时间花在格式适配而不是 Memory 质量上。

## 9. LongMemEval-S 全量接入基线结果

2026-09-04 已完成本地 Retrieval-only Adapter 和全量扫描：

- 数据：`storage/benchmarks/longmemeval-s/longmemeval_s_cleaned.json`
- Case：500
- 按官方规则评测：470；abstention 跳过：30
- 解析/运行异常：0
- Recall@1：40.96%
- Recall@5：78.58%
- Recall@10：88.26%
- Precision@1：67.23%
- Precision@5：28.85%
- Precision@10：16.60%
- MRR@1：67.23%
- MRR@5：75.50%
- MRR@10：76.13%

这是当前确定性的关键词检索基线，排序只读取问题与历史 Session，不读取 `answer_session_ids`，不存在答案标签泄漏。它验证了官方数据到 AntiSentinel `Incident → Session → Turn → Memory/Evidence` 映射可运行，但不能代表最终 Memory Recall 质量。

该结果暴露两个下一步方向：

1. Precision@K 随 K 快速下降，需要 Scoped ContextView、Rank/Filter 和有效期过滤，不能简单把 Top-10 全部注入模型。
2. 需要把命中的 Session/Turn 映射为真实 Memory/EvidenceRef，再进入 paired QA，才能测 Token 降幅和答案准确率。
