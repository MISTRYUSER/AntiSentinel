# Memory 演进候选设计（2026-09-05）

本文是候选方案，尚未由用户选定，不是实施计划，不推进实现阶段状态。

## 1. 范围、目标与非目标

范围覆盖 Memory、Persistence、Runtime Context 和 Evaluation，为 architectural 工作。目标是建立可恢复、可追溯、可测量收益的长期记忆。保留 Incident → Session → Turn → Task → ToolCall → Attempt → Evidence 领域关系。

本轮交付研究和候选设计；不修改运行代码、不替换存储、不下载模型、不运行外部服务。后续也不默认引入图数据库或额外向量服务。

证据：[调研记录](../../research/memory-evolution-research.md)。本轮 44/44 针对性测试通过（0.64s），但 5 项纯内存诊断正确行为为 0/5。旧 224 全量测试结果不代表当前全量回归。

## 2. 三个候选方案

| 方案 | 解决的问题 | 范围与代价 | 数字证据与门槛 | 建议 |
|---|---|---|---|---|
| A：可信记忆基础 | 写入内容能被正确召回，重启/重试不丢或混淆 | 固定 schema、ID、来源、状态、时钟、总预算及作业恢复；涉及 4 个层次 | 5 项探针正确行为 0/5→5/5；相关测试 44/44 保持；跨 scope 泄漏=0 | 下一期优先 |
| B：在线混合检索 | 同义表达、错误码、历史经验召回 | 将生产 Retriever 从 evaluation 拆出，接 FTS5+vector+融合排序；需模型/index 资源与新评测 | 当前在线向量调用点=0→至少1个可观测路径；固定470 queries重新建立基线；P@5>=0.80、R@5>=0.60、MRR>=0.80 | A 后执行 |
| C：经验整合与生命周期 | 重复、冲突、过期记忆与可复用排障经验 | 抽取和 consolidation 分离；使用反馈、版本替代、遗忘、经验提炼；模型复杂度更高 | 正确纠错/撤回20/20；无新输入不重复调用模型；成对任务 token 降幅>=30%且准确率不劣化>5% | A/B 后执行 |

三个方向不是平行替代基础契约的捷径。若以检索演示为首要目标可先研究 B，但上线仍以 A 为前置。推荐顺序 A→B→C；各方案收益目前均待验证。

## 3. A：可信记忆契约与可恢复写入

### 模块职责

- `memory/models.py`：定义版本化 MemoryRecord，不再用松散 dict 隐式拼字段。
- `memory/candidates.py`：候选绑定 operator/session/turn/source kind/source version；不同来源不共享常量 ID。
- Recorder：保存完整来源并写入 durable job；Runtime 只负责提供事实和允许的摘要。
- Persistence：提供原子、幂等写入；SQLite 保留 primary 职责，JSONL audit 降级单独报告。
- Worker：claim lease、ack、退避 retry、尝试上限、失败可见、drain；重启恢复不能依赖进程内队列。
- Recall / Context：使用统一时钟和统一总 token 预算；解析、验证 typed SourceRef，未知来源不能猜成 Evidence。

### 拟议数据契约

`MemoryRecord` 至少带 memory_id、schema_version、memory_type、owner/scope、content、content_version、status、valid_from/valid_to、source_refs、confidence、extractor_revision。

区分 memory 生命周期的 active/superseded/retracted 与 rollout 运行结果的 completed/failed；后者放 outcome。SourceRef 明确 session/turn/event/evidence 类型，Evidence 引用附原始版本/hash 的校验信息。legacy 迁移无法证明来源类型时保留 unknown 并限制其证据用途。

scope 默认拒绝隐式跨 operator/Incident；项目级共享经验应通过明确的共享/脱敏发布策略进入独立知识范围，不直接放宽 Incident 过滤。

### 错误与恢复

- 原始事实提交失败：返回 persistence failure，不报告持久化成功。
- 派生记忆失败：主业务状态与 memory_pending/failed 分开；原始事实保持可回放。
- Worker 分类失败：持久化错误与下次重试时间；超限进入可检索失败状态。
- SourceRef 无效：该记录不能作为证据注入；报告 provenance_invalid。
- 索引版本不符：拒用旧向量、触发可观察重建或词法降级，不能把降级当向量命中。

## 4. B：让评测和生产共用检索内核

```text
Query + AuthorizedScope + QueryTime + Budget
  → candidate retrieval（FTS5 / exact identifier / local vector）
  → typed canonical record hydration + scope/version/validity filter
  → fusion/rerank + confidence/relevance threshold
  → source verification + one context budget
  → ContextView → Runtime Model
```

筛选 scope 应尽量下推至候选检索，回源后再校验；不能全库 top-k 后才过滤造成漏召回或泄漏。Index 只保存派生数据，不能更新事实内容。LocalVectorMemory 当前 upsert 同时写主记录和向量，需要重新划清职责。

