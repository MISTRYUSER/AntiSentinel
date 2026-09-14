# PRD-005 恢复开发调研

调研日期：2026-09-14。范围：常驻 Worker → 真实 Embedding → 正常 Session 真实 LLM → Evidence → 重启恢复。本文为设计输入，不是生产验收报告；外部模型调用 0 次、启动外部服务 0 个、修改运行时代码 0 个。保留已确认的 SQLite + Milvus 和默认 hybrid，图候选选择优化仍 pending。

## 1. 当前问题与证据边界

`docs/validation/prd005-stage5/OPEN-ITEMS.md` 记录的历史基线是 728 passed、0 skipped、0 failed，10 个累计本地 Case 和 1 个 Standalone 切片 Case。该文件明确指出真实 Embedding 和真实答案旧证据来自独立脚本，尚缺正常服务入口的一体化验收。因此当前应补集成证据，不能从“组件分别运行过”推导“整链路生产就绪”。主任务已重新运行全量测试：728 passed、0 failed、7 warnings、51.82s，见 [本轮原始输出](../validation/prd005-resumption-20260914/pytest.txt)。真实服务 Case 仍未运行。

复核命令：`cat docs/validation/prd005-stage5/OPEN-ITEMS.md`。历史一体化真实 Case 通过数为 0（指该文件所列完整链路验收），本轮实际执行 0，下一阶段目标至少 1 个；进程重启场景需另有 1 个恢复 Case。旧企业语料临时目录缺失，原授权范围不扩大。

## 2. 一手资料事实

本轮实际查询并读取了 Codex、Agno 官方源码和 Agno 官方文档。源码固定到读取时 GitHub main 返回的 SHA；这不是安装版本，也不宣称该提交已随稳定版发布。在线文档为读取日期的内容，可能随更新变化。

