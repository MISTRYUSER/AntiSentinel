# PRD-005 代码检索设计

日期2026-09-07；architectural；Milvus/SQLite存储决策已确认；实现仍需按阶段验收。输入：[PRD-005](../../prd/PRD-005%20Code%20Retrieval%20and%20RAG.md)。

## 1. 目标、非目标与基线

固定已发布Code Map代次→独立全文/向量投影→混合排名→受控图扩展→来源回读→Evidence与模型上下文。保留事实与推断区别，不接入完整Sourcegraph服务、不生成LLM业务图、不做多轮检索规划。

基线9cc3ef1，本会话pytest 443/443通过、7警告、17.68秒；代码RAG Case0，吞吐/相关性/向量容量N/A。005A drain线程泄漏是长期联调前置，关系精度需单独验收。当前设计成功指标：候选方案3个、阶段映射5/5、所有候选返回固定来源身份；失败指标为越域/错误来源0；回归沿用443项全部通过。

## 2. 已确认存储决策与历史候选

2026-09-07用户确认：采用Milvus作为向量索引；SQLite保留代码事实、FTS5全文投影、任务/attempt、manifest和Evidence。向量索引是可重建投影，不是发布状态或证据的权威来源。不迁移Code Map/Memory数据库。

先使用Milvus Standalone/Lite完成正确性验证。5.2实现锁定PyMilvus 3.0.1、milvus-lite 3.2.1、COSINE度量和FLAT索引；Lite使用独立文件。生产部署形态固定为Milvus Standalone单节点容器，服务端3.0.x与客户端3.0.1匹配，4 vCPU、8 GiB内存、50 GiB SSD，Strong读语义；不部署Distributed。资源值是本期的部署基线，必须在5.5容量Case中用真实语料复核，不得据基线宣称容量达标。两种验证形态由同一MilvusAdapter承接，验证报告必须注明实际形态，未跑的形态不声称通过。

历史比较保留：A为SQLite+Qdrant，B为SQLite+独立本地向量表，C为PostgreSQL/pgvector。三者均非当前实施选择，用户已以Milvus决策替代此前推荐。

## 3. 模块职责与数据流

- CodeCorpusReader：按授权snapshot+generation从Code Map读取文件、符号与原始Chunk，验证hash；不执行Git同步。
- SearchDocumentProjector：生成有版本的检索文档、标识符词项及Embedding输入，维护原始字节范围。
- SQLiteCodeSearchStore：全文表、文档、任务、attempt、代次manifest及对账游标；与Memory表独立。
- CodeEmbeddingWorker：在短事务之外调用Embedder/VectorIndex；支持幂等恢复、重试与明确终态。
- VectorIndexPort：upsert/get/search，search要求完整范围过滤；MilvusAdapter负责PyMilvus调用、主键及显式标量字段编码。
- KeywordRetriever/VectorRetriever/Fusion：各自返回相同候选DTO，Fusion只处理排名。
- GraphExpander：在同代次内按经验证的边扩展，保存seed和edge依据；不让不确定关系变确定。
- CodeRetrievalService：预算、通道状态、来源校验、去重与最终选择；工具只接受查询条件，范围由服务端注入。
- SourceEvidenceService：复用并修复必要的范围/截断信息，按实际披露片段生成证据并恢复。

```text
已发布Code Map → 检索文档/SQLite任务 → FTS代次
                                → Embedder → Milvus → DB确认/向量代次
问题+服务端Scope → 精确/FTS + 向量 → RRF → 可选图扩展
                                  → 来源回读 → Evidence → Context
```

同一运行固定Scope(repository_id,snapshot_id,published_generation,commit_sha)。检索版本另外固定projection_revision、embedding_model_revision、dimension和input_template_revision，不用一个latest覆盖这些身份。

## 4. 双库一致性和错误语义

SQL记录文档与待处理任务后提交；网络调用在事务外。point_id由`document_id + model_revision + dimension + template_revision + projection_revision`的规范字符串SHA-256生成，编码为Milvus VARCHAR主键，禁用auto_id；collection名称同样包含版本组合摘要。已有collection在加载前校验字段、主键、维度、index和COSINE度量。向量upsert成功并读回核对input_hash/维度/范围后，再以任务lease条件更新DB ready。

若Milvus成功但SQLite确认失败，重试同point_id并对账；若DB任务提交后崩溃，重领任务；如果查询期间向量丢失则vector_degraded，不能虚报完整通道。manifest固定预期文档集合，全量核对后切换发布指针；正在构建的代次不参与查询。`search()`必须读取SQLite channel状态，非ready直接拒绝；对账一次只接受单一Scope。

