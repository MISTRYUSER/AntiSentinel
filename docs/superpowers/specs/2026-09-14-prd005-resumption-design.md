# PRD-005 恢复开发技术设计（待确认草案）

日期：2026-09-14。类型：architectural（跨 Code Map、Worker、Runtime、Evidence、存储）。本稿恢复设计和验收准备，不撤销图优化 pending，不代表 PRD Done。方案和真实 Case 尚未获本轮确认；不进入实施计划。

关联：[进度核对](../../validation/prd005-resumption-20260914/PROGRESS.md)、[一手调研](../../research/prd005-resumption-research.md)、[原 PRD](../../prd/PRD-005%20Code%20Retrieval%20and%20RAG.md)、[剩余项](../../validation/prd005-stage5/OPEN-ITEMS.md)。

## 1. 范围、目标与非目标

目标：把已有组件的独立验证收敛为正常应用入口的一条可审计真实链路，然后单独验收业务质量与生产运行边界。下一阶段只做第一项，按大标题逐段 review。

保持 SQLite 事实/任务/manifest + Milvus 投影、默认 hybrid、显式 Incident 版本绑定、Evidence 回读。非目标：重写检索架构、换向量库、引入 Agno 运行时、多轮 agentic retrieval、新图排名、把测试通过换算为 PRD 完成百分比。

事实基线：HEAD `16d83bf35209918005a46a4cfd54ba599763decb`；204 个 src Python 文件、21 个 retrieval Python 文件、127 个 test 文件。当前回归见进度表；真实生产入口完整 Case 的现有可确认记录为 0。外部链路 P95/吞吐/错误率本轮 N/A，因为未启动该 Case。

## 2. 候选方案与推荐

| 方案 | 下一段范围 | 量化交付门槛 | 权衡 |
|---|---|---|---|
| A：正常入口一体化验收（推荐） | 真实 Flash 常驻 Worker、真实 Session LLM、来源持久化与重启 | 1 个固定范围 Case；双库一致 100%；引用恢复 100%；后台异常 0 | 直接补组件与应用入口间的证据缺口，需要明确语料/服务范围 |
| B：业务质量先行 | 复用独立评测脚本，先冻结人工标签与无答案集 | 至少 20 题；P@5/Recall/MRR/FPR 与答案质量独立报告 | 更早暴露无答案问题，但不能补正常 Session 接线证据 |
| C：生产平台先行 | 最小权限/TLS、容量、并发与故障 | 1 套目标部署；越权成功 0；同模式 5 轮时延基线 | 适合已有上线日期与平台预算；当前规模/负载输入不足 |

推荐 A→B→C；不是要求替换既有技术。三方案当前新增实测均为 0，收益是设计推断。A 的前置是当前本地回归和用户确认 Case；B 的质量阈值须在运行前确认，C 的数据规模/并发目标由真实平台需求决定，暂为 N/A，不捏造容量指标。

## 3. 第一阶段：正常应用入口与真实服务链路

### 3.0 三种恢复语义

第一阶段把恢复拆成独立验收对象，避免共享“checkpoint”一词造成误判：

1. R1 验证已经完成的 Session 结果、Evidence 和检索投影在应用重启后仍可读取；它不证明运行中的 Session 能从中间回合续跑。
2. R2 验证 Embedding 在“Milvus 已写、SQLite 尚未确认”时进程中断，任务可依靠 SQLite 租约、缓存向量、稳定 `point_id` 和字段核对恢复；它不读取 Runtime checkpoint。
3. `RuntimeSnapshot` 当前已有序列化和内存恢复测试，但正常应用入口没有持久 `CheckpointStore` 接线或恢复 API。该生产接线不在 R1/R2 内，继续列为后续设计项；未实现前不得声称运行中 Session 跨进程续跑。

R1/R2 假定同一投影同一时刻只有一个活动 Coordinator。当前 task lease 能 fence 单条 Embedding 任务，但 projection reconciliation 没有跨进程的 run-level lease/state version；多实例 active-active 的慢报告覆盖问题属于生产并发硬化范围，本阶段不声称覆盖。

### 3.1 现有职责与接线

| 现有入口/模块 | 职责与验证方式 |
|---|---|
| `entry/application.py:from_environment` | 读取隔离持久化配置并构建协调器；Case 使用此工厂，避免 default_fake 后替换真实模型 |
| `api/app.py:create_app` lifespan | 启停 `start_background_services`/`stop_background_services`；通过正常生命周期进入 |
| `retrieval/config.py` | enabled、repo allowlist、Milvus URI/token、投影版本、query timeout；外发端点仅引用配置身份，不记录凭证 |
| `RetrievalCoordinator.tick/_project` | 消费已发布代次，生成全文与持久任务，调用 Worker；观测 status 与 SQLite 状态 |
| `CodeEmbeddingWorker` / `EmbeddingTaskQueue` | 租约、attempt、读回确认、manifest；从数据库读取实际重试，不用常量 0 |
| `IncidentRetrievalTools` | 服务端注入 Scope；模型不能指定 commit/generation 或扩权 |
| `CodeRetrievalService` / `SourceEvidenceService` | 候选排序、预算、源 hash 回读、Evidence、rehydrate |
| `POST /api/incidents/{incident_id}/sessions` | 正常模型模式启动；核对真实模型调用、tool attempts、final 引用及持久结果 |

