# PRD-005 Code Retrieval and RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在固定已发布 Code Map 代次上建立可恢复的 SQLite 全文投影、Milvus 向量投影、混合/图召回和 Evidence 回读链路。

**Architecture:** SQLite 是代码事实、FTS5、任务状态、发布 manifest 和 Evidence 的权威存储；Milvus 是可重建的独立向量投影。检索入口先注入服务端 Scope，再由关键词/向量通道召回，融合与图扩展只返回定位信息，最终由 Evidence 服务验证源码 hash 后进入 Context。

**Tech Stack:** Python 3.11+, SQLite FTS5, existing Code Map stores/query APIs, existing Embedder port, PyMilvus with Milvus Lite/Standalone, pytest.

**Spec:** `docs/superpowers/specs/2026-09-07-prd005-code-retrieval-design.md`

## Global Constraints

- SQLite 保留代码事实、FTS5 全文投影、任务/attempt、manifest 和 Evidence；不得写入 `memory_records`。
- Milvus 只保存 vector、确定性 point_id 和完整来源/版本标量字段；不保存凭证或作为 Evidence 权威来源。
- 查询必须使用服务端注入的 `repository_id/snapshot_id/published_generation/commit_sha` 及投影、模型、维度、模板版本过滤；SQL 必须二次核验。
- 构建中的代次不可查询；只有 manifest 全量核对后才能切换发布指针。
- 每个 Case 默认 120 秒、最多 2 次重试；后台异常、跨域结果、hash 不匹配均判失败。
- 每阶段顺序固定为针对性测试、用户确认的真实 Case、累计回归、用户 review。

---

### Task 1: 冻结 5.1 文本投影数据契约

**Files:**
- Create: `src/antisentinel/retrieval/models.py`
- Create: `src/antisentinel/retrieval/sqlite_store.py`
- Create: `tests/test_code_search_models.py`
- Create: `tests/test_code_search_sqlite_store.py`

**Interfaces:**
- `CodeSearchDocument(repository_id, snapshot_id, published_generation, commit_sha, node_id, chunk_id, path, symbol, language, source_hash, embedding_input_hash, projection_revision, text)`。
- `SQLiteCodeSearchStore.upsert_documents(documents)`, `SQLiteCodeSearchStore.publish_manifest(scope, projection_revision)`, `SQLiteCodeSearchStore.query_fts(scope, query, limit=30)`。
- 查询返回 `CodeSearchHit(document_id, rank, channel="keyword", source_identity)`，不能返回 Memory ID。

- [x] 写失败测试：同一 `document_id` 重放两次只保留一行；不同 commit/generation 的同路径文档互不覆盖；manifest 未发布时 FTS 不可见。
- [x] 运行 `pytest tests/test_code_search_models.py tests/test_code_search_sqlite_store.py -q`，预期因模块不存在失败。
- [x] 实现独立 SQLite 表、唯一键 `(repository_id,snapshot_id,published_generation,document_id,projection_revision)`、manifest 发布指针和参数化 FTS 查询。
- [x] 重跑 focused 测试，确认全部通过；再执行 `git diff --check`。

### Task 2: 精确标识符与 FTS5/BM25 检索

**Files:**
- Create: `src/antisentinel/retrieval/keyword.py`
- Modify: `src/antisentinel/retrieval/sqlite_store.py`
- Create: `tests/test_code_keyword_retrieval.py`

**Interfaces:**
- `KeywordRetriever.search(scope, query, *, limit=30) -> tuple[CodeSearchHit, ...]`。
- 精确标识符通道先过滤完整 symbol/path/error code；FTS 通道使用固定 tokenizer/规范化词项并按 `bm25` 升序。

- [x] 写失败测试：完整符号命中排在模糊命中前；中文注释和错误码可检索；跨代次结果为零；FTS 特殊字符不会改变 SQL。
- [x] 运行 focused 测试确认失败原因是接口缺失。
- [x] 实现 tokenizer、camelCase/snake_case 词项、FTS5 `bm25` 排序和稳定 document_id tie-break。
- [x] 运行 focused 测试，再用固定 fixture 记录输入字节、输出数、持久化数和 P95 查询延迟。

