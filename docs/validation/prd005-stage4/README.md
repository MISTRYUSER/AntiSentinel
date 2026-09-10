# PRD-005 5.4 Graph/Evidence Case

当前结果见“P1 review修复复验”；历史Case保留。仅当前fixture范围可报告`真实运行通过`、`回归通过`，尚未标记`用户 review 通过`。5.5正式运行暂停，生产/检索质量未验收。

## 2026-09-08 P1 review修复复验（当前结果）

本轮明确以下接口约定：

- GraphExpander默认只查询contains，显式传入calls/imports/inherits/tested_by在读取代次前抛ValueError。默认查询也不扫描这些未开放关系。
- graph结果采用确定性的种子/邻居交替选择，满5条keyword候选时仍保留图邻居；top_k=1选择带seed_document_id溯源的邻居。最终仍遵守top_k，返回顺序是graph选择策略，不宣称分数或检索效果改善。
- node/edge预算截断及最终候选裁剪都会传播truncated/incomplete。expand_graph统一返回ToolExecutionResult，search摘要同样暴露这些状态。
- rehydrate先检查当前incident绑定；Evidence的generation/commit必须完全匹配。绑定变化后拒绝恢复旧引用，不改写或删除旧Evidence。缺少generation/commit的旧引用返回source_identity_missing，不再猜测代次。
- 工具read_only明确指对源码事实只读；read_evidence允许通过Scope/hash/预算校验后追加内部Evidence。这一副作用写入工具描述及SourceEvidenceService说明。

正式命令：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_retrieval_case.py --case graph --output /var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-prd005-stage4-review-twbdcl53/graph --timeout 120`。外层subprocess同样限制120秒。累计keyword/vector/hybrid/graph各运行一次，4/4 exit0，重试0；5.5新诊断集未运行。

新fixture为220字节Python源码，真实parser生成6个chunk；增加4个带class词项的独立函数，使keyword查询实际有5个种子。另注入一条unresolved contains作为允许关系集合内的负例；它是验证fixture，不是实际语义调用边。保存checkpoint前检索结果5条，其中1条graph邻居；当前绑定可恢复1条Evidence，临时将binding的generation/commit分别改动再恢复时两次均拒绝，之后还原fixture绑定。

产物：[最新累计目录](/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-prd005-stage4-review-twbdcl53)，含四组stdout/stderr、各Case数据库/report，Graph另有checkpoint.json；independent-check.json由独立进程重开检查生成。

| 指标 | 基线 | 当前 | 阈值 | 差值/比例 | 证据命令/产物 | 结果 |
|---|---:|---:|---:|---|---|---|
| 新回归断言失败 | 10 | 0 | 0 | -10/-100% | tests/test_graph_review_contracts.py | 达标 |
| P1及Case focused | N/A | 12/12 | 全通过 | N/A | pytest tests/test_graph_review_contracts.py tests/test_graph_case_runner.py -q | 达标 |
| 全量回归 | 502/502 | 513/513 | 全通过 | +11/+2.19% | python -m pytest -q | 达标 |
| 满额keyword种子/可见邻居 | 未验证 | 5/1 | 5/≥1 | N/A | checkpoint.json + independent-check.json | 达标 |
| 负例拒绝 | 5/5 | 11/11 | 全拒绝 | +6/+120% | graph/report.json | 达标 |
| 完整性检查 | 13/13 | 15/15 | 全通过 | +2/+15.38% | graph/report.json | 达标 |
| Evidence/恢复 | 1/1 | 1/1 | 1/1 | 0/0 | SQLite + checkpoint | 达标 |
| 当前绑定/内容hash匹配 | 未覆盖绑定变更 | true/true | true/true | N/A | independent-check.json | 达标 |
| 累计Case/失败/重试 | 4/0/0 | 4/0/0 | 4/0/≤2 | 0/0/0 | 四组CLI日志 | 达标 |
| Graph耗时 | 不同fixture | 76.78ms | ≤120000ms | 不作性能比较 | graph/report.json | 达标 |
| t1/t2/lag | 不同fixture | 74.15/76.78/2.62ms | 明确记录 | N/A | graph/report.json | 记录 |

本轮focused 12 passed、0 failed、1.24s；全量513 passed、0 failed、7 warnings、16.63s；git diff --check退出0。独立核验：checkpoint候选5、可见graph1、truncated/incomplete=true/true、Evidence行1、源码39字节[15,54)、当前generation1/commit匹配、SHA-256一致、SQLite integrity=ok、外键错误0、memory_records0。

当前Case使用脚本模型，worker0、外部模型调用0、脚本异常退出0。这些不是生产并发/Embedding/Standalone验收；语义关系仍关闭，hybrid+graph质量和冻结评测仍未通过。用户review状态保持待确认。

## 2026-09-08 加固复验（历史结果）

命令：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_retrieval_case.py --case graph --output /var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-prd005-stage4-hardened-wjk6j5sw/graph --timeout 120`。外层subprocess同时施加120秒超时。

