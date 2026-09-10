# 固定代码语料：真实回答与 Ragas 答案评估

后续实验见[回答相关性专项](ANSWER-FOCUS.md)：统一focused策略在完整集相关性退化，未切换默认；通用引用行号schema修复保留，最新回归585项通过。本文v2仍作为答案基线，不用后续分类最佳值替换它。

日期：2026-09-08。范围为独立Case，不是生产Runtime发布。保持既有SQLite事实与Milvus索引、Qwen3.7 Flash1024维、DeepSeek服务和已授权语料范围。

## 实际数据流

从冻结Git blob重新构建并核验corpus，与Flash index报告指纹匹配；每条查询实时Flash编码，通过现有hybrid服务与SQLite二次权威校验取前5候选。按排名最多选择4个完整chunk、累计不超过32768 UTF-8字节，先检查预算与source hash，再落盘源码和SQLiteEvidenceStore。选择数量不足时保留incomplete，避免误称看过完整源码。

以实际披露的4片段以内上下文调用DeepSeek strict工具生成真实中文回答，返回status、response、文档ID及披露片段行号；代码本地提取原文，并关联持久Evidence ID。这个回答不是先前support-judge的reason。未启用尚未生产验收的support过滤。

回答和完整实际披露上下文先写answers.jsonl，然后交给真实Ragas0.4.3：Faithfulness提取回答声明并判定上下文支持；ResponseRelevancy（旧API名称answer_relevancy）生成3个反向问题并用已有Flash编码计算相似度。无答案分组独立汇总，空值/错误保留分母数量，不把拒答当作完美指标。

指标依据：[Ragas Faithfulness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/)、[Response Relevancy](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/answer_relevance/)。使用已固定0.4.3的legacy SingleTurnSample接口，当前可调用但官方已标记后续迁移；不是手写替代指标。

## Case 与预算

基线：该语料真实答案/Ragas答案完整Case=0；先验证q01精确查询与q21无答案查询，再原参数运行全部24查询（20有答案、4无答案）。三个执行成功指标：计划查询完成率100%、回答引用绑定/原文校验100%、持久Evidence重开hash校验100%。错误不得作为无答案；回归测试失败为0。指标质量筛查目标可参考Faithfulness≥.90、Answer Relevancy≥.80，但标签与最终验收尚未review，因此报告固定quality_pass/case_pass=false，不提前宣称RAG质量达标。

每个Case预算600秒（生成+多次Ragas判别远多于原检索Case）、最多200文本模型请求、文本HTTP无自动重试，Ragas传输层RunConfig max_retries=1代表一次尝试。Flash沿用已有适配器最多2次重试，实际HTTP attempts单独记录。Ragas自身解析修复可能产生额外prompt，全部会进入调用审计，不把实际次数估算为固定值。单请求30秒；父进程630秒终止上限，应用总时限前后检查，不假装单次网络read timeout是硬wall-clock终止。遵循用户要求，不设置客户端max_tokens。

Ragas遥测和LangSmith tracing禁用；模型请求/响应只保存私有Case目录，不打印密钥、不落盘Authorization头。模型请求名deepseek-chat，实际响应model逐条记录；答案与评估同模型，存在同模型评估偏差，需独立裁判/人工抽检后才能作更强结论。Ragas相关性额外调用Flash，文本为原查询与生成的问题；不扩大源码语料范围。

## 运行与产物

```sh
python scripts/run_rag_answer_case.py \
  --repository <私有语料根>/repo --manifest <私有语料根>/manifest.json \
  --index <私有语料根>/flash-baseline --output <不存在的新目录>
```

私有语料根：`/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-baidu-source-poitl0bv`。pilot输出rag-answer-pilot-v1；完整输出rag-answer-full-v1。

每次保存sources/、evidence.sqlite、model-calls.jsonl、answers.jsonl、evaluated.jsonl、report.json。模型调用审计含query/stage/model/usage/请求与原始响应，输出报告记录源语料和实现指纹。SQLite Evidence使用case_only元数据和独立文件引用，不冒充已有incident绑定与CodeMap generation发布流程的生产集成。

## 验证记录

新增rag_answers.py、run_rag_answer_case.py、test_rag_answers.py共3实现/测试文件。先见模块不存在红灯1；实现后29 passed/0 failed/1.56s；新增预算前存证测试后focused `python -m pytest tests/test_rag_answers.py tests/test_code_support_judge.py tests/test_code_retrieval_evaluation.py -q` 为30 passed、0 failed、1.36s。

真实pilot：2/2观测、错误0、7份持久Evidence重开hash通过、28.05秒。q01 Faithfulness1.0、Answer Relevancy.875861；q21 Faithfulness.583333、Relevancy0。12次文本请求45000tokens，6次Flash请求289tokens，实际模型deepseek-v4-flash。execution_pass=true，quality_pass=false；样本数量不足，不外推整体质量。

## 评测上下文修复与恢复

完整v1运行中发现评估接线缺陷：答案生成请求包含source_identity路径与source_lines行号，但Ragas只收到裸text，造成路径/行号声明假低分。例如q08 Faithfulness .52381的一些负判据明确称“上下文未提供文件名/行号”。这是评测输入不一致，不能当作回答优化基线。

