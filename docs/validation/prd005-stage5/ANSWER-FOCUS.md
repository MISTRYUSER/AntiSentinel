# 回答相关性与过度推断专项

2026-09-08。基线为rag-answer-full-v2的24条真实回答。固定原查询、标签、Ragas0.4.3、DeepSeek模型与原始披露上下文，不用改写评测输入抬分。

## 核实的问题

- q20旧回答从局部失败批次和offset分支推导“这些行不会被重放，数据确实丢失”。披露代码不足以证明这个端到端保证；日志中的data lost不自动等同系统恢复行为的完整事实。
- 部分定位查询先展开额外流程，直接答案不够集中；q18缺少明确文件路径。
- Ragas Answer Relevancy通过反向问题与原查询相似度计分。短日志字符串与自然语言问句的口径不同，低分不能直接等同回答错误，尤其不能因此修改旧query/labels制造通过。

## 改动

answer_request增加可选style=focused，默认baseline保持可复现。focused要求位置优先，然后触发条件和直接行为；日志查询先给准确日志与发出位置，省略无关展开。禁止由名字或日志推断恢复/投递保证；最终v2进一步要求明确局部实体与全局聚合的范围。引用仍走strict schema和本地Evidence校验，未设置客户端token上限。

run_rag_answer_case.py增加 --regenerate-saved 和 --answer-style，用已校验的历史context生成新回答，而非复用旧答案。模型看不到旧回答和标签。源码、Evidence绑定、模型看到的user消息和schema保持一致；仅system回答指令变化。新增测试断言这些不变量。focused目前为实验选项，未改生产默认策略。

## 5条开发复测

固定q14/q17/q18/q19/q20。focused-v1运行5/5、errors0、7引用/17唯一Evidence、20披露片段出现次数、56891字节、58.75秒。30文本调用136130tokens，10Flash调用599tokens，费用N/A。原始产物私有语料根目录rag-answer-focused-v1。

| 指标 | 原5条基线 | focused-v1 | 变化 | 筛查口径 | 结论 |
|---|---:|---:|---:|---|---|
| Faithfulness | .960000 | .959596 | -.000404 / -.04% | 均值≥.90，回归≤5% | 基本持平 |
| Answer Relevancy | .647124 | .758047 | +.110922 / +17.14% | ≥.80 | 改善但未达筛查值 |
| 执行错误 | 0 | 0 | 0 | 0 | 通过 |

逐条Relevancy：q14 .651155→.6789、q17 .585214→.8450、q18 .512581→.7240、q19 .811139→.7594、q20 .675533→.7829。存在q19退化，不声称每条改善。q20不再声明不会重放，但局部与聚合offset作用域仍有歧义，因此加上全局聚合约束后另跑完整v2，v1数字不冒充v2结果。

## 完整复测命令

```sh
python scripts/run_rag_answer_case.py \
 --repository <私有根>/repo --manifest <私有根>/manifest.json \
 --index <私有根>/flash-baseline --output <不存在的新目录> \
 --resume-answers <私有根>/rag-answer-full-v2/answers.jsonl \
 --regenerate-saved --answer-style focused
```

私有根为 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-baidu-source-poitl0bv`，完整v2输出rag-answer-focused-full-v2。此次是固定检索上下文的答案A/B，不声称重新检索测量，也不混用旧/新答案。

实现/测试共修改3文件。focused30 passed、0 failed、1.25s；补充范围指令后再次进行针对验证，再启动完整Case。600秒预算、最多200文本调用，HTTP无自动重试（Flash适配器仍保留既有上限并计量），父进程630秒上限。同模型生成/评估、已观察样本与未review标签限制不变，不能直接推断盲测或生产收益。

## 引用范围故障与修复

focused-full-v2在q05生成阶段失败：引用document 016313...的145–173行，而该披露chunk只有29行。另一引用属于416行片段，338–416合法。局部校验正确拒绝，未把非法引用计入通过。运行共5条观测、4条评分、1错误；25文本调用181298tokens、8Flash调用431tokens，57.06秒，execution_pass=false。

根因是服务端schema允许任一document_id搭配任意正整数行号，只有本地事后校验上限。修复strict_judge_request的citation items为anyOf分支：每分支绑定单个document_id及它实际披露行数的maximum。start≤end仍由本地验证；未知文档和越界不放行。新增测试先出现缺anyOf红灯1，再累计31 passed、0 failed、1.73s。此为通用判别/答案引用协议修复，不是q05特例。

原q05单独回归rag-answer-range-pilot-v1：1/1、errors0、17.65秒；通过后启动完整rag-answer-focused-full-v3。所有历史输出保留，完整v3不拼接v1/v2的高分样本。此次累计实现/测试改动扩展为5文件（新增通用schema与其回归测试改动）。

## 完整v3结论：不全量切换回答策略

24/24、errors0、44Evidence、49引用、96context出现次数/197010字节，314.15秒。独立验证24条与旧基线上下文逐对象完全一致、44Evidence源文件hash通过、SQLite integrity=ok。144文本调用544834tokens、48Flash调用2811tokens，费用N/A。客户端max_tokens未设置；全24条重新生成，无旧答案混入。原始记录、report.json与paired-comparison.json均在rag-answer-focused-full-v3。

| 有答案指标 | baseline完整v2 | focused完整v3 | 变化 | 筛查/回归标准 | 结论 |
|---|---:|---:|---:|---|---|
| Faithfulness | .973666 | .990285 | +.016619 / +1.71% | ≥.90 | 达筛查值 |
| Answer Relevancy | .778296 | .748641 | -.029654 / -3.81% | ≥.80 | 仍不达标，方向退化 |
| exact Relevancy（6） | .806641 | .889559 | +.082918 | 分类诊断 | 改善 |
| semantic Relevancy（10） | .814160 | .669164 | -.144996 / -17.81% | 分类诊断 | 明显退化 |
| error_log Relevancy（4） | .646117 | .735959 | +.089843 | 分类诊断 | 改善但不足 |

无答案4条仍全部insufficient_evidence；Faithfulness .8875，Relevancy .205362，单列不合入有答案平均。Relevancy非0不代表正确拒答更优，是Ragas反向问题/非承诺判别的结果，不据此声称无答案收益。

全24答案字符数12123→9391（-2732，约-22.54%），确有缩短，但不能等同相关性提升。q20不再声明“不会重放”，仍需人工仔细核对其局部offset措辞；高Faithfulness不能替代对系统级保证的人工review。

当前只保留可验证的通用引用范围schema修复；默认answer_style仍baseline，focused作为明确的实验选项。没有宣称完整RAG质量通过。后续候选方向是按查询意图区分定位/字面日志与自然语言解释，并在新冻结集验证；这只是从本次已观察开发样本提出的假设，不直接拼接各类别最优分数冒充新实验。

最终全量 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-ragas-clean-wxav1le_/bin/python -m pytest -q` 为585 passed、0 failed、6 warnings、48.80s（584→585新增1范围绑定用例）。回归在真实模型运行结束后执行。阶段为Case真实执行通过、回归通过，但回答策略整体优化未通过，未启用生产。

本轮全部文本调用205次（专项30、失败v2 25、q05回归6、完整v3 144），共907402tokens；包含失败及回归成本，费用N/A，不只报告最终成功部分。每轮产物独立保存。