### Task 3: 5.1 离线 runner 与真实 Case

**Files:**
- Create: `scripts/run_code_retrieval_case.py`
- Create: `tests/test_code_retrieval_case_runner.py`
- Create: `docs/validation/prd005-stage1/README.md`
- Modify: `docs/memory-development-log.md`

**Interfaces:**
- runner 接收固定 fixture、query-set hash 和隔离输出目录，生成 `report.json`、SQLite 文件和 `case_pass`。
- 报告必须包含输入规模、耗时、输出/持久化/完整性数量、后台异常、t1/t2 及差值。

- [x] 写失败测试：预存在输出目录拒绝；双 commit 隔离；SQLite 重开后 manifest/FTS 数量一致；报告缺任一数字字段时 `case_pass=false`。
- [x] 运行 focused 测试确认失败。
- [x] 实现 runner 和固定 fixture；不启动外部服务、不调用 Embedding。
- [x] 用户确认 Case 后运行 `python scripts/run_code_retrieval_case.py --case keyword --output <isolated-dir> --timeout 120`，保存原始输出并追加开发记录。

### Task 4: Embedding 任务与 Milvus 适配器

**Files:**
- Create: `src/antisentinel/retrieval/vector.py`
- Create: `src/antisentinel/retrieval/milvus_adapter.py`
- Modify: `pyproject.toml`
- Create: `tests/test_milvus_adapter.py`

**Interfaces:**
- `VectorIndexPort.create_collection(schema)`, `upsert(points)`, `get(point_ids)`, `search(vector, scope, limit=30)`。
- `MilvusAdapter(uri, collection_name)` 使用确定性 VARCHAR 主键、完整标量字段和服务端构造的 filter；Lite 文件路径必须独立于事实 SQLite。
- `EmbeddingTaskStore` 在 SQLite 中记录 pending/running/retry_wait/ready/failed，另记录 channel blocked。

- [x] 写失败测试：Lite/Standalone 使用相同接口；upsert 重放不增加逻辑点；scope/model/dimension 不匹配不可返回；读回字段或数量不一致不能 ready。
- [x] 安装并验证 PyMilvus/Milvus Lite 依赖；当前探针为 `pymilvus==3.0.1`、`milvus-lite==3.2.1`，未伪造 Milvus Case 通过。
- [x] 锁定实际 PyMilvus/Milvus 版本后实现 schema、upsert、filtered search、逐项读回和关闭/重开。
- [x] 运行 adapter focused 测试并记录版本；真实 Lite collection/向量数待用户确认 Case 后记录。

### Task 5: 双库恢复与发布 manifest

**Files:**
- Modify: `src/antisentinel/retrieval/milvus_adapter.py`
- Modify: `src/antisentinel/retrieval/sqlite_store.py`
- Create: `tests/test_retrieval_recovery.py`
- Modify: `scripts/run_code_retrieval_case.py`

**Interfaces:**
- `ReconciliationReport(expected_ids, actual_ids, missing_ids, extra_ids, field_mismatches)`。
- `reconcile(scope) -> ReconciliationReport`；只有空差集才能将 SQLite channel 状态置 ready。

- [x] 写失败测试覆盖写前退出、Milvus 已写 SQLite 未确认、发布前退出、重复事件；断点恢复后逻辑任务新增数为 0，集合/字段一致率 100%。
- [x] 运行 recovery focused 测试确认失败。
- [x] 实现幂等重试、对账、manifest 原子切换及旧 collection 隔离；网络调用不得持有 SQLite 长写事务。
- [x] 用户确认 Case 后运行累计 5.1–5.2 Case，保存 Lite 与 Standalone 分开结果。

### Task 6: 混合召回与统一入口

**Files:**
- Modify: `src/antisentinel/retrieval/hybrid_index.py`
- Modify: `src/antisentinel/retrieval/engine.py`
- Create: `src/antisentinel/retrieval/fusion.py`
- Create: `tests/test_code_retrieval_hybrid.py`

