# Flash 代码检索基线 Case

## 2026-09-08 实测结果

用户“先把上面的那个测了”确认后，按上轮披露范围运行已有阿里云 MaaS 配置。模型 qwen3.7-text-embedding-flash，1024维；冻结commit、manifest、查询和标签与hash报告一致。新增SQLite权威校验已在上轮562项回归中通过，排序规则没有调整。本次未修改检索实现。

命令为下方Case命令，安全读取 `.env.local` 三项配置到子进程环境；父进程150秒终止上限，runner预算120秒。退出0，总26596.19ms；244次HTTP尝试、244份usage、30032 tokens。4批文档+240次实时查询编码，无缓存；尝试总数等于计划请求数，未观察到额外重试，原报告retries字段仍为null。费用N/A，未获得账单。

| 指标 | hash hybrid基线 | Flash hybrid | 阈值 | 变化 | 证据 | 判定 |
|---|---:|---:|---:|---:|---|---|
| P@5 | .1000 | .2200 | ≥.80 | +.1200 / +120% | 两份report.json | 未达标 |
| Recall@5 | .5000 | .9250 | ≥.60 | +.4250 / +85% | 两份report.json | 达标 |
| MRR | .4100 | .8167 | ≥.80 | +.4067 / +99.19% | 两份report.json | 达标 |
| exact Hit@1 | 4/6 | 6/6 | 6/6 | +2/6 | 两份report.json | 达标 |
| 无答案误命中率 | 100% | 100% | 0%目标 | 0 | 两份report.json | 未达标 |
| 检索/scope错误 | 0/0 | 0/0 | 0/0 | 0/0 | Flash report.json | 通过 |

三模式各120次观测，共360次。keyword P@5/Recall/MRR=.10/.50/.475，vector=.22/.925/.79333，hybrid=.22/.925/.81667。keyword指标与历史一致。查询P50/P95：keyword4.60/7.11ms、vector82.29/129.29ms、hybrid89.93/116.93ms；模型和权威校验配置与旧hash不同，不做同配置性能改善声明。

SQLite/Milvus各63条，source/model/dimension重开校验全部通过，Memory写入0。4文件39523源字节，业务26575.10ms、持久化26592.37ms、滞后17.28ms；worker0，后台异常N/A（原报告null）。报告 execution_pass=true、quality_pass=false、case_pass=false。成功指标中Recall/MRR达标，但固定分母P@5理论上限.24，标签未review，不能称为整体质量通过。

本地Ragas0.4.3：360观测、invalid_inputs=0，execution_pass=true；hybrid/vector IDPrecision=.183333（120个定义值），IDRecall=.925（100个定义值，20个无答案为null）；keyword IDPrecision=.198718（65定义、55null），IDRecall=.50（100定义、20null）。不同分母/空值不能直接把IDPrecision当P@5。Faithfulness等答案指标仍not_evaluated，本次未调用裁判模型。

原始产物根目录：`/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-baidu-source-poitl0bv`，检索在 `flash-baseline/`，Ragas在 `flash-ragas/`，标准输出/错误在 `flash.stdout`/`flash.stderr`。

报告口径缺陷：evaluation/code_retrieval.py硬编码了 real_embedding_not_verified blocker，本次真实Flash调用已由模型响应校验、244份usage及向量版本回读证实；保留原始报告不修改，用本说明纠正该条。其他blocker（标签、Standalone、hybrid_graph、质量阈值、性能比较）仍成立，不能移除它们以改变case_pass。

本次证据支持“当前固定开发样本上，Flash配置显著提高观测到的召回和排序指标”，不宣称统计显著或跨仓库泛化。下一优化优先项是无答案判定和余下语义漏检，而不是继续依据hash结果判断真实模型质量。

## 原始运行准备记录（以下待执行措辞为运行前状态）

用户已确认复用已有 qwen3.7-text-embedding-flash。本轮为既有评测增加编码器选项，保留 hash 诊断，融合参数不变。真实远程 Case 尚未执行。

回归：隔离 Ragas 环境全量 521 passed、0 failed、6 warnings、39.03s。`.env.local` 已存在所需三项配置（含1024维），执行进程需安全加载这些配置，不打印密钥；上面的模型确认不单独替代公司源码发送范围确认。

准备运行的输入：此前选择的 Go 仓库固定 commit、4 文件、63 文档、24 查询。服务收到路径、符号、文档源码及查询文本。默认维度 1024，实际从既有环境配置读取并写入报告；不记录密钥。

```sh
python scripts/evaluate_code_retrieval.py \
  --repository /var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-baidu-source-poitl0bv/repo \
  --manifest /var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-baidu-source-poitl0bv/manifest.json \
  --diagnostic --qwen-flash --timeout 120 \
  --output /var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-baidu-source-poitl0bv/flash-baseline
```

输出必须是不存在的新目录。语料发送到已有 ANTISENTINEL_EMBEDDING_BASE_URL；执行前确认该目的地与公司代码发送授权。每模式24查询×5轮，vector/hybrid各120次编码，无查询缓存。默认 batch20，对63文档为4批，加240查询请求，总244个成功请求（重试另计）；实际用量取服务返回值，费用不可用则 null。每请求最多2次重试。120秒为执行预算检查；当前同步适配器无法在总时限内强制中断已开始的HTTP请求，超限报告失败，不声称硬截止保证。

三项执行门槛：63条SQLite事实和63向量、模型/维度/source identity全匹配、三模式各120条观测完整。失败门槛：作用域错误、重复候选、检索异常均为0。回归要求相关测试全部通过。质量基线 keyword MRR .475 / Recall .50，hash-hybrid .410 / .50；报告真实 Flash 值与逐查询变化，未达到原有质量阈值仍 case_pass=false。由于模型更换，历史 hash 时延只作背景，不能作为同配置性能回归结论。

产物：manifest.json、preflight.json、facts.sqlite、vectors.db、report.json；失败时 failure.json。数据库重开校验模型和source hash；无后台worker，后台异常指标不捏造。源码保持不变，清理范围仅新建的 Case 目录。本轮先通过 MockTransport + 真实Milvus集成测试，不替代本Case。
