# 图候选占位实验（2026-09-10）

本轮新增两个显式实验策略，并保留原默认行为：服务默认仍hybrid，hybrid_graph默认仍interleave。

- interleave：原策略，种子/图候选交错选择，可能挤掉不相关位置的融合结果。
- replace_seed：每个子候选只竞争来源种子的位置，其他种子次序不变；未带来整体指标改善。
- replace_container：同样限定位置，且只替换归档类型为class/module的种子，保留function/method和未知类型。类型从归档读取，不依赖可变节点表或查询文本。

重复候选、未知来源种子和已有融合结果不能重复占位；多个子候选最多占来源种子的一个位置。容器内部按已有图扩展顺序选择首个候选，没有增加语义reranker。因此只能作为候选策略，不能保证所有类/模块查询都适用。

## 可复现入口

Python：`/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python`。仓库根运行：

```sh
python scripts/evaluate_code_retrieval.py --diagnostic --diagnostic-vectors --hybrid-graph --graph-selection replace_container --output <new-root>/replace_container --timeout 120
python scripts/evaluate_ragas.py --report <new-root>/replace_container/report.json --output <new-root>/ragas
python -m pytest -q
```

替换graph-selection为interleave/replace_seed可重放对照。策略写入report和execution_key，避免将不同策略误认为相同执行配置。

产物根：`/Users/xuewentao/.local/share/antisentinel/cases/graph-selection-yavf856b`。保留第一轮未改善的replace_seed结果以及第二轮容器策略；最终三策略位于*-final，Ragas位于*-ragas，comparison.json核验冻结输入和未修改通道输出。final-commands.json保存全量后14条CLI及耗时；各CLI超时120秒、退出0、重试0。

## 最终对照

同一冻结Python语料82文档、24独立查询、4路各5轮。每策略480观察，三策略总1440观察，仍只有24条独立查询。manifest和标签未修改；不将该集合称作留出集。

| 策略/模式 | P@5 | Recall@5 | MRR | 本轮P95 ms |
|---|---:|---:|---:|---:|
| 默认hybrid | 0.12 | 0.483333 | 0.328333 | 见各报告 |
| interleave的hybrid_graph | 0.12 | 0.458333 | 0.328333 | 30.21 |
| replace_seed的hybrid_graph | 0.12 | 0.458333 | 0.328333 | 25.88 |
| replace_container的hybrid_graph | 0.13 | 0.508333 | 0.338333 | 20.61 |

三策略的keyword/vector/hybrid逐观察document_ids/source_identities完全相同。每条CLI输入错误及重复结果0，两库82/82来源核验保持通过。P95是顺序实验观察，无控制环境性能结论；不因本轮值更小而宣称加速。

replace_seed没能恢复q18，因为相关方法CodeMapWorker.run_once还包含嵌套函数watch，完整方法会被替换成局部函数。replace_container保护函数后，q18 Recall从interleave的0恢复为1，同时保留q13新增的0.5召回。

相对默认hybrid，容器策略P@5增加0.01（8.33%），Recall增加0.025（2.5个百分点，5.17%），MRR增加0.01（3.05%）。相对interleave，Recall增加0.05（5个百分点）。但与默认hybrid相比，20条可回答查询仅1条Recall提高、0条降低、19条不变。

comparison.json对每条可回答查询的Recall差值做描述性paired bootstrap：种子42，20000次，每次有放回抽20条，95%分位区间[0,0.075]，包含0。已检查过这些题再设计策略，不能据此得出独立泛化或统计显著改善结论。没有进一步按单题调参，也没有改变默认策略。

三策略分别执行真实Ragas0.4.3 ID评分，各480行输入错误0。replace_container的hybrid_graph ID precision为0.108333（120个有定义行），ID recall为0.508333（100个有定义行，20个无答案观察recall未定义）。ID precision不是P@5。无外部模型调用、答案类指标未运行，诊断向量不是Qwen Flash，也不是此前企业Go语料结果。

## 回归与验收边界

| 指标 | 基线 | 本次 | 门槛 | 变化 | 证据 | 结论 |
|---|---:|---:|---:|---:|---|---|
| 全量通过数 | 676 | 688 | 原用例无失败 | +12 | pytest-full.log | 通过 |
| 跳过/失败 | 0/0 | 0/0 | 0/0 | 0 | 同上 | 通过 |
| 未改通道输出 | 基线 | 完全一致 | 完全一致 | 0 | comparison.json | 通过 |
| 三策略查询错误 | 0 | 0/1440 | 0 | 0 | *-final/report.json | 通过 |
| 累计正确性Case | 8 | 8 | 全通过 | 0 | final-commands.json及report | 通过 |
| 整体质量 | 未通过 | 未通过 | 原质量门槛 | 未达标 | 各评测quality_pass=false | 未通过 |

实现前新增选择器测试因符号尚不存在ImportError；实现后逐步targeted21/37/42通过，最终针对性42 passed、2.93s。全量688 passed、0 skipped、0 failed、7 warnings、44.34s；基线39.99s，测试集合增加且环境未控，不声称同负载性能回归门槛已验证。

8累计Case shutdown/worker/publication/keyword/vector/hybrid/graph/lifecycle的90检查全部通过，内部耗时1183.30/6461.70/65.97/53.56/1022.56/1433.30/461.67/1570.29ms。三策略最终诊断耗时10854.49/9471.70/9115.42ms。compileall/diff-check退出0。

本轮源码/脚本/测试4文件，文档3文件；本地执行与正确性回归有证据，未标用户review或生产就绪。质量验收仍未通过；接下来需要独立查询及真实Flash验证，并保留Standalone容量、远程常驻Worker和答案评测验收。
