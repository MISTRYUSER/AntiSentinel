# 固定源码检索诊断集 v1

这份manifest供用户review，标签状态为`proposed_for_user_review`。标签由assistant阅读固定Git源码后提出，尚不是人工确认的ground truth。语料只含manifest列出的5个文件，各自取两个真实Commit；不表示全仓正确率。

- current：`c89b679db7080b25590ef3cc99e4cb4116f41f95`
- previous：`21ad8faf354c97c83fdc29d7525ba24e81106077`
- 10份Git blob、50,833字节，解析为82个CodeSearchDocument。
- 24条不同查询：完整标识符5、错误码5、中文语义4、英文语义1、跨函数2、版本限定3、无答案4。
- top-k固定5；有答案查询20条，无答案4条单独计算误命中率。五轮测量不增加独立query数。
- 每条标签按“函数直接实现所问行为”标注；标注函数的所有chunk相关，父class不自动继承子方法相关性。范围限定在已列出的5个文件及所选Commit。

只读预检：

```bash
/Users/xuewentao/miniconda3/bin/python scripts/evaluate_code_retrieval.py --preflight
```

预检验证每份源码SHA-256、每个标签的path/symbol是否存在、查询是否重复，以及Precision@5理论上限。当前上限0.25，小于默认0.80，必须在运行前披露；不改分母或复制相同结果提高分数。

manifest SHA-256：`6473b9ae5f596fb3dc2287a747ad24f97da15165ce7fc77f7a1cd97d2adab3d0`。运行报告还保存语料、查询和解析后标签各自的SHA-256。任何变更都需要新的版本和review；运行时复制原始manifest到结果目录，旧报告保持不变。

CLI默认只预检或在显式`--diagnostic`下测量keyword。`--diagnostic-vectors`额外运行Milvus Lite向量及hybrid，向量由`diagnostic-token-sha256-64-v1`生成：固定SHA-256桶、64维、L2归一化的未训练词项特征，不使用标签，不调用模型。它只能帮助检查数据存取、过滤和融合，不能证明真实Embedding语义质量。`hybrid_graph`组明确未提供；不得以keyword图模式冒充。
