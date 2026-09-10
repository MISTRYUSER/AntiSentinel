# PRD-005 5.5 累计评测准备

2026-09-10 用户决定先合入主分支；剩余生产/质量验收统一 **pending**，暂停继续推进。合入不改变未达质量门槛的结论，详见[剩余清单](OPEN-ITEMS.md)。

2026-09-10：[长源码切片与范围去重](SOURCE-SLICING.md)的显式 UTF-8 投影已通过 Lite/Standalone：18父块→38切片、4条Evidence恢复、重开编码0；728项全量测试、10个累计本地Case通过。旧投影不自动迁移，生产及质量验收仍未通过。

2026-09-10：[查询共享预算](QUERY-DEADLINE.md)接入默认10秒deadline、锁/SQL/RPC预算与异步HTTP取消、hybrid有余量时keyword降级；711全量测试及9累计Case通过。不是任意阻塞情况下硬截止的最终验收，图优化继续pending。

2026-09-10：[Standalone真实验证](STANDALONE-VERIFIED.md)已通过官方v3.0.1镜像的鉴权、停服/重启、修复前持久数据核验和正常Session/Evidence；另修正Strong对账读取，独立collection重试0。最终701测试通过；测试栈已停、数据保留，容量与真实Flash仍待验收。

2026-09-10：[Standalone准备](STANDALONE-PREPARATION.md)完成独立配置及连接鉴权准备，全量694测试通过；真实Case因镜像下载时磁盘不足/Docker I/O错误未启动，不能标Standalone通过。已补低磁盘空间预检，图优化继续pending。

2026-09-10 用户将图选择策略优化设为 **pending**，默认hybrid不变。最新完成边界与其余缺口见[剩余工作清单](OPEN-ITEMS.md)；以下实验保留为历史证据。

2026-09-10：[新构造问题A/B](SYNTHETIC-AB.md)按用户授权先冻结24题，再做一次配对试验；A/B Recall=.65/.60，发现整类请求被子方法替换的反例，不改默认。题目并非真实业务留出集，运行后已视为曝光诊断集。

2026-09-10：[严格配对A/B](STRICT-AB.md)完成用户指定A=hybrid/B=仅替换容器：同索引/同向量、AB/BA各60对，240正式观察；B Recall增量0.025，95%区间[0,0.075]，未通过优效门槛，不改默认。693测试通过。一次对账技术失败及其原始观察已单独保留，不并入有效试验。

2026-09-10：[图候选占位实验](GRAPH-SELECTION.md)新增仅替换容器的可选策略，本地诊断Recall相对hybrid增加2.5个百分点，但仅1条可回答查询受益、bootstrap区间包含0，默认不变。688测试通过、8累计Case通过，未声称整体优化或生产验收通过。

2026-09-10：[四路检索诊断](HYBRID-GRAPH.md)新增显式hybrid_graph实验模式与真实图归档评测，四路480观察错误0、676测试通过。图扩展后Recall相较hybrid下降2.5个百分点，默认仍hybrid，不报告优化或质量通过。

2026-09-10：[持久Ragas环境恢复](RAGAS-ENVIRONMENT.md)后隔离环境全量670通过、0跳过；360条冻结检索观察完成真实Ragas ID评分，8个累计Case通过。外部模型调用0，质量结论仍未通过。后续完整验证使用文中持久venv路径。

2026-09-10：[关闭边界专项](SHUTDOWN-BOUNDARIES.md)修复停止后继续调度、停止期间吞异常和关闭失败误报stopped；668项测试通过、2项Ragas依赖跳过，8个累计Case通过。编码中停止超时后的恢复序列为1/1/0，无重复编码。

2026-09-10：[常驻检索与应用生命周期](RETRIEVAL-LIFECYCLE.md)已通过本地 SQLite/Milvus Lite 启停、自动投影、Session hybrid/Evidence 和客户端重建 Case；659项测试通过、2项Ragas依赖缺失跳过。默认关闭，启用需显式仓库白名单；远程Worker、Standalone、容量和质量验收仍未完成。

2026-09-10：[Fusion与Graph来源一致性](SOURCE-CONSISTENCY.md)已修复代表ID/source错配、无范围误合并及Graph手工挑chunk路径；六本地Case通过，当前环境638测试通过、2项Ragas可选测试跳过。完整字节范围索引、四路评测及生产集成仍待完成。

2026-09-09：[Embedding Worker租约与恢复](EMBEDDING-WORKER.md)已通过真实进程退出/恢复Case、版本通道隔离与624项累计回归；仍未完成常驻调度、真实模型Worker/Standalone和整体生产验收。

生产整改进展：[FTS投影隔离与manifest对账发布](LEXICAL-PUBLICATION.md)已完成本轮实现、5个本地Case与609项回归；旧ready索引需显式对账发布，不自动迁移为可读。Worker/分模型通道、性能与整体生产验收仍未完成。

最新补充：[意图路由新查询对照](ANSWER-ROUTING.md)两轮各24条完成，595项回归通过；未证明路由收益，默认baseline不变。后续回到生产发布/恢复边界，而非继续调同模型评分。

最新状态（2026-09-08）：[Flash真实Embedding基线](FLASH.md)、[候选判别整改](SUPPORT-JUDGE-REMEDIATION.md)、[真实答案与Ragas评估](RAG-ANSWERS.md)已有独立Case证据；答案Case24/24、584项累计回归通过，但相关性筛查、生产Runtime、Standalone、四路图评测与发布/worker边界仍未通过。以下数值保留为早期hash诊断历史，不代表当前Flash质量。

