# 回答意图路由：新冻结查询对照

2026-09-08。先冻结规则与新查询，再运行baseline/routed两轮；默认策略仍baseline。

## 规则

select_answer_style只读取query和披露的候选源码/路径，不读取query_id、category、相关性标签或历史分数：

- 精确匹配候选文件路径：focused。
- 查询是单个标识符，且候选文本中找到Go/Python函数声明形状：focused。
- 查询是2–8个ASCII日志词项，且出现在候选日志调用的首个字符串参数中：focused。
- 其他情况，包括自然语言问句与未找到对应声明/日志的输入：baseline。

这是有限语言/调用形状的文本启发式，不是新增AST事实判定；只选择回答风格，不产生或删除检索候选。查询类型误判仍需后续样本验证，不能把它视为权限或事实校验。两种风格均保留strict引用schema、SQLite Evidence、4片/32KiB和原Ragas输入。

runner新增routed选项并逐条记录resolved_answer_style；允许用同一固定源码索引评估新的查询集，独立校验corpus_version/source hash/commit/source_count，旧索引execution必须通过。新query/label指纹写本次report，旧索引指纹单独保存；源码改变不允许复用。生产Scope、文档和task仍由既有检索入口二次权威校验。

## 新冻结集

24个此前未使用的query文本，分布6函数名/4日志字面量/10自然语言问题/4无答案。与原始24和上一轮24文本无交集；相同四文件63文档，所以只是查询层留出，不是跨文件/跨仓库盲测。所有label仍proposed_for_user_review。

manifest为私有语料根目录answer-routing-heldout-v1.json，SHA256 `dc716c3036db0b14af92d6bc577e2fa6cfb5274bed96c31f2cfbc0429feb2d60`，在模型运行前保存。preflight 24查询/24不同文本/63文档/errors0。

## 执行

baseline先对新查询执行实时Flash检索、Evidence、真实答案及Ragas；routed复用该轮已验证context但重新生成全部24答案并独立评分，不复用旧答案，也不拼接各类别最好分数。运行参数与模型相同，客户端不设置token上限。

```sh
python scripts/run_rag_answer_case.py --repository <私有根>/repo \
 --manifest <私有根>/answer-routing-heldout-v1.json --index <私有根>/flash-baseline \
 --output <私有根>/answer-routing-baseline-v1
python scripts/run_rag_answer_case.py --repository <私有根>/repo \
 --manifest <私有根>/answer-routing-heldout-v1.json --index <私有根>/flash-baseline \
 --resume-answers <私有根>/answer-routing-baseline-v1/answers.jsonl \
 --regenerate-saved --answer-style routed --output <私有根>/answer-routing-routed-v1
```

私有根：`/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-baidu-source-poitl0bv`。两轮串行，各600秒/200文本请求预算，父进程各630秒；遇执行错误停止。成功指标：24条完整、引用/Evidence校验100%、生成/评估披露一致；失败指标：执行/引用错误0。质量看Faithfulness≥.90、Relevancy≥.80筛查，同时均值相对回归≤5%，无答案/unknown单列。标签review与生产验收尚未满足时仍不标整体质量通过。

## 本地验证

新增10项路由和索引语料兼容测试。先见缺失select_answer_style导入红灯；实现后41 passed、0 failed、1.65s；收紧缺失指纹字段拒绝后再次41 passed、0 failed、2.35s。命令 `python -m pytest tests/test_answer_routing.py tests/test_rag_answers.py tests/test_code_support_judge.py tests/test_code_retrieval_evaluation.py -q`。

修改rag_answers.py、run_rag_answer_case.py，新增test_answer_routing.py，共3实现/测试文件。新冻结样本运行结果尚未记录前，不宣称路由提升效果。

## 新查询baseline结果

baseline24/24、errors0，20有答案Faithfulness .996667、Answer Relevancy .801647（各20定义值）；4无答案Faithfulness .958333、Relevancy0（各4定义值），单独汇总。这些分数来自新查询集，不能替换旧24条基线的低分。仍保留quality_pass=false、case_pass=false，因为整体生产/标签review/泛化验收未完成。

在已冻结baseline上下文上检查路由分布：focused10、baseline14，与规则预期一致；没有依据分数改变路由。routed将重新生成24答案并独立评分，结果待完整运行后比较。

## 最终新查询对照：未证明路由收益

两轮各24/24、errors0；routed为10 focused/14 baseline。独立校验24条生成与评估上下文逐对象完全一致，两数据库integrity=ok、各46份Evidence hash全部通过。各96个context出现次数、214500披露字节、45引用。默认策略未改变。

| 有答案指标 | 新baseline | routed | 变化 | 筛查值 | 结果 |
|---|---:|---:|---:|---:|---|
| Faithfulness（20定义值） | .996667 | .967917 | -.028750 / -2.88% | ≥.90 | 都达到，但观测下降 |
| Answer Relevancy（20定义值） | .801647 | .805992 | +.004345 / +.54% | ≥.80 | 增益不足以支持切换 |
| 执行错误 | 0 | 0 | 0 | 0 | 通过 |

4无答案单列，两轮Relevancy均0，Faithfulness .958333→.905769；不得用拒答组影响有答案均值。完整report仍execution_pass=true、quality_pass=false、case_pass=false，不能把筛查值达到当作生产验收。

对20个有答案query做配对bootstrap（seed42，10000次），相关性差值95%区间[-.034013,+.040980]，跨0。本区间仅反映query重采样，未覆盖完整LLM多次运行方差，不作更强统计保证。保持baseline风格的10条有答案控制组也出现相关性+.013301、Faithfulness-.051667，而实际切到focused的10条相关性为-.004612、Faithfulness-.005833。这不支持把总均值的微小上涨归因于路由，也不能将Faith下降全部归因于路由。两组各10条答案文本均未与首次输出完全相同，单次模型评估波动不可忽略。

baseline耗时285.16秒，144文本调用559012tokens、72Flash调用3584tokens；routed耗时280.55秒，144文本调用557922tokens、48Flash调用2970tokens。第二轮复用检索上下文，不能据总耗时少4.61秒声称端到端加速。两轮合计288文本调用1116934tokens、120Flash调用6554tokens，费用N/A，原失败/空值数均0，没有选择性重跑。

原始产物在私有根answer-routing-baseline-v1和answer-routing-routed-v1，逐query差值、bootstrap和完整性检查见后者paired-comparison.json。路由保留为实验选项，不启用默认。后续优先生产FTS投影隔离、manifest对账发布与Worker恢复，不继续通过重复同模型评分调提示词刷过线。独立裁判、用户review标签和新的跨文件/跨仓库评测仍未完成。

最终全量回归命令 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-ragas-clean-wxav1le_/bin/python -m pytest -q`：595 passed、0 failed、6 warnings、41.04s；585→595新增10项，通过率100%。在两轮模型Case结束后运行，未干扰其延迟。git diff --check通过。当前Case真实运行通过、回归通过；路由优化收益和生产就绪未通过，不代替用户review。
