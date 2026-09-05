# Memory 演进调研（2026-09-05）

## 范围与证据等级

对象为当前 AntiSentinel 工作目录。任务为 architectural：跨 Memory、Persistence、Runtime Context 和 Evaluation。本轮仅检查、离线测试、纯内存诊断与候选设计，无模型下载、API 调用或真实服务 Case。目录无 Git 元数据，无法绑定 commit 或比较工作区 diff。

当前代码优先于旧 PRD 和旧日志。历史指标只作历史参考，不能当本轮实测。外部 main 分支资料可能移动，以下列明本轮实际打开的来源；没有固定上游 commit。

## 1. 当前基线

| 指标 | 历史/基线 | 本轮 | 阈值与解释 |
|---|---:|---:|---|
| 源码文件 | N/A | 141 | 盘点，无质量阈值 |
| memory / persistence / evaluation 文件 | N/A | 13 / 14 / 7 | 包含 __init__.py |
| 测试文件 / test_ 函数 | N/A | 50 / 213 | AST 函数数，不等于参数化用例数 |
| 全量测试 | 224 passed（旧日志） | N/A | 未重跑全量，禁止宣称无回归 |
| 本轮针对性测试 | 无同范围历史结果 | 44/44，0 failed，0.64s | 本轮阈值 44/44，达标 |
| 纯内存诊断正确行为 | N/A | 0/5 | 目标 5/5，待修复 |
| 真实服务 Case / 模型调用 | 0 | 0 / 0 | 本轮只评估 |
| 线上延迟/吞吐/错误率/后台异常 | N/A | N/A | 未运行线上 Case |
| 原始验证产物 | 0 | 3 | probe.py、probe.json、pytest.txt |

命令：

```sh
python3 -m pytest -q tests/test_memory*.py tests/test_local_memory.py tests/test_local_vector_memory.py tests/test_sqlite_memory_search.py tests/test_operator_graph.py
PYTHONPATH=src python3 docs/research/memory-evolution-20260905/probe.py
rg -n 'LocalVectorMemory|BGE_M3Embedder|MultilingualE5SmallEmbedder' src scripts tests -g '*.py'
```

源码数量复现：用 pathlib 对 src/antisentinel、memory、persistence、evaluation 和 tests 分别 rglob('*.py')；test_ 函数数用 ast.walk 统计 FunctionDef/AsyncFunctionDef。

### 实际读写结构

```text
RuntimeResult
  → MemoryRecorder
    → SQLite Event / Evidence / State + JSONL audit
    → SessionTimelineTree（进程内）
    → RolloutMemory
    → RuleCandidateExtractor → queue → classifier → preference / episodic

Runtime Context
  → list_by_operator（取用户全部记录）
  → MemoryRecall
  → evaluation 模块中的 Filter / Rank / Resolver / Assembler
  → digest + refs → ContextBuilder → model

旁路能力：SQLite FTS5 search、LocalVectorMemory
```

`entry/application.py` 默认选择 SQLite，Redis 可选；worker 仅在配置 Redis 时自动启动。事实源实际为 SQLite primary + JSONL audit，不能沿用旧文档“SQLite 只是全部事实的可删 projection”的简化说法。重建范围必须按表定义。

## 2. 五项可复现缺口

原始输出见 [probe.json](memory-evolution-20260905/probe.json)。以下是独立代码探针，不是生产事故统计。

| 缺口 | 当前实测 | 目标 | 证据位置 |
|---|---:|---:|---|
| Candidate ID 缺作用域 | 2 个不同用户/Session 候选只有 1 个唯一 ID | 2 个 | memory/candidates.py；recorder.py 用 type:candidate_id 持久化 |
| 正文契约不一致 | text 输入 2 字，Recall 输出 0 字 | 保留 2 字 | recorder.py 写 text；recall.py 仅读 content/SPO |
| 来源类型混淆 | 1 个 Session ID 转成 EvidenceRef | 0 个 | recall.py legacy source_ids 转换 |
| 有效期使用极远未来时间 | 2099 年生效记录召回 1 条 | 0 条 | recall.py 固定 query_time=9999… |
| 总预算分别使用后拼接 | 20 token 预算，输出 121 字；assembler 同口径估计 61 token | <=20 | Recall 分别预算 digest 和 records |