**Interfaces:**
- `CodeRetrievalService.search(query, scope, mode, top_k=5, budget=...)`。
- 通道 DTO 统一包含 source identity、channel、rank、truncated/degraded 原因；RRF 使用固定 `k=60` 和稳定 tie-break。

- [x] 写失败测试：keyword/vector/hybrid 对照；vector 不可用时 hybrid 显式 degraded，vector-only 返回 vector_unavailable；重叠范围只保留一个最终片段。
- [x] 实现预过滤、RRF、预算和最终 top-k；不把 BM25 与向量距离直接相加。
- [x] 固定至少 20 条查询和标注并记录 P50/P95；当前三组 Case runner 已固定查询，graph 组留到 Task 7。
- [ ] 用户确认 Case 后运行 keyword/vector/hybrid 三组并记录 P@5、R@5、MRR、P95；graph 组留到 Task 7。

### Task 7: 受控图扩展、Evidence 和 Runtime 工具

**Files:**
- Modify: `src/antisentinel/retrieval/graph.py`
- Modify: `src/antisentinel/code_map/source_context.py`
- Create: `src/antisentinel/retrieval/evidence.py`
- Create: `tests/test_code_retrieval_graph_evidence.py`

**Interfaces:**
- `GraphExpander.expand(seeds, scope, depth=1, node_budget=20, edge_budget=40)` 保留 seed/edge 原因。
- `SourceEvidenceService` 仅对选中候选回读并核验切片 hash；Context 最多 4 片/32 KiB。

- [x] 写失败测试：unresolved/越域/错误 hash 拒绝；同名跨文件负例不生成确定边；Evidence 范围等于实际披露范围。
- [x] 实现关系白名单、预算、generation 绑定和恢复；把 graph_degraded 与 incomplete/truncated 分开。
- [x] 已运行累计本地5.1–5.4 Case与Runtime恢复，具体证据见stage4报告；不涵盖真实Embedding/Standalone/语义图关系。

### Task 8: 冻结评测与累计验收

2026-09-08状态纠正：5.4只在fixture范围通过，用户明确尚未review通过；5.5正式运行暂停。评测准备代码保留，需先完成5.4修复review。

后续用户“继续，最后要引入ragas”后恢复本地诊断执行；该指令不降低质量门槛。实际结果与新增Ragas入口见stage5/RAGAS.md，quality_pass仍false。

**Files:**
- Create: `scripts/evaluate_code_retrieval.py`
- Create: `tests/test_code_retrieval_evaluation.py`
- Create: `docs/validation/prd005-stage5/README.md`
- Modify: `docs/memory-development-log.md`

**Interfaces:**
- 评测输入固定 corpus/query/label hash、commit/generation、top-k=5 和随机种子（如无随机则写 N/A）。
- 输出宏平均及每条最小 P@5/R@5/MRR、P95、P50、token、Embedding 批次/失败和成本。

- [ ] 写失败测试：少于 10 条查询标本不足；相关答案集 Precision 上限低于 0.80 时阻止声称整体达标；无结果集单独报告误命中率。
- [ ] 实现四组评测、五轮同 fixture 性能基线及双库/旧 Memory 回归。
- [ ] 用户确认累计 Case 后运行 `python scripts/evaluate_code_retrieval.py --corpus <frozen> --queries <frozen> --top-k 5`，只有所有硬门槛满足才写 `case_pass=true`。

2026-09-08实际准备进展（覆盖上面的概念命令）：

- [x] 新增指标/错误行/Scope/唯一查询/性能比较及只读CLI测试；指标入口在evaluation/code_retrieval.py。
- [x] 固定两Commit的10份blob与24个不同查询，标签含rationale且待用户review，manifest统一承载语料及query版本。
- [x] CLI `scripts/evaluate_code_retrieval.py --preflight` 验证82文档、24query及Precision上限0.25；默认目标0.80保持。
- [x] 准备keyword和可选本地哈希向量诊断入口、五轮数据、逐查询报告及两库读回；hybrid_graph明确未支持。
- [ ] 用户确认 `docs/validation/prd005-stage5/README.md` 的诊断Case后运行，首次五轮只创建性能基线。
- [ ] 人工标签review、完整四组效果、真实Embedding、Standalone故障恢复与容量验收。

