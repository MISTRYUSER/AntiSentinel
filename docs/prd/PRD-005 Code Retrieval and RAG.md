# PRD-005 Code Retrieval and RAG

- 日期：2026-09-07
- 状态：当前实现合入主分支；剩余生产验收、质量验收及图优化统一 pending（2026-09-10 用户决定），暂停继续实施。默认 hybrid 不变；不标生产就绪或 RAG 质量通过。详见 `docs/validation/prd005-stage5/OPEN-ITEMS.md`。
- 优先级：P0
- 依赖：PRD-005A发布的代码快照、源码回读、Incident绑定；PRD-003 Evidence/Trace；PRD-004工具接入。
- 已明确方向：固定Commit事实DB + 独立Embedding投影；全文与向量召回结合代码关系，所有答案回到源码Evidence。

## 1. 用户问题与目标

开发者输入错误码、日志片段或业务问题，系统应在指定部署版本中找到相关代码，沿可信关系扩展，并提供可回读的Commit、路径、行号和Evidence。用户无需预先知道函数名或chunk_id。

Code Map回答“代码在哪里、结构是什么”；本期回答“哪些代码与当前问题相关”。不把相似度当成事实正确性，不把静态调用边当实际请求执行记录。

## 2. 基线与前置项

本会话对9cc3ef1已运行 `/Users/xuewentao/miniconda3/bin/python -m pytest -q`：443 passed、7 warnings、17.68秒。Code Map真实企业Case的2464节点/4930边/2464 Chunk为用户交付记录，本轮未复跑，不作为RAG正确率。

retrieval/仍为骨架；已有Memory FTS/向量适配和RRF可参考，但代码不得写入memory_records或触发偏好抽取。现有LocalVectorMemory同时写Memory事实并用Python遍历向量，不能直接用于代码向量库。

前置：005A超时后drain线程泄漏须在长期后台联调前修复，验收连续超时后新增线程数回到0。图扩展前必须验证关系作用域与歧义；当前解析关系不因标记resolved就免除验证。未达门槛时关闭该类关系扩展，保留全文/向量功能并显示graph_degraded。

新功能基线：代码检索Case0；Precision/Recall/MRR/P95、Embedding成本及向量库容量均N/A（尚未实现）。

## 3. 本期范围

1. 消费已发布Code Map，生成代码全文投影及Embedding输入；同版本可重建。
2. 精确标识符检索与FTS5/BM25全文检索。
3. 异步Embedding生成、独立向量存储、版本及生成状态管理。
4. 关键词/向量混合召回、去重和排序。
5. 有预算的图邻居扩展、来源回读及Runtime工具接入。
6. 固定数据集评测与降级/恢复验证。

本期GraphRAG指“召回后沿代码关系扩展并生成有来源回答”。不做LLM建业务图、社区摘要、全仓LLM预总结、自主多轮检索规划、reranker训练、Plan/DAG，也不部署Sourcegraph。现有AST/Tree-sitter产物通过适配器接入；SCIP可以后续成为另一事实输入。

## 4. 两份数据与身份

| 存储 | 必需数据 | 用途 |
|---|---|---|
| SQLite事实与全文投影 | 已有Code Map；CodeSearchDocument；FTS5；任务、attempt、发布状态、manifest、Evidence | 精确过滤、BM25、任务恢复、来源验证 |
| 独立Milvus向量索引 | vector、point_id、repository/snapshot/generation/chunk/document ID、input_hash、模型/维度/模板版本 | 预过滤后的向量检索 |

原始Code Map不修改。CodeSearchDocument含repository_id、snapshot_id、published_generation、commit_sha、node_id、chunk_id、path、symbol、language、原始source_hash、embedding_input_hash、projection_revision。检索文档ID绑定来源代次及投影版本；一个Node可以有多个Chunk。

