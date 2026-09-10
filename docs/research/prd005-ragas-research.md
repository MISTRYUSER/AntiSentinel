# Ragas 0.4.3 接入资料与验证边界

本轮实际下载并检查0.4.3 wheel、在隔离环境导入和运行ID指标。来源事实与本项目决定分开记录。

| 一手来源 | 上游事实 | 本项目采用方式 |
|---|---|---|
| [0.4.3 Context Precision源码](https://github.com/vibrantlabsai/ragas/blob/v0.4.3/src/ragas/metrics/_context_precision.py) | IDBasedContextPrecision使用相关唯一ID/检索唯一ID，检索为空返回NaN | 不替换固定分母5的P@5，保留独立名称与有效样本数；NaN输出null |
| [0.4.3 Context Recall源码](https://github.com/vibrantlabsai/ragas/blob/v0.4.3/src/ragas/metrics/_context_recall.py) | IDBasedContextRecall比较ID集合，reference为空返回NaN | 每次观测保留样本；无答案Recall单独标未定义，不填满分 |
| [Faithfulness官方文档](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/) | 衡量回答声明是否获检索上下文支持，依赖response、retrieved_contexts及评估模型 | 当前没有真实回答与授权裁判，不构造假回答，也不报告Faithfulness分数 |

ID指标在0.4.3使用公开的`ragas.metrics`旧API；实际探针确认`collections`没有导出这两个ID类。故锁定0.4.3并单独处理弃用提示，不凭提示构造不存在的导入路径。

初次不加版本约束安装时，LangChain Community0.4.2移除了Ragas仍导入的vertexai模块，导致导入失败。重新创建隔离venv并固定LangChain0.3.27、Core0.3.80、Community0.3.31、OpenAI适配包0.3.35、Instructor1.12.0后，真实测试和pip check均通过。主解释器未安装这套依赖，项目通过`ragas-evaluation`可选依赖声明。

本地ID路径在导入前设置RAGAS_DO_NOT_TRACK=true，并关闭LangSmith/LangChain追踪；不创建模型client。网络阻断单测执行了真实Ragas，验证该路径无需网络。不代表其它Ragas指标均离线。

本项目的Ragas输出先复核原报告的语料/查询/标签指纹、观测数量、来源Scope与hash；检索失败或重复ID不被Ragas的集合去重掩盖。原始Ragas结果与项目质量门槛分离。标签仍为assistant建议稿，不称人工标注真值。