FTS与向量各自发布。hybrid可在向量缺失时显式降级；vector-only返回vector_unavailable。错误区分scope_mismatch、snapshot_missing、index_building、index_missing、embedding_auth_failed、embedding_timeout、vector_unavailable、input_hash_mismatch、budget_exceeded。凭证和源码原文不写Trace。

删除/旧版本通过manifest控制可见性；不依赖立即物理删除远端point保证权限。历史诊断绑定仍可回读，GC本期不执行。重试总attempt最多3，领取检查next_attempt_at；失租旧Worker不能确认ready。超时终止与资源清理由前置修复的进程监督方案承担。

## 5. 召回、排名与图边界

FTS建议unicode61处理代码词项，辅以显式标识符拆分及中文trigram候选；短中文查询走明确字面匹配后备，所有结果依旧受同Scope约束。最终tokenizer与列权重在5.1离线评测前冻结，中文不能仅依赖默认空白切词。

先按Scope过滤再做每通道top30。精确标识符命中作为显式优先级，其余采用RRF(k=60)，每通道每文档只计一次；相同分数用document_id稳定排序。排序算法可参考已有纯RRF，使用代码候选DTO，不写Memory ID字段。重叠片段先按来源范围去重，再做预算选择。

图扩展保留前5种子，默认深度1、节点20/边40上限；新增节点以seed排名、跳数、稳定ID排序，单独标记graph通道，不和BM25原始分数相加。最终top5为建议展示数，Context最多4片/32KiB；未披露候选不能作为答案引用。

图上线门槛：同名跨文件、参数/局部变量遮蔽、类方法、跨语言混合仓库、未知外部调用等负例必须无伪确定边。必要时仅启用contains，其他关系标disabled/unverified，不能用已有边数量作为精度证明。

## 6. 安全与恢复边界

代码只来自服务端登记仓库和已发布代次。向量查询的标准预过滤条件必须含完整Scope及向量版本身份，回查SQL核对来源hash和任务发布状态；对账不会把越域point补进合法结果。用户输入作为检索文本处理，FTS表达式单独转义/构造，不接受任意SQL。

外部Embedding意味着传输代码；5.2真实Case前确认提供方、模型、凭证及可发送仓库范围。离线阶段FakeEmbedder只验证协议，不证明语义效果。原始代码与生成文本始终标来源；不把向量近似匹配或静态calls解释为实际故障因果。

## 7. 阶段与验收映射

| 阶段 | 文件区域 | 基础测试 + 真实Case |
|---|---|---|
| 5.1 | retrieval文档/keyword，persistence全文适配 | 双Commit隔离、错误码与中文固定集、SQLite重开；创建累计runner |
| 5.2 | retrieval投影任务，adapters向量，Embedder边界 | 独立Milvus collection（Lite使用独立文件）、写前/写后/确认失败点、模型版本切换、资源回收 |
| 5.3 | retrieval融合/facade | 冻结至少20查询四通道对照，降级及空结果 |
| 5.4 | retrieval图与tools，SourceEvidence/Context | 带负例的关系门槛、Loop按需来源、checkpoint恢复 |
| 5.5 | evaluation与验证报告 | 累计前序Case、旧Memory回归、成本/延迟/相关性报告 |

每个Case预算120秒、最多2次重试；扩大真实语料预算前明确原因。语料/查询/标签及版本必须冻结。报告输入文件/字节、生成文档/向量数、持久化读回数、完整性数、后台异常、t1/t2和耗时。检索指标与门槛见PRD第9节；数据不足时标N/A，不宣称收益。

当前新Case执行0、向量服务启动0、外部Embedding调用0；本稿未生成实施计划。存储已选择Milvus；其余设计评审后按5.1开始逐阶段计划和review。

参考：[研究记录](../../research/prd005-retrieval-research.md)。

## 8. 本轮评审补充（未定稿）

本轮HEAD仍为9cc3ef1，重新执行 `/Users/xuewentao/miniconda3/bin/python -m pytest -q` 得443 passed、0 failed、7 warnings、19.51s。历史17.68s→19.51s，增加1.83s/10.35%；只有单次全量测试结果，不能代替同fixture五轮性能中位数。生产Python文件181、测试文件95；代码检索Case仍为0。第1节17.68秒属于历史记录。

以下4项在实施前必须明确，不能从现有接口推断已满足：