Embedding输入采用固定版本模板：路径、符号名/签名、所属范围、注释及源码片段。业务描述首期不由LLM生成。超长或不可独立解码的Code Map Chunk须做有版本的检索切片，保留父chunk_id及精确字节范围，不静默改变来源hash。范围重叠的模块/类/函数不得无限重复占据结果。

生成状态及API调用状态以SQLite为准：pending/running/retry_wait/ready/failed；保存attempt、started/completed、lease owner、模型、维度、input_hash、Token/耗时及脱敏错误。向量库payload不保存凭证；源码可只保留在事实库。代码调用关系属于Code Map边，与Embedding API调用状态分开。

## 5. 构建、更新与双库恢复

- 只消费明确发布代次，默认ready；partial仅在显式许可且索引记录完整性不足时进入，所有返回传播incomplete。
- 通过持久化发布事件或对账扫描发现缺失投影；重复事件幂等。不上第二套Git轮询。
- 先持久化期望文档和任务，再生成向量。point_id由document_id、model、dimension、template和projection版本生成；每个版本组合使用独立collection，幂等upsert后逐项读回核对再标ready。
- 向量已写但DB未确认：重试读回并确认；DB任务存在但向量未写：继续生成/写入。异常退出不能使任务永久丢失。
- lexical_status、vector_status、graph_status分别记录。Code Map ready不等于Embedding ready；向量失败不使源码不可查。
- 每通道按代次发布manifest；构建中的向量不对查询可见。统一入口先检查SQLite channel=ready，再读取固定发布代次并回查DB，不混入旧模型或半成品。
- 同input_hash、模板、模型和维度可在授权范围内复用向量计算结果，但新Commit必须建立自己的来源关联。不得仅凭source_hash复用包含路径/签名的旧输入向量。
- 删除/重命名后新版本排除旧关联，历史快照仍可查询；模型升级另建索引版本并切换，不原地混合维度。
- 管理员可重试、停用通道、重建；模型只获得查询权限。配额触限暂停新投影，不删除诊断已引用来源。

## 6. 查询行为

输入query、服务端授权的repository/snapshot/generation范围、模式keyword/vector/hybrid/graph、top_k及预算；范围从诊断绑定取得，模型不能自行扩权或回退最新Commit。

1. 精确通道匹配完整符号、路径和错误标识符；Commit/ID采用精确过滤，不参与模糊排名。
2. FTS5索引path、symbol、identifiers、comments、code。保留原始错误码，另生成camelCase/snake_case检索词；中文注释/问题必须有冻结分词策略与测试，不能假设默认分词已覆盖。
3. 向量通道在候选生成前限制授权仓库、代次、模型及维度；回查DB再次验证。不可只做全库top-k后过滤。
4. 混合模式用确定排序融合，不直接相加BM25距离和向量相似度；记录通道贡献。FTS5 bm25升序，越小越相关。
5. 图模式在种子召回后按许可关系/方向扩展，保留种子与扩展原因。默认只使用经验证的结构关系；unresolved保留提示，不伪造目标。限制深度、节点/边数及最终源码预算。
6. 返回候选的来源范围、通道、排名、扩展路径、截断/降级原因、各通道状态。候选不自动成为Evidence；实际选中并回读核验后才生成Evidence进入Context。

建议初始预算：每通道top30，最终top5，最多5个图种子、深度1、扩展20节点/40边；Context最多4个片段/32KiB。Graph成本及query embedding计入端到端耗时。具体默认值在设计评审后冻结。

## 7. 失败与运行边界

vector不可用时hybrid可返回keyword结果并标degraded；vector-only明确报错，不假装执行成功。无结果与索引缺失、超时、模型版本不匹配分别返回。源码hash错误或跨域结果拒绝进入Context；不能靠降级接受不可信证据。

后台投影单attempt默认120秒，瞬时错误最多2次重试；认证/权限错误直接blocked，需修正配置后恢复。数据库和网络等待不能持有SQLite长写事务。查询建议总预算10秒；向量通道超时后hybrid在剩余预算内返回可用结果，超时值需实测再调。