配置陷阱：当前 `ANTISENTINEL_CODE_RETRIEVAL_PROJECTION` 默认 `path-symbol-source-v1`；新切片必须显式设为 `utf8-slices-8192-v1`。`_project` 会拒绝已有 active projection 与新版本不同的隐式迁移。Case 用新建事实库与独立 collection，不能在旧库直接改环境变量后冒充迁移通过。

当前应用工厂把 Milvus collection 基名固定为 `antisentinel_code`。为使共享 Standalone 上的验收 Case 可隔离，第一阶段允许新增一个受格式校验的 collection 基名环境变量，默认值仍为 `antisentinel_code`；Case 使用由冻结输入 hash 派生的独立基名。此配置只改变命名空间，不改变 point identity、版本 collection 规则或默认部署行为。

当前 `CodeSearchDocument.document_id` 和 `projection_revision` 会持久化到 SQLite/Milvus，但 `source_identity` 与 Evidence metadata 没有携带这两个字段。第一阶段不悄悄修改 Evidence 数据模型；R1 通过 scope/path/hash/byte range 唯一回连 active SQLite document，再核对 Milvus 的 document/version 字段，并把 Evidence 自身缺少投影字段列为后续接口项。

### 3.2 数据流与身份契约

```text
授权的固定 Git commit → 已发布 Code Map snapshot/generation
  → 应用 lifespan → Coordinator → SQLite 文档/任务 → Flash → Milvus → 读回 → ready
  → Incident 绑定 → 正常 Session 真实 LLM → code_retrieval.search(hybrid)
  → code_retrieval.read_evidence → source_context → final/evidence_refs
  → SQLite Session/Evidence/checkpoint → 停止并重开应用 → 恢复/来源 hash 核验
```

不新增生产 DTO。复用 Scope 的 repository_id/snapshot_id/published_generation/commit_sha，以及 source_identity 的 chunk/document ID、父 hash、切片范围和投影版本。模型看到已披露 SourceSlice 和引用摘要；未选中的候选不能成为 Evidence。Context 不灌入 Evidence 存储的整份原文，仅通过既有 source_context 边界提供已核验片段。

新增物只计划为 Case runner 和报告契约：报告至少含 input、versions、checks、counts、attempts、model_usage、timing、recovery、errors、execution_pass；证据不足时 case_pass=false。runner 文件尚未创建，不提供不存在的可执行命令。

### 3.3 可执行 Case 提案与准备命令

Case：R1，真实服务正常 Session 与恢复；状态：待确认，不执行。可先执行现有本地基础回归命令：

```sh
/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python -m pytest -q
```

实施时扩展现有 `scripts/run_retrieval_coordinator_case.py` 的生命周期/数据库校验，增加独立 real 入口并保留原 deterministic Case。新 runner 必须先实现、用 `--help` 和离线检查验证，再交付精确运行命令；本稿不把脚本设计当成已可执行的外部 Case。

输入：优先恢复原授权固定语料；如果不存在，由用户明确一个可外发的最小仓库/commit。先 2 个 Session（1 个可回答、1 个无答案），每个绑定唯一 Scope；manifest 冻结路径、字节数、查询、标签和 hash。该 pilot 只判断接线与可恢复性，不代表质量评测样本。

启动：独立 `ANTISENTINEL_STORAGE_ROOT`、repo allowlist、Milvus collection、真实模型配置和切片版本；通过应用工厂与 HTTP Session 入口。不得以 Model fixture 或直接 complete 调用代替正常 Runtime。

观测：`/api/retrieval/status`、Session API、工具 attempts、SQLite 文档/队列/结果、Milvus get/reconcile、Evidence/checkpoint；capture 后台线程异常及退出线程。凭证不进 JSON/日志。

预期：所有期望向量逐项匹配，2 个 Session 都有真实调用审计，至少可回答 Session 有经披露引用；无答案 Session 单列回答行为，不强制伪造 Evidence。重启后已完成投影不重新编码，持久引用全部 rehydrate。

失败：跨域/错版本/错 hash、缺失产物、任意后台异常、deadline 超限、请求或 token 预算超限、未声明降级。失败输入保留，同输入连续失败 2 次停止扩展。禁止删除失败轮次后挑最好结果。

清理：仅本 Case 创建的进程/collection/临时文件；默认保留数据库与脱敏报告，不删除既有服务卷。外发范围不因“继续”自动扩大。

