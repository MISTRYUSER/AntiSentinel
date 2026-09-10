# 5.5 Ragas本地评估

最新进展（2026-09-08）：已在独立固定语料Case中完成真实回答与Ragas Faithfulness/Answer Relevancy，24/24、错误0，20有答案均值.973666/.778296，584项回归通过；质量与生产验收仍未通过。详见[RAG真实答案Case](RAG-ANSWERS.md)。以下保留原ID指标阶段记录，其“未评估答案”描述仅适用于该早期运行。

已实际引入Ragas0.4.3并运行IDBasedContextPrecision/Recall；未引入默认LLM调用。安装到独立虚拟环境，使用项目`ragas-evaluation`可选依赖（建议在独立venv运行`pip install -e '.[ragas-evaluation]'`）。

入口：

```bash
python scripts/evaluate_ragas.py \
  --repository <固定语料所在本地Git仓> \
  --report <evaluate_code_retrieval生成的report.json> \
  --output <不存在的新目录>
```

输出samples.jsonl和report.json，原始检索报告与manifest不修改。每行保留mode/query_id/run；重复性能测量不扩大独立query数。`source_report_sha256`绑定输入报告，语料/标签指纹也必须相同。来源错误、重复候选和错误行标invalid_input；无检索结果precision为null，无reference结果recall为null，并记录原因与数量。

区别：项目P@5除以5；Ragas ID precision除以实际检索的唯一ID数。Ragas mean_defined仅平均有定义值，报告同时保留defined_count和undefined_or_invalid_count，不能与原始P@5直接比较。

## 实际验证

- 实际网络阻断测试与空集合/错误行测试：2 passed、0 failed、11.16s。
- 含Ragas依赖的最终全量测试：518 passed、0 failed、6 warnings、35.48s；Ragas及评测focused17/17、9.31s。
- 隔离依赖检查：`python -m pip check` 输出No broken requirements found。
- 原本地双Commit样本：360条Ragas观测，输入错误0，external_model_calls=0，execution_pass=true，quality_pass=false。
- 用户授权自行选仓后，iCode列表返回27仓，单独验证2候选read权限为true。所选Go仓53文件/39 Go/328586字节；本地选取4个逻辑文件、单Commit、63文档、24独立query、每模式5轮。
- 企业样本检索错误/Scope错误/重复均0；P@5 keyword/vector/hybrid=0.10/0.06/0.10，Recall=0.50/0.30/0.50。Ragas ID Recall=0.50/0.30/0.50，各100条有定义观测；每模式20条无答案观测Recall未定义。质量不通过。
- 企业精确符号Hit@1：keyword=1.0、vector=0.1667、hybrid=0.6667；混合模式尚不能保证所有精确符号排第一，该缺口如实保留。无答案误命中率keyword=0.25、vector/hybrid=1.0。
- 独立新进程再次打开Milvus，63个point_id唯一，63个document_id及完整Scope/hash与固定Git文档逐项一致；SQLite integrity=ok、外键错误0、memory_records0。企业仓git status --porcelain为空。

企业权限清单、完整仓名/Commit和私有语料manifest只保存到用户本地临时目录，不提交企业源码或标签到本仓：

- [权限清单](/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-icode-inventory-zy0qme5l/repositories.json)
- [选仓及独立核验](/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-baidu-source-poitl0bv/selection-and-validation.json)
- [企业诊断报告](/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-baidu-source-poitl0bv/diagnostic/report.json)
- [企业Ragas报告](/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-baidu-source-poitl0bv/ragas/report.json)

本次只读源码，未执行企业仓的业务程序/Go集成测试，未调用数据库/BigQuery/Bigpipe。诊断向量仍为未训练哈希特征，不是Embedding模型。

## 尚未完成

Faithfulness、Answer Relevancy及LLM-based context指标需要真实response、模型实际披露的context及授权裁判服务。代码仓权限不能确定评估模型服务或外发权限；用户尚未提供该信息。因此这些指标为not_evaluated，不能以ID指标替代。Graph四组对照、人工标签review、真实Embedding、Standalone/容量和合格的性能对照均仍未完成，不标PRD Done。

原本地语料的性能复测记录中，中位耗时比值keyword/vector/hybrid=1.17/1.18/1.28，未达≤1.05门槛。该复测与Ragas初始化并发，运行条件未隔离；保留原始失败记录，不据此声称性能通过或确定存在代码性能回归。