使用已有Embedder接口的适配能力，但复用外部Embedding服务必须先确认代码可发送范围及凭证配置。本稿不调用模型、不发送企业源码、不启动新服务。

## 8. 阶段与交付

| 阶段 | 交付 | 验收 |
|---|---|---|
| 5.1 文本投影与全文检索 | 固定快照文档、精确标识符、FTS5/BM25、离线runner | 身份/来源/版本隔离、中文及错误码检索、词法基线 |
| 5.2 Embedding与向量库 | 任务、输入模板、幂等写入、对账、向量查询 | 双库故障恢复、旧向量隔离、真实存储读回 |
| 5.3 混合检索 | 融合、重复范围处理、降级、统一入口 | 关键词/向量/混合对照及成本 |
| 5.4 图扩展与Evidence | 受控邻居扩展、源码回读、Loop/checkpoint | 关系正确、引用可恢复、预算与部署版本一致 |
| 5.5 累计评测 | 冻结语料/查询/标注、报告、操作说明 | 所有前序Case累计回归，失败公开 |

每阶段交付可执行Case并串行执行针对性测试→确认的真实Case→用户review；runner从5.1开始扩展，不能留到最后才创建。

## 9. 量化验收

冻结至少20条中文/英文查询，覆盖错误码、符号、业务语义、跨函数定位、无结果和版本歧义；固定代码Commit、查询hash、人工相关性标注与top-k=5，运行前冻结，不根据成绩删题。

精确标识符子集Hit@1=100%；跨repo/Commit/generation泄漏0；源码hash及引用完整率100%；未披露引用0；重复事件新增逻辑任务0；双库各故障点恢复后期望文档/向量一致率100%；连续超时新增常驻线程0。

有相关答案的查询报告宏平均Precision@5、Recall@5、MRR和每条最小值，标准Precision@5分母固定5；少于5个相关答案的查询说明其上限，不修改分母粉饰成绩。无结果集单独报告误命中率。默认效果目标Precision@5≥0.80、Recall@5≥0.60、MRR≥0.80；若标签集使目标不可达，在运行前评审调整，不能测后换门槛。

比较keyword-only、vector-only、hybrid、hybrid+graph四组；相同代码范围与最终上下文预算，图扩展使用的额外候选预算单独报告。报告P50/P95、Token、Embedding批次/失败数及成本。图增强只有在固定集证据支持时才声称提升。

同模式同fixture重复5次建立性能基线，后续中位耗时不劣化超过5%；优化实验P95不超过对照1.20倍。跨模式额外成本如实报告，不套用同模式回归门槛。

所有当前443项和新增测试通过；旧Memory/Session数据保持不变。真实Case需独立目录、SQLite和向量collection；报告输入规模、耗时、输出/持久化/完整性数、后台异常、t1/t2及差值；未全部验证不能case_pass=true。

## 10. 本轮待决策与完成定义

用户已确认采用Milvus作为向量索引；SQLite保留代码事实、FTS5全文投影、任务、manifest和Evidence。先以Milvus Standalone/Lite完成正确性验证。5.2实施设计锁定PyMilvus 3.0.1、milvus-lite 3.2.1、COSINE/FLAT；生产固定为Milvus Standalone单节点容器、服务端3.0.x、4 vCPU/8 GiB/50 GiB SSD/Strong读语义，不部署Distributed，容量仍须在5.5真实Case复核。首期Embedding模型沿用现有适配接口，实际模型版本/服务与可发送代码范围需在5.2 Case前明确。

完成定义：在固定双Commit语料及授权真实仓库上，走通快照→全文/向量投影→混合/图检索→Evidence→回答与恢复，冻结评测达标或明确未达标，双库故障恢复和旧业务回归通过。未完成上述验收前不标Done。
