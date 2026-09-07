# PRD-005A Code Map Foundation 设计草案

日期：2026-09-07。**待用户评审，未定稿；不推进实施状态。** 本稿是architectural设计。输入为同日用户附件及字节相同的 `docs/prd/PRD-005A Code Map Foundation.md`；替代旧 `2026-09-05-prd004-code-map-design.md` 的设计建议，不覆盖旧稿。

一手调研：[研究记录](../../research/prd005a-code-map-research.md)。本轮重新查询Codex/Agno并固定2个Commit、4份源码；外部事实与项目建议分开记录。实施计划将在设计确认后另写。

## 1. 范围、目标与非目标

## 1.1 多语言范围修订（2026-09-07）

真实业务仓库以多语言为常态。005A 首批支持 Python、Go、TypeScript，通过统一 ParserAdapter 产出文件、模块、类、函数、contains、imports、calls、inherits 与 unresolved 关系。Tree-sitter 作为基础语法解析方案，SCIP/Sourcegraph 作为可选语义增强；全文检索、向量索引与统一 RAG 策略仍属于后续 PRD-005。


已确认：自适应定时检查，30分钟起步、无变化翻倍、4小时封顶；固定完整Commit入队，后台异步建图。本文不重新讨论是否采用异步。

目标是让现有Loop按部署版本完成符号定位、邻居查询、源码Evidence与回答；源码是事实源，图和后续索引是投影。首期 Python/Go/TypeScript、SQLite、精确查询、保守静态关系。管理员显式提交旧部署Commit与定时发现使用同一构建入口。

不做统一RAG策略、向量索引、模糊排序、多语言调用图、Webhook、外部CI、自动GC或执行被分析源码。TypeScript企业仓库只验Git访问，不能冒充Python语义Case。

### 2.1 与 PRD-005 的范围边界

本次将范围锁定为：005A 交付固定 Commit 的源码事实、Python 符号/关系、精确查询、源码 Evidence 和 Loop 接缝；PRD-005 负责消费这些接口并设计 Sourcegraph/SCIP 关联、全文检索库（含 FTS5/BM25 评测）、向量库/Embedding、reranker 及统一 RAG 策略。005A 不引入 Sourcegraph/SCIP SDK、全文检索表或向量存储，避免在事实地图尚未通过固定 Commit 与 hash 验收前把派生索引当作事实源。

如果产品目标改为在 005A 内同时交付上述三类派生索引，必须先重新评审 PRD-005A 的范围、数据流、预算、Case 和完成定义；现有计划不会默认扩大范围。

基线：160生产Python文件、74测试文件、363/363通过、0失败、1警告、3.55秒；code_map文件0、真实Case0。证据为 `docs/validation/prd005a-design-baseline/`。扫描/查询/后台错误基线N/A，尚无实现。PRD-003/004仍保留各自未完成验收。

## 2. 三个候选方案与推荐

| 方案 | 新增运行组件/依赖（设计计数） | 事务和恢复 | 增量策略 | 主要代价 |
|---|---|---|---|---|
| A 推荐：SQLite任务+同机独立服务 | 1个服务进程，解析时最多1个子进程；0个新常驻外部服务 | 入队意图、租约、发布都在SQLite；Git缓存由进程锁保护 | 缓存变化文件AST事实，全图重算关系 | 写事务串行；大仓库性能需实测 |
| B：Redis派送+SQLite事实库 | A基础上增加1项Redis派送依赖（部署可能已有Redis） | 必须额外实现事务outbox及重复投递恢复，租约发布仍由SQLite裁决 | 同A | 2种持久化系统一致性；当前并发1收益未测 |
| C：外部索引服务+本地版本适配 | 至少1个新索引服务及1层适配 | 外部任务状态与本地snapshot/Evidence关联 | 取决于选用引擎 | 固定Commit、Python边准确性、部署权限均需独立验证 |