生产接口拟分为 `MemoryRetriever.retrieve(query, scope, query_time, limit)`、`SourceResolver.resolve(refs, scope)`、`MemoryContextAssembler.assemble(digest, records, token_budget)`；接口为候选，不宣称已存在。Evaluation 导入生产内核，生产不得依赖 LongMemEval 类型。

固定 E5-small/BGE-M3 的真实模型身份、revision、维度和 query/passage 编码；兼容别名不能作为模型版本。Python cosine scan 可作小规模基线，是否换 ANN 由 1k/10k/100k 记录规模的 P95、内存和 build time 决定。

候选和排序还需允许“没有可信记忆”；低置信度命中不应仅凭 scope/recency 得分自动进入上下文。

## 5. C：从记录积累到经验整合

参考 Codex 的 2 阶段后台流程：先抽取可追溯候选，再对每个授权 scope 的有界候选集整合。增加去重、supersedes/retraction、有效期、使用次数、最近使用时间和纠错反馈。整合不修改原始 Event/Evidence。

优先形成三类有区别的记忆：用户明确偏好、带适用条件的故障经验、经过验证的操作步骤。模型结论保持 hypothesis/derived 属性，不能凭一次输出升级成事实。

使用次数只代表被选用，不代表有用；分别记录 retrieved、injected、cited、outcome，并以成对任务检验收益。借鉴 Agno 的 user memory/history/state 分层，受控地提供查看、更正和遗忘入口；本期不设计 UI 或新增管理 API。

## 6. 测试、基线与验收映射

| 阶段 | 至少3项成功指标 | 失败指标 | 回归指标 | 当前证据与复现 |
|---|---|---|---|---|
| A | 探针正确5/5；不同来源ID唯一率100%；有效引用可回源100% | 跨scope泄漏0、重复副作用0、未捕获后台异常0 | 相关44个测试保持100%；同口径P95恶化<=5% | probe当前0/5；上述pytest当前44/44；恢复/P95 N/A |
| B | P@5>=0.80；R@5>=0.60；MRR>=0.80 | scope泄漏0、悬挂引用0 | 检索P95<=基线1.20x；不变路径P95恶化<=5% | 当前在线向量调用点0；质量/P95须重新测 |
| C | 纠错20/20；撤回后错误注入0/20；输入不变模型调用0 | 原始事实被覆盖0、后台异常0 | token降低>=30%；任务准确率相对下降<=5% | 当前均N/A，不能宣称压缩收益 |

所有成功指标同时满足；失败指标必须0。历史470 Case数字仅作参考：R@5=77.78%、P@5=36.09%、MRR=78.21%；当前不同检索器可能口径不同，先固定相关性标注再重测。

检索实验固定 dataset/query/label版本及SHA、k=5、时钟和种子（无随机则N/A）。至少10 queries，报告宏平均、逐查询最小值、P95，并保留每条查询详情。正式使用470 non-abstention；30 abstention单独评拒答和错误注入。不能把含has_answer的候选投影用于评估。

固定分母 P@5 在相关条数少于4的查询上可能无法达到0.80，须在执行前检查数据集的理论上限；不适用时明确未达门槛，并提交新的验收定义讨论，禁止静默换分母。

### A 阶段拟议真实 Case（准备稿，尚未确认或执行）

- 输入：2个operator、2个Incident、各2个Session；提供不同诊断、一次偏好纠错、过期/未来记录和失败分类任务。
- 运行：先完成针对性回归，再使用独立临时SQLite及隔离Redis namespace，验证跨进程恢复。具体runner和命令在方案选定后的实施计划中提供；当前不冒充可执行Case。
- 观测：business_completed_at、memory_persisted_at及差值，Event/Evidence/Memory/job数量、引用关联、版本、context、错误日志、重启后的结果。
- 预期：4个Session可追溯；ID无冲突；隔离/有效期/预算均满足；故障后任务有终态且重放无重复副作用。
- 失败：任何跨scope内容、悬挂引用、遗漏作业、版本混用、未捕获异常或超预算。
- 清理：只清理本Case临时目录和namespace，不触碰正式storage。
- 预算：120秒，最多重试2次；连续同输入失败2次停止扩展并定位。
- 基线：本轮纯内存0/5；真实业务/持久化耗时、输入字节、输出/持久化/关联数量、后台异常均N/A，执行时填实数。

## 7. 方案边界与待选择项

本轮没有进入实现，因此不启动真实Case，也没有“真实运行通过”状态。选定方案后才生成按大标题组织的实施计划、可执行Case及阶段门槛。推荐下一期选择 A，完成后再独立评估 B。

设计自审清单：外部事实与项目建议分离；旧指标不冒充当前；接口声明为拟议；真实Case标明未就绪；全部效果结论待验证；没有把PRD-005检索范围伪装成PRD-003原有承诺。
