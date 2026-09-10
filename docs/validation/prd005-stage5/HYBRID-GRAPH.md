# 四路检索接通与诊断（2026-09-10）

新增显式实验模式hybrid_graph，使用keyword/vector经RRF融合后的最多5个结果作为种子，沿现有contains关系扩展，交错选择top-k（最大5）。图深度1、节点20、边40、扩展候选最大30；保留graph的keyword种子语义，默认hybrid不变。新图候选文档ID与SQLite采用相同的Scope/node/chunk/projection派生规则，能够进入冻结标签核验和Evidence回读。

冻结语料的同一批Git blob及解析结果现在可发布成真实CodeMap归档。评测CLI增加`--hybrid-graph`；只有显式启用且有编码器才运行第四路，未选中时仍记hybrid_graph_not_measured。没有自动将“通路已实现”改写成质量或生产验收通过。

## 命令与产物

Python：`/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python`。

```sh
python -m pytest -q
python scripts/evaluate_code_retrieval.py --diagnostic --diagnostic-vectors --hybrid-graph --output <new-root>/retrieval-final --timeout 120
python scripts/evaluate_ragas.py --report <new-root>/retrieval-final/report.json --output <new-root>/ragas-final
```

本轮产物根：`/Users/xuewentao/.local/share/antisentinel/cases/hybrid-graph-h3qoua05`。首次和最终检索/Ragas报告分别保存，final-commands.json记录全量回归后的10条CLI（2个评测入口及8个累计Case），每条超时120秒、退出0、重试0。pytest-full.log保存全量输出。

首轮针对性26通过/1失败，新图发布helper遗漏独立数据库初始化；补齐后27通过。增加vector-only种子经真实归档扩展并读取Evidence用例后，针对性44 passed、0 failed、3.61s。最终全量676 passed、0 skipped、0 failed、7 warnings、39.99s；基线670通过，新增6项。compileall与diff-check退出0。

## 四路诊断结果

固定manifest SHA-256仍为`6473b9ae5f596fb3dc2287a747ad24f97da15165ce7fc77f7a1cd97d2adab3d0`。输入10份版本化文件、50833bytes、82文档、24独立查询，四路各5轮，每路120观察、合计480。存储读回2个snapshot、82节点、54条contains边、82chunks；SQLite/Milvus检索投影分别82/82，来源身份全部校验。最终检索8248.97ms，业务/存储观察差8.07ms；诊断无后台worker，异常字段null不能冒充测得0。

| 模式 | P@5 | Recall@5 | MRR | P95 ms | 查询错误 |
|---|---:|---:|---:|---:|---:|
| keyword | 0.14 | 0.55 | 0.485 | 7.72 | 0/120 |
| vector | 0.08 | 0.283333 | 0.1825 | 8.60 | 0/120 |
| hybrid | 0.12 | 0.483333 | 0.328333 | 17.78 | 0/120 |
| hybrid_graph | 0.12 | 0.458333 | 0.328333 | 18.62 | 0/120 |

各模式逐查询P@5/Recall/MRR最小值均0，重复候选0。P95为本轮实测，不构成生产容量或严格同环境性能改善证明。

hybrid_graph的65/120观察（13/24独立查询）实际出现图候选。相比hybrid，P@5/MRR未提高，Recall降低0.025（2.5个百分点，相对下降5.17%）：

- q13「怎样判断回读源码有没有被改动？」Recall 0→0.5：增加1个相关结果，共2个参考目标。
- q18「构建结果何时从子进程队列读取？」Recall 1→0：图结果占位使唯一相关结果退出top-5。

这解释了P@5不变而宏平均Recall下降。没有按这两题修改冻结标签或调整默认策略。后续实验方向是控制图候选占位，并以独立查询验证是否保留融合结果的有效召回；本轮没有证明这种策略已经有效。

Ragas0.4.3对480观察执行真实ID评分，invalid_inputs=0，external_model_calls=0。hybrid/hybrid_graph的ID precision均0.10（各120有定义行）；ID recall分别0.483333/0.458333（各100有定义行，20无答案观察不定义recall）。keyword/vector ID指标与前轮一致。答案类指标未运行。

## 通过范围与累计验证

| 指标 | 基线 | 本次 | 门槛 | 变化 | 证据 | 结论 |
|---|---:|---:|---:|---:|---|---|
| 可测检索模式 | 3 | 4 | 4 | +1 | retrieval-final/report.json | 执行通过 |
| 观察数 | 360 | 480 | 480 | +120 | 同上 | 执行通过 |
| 评测查询错误 | 0 | 0 | 0 | 0 | 同上 | 执行通过 |
| 完整回归 | 670 | 676 | 原用例无失败 | +6 | pytest-full.log | 通过 |
| 累计正确性Case | 8 | 8 | 全通过 | 0 | final-commands.json及各report | 通过 |
| hybrid_graph质量 | 未测 | Recall0.458333 | 默认Recall≥0.60等原门槛 | 不达标 | retrieval-final/report.json | 未通过 |

8个累计Case shutdown/worker/publication/keyword/vector/hybrid/graph/lifecycle的90项检查全部通过，内部耗时1201.12/7049.60/73.12/52.29/1019.32/2226.99/456.26/1127.24ms。首轮及最终四路诊断和Ragas均execution_pass=true，**quality_pass=false、case_pass=false**。

当前证明的是新检索通路与本地评测可以执行。诊断向量不是Qwen Flash，标签仍待review，P@5理论上限0.25，不能据此宣称优化成功。仍待真实Flash四路/答案评测、远程常驻Worker及Standalone容量部署；未标用户review通过或整体生产就绪。