| 项目 | 固定提交 | 实际读取入口 |
|---|---|---|
| Codex | `3abbf9fe2c6b6910e9de61f6a0c5bb468f74b5c8` | [tool_search.rs](https://github.com/openai/codex/blob/3abbf9fe2c6b6910e9de61f6a0c5bb468f74b5c8/codex-rs/core/src/tools/handlers/tool_search.rs)、[context.rs](https://github.com/openai/codex/blob/3abbf9fe2c6b6910e9de61f6a0c5bb468f74b5c8/codex-rs/core/src/tools/context.rs) |
| Agno | `44219f8532e2fe6ce936850455f4b9a2567e4194` | [knowledge.py](https://github.com/agno-agi/agno/blob/44219f8532e2fe6ce936850455f4b9a2567e4194/libs/agno/agno/knowledge/knowledge.py) |

### Codex：工具发现与结果协议

`ToolSearchHandler` 校验非空 query 和正数 limit，以搜索引擎查找工具条目，输出 `LoadableToolSpec`；`ToolSearchOutput` 把工具规格转换为带 call_id 的模型输入项。这里检索的是工具定义，不能将其当作 Codex 内建源码 RAG 或 Evidence 存储方案的证明。[源码：tool_search](https://github.com/openai/codex/blob/3abbf9fe2c6b6910e9de61f6a0c5bb468f74b5c8/codex-rs/core/src/tools/handlers/tool_search.rs)、[源码：context](https://github.com/openai/codex/blob/3abbf9fe2c6b6910e9de61f6a0c5bb468f74b5c8/codex-rs/core/src/tools/context.rs)。

**本项目推断：**保留 Registry 发现、Executor 执行、Runtime 关联 ID 和 Context 摘要的职责分离；Evidence 全文留在存储边界，模型上下文通过既有 source_context 获得预算内的已核验片段与可追溯 ID。这是本项目已确认约束与源码分层的结合，并非声称 Codex 实施了相同的 Evidence 原文禁入规则。

### Agno：hybrid、过滤与上下文方式

Agno 官方检索文档区分直接检索、模型调用 knowledge 工具、预检索后加入上下文，并提供 hybrid 后端配置。过滤文档说明内容写入时附带 metadata，查询时按 metadata 限定候选；可用运算和检索能力依赖后端。[Search & Retrieval](https://docs.agno.com/knowledge/concepts/search-and-retrieval/overview)、[Filtering](https://docs.agno.com/knowledge/concepts/filters/overview)。

**本项目推断：**正常 Session 必须调用已有检索工具路径，而非评测脚本直接拼提示词。服务端从当前授权环境派生 scope/version，不能只依赖模型传来的 filter。查询返回 0 条、向量不可用并降级、查询超时应分别可观察；此项不要求更换 Milvus 或把 SQLite 词法召回迁到 Agno 示例的 PgVector。

### Agno：异步 API 与耐久执行不是同一承诺

`Knowledge.ainsert` 构造带内容 hash 的 Content 并 await 加载；向量写入路径选择 upsert/insert，处理异常并更新内容状态，成功后记录向量已索引标记。仅观察 async 方法正常返回不足以证明内容全部成功，需读取状态及向量结果。[固定版本源码](https://github.com/agno-agi/agno/blob/44219f8532e2fe6ce936850455f4b9a2567e4194/libs/agno/agno/knowledge/knowledge.py)。

官方另明确区分 direct `arun(background=True)` 的进程内任务与 AgentOS 耐久队列：数据库中的 run 状态不使前者在进程退出后自动恢复。这是 Agent 运行说明，不能把 AgentOS 队列能力直接归给 Knowledge ingestion。[Batch and durability](https://docs.agno.com/use-cases/document-processing/batch-and-durability)。

**本项目推断：**保留持久 Embedding 队列、租约和幂等逻辑，验收时分别记录队列业务终态、SQLite 状态、Milvus 可读性、Evidence 持久化时间。异常不能因最终 Session completed 而被忽略。重启后应核对已提交作业、未完成作业和重复向量，而不仅是重新发起一次查询。

## 3. 可选恢复路径

以下是待用户选择的方案，均不修改默认 hybrid。

| 方案 | 范围与优点 | 代价/限制 | 可测退出门槛 |
|---|---|---|---|
| A：先补完整真实 Case（推荐） | 复用正常应用生命周期、队列和 Session；以最小授权语料串起全链路 | 不能直接证明容量或真实问答质量 | 完整链路 1/1、重启恢复 1/1、后台异常 0、失联 Evidence 0、重复逻辑向量 0 |
| C：先做 Standalone 运维硬化 | 优先最小权限、TLS、部署、并发和故障时限 | 可能延后发现 Session 装配缺口 | 安全配置检查全部满足；真实故障 deadline 验收 100%；固定负载 P95 不劣化超过 5%；完整链路仍 pending |
| B：先完成真实质量基线 | 固定语料、至少 20 条业务查询与标签，真实 Flash 和生产 Session 答案评价 | 依赖语料恢复和标签 review；集成缺口仍可能使评测无代表性 | P@5 ≥ 0.80、R@5 ≥ 0.60、MRR ≥ 0.80、检索 P95 ≤ 基线 1.20 倍；未达标保留失败，不自动调图策略 |

推荐 A → B → C（完整链路→业务质量→生产硬化）；若有明确上线平台期限，可在确认后交换 B/C。推荐理由是历史记录已明确完整链路缺失，而组件正确性存在局部证据；这是一项减少未知集成面的工程判断，不是已测得的性能提升。

## 4. A 方案验收协议草案

执行前需要用户确认输入与 Case；本轮只准备设计，不启动服务。用隔离数据目录和明确允许发送到现有模型服务的语料，固定 manifest hash、文件数、字节数、chunk 数、模型标识、维度、scope/version。单 Case 120 秒、最多重试 2 次；超过预算先报告原因与新预算。同一失败输入连续失败 2 次停止扩展。

必须通过正常服务入口提交 1 份语料，等待常驻 Worker 真实 Embedding 完成；在正常 Session 发起至少 1 次带依据回答，再重启进程读取同一 Evidence，并验证恢复后的 scope/version/hash/chunk 范围。另安排 1 个可控未完成队列作业的重启恢复子场景。避免把“完成后重启可读”当作“处理中断后可恢复”。

| 指标 | 历史基线 | 本轮实际 | 下一阶段阈值 | 采集方法 |
|---|---|---|---|---|
| 正常入口完整链路 | 0 个验收 Case | 0 | ≥ 1 个成功 | 正常 HTTP/Session 事件及 Case 报告 |
| 两库目标 chunk 关联 | N/A：旧局部报告不代表新语料 | N/A：未运行 | manifest 期望记录 100% 对齐 | SQL 与 Milvus 读取结果对照 |
| Evidence 恢复完整率 | N/A：未执行新 Case | N/A | 100%，失联 0 条 | 重启前后按 Evidence ID 读取对比 |
| 后台异常（失败指标） | N/A：无本轮真实日志 | N/A | 0 | Worker、SSE producer、持久化日志汇总 |
| 重复逻辑向量 | N/A | N/A | 0 | scope/version/chunk/model 联合身份计数 |
| 本地回归（回归指标） | 历史 728 passed/0 failed | 728 passed、0 failed、7 warnings、51.82s | 全量现存测试 0 failed、0 新增 skipped | 仓库现有 pytest 命令与原始输出 |
| 持久化落后业务完成 | N/A | N/A | 必须实测；Case 总时限 ≤ 120 秒 | 单调时钟记录 t_business、t_persist 及差值 |

Case 报告还必须给出输入字节/文件/chunk 数、输出数量、持久化数量、关联检查数量、业务耗时、持久化耗时、后台异常数和重试次数。质量集按固定版本、k=5 和标签规则输出逐查询指标、宏平均和最小值；真实 Session 的答案质量单独验收，不能以检索 ID 命中指标替代。

## 5. 可复核资料命令与本轮交付

以下命令仅下载公开源码到标准输出，不发送本项目内容：

```sh
curl -fsSL https://raw.githubusercontent.com/openai/codex/3abbf9fe2c6b6910e9de61f6a0c5bb468f74b5c8/codex-rs/core/src/tools/handlers/tool_search.rs
curl -fsSL https://raw.githubusercontent.com/openai/codex/3abbf9fe2c6b6910e9de61f6a0c5bb468f74b5c8/codex-rs/core/src/tools/context.rs
curl -fsSL https://raw.githubusercontent.com/agno-agi/agno/44219f8532e2fe6ce936850455f4b9a2567e4194/libs/agno/agno/knowledge/knowledge.py
cat docs/validation/prd005-stage5/OPEN-ITEMS.md
```

本轮实际使用 Python urllib 读取以上 3 个固定源码文件，并使用 web open 阅读官方文档。研究文件产物基线 0 → 1，目标 1；来源项目 2/2，方案 3/3，真实 Case 0、运行时代码修改 0。性能/吞吐/错误率当前均 N/A，因为没有启动新 Case。剩余风险 3 类：授权语料待恢复、完整链路尚未实测、外部 main/doc 与部署版本可能不同。进入实现前必须以本项目依赖锁定版本和实际 Provider 配置复核适用性。
