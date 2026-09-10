# PRD-005 检索优化调研

日期：2026-09-08。状态：调研完成；优化尚未实施，收益尚未验证。

## 结论与现有证据

可以通过冻结语料、逐查询配对比较和消融实验证明局部收益。目前不能声称 hybrid 整体优于 keyword。当前向量是未训练的 64 维 hash 特征，不是真实语义 Embedding；结果不能外推到 Milvus 或语义检索本身的能力。

本次重新读取实际报告并按 manifest category 聚合，没有运行新检索或模型请求。证据路径：

- `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-baidu-source-poitl0bv/diagnostic/report.json`
- 同目录上级 `manifest.json`；manifest SHA256：`a586c5573b9a9622bc2eccdffc30d3715fd135b5c622116df77152f6336c647e`。
- 4 个 Go 文件、63 文档、24 条不同查询，每模式重复 5 次。标签仍为 proposed_for_user_review。

| 分组 | 查询数 | keyword Recall@5 / MRR | hybrid Recall@5 / MRR |
|---|---:|---:|---:|
| 精确符号 | 6 | 1.000 / 1.000 | 1.000 / 0.8333 |
| 中文语义 | 10 | 0 / 0 | 0.100 / 0.100 |
| 错误日志 | 4 | 1.000 / 0.875 | 0.750 / 0.550 |

20 条有答案查询整体 MRR 从 0.475 下降至 0.410（下降 0.065，约 13.7%）；Recall@5 均为 0.50。4 条无答案查询误命中率从 25% 升至 100%。精确 Hit@1 从 6/6 降至 4/6。

逐查询差异：q02、q06 MRR 从 1 降至 0.5；q10 从 0 升至 1；q17 从 1 降至 0.2；q19 从 0.5 降至 0。即有答案查询中 1 条改善、4 条退化、15 条持平。这证明问题存在，不证明某个拟议修复有效。

## 优化方向与依据

### 1. 精确符号和日志短语保护，优先实验

本地 `keyword.py` 先排精确匹配，`fusion.py` 随后只根据各通道排名进行等权 RRF，没有保留 exact 信号。建议在 scope 校验后保留精确符号/路径特征，对有明确精确命中的请求优先排列精确结果；日志先识别完整短语，再允许词级召回。不能用评测 query ID 写特例，也不能看到英文就判断为精确查询。