预算：每个 Case 默认 120 秒，最多重试 2 次；启动/拉镜像不隐含纳入此授权。正式批量答案与 Ragas 另设预算，参考历史 268.10 秒结果，若需 600 秒须在该 Case 提案写明并确认。实际 tokens 按服务响应汇总；费用无账单写 N/A。

R2 恢复子 Case：R1 之后独立执行，先让 1 个任务停在“向量已写、SQLite 未确认”的可控故障点，保存 attempt/point_id 后中止应用进程，再重启正常工厂。检查逻辑向量重复0、待处理任务最终ready、重复编码/实际attempt如实记录、后台异常0（预期注入单列）。每Case120秒/最多2次重试。故障hook仅限Case，不能启用在生产配置。R1的完成后重开不替代R2；R2也不能替代已保存Evidence恢复。该子Case需随精确runner一起确认，当前没有执行。

### 3.4 验收表（执行前冻结）

| 指标 | 基线 | 目标/阈值 | 证据 | 判定规则 |
|---|---|---|---|---|
| 正常入口 pilot Session | 0 个当前完整 Case | 2/2 有实际模型及工具审计 | session/model-calls/checks | 100% |
| 双库完整性 | 新目录 0 条 | N 文档=N ready 任务=N 读回匹配向量，N>0 | SQLite+Milvus 对账 | 100% |
| 引用与恢复 | 新目录 0 条 | 可回答至少 1 引用；全部披露/绑定/hash 正确且重开恢复 | Evidence/checkpoint | 100% |
| 失败指标 | 目标 0 | 越域、后台异常、缺失产物、超预算均 0 | errors/线程/时间审计 | 任意非 0 失败 |
| 重开编码 | 初次 N 个输入 | 第二次新增文档编码 0 | usage/attempts | 等于 0 |
| 回归指标 | 本轮 pytest 快照 | 既有测试全部通过 | pytest 日志 | 失败/跳过均 0 |
| 性能回归 | N/A：无同配置真实入口 5 轮基线 | 建立 5 轮基线后，中位数比≤1.05 | 逐轮 timings | 未建立前不得声称性能通过 |

必须记录输入文件/字节/文档数、运行时间、输出数、持久化数、关联校验数、后台异常数。分别测 t_business、t_persisted、t_verified；前两者差值才是观测到的持久化完成差，关闭线程/读回耗时单列。未知不填 0。execution_pass 与 quality_pass 分开，pilot 不设置质量通过。

## 4. 第二阶段：默认 hybrid 与答案质量

先 review 业务查询与标签，再运行至少 20 题固定集，top-k=5；5 轮重复查询只用于时延/稳定性，统计样本仍为查询数。报告每题与宏平均 Precision@5、Recall@5、MRR、P95；有答案/无答案分开，无答案误命中和最终拒答分开。

历史 Flash 开发集 P@5=.22、Recall=.925、MRR=.81667；P@5 理论上限 .24，原 .80 门槛不可达。不能测后换分母，应在新集运行前确认相关性标注是否完整及门槛，保留原失败结论。门槛未重新确认前沿用 .80/.60/.80，不能声称质量通过。历史答案 Faithfulness=.973666、Relevancy=.778296 是独立脚本的有答案分组，不代表 Session。

生成和裁判必须看到同一份披露上下文（含来源元数据）；逐样本保持引用/输入指纹。答案 Faithfulness≥.90、Relevancy≥.80 暂沿用筛查门槛，业务最终标准仍待确认。图策略继续 pending，先只验默认 hybrid；不把启用图列为完成必要项。

## 5. 第三阶段：生产边界与操作文档

分段验收最小权限/TLS、目标部署镜像、容量/并发/磁盘、真实故障时限、管理员重试/停用/重建、usage/配额暂停。当前尚无管理员完整闭环；状态接口不是管理入口。单独确认规模后再确定数据与并发预算。

超时错误不可视为无结果。保留 query_deadline_exceeded 等错误语义；hybrid 可显式降级，vector-only 不假装成功。同步 SDK 重试/DNS/CPU 可能越过检查边界，真实故障实验前不承诺硬截止。认证错误 blocked，不自动重试风暴。

不在查询中做隐式投影迁移或删历史 Evidence。恢复依据 SQLite lease/manifest 与 point identity；客户端重开、服务重启、进程崩溃是三个不同 Case，报告必须区分。

## 6. 设计审阅与下一步

本稿仅整理现有架构的接线和分阶段验收，没有引入新的生产模块/数据模型。本轮不额外插入学习练习。设计确认后才写对应第一阶段实施计划，准备精确 runner 和最终可执行 Case；真实运行仍需 Case 确认。每个测试/Case Step 追加 `docs/memory-development-log.md`。

待决策只有优先顺序：选择推荐 A，或改为 B/C。具体语料/模型身份在 A 的 Case 准备中收敛，不在此假设授权给新仓库或新服务。
