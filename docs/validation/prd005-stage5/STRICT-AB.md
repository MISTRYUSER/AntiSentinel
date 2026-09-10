# 用户指定严格配对 A/B（2026-09-10）

**结论：保留默认策略。B点估计更高，但未通过预先固定的Recall优效门槛。**

A为当前hybrid；B为hybrid_graph、graph_selection=replace_container。生产代码与默认策略本轮未改，仅新增独立实验脚本和验证测试。以下只有一个有效配对试验；首次对账失败的尝试无效且完整保留。

## 预先固定的协议

使用同一份Git冻结语料、同一组参考标签、同一SQLite/Milvus索引，以及每条查询同一个预计算向量。语料82文档、50833bytes，24查询，其中20可回答、4无答案。仍是已经用于诊断/设计策略的集合，held_out=false；标签未人工review。

编码器为diagnostic-token-sha256-64-v1，本轮无Flash或其他外部模型调用。这个实验比较选择策略，不是Qwen语义检索或端到端LLM验收。

- top-k=5、candidate_limit=30、RRF k=60。
- 每条查询先各臂预热一次，48调用；正式每臂5轮，共120对、240调用。
- 查询顺序随机种子42；每条查询每轮交换AB/BA顺序，整体AB/BA各60对。
- 统计单位为独立查询：先聚合同一查询5轮的B−A差值，再按查询做20000次paired percentile bootstrap，种子42。重复轮次不增加独立样本量。
- 主门槛：Recall差的95%区间下界>0。保护指标：P@5、MRR区间下界≥0，误命中率差区间上界≤0，P95 B/A≤1.20，执行错误0。
- 计划、调用顺序、实现hash和query-vector hash在测量前写入plan.json，结束后核验计划未变。
- P95只覆盖检索和结果核验，不包含查询编码、LLM或答案生成。正式测量未与测试套件并行。

## 有效试验结果

| 指标 | A：hybrid | B：仅替换容器 | B−A | 门槛结果 |
|---|---:|---:|---:|---|
| P@5 | 0.12 | 0.13 | +0.01 | 95%差区间[0,0.03]，不劣门槛通过 |
| Recall@5 | 0.483333 | 0.508333 | +0.025 | 95%差区间[0,0.075]，**优效门槛未通过** |
| MRR | 0.328333 | 0.338333 | +0.01 | 95%差区间[0,0.03]，不劣门槛通过 |
| 无答案误命中率 | 1.00 | 1.00 | 0 | 差区间[0,0]；只是不劣，绝对效果仍不合格 |
| P95检索延迟 | 21.87ms | 23.08ms | +1.21ms（5.55%） | ≤1.20倍，通过 |
| 查询/预热错误 | 0 | 0 | 0 | 通过 |

P@5/Recall/MRR逐查询最小值两臂均0。20条可回答查询中，1条改善、0条退化、19条不变；无答案指标只有4条独立查询。CI包含0，加上集合已用于策略设计，不支持显著或泛化优效结论。没有在结果出来后调整门槛或默认策略。

240条观察共返回1200个结果位置，A/B分别涉及51/48个唯一文档；五轮每条查询的结果稳定。实际调用顺序与冻结schedule完全匹配，AB/BA=60/60。SQLite/Milvus各82文档/向量，逐Scope对账及SQLite完整性检查通过。

有效运行总7456.83ms，业务完成观察7015.55ms，持久化读回完成观察7030.34ms，差14.79ms。无后台worker，background_exceptions=null；不能冒充后台异常测得0。

同一240条正式观察另行交给Ragas0.4.3评分，没有再次执行检索。A/B ID precision为0.10/0.108333（各120有定义行），ID recall为0.483333/0.508333（各100有定义行）；输入错误0，答案类指标未评估。ID precision分母不同于P@5。

## 原始记录、技术重试和复现

产物根：`/Users/xuewentao/.local/share/antisentinel/cases/strict-ab-ea1zf1jl`。

- `ab/`：首次尝试。240条测量已落盘，但最终把两个Scope合并传给单Scope对账函数，校验失败，无有效结论。
- `invalid-first-attempt.json`：记录失败原因和排除理由；未用第一轮指标选择第二轮，也未合并两轮。
- `ab-retry1/`：修复后唯一有效试验，含plan.json、query-vectors.json、warmup.json、observations.jsonl、report.json、audit.json、ragas.json及真实两库存储。
- `retry1.log`、`retry1-command.json`：技术重试命令与输出。正式技术重试1次；后续未再执行正式A/B。
- `pytest-final.log`：最终完整回归。

有效计划SHA-256：`f81cd37ecb4a64a9e1b5c0bd6883301fb77acbd8fde6f1936033c845159711d0`。原始记录和报告可直接核对，预热记录未混入正式统计。

复现命令（新目录）：

```sh
/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python scripts/run_retrieval_ab.py --output <new-output>
```

本轮父进程timeout120秒；full pytest上限180秒。新增两Scope完整试验回归用例后targeted5 passed、9.42s；最终全量693 passed、0 skipped、0 failed、7 warnings、58.82s（基线688通过，新增5项）。完整测试耗时不是同负载检索性能对照。compileall/diff-check通过。

执行检查6/6通过；统计门槛4/5通过，主指标失败，所以statistical_gate_pass=false、promote_default=false、quality_pass=false。后续若验证泛化，需要独立查询及真实Flash；本轮未标用户review或生产验收通过。