输入是54字节Python fixture，由PythonAstParser生成2节点/2chunk；CodeMapStore正式发布归档代次1。修改当前nodes/edges后重新查询，仍返回归档中的正确方法。真实Runtime注册retrieval工具，由脚本模型依次搜索图、从返回的chunk定位读Evidence；两回合后保存checkpoint，再从磁盘重载并恢复来源，最终答案只引用实际披露的Evidence。脚本模型不证明LLM诊断质量。

产物根目录：[累计验证目录](/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-prd005-stage4-hardened-wjk6j5sw)。内含keyword/vector/hybrid/graph各自数据库及报告、四组原始stdout/stderr。Graph额外持久化checkpoint.json；独立进程核验写入independent-check.json。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据命令/产物 | 结果 |
|---|---:|---:|---:|---|---|---|
| 全量回归 | 479/479 | 490/490 | 全通过 | +11/+2.30% | python -m pytest -q | 达标 |
| 新负例首次失败→修复 | 8 | 0 | 0 | -8/-100% | tests/test_retrieval_graph_boundaries.py | 达标 |
| 累计本地Case | 未累计复跑 | 4/4 | 4/4 | N/A | 四组CLI stdout | 达标 |
| Graph完整性 | 基础Case无综合门槛 | 13/13 | 13/13 | N/A | graph/report.json | 达标 |
| generation/repo/commit/hash/预算负例 | 常量或未执行 | 5/5拒绝 | 5/5 | N/A | negative_results | 达标 |
| 预算拒绝后新增Evidence | 未验证 | 0 | 0 | N/A | budget_rejected_evidence | 达标 |
| 图扩展/unresolved | 1/1 | 1/1 | 1/1 | 0/0 | graph/report.json | 达标 |
| Runtime工具调用 | 0 | 2 | 2 | +2/N/A | checkpoint.json | 达标 |
| Evidence/恢复/引用 | 1/1/未验证 | 1/1/1 | 1/1/1 | N/A | independent-check.json | 达标 |
| 实际源码字节及范围 | 基础fixture不同 | 39，[15,54) | hash重算一致 | N/A | independent-check.json | 达标 |
| Graph端到端 | 49.16ms（不同fixture） | 85.95ms | ≤120000ms | 不作性能比较 | graph/report.json | 达标 |
| t1/t2/差值 | 旧版共用时间戳 | 85.17/85.95/0.78ms | 明确记录 | N/A | graph/report.json | 达标 |
| 重试/Case失败 | 0/0 | 0/0 | ≤2/0 | 0/0 | 四组CLI | 达标 |

全量回归本轮为490 passed、0 failed、7 warnings、19.49s；`git diff --check`退出0。独立新进程核验SQLite integrity=ok、外键错误0、memory_records=0，checkpoint source refs、Attempt EvidenceRef、SQLite Evidence同一ID；归档名与被修改的当前名不同，39字节源码SHA-256一致。

运行状态：Runtime完成；持久化状态：SQLite与文件checkpoint均独立重开；本地脚本模型外部调用0、worker0、异常退出0。后台异常0仅针对本次同步验证，不代表异步worker已验证。

边界：只开放验证过同文件及父子范围的contains结构关系，calls/imports等语义边保守关闭并标degraded。Graph种子当前仍由keyword产生；hybrid+graph质量对照、真实Embedding、Standalone、生产容量和20+独立查询冻结评测未完成。归档仍一次加载整个代次，图输出预算不是存储读取容量上限。旧hybrid fixture的20次查询来自4条问题重复，不能充当20条独立评测集。5.5不据本次通过自动标Done。

## 2026-09-08 正式运行与独立核验