建议选择A。理由是PRD单机并发1与现有SQLite一致，避免立即引入跨存储派送。以上是结构数量，不是性能基准；三方案延迟/吞吐均N/A，不能声称A更快。A下初期全量构建；5A.4才增加AST事实缓存，不先做复杂依赖闭包增量。

待本次用户选择的唯一架构问题：是否以A作为后续详细计划基础？其余参数按PRD建议值暂拟，均可在评审中调整。

## 3. 模块职责与数据流

拟新增 `src/antisentinel/code_map/`，含models、ports、service、scheduler、worker、git_reader、python_parser、relations、query、source_context；持久化适配器置于 `persistence/code_map_store.py`。具体文件拆分在实施计划确认。

- RepositoryService：管理员登记/暂停/恢复/显式构建；唯一接受remote_url和credential_ref的边界。
- RepositorySynchronizer：服务专属bare缓存clone/fetch、固定SHA、对象pin、预算与错误分类；不读取开发者工作区。
- Scheduler：领取到期登记，完成同步后原子保存版本观察+构建意图；不解析源码。
- ScanWorker：持久化领取、独立心跳、隔离解析、暂存、完整性验证和有条件发布。
- PythonParser：仅AST事实，不import；RelationResolver从全部文件事实解析确定边、候选与未解析边。
- CodeMapQuery：只查已发布快照，校验repository/snapshot/commit范围，限制深度与数量。
- SourceEvidenceService：校验当前Incident和已绑定地图，保存源码Evidence，按范围和hash回读。
- Loop/Context：显式选择SourceContextSlice并按预算披露；不更改Model Port输出解析职责。

```text
管理入口 → 登记/显式提交
                ↓
调度器 → Git同步并pin SHA → SQLite事务：观察版本+ScanJob
                                  ↓
独立Worker：领取(token) → Git blob → AST事实/关系 → token隔离暂存
                                  ↓
                    完整性检查 → fenced发布事务
                                  ↓
诊断绑定repo/commit/snapshot → 5个只读工具 → SourceEvidenceService
                                  ↓
                  EvidenceRef + 受控SourceContextSlice → Loop → Context
```

控制器API不等待Git或构建。显式提交完整SHA时先持久化queued请求；Worker验证该对象可获取后构建。定时路径先fetch、resolve、pin再入队；两种trigger的任务始终持有固定SHA。只读查询既不入队，也不回退分支最新版本。

## 4. 5A.1 登记、调度与任务

### 4.1 数据与身份

新增前缀为code_map的登记、同步attempt、job、job_attempt、worker_slot、snapshot、blob、file、node、edge、chunk、deployment、diagnosis_binding表；表内每个引用必须携带相应作用域。地图表与Memory分开，绝不触发用户偏好抽取。

snapshot_id = SHA256(规范JSON(repository_id, commit_sha, parser_revision, rules_digest))。JSON字段固定、键排序；规则规范化后hash，不用拼接歧义字符串。parser_revision包含实现版本、Python语法版本、源码切片版本。node_id由snapshot_id、path、kind、qualified_name、起止位置生成；重复定义同名函数仍以位置区分。

一个去重键对应一个逻辑ScanJob和一个snapshot_id；自动尝试总数最多3（初次+2次重试），每次保存独立attempt记录。终态failed/partial不被常规检查复活。管理员显式retry建立新retry_cycle并审计，保持job_id与snapshot_id；已发布partial在新attempt暂存期间继续保持原内容，成功发布后替换该逻辑快照的可用代次。对已绑定partial的诊断，binding必须额外固定published_generation，旧代次保留，不能只绑定snapshot_id而读到重试后的另一版。

ready输入重复请求复用快照，不重建；升级规则/解析器产生新身份。显式retry需要管理命令，不是查询参数。

### 4.2 自适应检查、暂停与终态

所有调度时间为服务端UTC，与Commit作者/提交者时间无关。首次启用立即检查；首次成功current_interval=1800秒；随后无变化依次3600/7200/14400/14400秒；SHA改变重置1800秒。next_check_at严格取check_completed_at+current_interval。首次成功与变更成功更新last_change_observed_at。