### Task 8 后置评估层：Ragas（用户新增要求）

- [x] 一手资料及实际wheel/API核对，Ragas0.4.3与LangChain0.3系列隔离安装，pip check通过。
- [x] 可选ragas-evaluation依赖、真实IDBasedContextPrecision/Recall适配与CLI，禁用遥测；输入指纹和错误Scope重新核验。
- [x] 原双Commit诊断报告与用户授权选定的企业Go样本均实际执行Ragas ID评估；每份360条观测，保留null及有效数量。
- [x] 企业权限与仓库选择：列表27仓、两候选read=true，选定小型Go仓并固定Commit，四个纯逻辑文件在本地验证；源码及私有标签不提交本仓。
- [ ] 真实response/披露context、裁判模型服务和发送范围确认后，才执行Faithfulness/Answer Relevancy等模型指标。
- [ ] 修复并验证实际诊断暴露的质量缺口；不以ID指标替代完整RAG验收。

### Review follow-up: 5.2 生产边界修复

- [x] canonical point ID 绑定 document/source 与 model、dimension、template、projection 版本；同文档不同 embedding 身份不能复用 ID。
- [x] 已有 collection 在加载前校验主键类型、auto_id、必需字段、向量维度、FLAT/COSINE index；不兼容直接失败。
- [x] 物理 collection 名包含版本组合摘要；adapter 强制点、查询、对账版本一致。
- [x] `search()` 强制接收 SQLite channel store，非 `ready` 状态拒绝查询；对账限制单一 Scope。
- [x] 解析 Milvus Lite 的嵌套 `entity` 返回；runner 关闭原 client 后才创建第二 client。
- [x] SQLite embedding task 增加 attempt、started/completed、duration、error、lease owner，并对未知 task 抛错；显式锁定 `milvus-lite==3.2.1`。
- [x] 复验命令、独立两库核验和全量回归已记录在 `docs/validation/prd005-stage2/README.md`。

### Review follow-up: 5.3 hybrid 边界修复

- [x] Service 仅将瞬时网络/超时映射为 `vector_unavailable`；schema、版本、channel、权限异常透传。
- [x] keyword 通道检查 SQLite lexical manifest；非 ready 显式返回 `lexical_unavailable/degraded`。
- [x] 强制 `top_k≤5`、`candidate_limit≤30`，单通道分数使用实例 `rrf_k`。
- [x] Fusion 先构建重叠范围连通分量，保留跨通道贡献并稳定选择代表来源。
- [x] runner 通过实际 unique document_id 计算 `output_count`，记录 hybrid duplicate 数及各模式 P50/P95。
- [x] 修复后 5.3 Lite Case 与两库独立核验已记录在 `docs/validation/prd005-stage3/README.md`。

## Plan self-review

2026-09-08 5.4 P1修复复验：contains-only入口、满额种子/邻居交替选择、当前binding约束rehydrate、truncated/incomplete传播、统一ToolExecutionResult及内部存证副作用说明均已补齐。测试513/513与本地累计4/4 Case的实际证据见stage4最新报告。用户review仍未通过，不能凭勾选自动进入5.5。

- Spec coverage: 目标、SQLite/Milvus边界、任务状态、恢复、检索、图、Evidence、评测和 Runtime 接入均有任务映射。
- Placeholder scan: 本计划无 `TODO`、`TBD` 或未定义的“适当处理”步骤；版本和资源被明确留在 Task 4 的 5.2 实施设计中，而非隐瞒为空。
- Quantitative gates: 当前基线 443/443、生产 Python 181、测试文件 95、retrieval 文件 6、真实 Case 0；后续阶段报告必须给出输入规模、耗时、输出/持久化/完整性、后台异常、t1/t2 和阈值。