[Milvus 官方 RRF 文档](https://milvus.io/docs/reranking.md)说明 RRF 按名次融合，多路共同命中会被优先考虑；它并不保证 lexical 第一名保持第一。因此优先保护 exact 是基于本项目目标的设计假设，不是官方效果保证。SQLite 与 Milvus 的结果仍需应用层融合，不能直接把 SQLite 结果传给 Milvus 多向量 Ranker。

验证：精确 Hit@1 恢复到 6/6，错误日志 Recall 不低于基线；中文语义 Recall 不退化。单独保护 exact 即使成功，也不能解决大部分中文语义漏检。

### 2. 替换 hash 向量，建立真实语义基线

更正：项目已有 `QwenFlashEmbedder`，模型为 `qwen3.7-text-embedding-flash`，默认 1024 维，接入 Memory 向量检索。此次代码检索评测脚本没有复用它，报告明确记录 `untrained_feature_hashing`、`external_calls=0`。因此优先方向应为在明确语料发送范围后复用已有 Flash 建立代码检索基线；下述本地 0.6B 仅作为不发送源码时的备选，并非建议默认更换现有模型。现有 hash Case 的分数不能归因于 Flash。

建议首个本地候选为 Qwen3-Embedding-0.6B；资源允许后再比较更大模型，不预设更大必优。其[官方模型卡](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)列出多语言与代码检索能力、1024 维输出和查询 instruction 用法。应固定模型 revision、维度、输入模板、归一化方式，创建独立版本 collection。文档输入包含路径、符号、签名、注释与函数正文，并记录截断。

这只是值得实验的候选，不是本机性能或公司语料效果承诺。读取公司仓库的权限不等于向外部模型发送代码的授权；本地推理可先评估可行性。本轮未下载模型、未发送源码。

### 3. 改善 lexical 分词和字段匹配

`_fts_expression` 使用 Unicode `\w` 与 OR 拼接，没有 camelCase 分解或中文词语切分。应同时在索引与查询端增加规范化标识符词项，保留原始符号；中文切分、字符 n-gram 与日志短语分别消融。

[SQLite FTS5 官方说明](https://www.sqlite.org/fts5.html)表明默认 unicode61 将连续 token 字符组成词，trigram 则使用连续三个字符；少于三个 Unicode 字符的全文查询无法通过 trigram 命中。因此不能把“换 trigram”当作完整中文方案。分词也无法独立解决中文描述到英文代码的跨语言语义匹配。

### 4. 融合权重与学习式重排，放在真实召回之后

先记录 candidate Recall@30，再比较等权 RRF、带权 RRF、真实 reranker。前 30 条无相关代码时，重排没有机会补回漏检。每通道最多 30 条，合并最多 60 条；重排实际输入上限、去重与裁剪策略必须固定，避免隐藏扩大预算。

Milvus 文档提供加权分数与 RRF 两种策略，但 SQLite BM25 与 COSINE 分数不可未经校准直接相加。Qwen 官方模型卡也列出 Reranker 系列，可作为后续候选。权重、RRF k 和 reranker 阈值只在开发集选取。

### 5. 无答案判定

目前 vector 总是取 top-k，低相关结果仍会进入 hybrid。应保留原始向量/重排分数，在独立开发集上校准拒答阈值，再测无答案误命中与有答案误拒绝的权衡；不能用 RRF 分数直接当语义置信概率。

[Milvus Range Search](https://milvus.io/docs/range-search.md)允许限制返回结果的距离/分数范围，提供实现工具，但不会自动学习合理阈值。Lite/Standalone 与固定版本的具体支持应另行验证。4 条无答案只够回归，不能据此声称生产可靠。

## 怎样证明改善

1. 保留当前报告作历史基线；先复核标签。当前语料作为开发/诊断集，另外冻结不同文件、不同表达的留出查询，避免看过失败样本后继续用它证明泛化。
2. 至少 20 条独立留出查询，并覆盖精确、语义、日志、无答案；该数量只是最低门槛。同一查询重复 5 次不是 5 个独立质量样本。当前固定分母 P@5 理论上限为 0.24，不能满足 0.8；不改分母或补标签刷分。
3. 按单变量顺序运行：现状 → exact 保护 → lexical 规范化 → 真实 Embedding → 融合/重排 → 拒答。另做关键组件移除实验，避免将组合收益错误归因给一个组件。
4. 同时报告三类成功指标：精确 Hit@1、语义 Recall@5、整体 MRR；失败指标：无答案误命中及有答案误拒绝；正确性护栏：scope/hash/重复/执行错误为 0。输出每条查询的改善、退化、持平和配对差值。扩充样本后可按查询做配对 bootstrap 区间；小样本不作总体显著性承诺。
5. 单独测性能，固定机器、模型加载/预热口径、并发、缓存与候选预算，交替运行基线/候选各 5 次。沿用 median ≤ 1.05x、P95 ≤ 1.2x 护栏；引入模型若超预算，诚实报告质量/延迟取舍。此前和 Ragas 初始化并发的时延对比不能用来证明性能变化。
6. Ragas ID Precision/Recall 继续作离线交叉核验，且同时报告空值数；它们不能替代排序 MRR、固定分母 P@5 或无答案指标。[Ragas Context Precision 文档](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/)区分不同计算方式。答案阶段再固定生成器、真实回答和实际上下文，评 Faithfulness 等；检索指标提升不等于答案质量已提升。

推荐下一步：先做 exact 保护的单变量实验，再建立本地真实 Embedding 基线。尚未实施，也不将这些预期标记为通过。