sync_attempt记录独立next_attempt_at，瞬时错误最多重试2次，等待30/120秒。失败不改变observed_commit/current_interval/last_change_observed_at；耗尽后的next_check_at=失败完成时间+原间隔。认证/权限/仓库不存在进入blocked；ref_missing保留独立错误，建议同样blocked至管理员恢复。暂停取消queued和retry_wait；running可完成。恢复立即检查1次，不补所有过期周期。

调度器原子领取登记的check_token/check_expiry；Git操作期间不持有SQLite事务。同步完成后仅有效token且登记仍启用时，单事务提交observed_commit、调度时间和构建意图。即使相同SHA，只要解析器/规则变了，也可产生新输入。失败/partial终态key仍禁止自动重扫。

本期不启用可选的“仅保留最新排队Commit”合并；保留观察到的任务以减少取消竞态。后续增加合并也不能取消显式部署任务。最近ready只由与当前observed_commit及配置版本匹配的ready发布更新；历史任务成功不覆盖跟踪指针。

### 4.3 Worker租约和发布防护

BEGIN IMMEDIATE短事务中领取一个到期job，同时领取唯一全局worker_slot；全局并发1由数据库保证，而不是进程配置。每次领取递增lease_token，心跳10秒、租约60秒；所有状态更新、暂存关联和发布都校验owner/token及expiry>now。旧token影响行数0时返回lease_lost，不能写状态或发布。

Worker监督循环独立心跳；AST工作放可终止子进程，超时120秒从领取时算起，包含对象准备、解析、暂存和发布，不含排队。失租停止子进程；暂存按(job,token)隔离。租约恢复计一次新attempt，耗尽就failed。

Git缓存的真实互斥使用本机OS文件锁，不能只靠可过期的数据库租约；Git子进程超时60秒并终止进程组。缓存写操作与pin在锁内完成；pin refs按完整SHA保留，不被后续force-push/fetch覆盖，关闭自动GC。旧Worker失租不能继续发起缓存写入；被中断的Git操作必须释放进程后才释放锁。

最终发布事务重新检查job租约、全局slot、deadline、预算、已验证代次；更新snapshot的published_generation/status、job终态、跟踪ready指针及published事件。事件与发布用同一连接提交。Trace导出放在事务外，避免SQLiteSpanExporter嵌套写锁；Trace丢失令Case失败，不把已经提交的快照伪装成未提交。

### 4.4 已确认的Git前置调整

为完成5A.1 daemon 的“启动→发现固定Commit→发布→停止重启”Case，固定Commit Git读取、服务专属缓存、对象pin、tree/blob预算和本地远端安全检查提前到5A.1。该调整只移动输入边界，不移动Python AST、Chunk、符号或关系构建：这些仍属于5A.2。daemon 仅从服务端登记读取remote_url和credential_ref；模型输入不能指定Git参数。

## 5. 5A.2 最小地图、持久化与源码

### 5.1 Git与安全边界

固定argv运行Git，不经shell；管理员登记协议/主机白名单，禁止模型设置地址、Git选项、SSH命令、凭证。凭证通过运行身份SSH agent/helper，启用主机校验，非交互失败明确分类。Git配置使用专用受控环境，只允许登记的凭证来源；不加载仓库hooks/filter、不执行submodule/LFS拉取。子进程日志脱敏并限长。

`git ls-tree -r -z`按固定Commit枚举；`git cat-file --batch`按OID读取，先查对象大小再分配内存。不checkout、不读取现有工作区，不用工作区.gitignore作为规则源。模式120000符号链接全部标记excluded（比不跟随到仓库外更保守）；160000 submodule标记未展开。非UTF-8路径首期明确unsupported_path，不能静默改名。

Python文件与管理员登记配置白名单纳入；vendor、依赖、构建目录排除；二进制与不支持语言分别报告。声明范围之外的unsupported不自动导致partial；范围内解码/语法错误产生partial。失败文件仍可保留文件与blob，不能伪造符号。

