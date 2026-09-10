# PRD-005 检索方案资料核对

日期2026-09-07。本轮阅读一手资料，未运行外部模型或向量服务。

| 一手资料 | 事实 | 本项目建议（非上游保证） |
|---|---|---|
| https://www.sqlite.org/fts5.html | FTS5提供全文匹配、分词器及bm25/rank；bm25较小更相关 | 精确身份过滤与全文排名分开，固定中文/标识符规范化策略 |
| https://docs.agno.com/knowledge/overview | Knowledge提供分块及向量、关键词、混合检索能力 | 参考读入→分块→索引→查询职责；不复用Memory业务表承载代码 |
| https://docs.agno.com/reference-api/schema/knowledge/search-knowledge | 查询接口支持filters和search type | 在统一CodeRetrieval入口显式表达通道与过滤范围 |
| https://github.com/openai/codex/blob/main/codex-rs/file-search/src/lib.rs | 独立file-search实现文件查找 | 文件定位与源码语义检索分开；不把模糊文件搜索宣称为GraphRAG |
| https://qdrant.tech/documentation/search/filtering/ | 查询可以使用payload条件组合，过滤字段可建立payload索引 | repository/snapshot/generation/model作为向量检索前置过滤，并在SQLite二次校验 |

本地事实：LocalVectorMemory写SQLiteMemoryStore并在Python中计算cosine；HybridRanker的ID名为memory_id。可以提取纯算法或通过适配层复用，不能把代码ID伪装成Memory记录。retrieval仍为骨架。

限制：资料URL中的main及在线文档可变化；实施阶段需固定SDK/服务镜像/模型版本，并验证过滤、幂等写和恢复行为。本次未完成Qdrant容量或延迟测量。

## 本轮重新核对（2026-09-07）

实际重新打开Codex官方仓库上述file-search源码（可见Pattern/AtomKind::Fuzzy及搜索会话结构）、Agno Knowledge overview，以及[Qdrant过滤文档](https://qdrant.tech/documentation/search/filtering/)（must条件为AND）。Agno的 `/knowledge/vector-dbs/qdrant/overview` 本轮抓取失败，改读Knowledge overview，不将失败页面当作证据。

参考事实：Codex此模块提供文件模糊定位；Agno介绍Knowledge读入和检索能力；Qdrant支持组合payload过滤。本项目建议：分开文件定位与代码相关性，检索入口固定服务端Scope，向量请求所有身份字段组合为must条件，SQL再次验证。以上资料不证明本项目已有版本隔离、双库恢复或性能收益。

本地复核：CodeMapQuery未显式接收generation，而SourceEvidenceService通过诊断绑定读取generation；新增检索不能忽略二者差异。只检查接口和实现文本，未执行新代码检索Case。上游资料未固定commit，选型确认后须锁定实现依赖；不伪造版本号。

## Milvus选型确认后的资料核对（2026-09-07）

用户决策：Milvus向量索引，SQLite保留事实、任务、manifest、Evidence及全文投影。以上Qdrant研究仅为历史候选，不代表当前推荐。

本轮重新查询[Codex file-search源码](https://github.com/openai/codex/blob/main/codex-rs/file-search/src/lib.rs)和[Agno Knowledge](https://docs.agno.com/knowledge/overview)，继续仅参考文件定位与Knowledge职责边界，不推断其具有本项目的Commit代次发布协议。

| 一手资料 | 参考项目事实 | 本项目建议/推断 |
|---|---|---|
| [Milvus Lite](https://milvus.io/docs/milvus_lite.md) | Lite支持本地文件持久化、向量CRUD和metadata过滤；与Standalone共享客户端API。当前页面列出不支持partition key、仅Strong一致性及FLAT索引等限制 | 用于小规模正确性验证；以选定版本重新核实限制，不把Lite结果外推生产容量 |
| [Milvus Filtered Search](https://milvus.io/docs/filtered-search.md) | 标准过滤搜索先进行标量过滤，再在符合条件的实体中搜索向量 | 服务端构造Scope与版本预过滤；SQL回查与manifest发布仍由本项目实现 |

资料页面未作为锁版本依据；本项目在5.2实施设计中锁定PyMilvus 3.0.1、milvus-lite 3.2.1、COSINE/FLAT及Standalone单节点基线（4 vCPU/8 GiB/50 GiB SSD/Strong）。容量/延迟仍需真实Case测量，不能从资料推断。