主动停止v1（退出-2），保留14条已生成答案、13条已评分记录和82条模型调用审计；这些旧Faithfulness分数作失效诊断保留，不选择性展示。pilot同样使用旧接线，Faithfulness结果不作最终质量结论。

修复为 `disclosed_contexts(packet)`，直接使用生成请求中同一份candidate JSON（包含原文、路径、scope、Evidence ID和披露行号）传给Ragas，测试逐对象校验一致性。新增 `--resume-answers`：从冻结Git重建上下文并再次存证，校验旧/新候选内容、query、Evidence绑定及引用后复用实际答案，不重复生成14条答案；所有24条重新评分。v2输出rag-answer-full-v2，旧答案输入SHA256进入报告，客户端不设输出token上限。

该修复不改变生成提示词/已有答案/标签，只修正评估所见证据。新增预算前存证测试后focused30 passed、0 failed、1.50s。恢复过程独立存储Evidence与评分，不覆盖旧产物。

## 完整v2结果

命令为上方完整Case命令另加 `--resume-answers <私有根>/rag-answer-full-v1/answers.jsonl`。24/24观测、错误0、deadline_exceeded=false，268099.87ms；14份答案从已校验产物恢复、10份新生成。20条label_answerable均answered，4条无答案均insufficient_evidence。恢复不是重新生成后挑选高分，旧输入SHA256为1f3c59be172a160f9297225d4f92fdd4a2a13ee29c0f0cc9a84ca9a715f47509。

| 指标 | 既有完整答案基线 | v2实际 | 阈值 | 差值 | 证据 | 结果 |
|---|---:|---:|---:|---|---|---|
| 完整样本数 | 0 | 24/24 | 24 | +24 | report.json | 通过 |
| 引用可追溯 | N/A | 59/59 | 100% | N/A | independent-check.json | 通过 |
| 生成/评估披露一致 | v1不一致 | 24/24 | 100% | 修复 | independent-check.json | 通过 |
| Evidence重开hash | N/A | 44/44 | 100% | N/A | evidence.sqlite及sources | 通过 |
| 有答案Faithfulness | N/A | .973666 | ≥.90筛查 | N/A | evaluated.jsonl | 达到筛查值 |
| 有答案Answer Relevancy | N/A | .778296 | ≥.80筛查 | N/A | evaluated.jsonl | 未达到 |
| 执行错误 | 0目标 | 0 | 0 | 0 | report.json | 通过 |

上述答案指标均为20个定义值，undefined/error0。4无答案独立组：Faithfulness .897436、Answer Relevancy0，定义值各4；不能把拒答相关性0当作召回失败，也不能拿该组抬高有答案平均。所有Ragas评估是真实0.4.3调用，没有手工替换分数。

按查询类别：exact6条Faithfulness1.0/Relevancy.806641；semantic10条.967332/.814160；error_log4条.95/.646117。最低相关性样本q18 .512581、q17 .585214、q14 .651155、q20 .675533。短错误字符串和完整自然语言问题的反向问题相似度口径不同，后续需要逐条核验回答是否回应意图，不能仅把低分归因于生成能力或修改查询来刷分。

96个披露片段出现次数、合计197010源码字节；每样本≤4片/32KiB，44份唯一Evidence。独立读回确认24份生成/评估上下文一致、24份预算合规、59引用原文与Evidence身份匹配、SQLite integrity_check=ok。实际回答可读产物为rag-answer-full-v2/ANSWERS.md，源码引用链接到各自持久片段；仍未人工review。

v2新调用130次DeepSeek（prompt361344/completion37308/total398652tokens），58次Flash（3005tokens），对应10份新答案、24份Faithfulness评估和24份strictness=3相关性评估。未观察到额外解析修复请求；所有实际模型deepseek-v4-flash，同模型生成/裁判。pilot另12次45000tokens，已中断v1另82条文本调用/277553tokens，本轮文本累计224次/721205tokens，不隐藏修复开销；中断v1的Flash累计用量没有最终报告，标N/A，不能据此估算账单。费用均N/A。

report.execution_pass=true，quality_pass=false，case_pass=false；这是端到端离线答案Case完成，不是PRD生产验收通过。最终质量仍需标签review、独立盲测、异模型/人工抽检、性能目标与生产Runtime集成。

最终全量命令 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-ragas-clean-wxav1le_/bin/python -m pytest -q`：584 passed、0 failed、6 warnings、29.57s；581→584新增3项，通过率100%。全量在实际Ragas运行结束后串行执行，不将pytest时长当作RAG延迟指标。阶段为本Case真实运行通过、回归通过，质量/生产及用户review未通过。

## 生产工作仍未完成

生产Runtime Model/Tool/Context链路集成、完整发布manifest与投影隔离、Embedding worker、Graph四路评估和新的用户review标签/盲测仍未完成。本Case只填补“真实回答、实际上下文、实际Ragas答案指标”证据缺口，不据此改变整体PRD完成状态。