### 5.2 字节、位置与Chunk

保存原始Git blob字节、Git OID、SHA256、编码、换行信息；Python使用编码声明检测后AST解析。行范围1-based且两端包含；AST列为UTF-8字节且end_col排他，不直接当作原始编码字节偏移。

源码切片按原始字节行边界提取，不归一化CRLF；源码hash针对实际返回/保存的那段字节。display_text另标编码，不能用重新编码的显示文本替代原始hash。符号范围包含装饰器起始行，列坐标另外保存AST原值。Chunk建议最多200行且不超过16KiB，长单行按原始字节区间拆分并保存byte_start/byte_end及partial_line；拆分规则进入parser_revision。任何Context二次裁剪必须保存实际范围与新hash，不能沿用完整Chunk hash冒充切片hash。

### 5.3 迁移与原子可见性

新增按版本执行的迁移器：识别已存在V1；V2引入登记/任务/租约与追踪需要的增量字段；V3引入地图/blob/代次；V4引入部署/诊断binding。迁移采用逐条SQL的事务，不在事务内依赖会隐式提交的executescript。启动同时迁移受SQLite写锁串行保护；新版本数据库拒绝旧程序写入。保留所有现有业务表和内容。

blob存在SQLite BLOB列，跨快照按字节hash复用；业务身份仍带repository/snapshot。构建数据分批写入building generation，查询只允许published_generation。发布前检查引用完整性、文件hash及统计，最后短事务切换；失败或崩溃不切换旧ready。部分构建可显式发布partial，但不更新ready指针。

输入预算10000文件、单文件1MiB、总文本64MiB；以纳入集合计输入量，另披露全部枚举/排除数量，限制枚举资源防止巨大tree无界。累计配额建议1GiB，包含Git缓存、保留源码、地图、暂存及WAL开销，检查发生在fetch前、fetch后、每批暂存及发布前。fetch使用隔离暂存缓存以便超额时保留旧缓存与已引用对象。

共享SQLite无法直接按repository准确分摊物理页：首期采取保守上界，某仓库预算计其Git/临时文件+整个共享SQLite主文件/WAL/SHM大小；UI同时展示该上界与本仓库逻辑字节数。可能因Memory增长提前拒建，是明确取舍，不能称精确仓库用量。已有数据绝不因超额删除；如该取舍不适用，另评审独立地图数据库，而不在实现中偷换口径。

## 6. 5A.3 查询、Evidence与Loop

### 6.1 接口草案

所有DTO的可选字段有显式null语义；所有只读入口先校验服务端QueryScope，包含Incident允许的repository与binding。错误返回 `{code,retryable,details}`；details脱敏。返回统一Envelope：repository_id、snapshot_id、published_generation、requested_commit、indexed_commit、freshness、incomplete、truncated、budget_reason、sync_status、last_sync_at。

| 接口 | 输入 | 输出/限制 |
|---|---|---|
| 管理 register/pause/resume/retry | 管理员登记/任务ID | 登记/任务状态；不向模型注册 |
| 管理 submit_build | repository_id、完整commit、trigger=deployment | job_id、固定输入、status，异步返回 |
| get_snapshot | repository_id、requested_commit、可选明确snapshot_id/allow_partial | 精确匹配当前登记规则/解析器或显式snapshot；缺失返回snapshot_missing或indexing及已有job_id |
| find_symbols | scope、exact qualified_name、可选path、limit/cursor | 候选及完整范围；默认20、上限100，按path/qualified_name/位置/node_id排序，cursor绑定快照代次 |
| get_node | scope、node_id | 单节点；跨范围scope_mismatch |
| get_neighbors | scope、node_id、direction、relations、depth、node_budget | 默认深度1/最多3，默认50/最多200节点；BFS visited集合，边预算最多1000；截断显式标记 |
| read_source | scope、chunk_id或node_id及受限行范围 | 原始字节来源描述、hash、范围、EvidenceRef；单次最多16KiB |