1. **固定代次读取**：`CodeMapQuery`的符号/邻居接口仅接受snapshot，不能直接作为代次隔离的读取端口。CodeCorpusReader必须从已发布代次读取文档及边；SourceEvidenceService已有诊断绑定→generation→read_generation_chunk路径，检索切片应沿用此身份语义。发现绑定与请求不符即拒绝，不回退当前快照表。
2. **任务与通道状态分离**：文档任务沿用pending/running/retry_wait/ready/failed；认证错误记录failed及retryable=false，通道另置blocked，管理员修复配置后显式重试。blocked不静默成为第6种任务状态。部分来源用incomplete表示，预算截断用truncated表示，二者分别传播。
3. **来源与上下文边界**：候选只携带定位信息；选中后核验父chunk hash及切片字节范围，Evidence存证后由SourceContextSlice披露最多4片/32KiB源码。Context不得直接拼接Evidence存储原文。引用绑定实际披露范围，不能用父chunk全部范围冒充已披露内容。
4. **效果阈值可达性**：至少20条冻结查询，相关性标签按最终去重文档定义；运行前计算宏平均Precision@5上限 `mean(min(5, relevant_count)/5)`（仅有答案集）。若上限低于0.80，先评审语料或阈值，不通过扩大同一源码重复切片提高分数。无答案查询单独统计误命中率。

5.1可先处理已发布的离线快照；长期后台联调必须先验证005A连续超时新增常驻线程为0。5.3比较keyword/vector/hybrid三组，5.4接入graph后补齐四组，5.5执行累计评测，避免在图尚未接入时虚报四组结果。

当前存储已由用户确认采用Milvus，模型/代码外发范围留在5.2真实Case前确认。此次不生成实施计划。成功指标：候选3个（阈值≥2）、阶段映射5/5（阈值100%）、回归443/443（阈值100%）；失败指标：回归失败0；性能回归指标：五轮中位数劣化≤5%，当前N/A。未运行真实Case，不据上述数字推进实现阶段状态。

## 9. Milvus正确性验证边界

以下为本项目约束，不是对上游的兼容性保证。官方资料见研究记录。

- SQLite保存唯一权威的任务和发布manifest；Milvus保存vector、确定性point_id及repository/snapshot/generation/document/chunk/input_hash/模型/维度/模板/投影版本标量字段。Evidence及原始源码仍由SQLite管理，不复制凭证到Milvus。
- 使用标准filtered search，在候选生成前组合全部授权及发布版本条件；不接受模型提供的原始过滤表达式，不依赖partition key充当安全隔离。SQL二次核验仍为硬门槛。
- 模型/维度升级使用独立collection与SQLite manifest切换。发布前幂等upsert、显式读回、比对主键集合及字段，缺失或不一致不得置ready。不能以upsert确认、flush返回或collection统计数代替逐项核验。
- 5.2冻结客户端与读一致性配置；首次正确性验证优先采用Strong读语义，并验证关闭/重开后的持久化。Lite上的成功不能代替Standalone的断连、超时、服务重启与跨客户端可见性验证；网络故障项在Lite报告中标N/A，留给Standalone真实Case。
- Lite文件独立于事实SQLite路径。官方当前页面列有索引、分区和一致性限制，版本尚未锁定，因此不得把Lite的耗时或资源量外推为生产容量；5.2选择实际版本后复核能力矩阵。
- 成功指标：来源/版本完整率100%、期望文档与向量集合一致率100%、重启读回完整率100%；失败指标：跨域结果0；回归指标：同形态同fixture五轮中位耗时劣化≤5%。双库故障点至少覆盖写前、向量写后DB确认前、发布前退出，恢复后重复逻辑任务增量0。

本轮仅确认存储决策；真实Case0、服务启动0、Embedding调用0，生产容量及检索效果N/A。5.2仍需给出可执行Case并经用户确认后运行，不将本次选型确认扩大为代码外发或生产部署许可。

## 10. 5.2 Review修复结论

5.2复验已将以下约束落到代码和测试：canonical point ID不随node_id复用；版本组合生成独立collection名；已有collection schema/index不匹配即失败；Milvus返回嵌套`entity`可解析；SQLite channel是查询硬门控；reconcile拒绝跨Scope输入；任务记录包含attempt、started/completed、duration、错误和lease owner并拒绝未知task；runner严格关闭首个client后再重开。Lite Case与独立两库核验见[阶段报告](../../validation/prd005-stage2/README.md)，Standalone、真实Embedding及容量仍未验证。

## 11. 5.3 混合召回设计

`CodeRetrievalService` 接收服务端 Scope、模式、查询向量和预算，keyword 通道调用 SQLite FTS，vector 通道调用带 channel store 的 MilvusAdapter；hybrid 先分别取候选，再用固定 RRF(k=60)融合。BM25 与 Milvus distance 不做直接加法，document_id 与重叠 path/source_hash 范围去重后按 score/document_id 稳定排序。vector 不可用时 hybrid 返回 keyword 候选并标 `degraded/vector_unavailable`，vector-only 返回空结果与明确错误。5.3 Case 固定20条查询，仅报告 keyword/vector/hybrid；graph 组在5.4接入后补齐。