ID 重复与 append 按 memory_id 去重组合，会造成不同 Session 的 episodic 记录被当成同一条；探针量化的是 ID 冲突，并未执行数据库写入。预算探针使用受控 digest stub，不是 tokenizer 真实 token 数。

其他静态检查得到的风险，尚未做故障注入：

- `RolloutMemory.record` 没有 operator_id，status 使用 completed/failed；在线查询按 operator_id 查，Filter 仅接受 active。当前 Rollout 记录与在线检索契约不一致。
- `process_one_memory_job` 的 classifier 抛错时没有调用 queue.retry；内存队列 claim 后移除 pending，worker 只记错误，可能留下不可再次 claim 的作业。Redis 有 lease，但 retry 当前无退避、终止次数和死信策略。
- `LocalVectorMemory.search` 当前 Python 全量 cosine scan；存储 content_version，却未在 JOIN 中比较向量版本与记录版本。upsert 的主记录和向量为分开的事务。
- `EvidenceRefResolver` 只分类引用，没有回源验证 Evidence 是否存在、hash/version 是否匹配；引用数量不能替代证据完整性。
- Session digest 从最早节点顺序截断；可能牺牲最近结论和未决假设。未量化诊断影响。

## 3. 模型与评测漂移

旧日志写 BGE-M3 1024 维、首次加载 120 秒超时；本轮源码为 `MultilingualE5SmallEmbedder`，模型 `intfloat/multilingual-e5-small`，384 维；`BGE_M3Embedder` 仅是其兼容别名。不能继续用类名推断模型身份。当前 src/scripts 中 LocalVectorMemory 只见定义，实际调用见 3 个 FakeEmbedder 单测；未找到在线调用路径。

旧日志 470 个非拒答 Case：Recall@5=77.78%、Precision@5=36.09%、MRR@5=78.21%。本轮未重算，P95=N/A。FTS5 0/30 是历史结果，不能据此判定当前 FTS5 的表现或把失败全部归因于语义改写。

`evaluation/longmemeval.py` 的 keyword baseline precision 使用命中数/实际返回 session 数；技能 P@5 定义是命中记录数/5。Session 级和 MemoryRecord 级不能混用，历史指标不能直接用来验收新链路。若查询只有 1 条相关记录，则固定分母 P@5 上限为 20%；必须保存每条 query 的全部相关记录数，单列可达性，不能改分母抬高分数。

建议固定 dataset SHA、query 集、相关性标注、top-k=5、索引/模型/提取 prompt 版本，时间也固定；当前 Ranker 的 recency 依赖 datetime.now，回放可重复性需要注入时钟。

## 4. 参考项目事实

### Codex

本轮打开：[官方 Memory README](https://github.com/openai/codex/blob/main/codex-rs/memories/README.md)。旧 ext/memories/README 路径返回 404，已找到新位置。

公开实现描述 2 个后台阶段：按 rollout 抽取并将结果存入 DB；随后持有全局锁，对有界输入做 consolidation。失败作业退避；选择考虑 usage_count 与 last_usage/generated_at；读路径拥有引用和使用统计职责。以上不能证明本项目已经具备相同能力，也不能证明所有 Codex 部署行为相同。

### Agno

本轮打开：[Memory overview](https://docs.agno.com/memory/overview)、[Production best practices](https://docs.agno.com/memory/best-practices)。

官方区分跨 Session 的 user memory、Session history、Session state。自动和 agentic 是两种更新方式；同时开启时 agentic 优先。生产建议强调稳定 user_id、裁剪、token 监控和真实规模测试；agentic 更新可产生嵌套模型调用。文档示例成本不当作本项目实测。

## 5. 本项目建议（推断）

- 先固定 identity、content、typed provenance、lifecycle status、validity 和 budget 契约；这 5 项探针应先从 0/5 变成 5/5，再谈检索收益。
- 吸收 Codex 的“抽取→持久化中间结果→有界整合”，按 operator/授权项目边界做整合，不混合所有用户。
- 吸收 Agno 的 memory/history/state 分层及受控更新；保留 AntiSentinel 的 Incident/Evidence 模型。
- 后续按三条路线取舍，见 [候选设计](../superpowers/specs/2026-09-05-memory-evolution-design.md)。任何效果目标均待同范围重测。