freshness描述版本匹配，sync_status描述远端健康，两者分离。旧部署版本匹配仍是matched。partial显式允许时incomplete=true；默认拒绝partial，never fallback。诊断开始持久化repository→snapshot+generation binding，恢复会话使用同一绑定。

5A.3注册上述5个只读查询工具。5A.2先提供contains关系，5A.3用contains完成邻居演示；calls/imports/inherits/tested_by在5A.4后才暴露为已支持类型。提前请求未支持关系返回relation_not_available，不返回空集冒充无关系。

### 6.2 源码披露契约

用户PRD明确允许按需源码原文进入Context，优先于技能中通用“不注入Evidence原文”默认。仅source_code按下述通道披露，其他工具仍保持现有摘要行为。

SourceContextSlice = {incident_id, evidence_id, repository_id, snapshot_id, generation, commit_sha, path, start_line, end_line, byte_start, byte_end, encoding, content_hash, display_text, truncated}。SourceEvidenceService验证绑定与权限，hash核验后返回；Evidence.content_ref是内部code-map内容地址，metadata只存来源，不复制源码正文。

ToolExecutionResult沿用单Evidence能力，源码工具每次读取一个受限片段。Loop在持久化Evidence及引用后，由可信服务根据EvidenceRef构建SourceContextSlice；不能信任模型返回或任意handler塞入的文本。ContextBuilder新增可选source_context参数，每回合总量建议32KiB、最多4片段，完整行优先；未披露或裁剪范围不可以引用为模型依据。

checkpoint只存绑定和已披露片段的引用/范围/hash，恢复时重新回读核验；事件、Trace、Memory不自动复制源码。最终回答evidence_refs必须属于本Incident且已实际披露的集合。成功查找命中不代表已创建源码Evidence。

A1/A2在本阶段源码投影复用前作为独立前置修复，按实际披露集合校验来源并记录真实生成版本；不顺带重构整个Memory。A3在5A.1首次跨进程Case前解决Trace包装器与新job载体，既有Memory队列传播回归纳入累计验收。

## 7. 5A.4 关系与增量

必做contains/imports/calls/inherits；tested_by只由明确测试代码静态调用推导，绝不表示测试覆盖率。确定解析范围：模块内唯一且未重绑定的函数、显式import别名、可唯一确定的模块属性。局部遮蔽、星号导入歧义、条件重绑定、动态getattr、多态与猴子补丁保留candidate/unresolved及表达式和调用点；不按同名直接连边。

边含resolution、basis、source位置，resolved目标必须在同snapshot/generation内；外部依赖仅保留未解析表达式，不创建跨仓库假确定边。未支持语言/关系单独统计，不进入支持范围的precision分母。

缓存key=(blob_sha256,path,module_root,parser_revision,rules_digest)；缓存是无snapshot节点ID的文件事实。增删改/重命名通过Git diff识别；不变文件复用AST事实，所有文件关系重新解析，再生成新snapshot的节点/边/Chunk。这样无需先实现反向依赖闭包，也不会遗留旧目标边。

全量与增量使用同一关系解析器。规范化输出仅忽略时间/attempt等非语义字段，节点/边/Chunk/源码hash必须完全一致。旧snapshot保持可回读。规则/解析器改变强制全量。

## 8. 错误语义、恢复与追踪

| 类别 | 处理 |
|---|---|
| auth_failed / permission_denied / repository_missing | blocked，管理员修正后显式恢复 |
| ref_missing | 展示跟踪分支缺失并blocked；旧绑定查询仍可使用 |
| sync_timeout / network_unavailable | 最多2次重试，保留原间隔；耗尽保存错误 |
| syntax_error / decode_error | 文件失败清单；快照partial，不推进ready |
| unsupported_language / excluded | 披露范围及数量，不伪装已解析 |
| snapshot_missing / indexing / partial_not_allowed | 查询状态，无隐式构建与回退 |
| scope_mismatch / hash_mismatch | 拒绝输出源码，不产生成功Evidence |
| budget_exceeded / storage_budget_exceeded / integrity_failed / build_timeout | failed，不发布ready |
| lease_lost | 旧持有者停止，无权写终态和发布 |