用户继续并要求最后引入Ragas后，已执行本地诊断及Ragas ID评估；随后按用户授权查看iCode权限并自行选择企业Go仓，完成单Commit/四文件的本地样本诊断。当前execution_pass=true，quality_pass=false，case_pass=false；不标5.5或PRD整体通过。真实Embedding、Standalone、生产容量和hybrid图种子限制继续保留。

## 本轮实际结果

详见[Ragas接入与企业样本报告](RAGAS.md)。原本地双Commit语料：82文档/24独立query/三模式各5轮，keyword/vector/hybrid的P@5分别0.14/0.08/0.12，Recall分别0.55/0.2833/0.4833，均未达质量目标。企业单Commit样本：63文档/24独立query/三模式各5轮，P@5为0.10/0.06/0.10，Recall为0.50/0.30/0.50；它仍使用未训练哈希特征，不是语义Embedding。

Ragas0.4.3 ID指标已在隔离环境真实执行，企业报告360条记录、输入错误0、无模型调用。Faithfulness等需要真实回答及授权裁判，当前not_evaluated。Go语料加载已接入现有多语言parser并校验Go语法；配置与凭证文件没有入样本，企业业务程序未执行。

最新Ragas/评测focused：17 passed/0 failed（9.31s）。含可选依赖的全量回归：518 passed/0 failed、6 warnings（35.48s），基线513→518（+5/+0.97%）。pip check无破损依赖，git diff --check退出0。全量测试耗时不可当作同fixture检索性能基线。

## 本轮验证证据

`/Users/xuewentao/miniconda3/bin/python -m pytest tests/test_code_retrieval_evaluation.py -q`：12 passed、0 failed、1.41s。`/Users/xuewentao/miniconda3/bin/python -m pytest -q`：502 passed、0 failed、7 warnings、21.24s；基线490→502（+12/+2.45%），失败仍为0。测试集合变化，本次全量耗时不作为五轮性能对照。`git diff --check`退出0。

`/Users/xuewentao/miniconda3/bin/python scripts/evaluate_code_retrieval.py --preflight`：24/24独立query、20有答案/4无答案、82文档、50,833字节、预检错误0，Precision@5理论上限0.25<0.80。预检只读取Git对象、校验标签与hash；未执行检索数据库查询，不存在本阶段P50/P95、Token或成本实测结果。

## 已准备的输入与入口

固定输入为`tests/fixtures/code_retrieval/v1/manifest.json`：两个真实Git Commit、10份blob、82个解析文档、24条不同查询、20个有答案问题和4个无答案问题。每次仅从固定Git对象回读，不读取当前未提交源码作为语料。标签是待review的建议稿，含逐条解释。

`scripts/evaluate_code_retrieval.py --preflight`只读检查源码hash、标签映射、样本唯一性和理论上限。`--diagnostic`为显式诊断运行；`execution_pass`表示所选路径是否无异常且通过数据读回，`quality_pass`/`case_pass`仍需完整四组和所有验收前置，不能因诊断执行成功而置true。

指标口径：P@5分母固定5，重复结果不增加相关性数量且保留重复计数；无答案不混入P/R/MRR平均，单列误命中率；保留每次查询的结果ID、来源字段、通道贡献、错误、延迟及排名；有答案报告宏平均和最小值。异常查询仍保留在测量记录，有答案异常记0分。每模式独立查询24，五轮观测120；三模式总360次测量。

性能报告包含每模式P50/P95及五轮总耗时中位数。首次运行创建基线数据，不声称回归通过；`--baseline <旧report.json>`只在语料/标签/参数/环境指纹一致时比较，同模式中位耗时≤1.05x、P95≤1.20x。跨模式成本只记录，不套用同模式回归阈值。

## 原诊断Case方案（已执行，保留设计口径）

- 输入：上述冻结manifest，SHA-256 `6473b9ae5f596fb3dc2287a747ad24f97da15165ce7fc77f7a1cd97d2adab3d0`。
- 运行：`/Users/xuewentao/miniconda3/bin/python scripts/evaluate_code_retrieval.py --diagnostic --diagnostic-vectors --output /tmp/antisentinel-prd005-stage5-diagnostic-v1 --timeout 120`
- 外层执行用subprocess timeout=120秒约束整个CLI；内部timeout用于记录超时测量。超时保留输出和错误，不盲目继续。
- 输出：新目录中的原manifest、preflight.json、facts.sqlite、vectors.db目录、逐查询report.json；不复用已有用户数据库。
- 存储：SQLite真实FTS5，Milvus Lite3.2.1/PyMilvus3.0.1，独立版本化collection。诊断向量为64维未训练词项哈希特征；外部Embedding调用0，token和账单成本为N/A。
- 观测：两库文档/向量集合读回、来源身份校验、错误行、重复结果、五轮查询指标、旧Memory数据行数。
- 成功指标：24个独立query、所选三模式各120次观测、两库读回ID及来源字段一致率100%。失败指标：异常/错误Scope/重复输出计数0；任何非0都公开并使execution_pass=false。
- 回归指标：后续同模式五轮中位数劣化≤5%、P95≤1.20x；当前无5.5性能基线，N/A。
- 质量结果预期：Precision上限0.25<默认0.80，`precision_goal_feasible=false`；不调整门槛。本次仅完成诊断，`case_pass`不得true。标签未review、hybrid_graph、真实Embedding及Standalone仍是验收阻塞项。
- 预算：120秒，最多2次失败重试，遇同输入连续失败2次停止扩展。清理范围仅本Case创建的新目录，失败产物保留。

这次确认只授权本地诊断数据运行，不代表接受标签为人工真值、降低质量门槛或批准源码外发。后续需review标签与目标可达性后，选择更合适的语义评测集或分层指标；不能测后改题来掩盖失败。