命令：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_retrieval_case.py --case graph --output /tmp/antisentinel-prd005-stage4-e85zBIZE/case --timeout 120`。

产物：[report.json](/tmp/antisentinel-prd005-stage4-e85zBIZE/case/report.json)、[facts.sqlite](/tmp/antisentinel-prd005-stage4-e85zBIZE/case/facts.sqlite)。退出码0，首次执行，重试0。以下仅是当前手工构造 SQLite fixture 的观测，不能外推真实仓库图关系正确率或完整 Runtime 接入。

| 指标 | 基线 | 实际 | 阈值 | 差值/比例 | 证据 | 结果 |
|---|---:|---:|---:|---|---|---|
| 正式Case次数 | 0 | 1 | 1 | +1/N/A | CLI | 已执行 |
| 输入文件/字节 | 0/0 | 1/31 | 1/31 | +1/+31 | report | 符合fixture |
| 扩展节点 | 0 | 1 | 1 | +1/N/A | 独立GraphExpander调用 | 达标 |
| unresolved提示 | 0 | 1 | 1 | +1/N/A | 独立GraphExpander调用 | 达标 |
| Evidence持久化/恢复 | 0/0 | 1/1 | 1/1 | +1/+1 | 新建store/service后读取 | 达标 |
| 源码hash重算 | N/A | 1/1 | 100% | N/A | hashlib.sha256 | 达标 |
| 源码范围/字节 | N/A | [16,31)/15 | 与chunk一致 | N/A | SQLite chunk + restored slice | 达标 |
| SQLite完整性/外键错误 | N/A | ok/0 | ok/0 | N/A | PRAGMA integrity_check/foreign_key_check | 达标 |
| 耗时 | N/A | 49.16ms | ≤120000ms | N/A | report | 达标 |
| 稳定产物数 | 0 | 2 | 2 | +2/N/A | 目录枚举 | 达标 |

报告返回 `case_pass=true`、`graph_degraded=true`、`truncated=false`。独立检查 `memory_records=0`。无worker运行，后台异常计数0不构成异步运行验证；t1=t2=49.16ms、lag=0为runner共用时间戳，不是独立测量的持久化延迟。

验收限制：scope_leaks为runner常量，未通过本次负例验证；GraphExpander接收generation但查询当前nodes/edges表；resolved/AST标签不等于关系已验证；预算拒绝前SourceEvidenceService已写Evidence；Runtime工具目前仅定义，未通过Loop/checkpoint全链路。完整5.4与累计5.1–5.4真实Case仍未通过，不推进下一阶段。

本轮最终回归：`/Users/xuewentao/miniconda3/bin/python -m pytest -q`，479 passed、0 failed、7 warnings、25.36s。与历史479/0相同，通过率100%；历史15.87s→25.36s（+9.49s/+59.80%），运行条件与单次全量测试不足以评价五轮同fixture性能门槛，性能待验证。`git diff --check`退出0。

## Case

- 范围：已发布 Code Map snapshot 上的受控关系扩展、unresolved 提示、源码 hash 回读、Evidence 持久化/rehydrate 和 Runtime tool manifest。
- 输入：1 个 repo/snapshot/commit，2 个函数节点，1 条 resolved contains 边，1 条 unresolved calls 边，1 个源码 blob 和 2 个 chunk；Graph seed 为 root，Evidence 候选为 child chunk。
- 运行：`/Users/xuewentao/miniconda3/bin/python scripts/run_code_retrieval_case.py --case graph --output <全新隔离目录> --timeout 120`。仅写 SQLite，不启动 Milvus/Embedding。
- 观测：`facts.sqlite`、`report.json`；检查 expanded node、unresolved edge、Evidence 数量、hash_verified、rehydrate、Scope leak、后台异常和 Runtime tool 定义。
- 预期：resolved 扩展 1 节点；unresolved=1 且 `graph_degraded=true`；Evidence=1；hash/rehydrate=1；Scope leak=0；后台异常=0。
- 失败判定：unresolved 被当作确定边、跨 repo/snapshot 扩展、hash 不一致仍进入 Context、Evidence 未持久化/不可恢复、预算超限未标 truncated、后台异常>0。
- 清理：仅删除本 Case 创建的 `<全新隔离目录>`。

## 基线与指标

运行前基线：Graph/Evidence 真实 Case=0，扩展节点/Evidence/hash verified=0；5.3 全量回归=473 passed/0 failed。Milvus/Embedding 不在本 Case 范围。

| 指标 | 基线 | 目标阈值 | 证据 |
|---|---:|---:|---|
| 输入文件/blob/chunk | 0/0/0 | 1/1/2 | report + SQLite |
| resolved 扩展节点 | 0 | 1 | report.json |
| unresolved 边提示 | 0 | 1 且 degraded | report.json |
| Evidence 持久化/rehydrate | 0/0 | 1/1 | SQLite + report |
| hash_verified | 0 | 1 | report.json |
| Scope leak | 0 | 0 | report.json |
| 后台异常/失败 | N/A/0 | 0/0 | report.json |
| 业务/持久化完成差值 | N/A | 明确非负毫秒值 | report.json |

预算：120 秒，最多 2 次重试；Graph 只使用 resolved AST/tree-sitter 关系，真实 Standalone/Embedding 不在此 Case。