任务保存W3C traceparent/tracestate与业务correlation/request/causation字段；消费extract父上下文，新attempt创建独立span_id。后台构建无Session，不伪造Session ID。事件因果ID指事件；request_id只关联请求。增加Telemetry的force_flush/shutdown能力，保留真实OTel trace/span树；历史业务trace_id可作为属性，不覆盖标准身份。

阶段Span包括sync/build/parse/persist/publish/query/read_source。事件类型按PRD记录，元数据不含源码或密钥。t1=构建业务结果产生时间，t2=数据库读回且Trace flush确认时间；报告t2-t1，后台异常>0一律Case失败。若持久化成功而导出失败，分别报告两项状态，不能靠API succeeded宣布Case成功。

## 9. 5A.5 测试策略与验收映射

先单测/针对性回归，再经确认的真实Case，最后用户review；每阶段累计覆盖此前范围。以下是成功阈值，不是已测成果。各阶段至少3个成功指标；失败指标统一为错误发布/跨范围引用/后台未捕获异常0；回归要求全部旧测试通过且同fixture重复5次中位耗时不超过已建基线1.05倍，首轮基线N/A。

| 阶段 | 成功指标（至少3项） | 特有失败指标 | 累计回归与依赖 |
|---|---|---|---|
| 5A.1 | 10次相同检查活动逻辑job=1；2调度器重复入队=0；间隔5个预期值匹配5/5；失租错误发布0 | 禁止自动重试的终态输入重扫0 | 363项+任务/租约测试；假构建器只验调度，不能称地图完成 |
| 5A.2 | fixture符号precision=recall=100%；Chunk hash/范围一致100%；重复ready发布1次；SQLite重开读回100% | 半发布查询可见0 | 5A.1–2累计；迁移前后所有旧表内容变化0 |
| 5A.3 | requested_commit匹配100%；来源引用完整率100%；Loop定位/邻居/源码/回答步骤4/4；恢复绑定变化0 | 未披露源码被引用0 | A1/A2修复及5A.1–3；默认仅contains邻居 |
| 5A.4 | 支持关系precision=recall=100%；增删改/重命名/导出/调用方6类变更增量=全量6/6；旧版本内容变化0 | 假确定边0 | 5A.1–4；动态调用未解析率单列，不设虚假低值目标 |
| 5A.5 | fixture/项目固定Commit/企业Git访问3类Case均有报告；持久化读回100%；孤立子Span0；5轮性能数据齐全 | 任一硬门槛失败时case_pass=true次数0 | 全部累计测试与Case；真实模型配置未确认则相关Case待验证 |

精确查询不使用FTS/BM25，Precision@5等本期N/A；符号precision/recall是固定标注实体匹配指标。真实仓库人工抽样建议至少30符号、30条边，单独报告样本与排除量，不外推100%全仓准确率。

Case提案见 [阶段Case](../../validation/prd005a-cases-proposal.md)。所有runner与新测试路径均为规划产物，当前不存在，文档命令不能当执行证据。

## 10. 调研验收、风险与下一步

本轮成功指标：官方项目来源≥2（实际2），候选方案=3（实际3），PRD阶段映射=5（实际5）；失败指标：生产代码修改=0；回归：363/363，失败0。原始证据及文档自审存于design-baseline目录。真实Case0，不报告功能已实现，不标PRD Done。

开放风险共5类：R1共享SQLite锁与保守配额可能限制容量；R2目标部署身份的企业Git权限未验；R3静态Python语义支持范围有限；R4 A1/A2/A3前置修复与Context/checkpoint兼容性；R5真实fixture性能与120秒预算尚未建立。每项在对应阶段Case验证，未将建议默认值写成容量承诺。

下一步：用户评审A/B/C及本稿；确认后按5A.1–5A.5大标题写实施计划，先准备5A.1 Case，不提前实现后续阶段。
