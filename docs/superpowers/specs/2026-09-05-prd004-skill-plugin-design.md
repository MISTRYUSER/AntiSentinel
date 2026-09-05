# PRD-004 Skill / Plugin Integration 中文设计草案

日期：2026-09-05。类型：architectural。草案待用户选择方案及review，尚未定稿；不推进实现阶段状态。依据为本轮粘贴PRD，旧 `2026-09-05-prd004-code-map-design.md` 不属于本期。

## 1. 范围、目标与基线

本期串起本地可信 Plugin → Skill摘要 → 显式指定或模型load → 指令/资料 → 已有ToolExecutor → 诊断、持久化与Tracing。首个生产Skill是diagnosis；测试目录可含多个干扰Skill。保留无Skill调用，运行支持有序激活多个Skill，维持既有Task/ToolCall协议。

不做Code Map/RAG、Plan/DAG、市场、下载执行、热更新、进程隔离或通用脚本工具。插件格式仅供AntiSentinel使用；不新增Agno或Codex运行依赖。高风险执行仍不在本期。

| 指标 | 历史基线 | 本轮 | 阈值／说明 |
|---|---:|---:|---|
| Python源文件 | 147 | 147 | 设计期生产代码变更0 |
| 测试文件 | 64 | 64 | 设计期测试文件变更0 |
| 测试通过／失败 | 288／0 | 288／0 | 288全通过，失败0 |
| 单次测试耗时 | 2.61s | 2.86s | +0.25s，+9.58%；非性能实验，不宣称≤5% |
| 真实Skill Case | 0 | 0 | 未确认前不执行 |
| 加载延迟／吞吐／后台异常 | N/A | N/A | 尚无真实运行 |

命令：`pytest -q`、`rg --files src -g '*.py' | wc -l`、`rg --files tests -g 'test_*.py' | wc -l`。HEAD `9d998849cf53b44e48cf745628058a3dde31dead`；原始输出见 `../../validation/prd004-skill-design-baseline/pytest.txt`。1条既有Starlette弃用警告。用户已有文档改动保留。

设计交付门槛：3个候选方案、5个阶段、24条验收映射；占位符0；生产/测试代码差异0。实现阶段回归要求既有288项通过数不下降；同fixture同环境5次取中位数的既有无Skill路径性能不劣化超过5%，未测得可靠基线前标N/A。

## 2. 候选方案与关键语义

严格按需读取、旧运行能读取旧资料、资源原地被覆盖，这3个条件不能仅靠一个hash同时满足。建议把可编辑源目录与运行绑定版本分开。

| 方案 | 注册正文读取 | 运行期读取 | 原文件变动后的行为 | 代价 |
|---|---|---|---|---|
| A 推荐：本地不可变版本包 | 0字节 | 仅加载需要的登记资源 | 继续读旧发布版本 | 增加1个本地打包步骤，保留旧版本 |
| B：运行开始缓存全部资源 | 注册时0；运行前读取全部 | 缓存读取 | 继续读旧字节 | 每运行内存与I/O随全部资源增长，弱化真正按需读取 |
| C：manifest hash＋首次读缓存 | 0字节 | 首次读取时校验 | 已加载内容稳定；未加载旧资料可能失败 | 实现少，但不能保证原地更新后旧运行继续读所有资料 |

A不引入安装器：开发资源仍只写在capabilities下，本地打包只生成不可变部署产物及hash清单。无网络下载、无自动激活、无任意代码执行。服务启动绑定一个已发布catalog；资源更新通过显式重建／服务重启生效，新启动实例的新运行使用新版本。旧实例的运行继续固定旧版本；恢复旧运行必须保留对应包。若希望同一进程不重启即可让新运行使用新版本，这是额外的catalog换代机制，需单独确认，不能暗中加入热更新。

以下具体设计按A展开，是供review的推荐方案，不表示用户已选择。

“同版本只注入一次”解释为每份ModelRequest中只有1个规范指令块，并且运行状态只有1次激活记录。现有请求每轮重建且不依赖provider保存会话，后续请求仍必须携带该块；不能在第2个请求删掉指令。记录实际每次传输Token，不能把1次激活误算成只支付1次输入Token。

### 2.1 版本控制与兼容性（按用户反馈补充）

目标：上线新Skill不会替换旧运行依赖的内容或工具契约。仅保留正文hash不足以做到这一点。版本存储允许多版本并存；每次运行固定一套可恢复的发布清单。本期保持显式发布／部署，不引入目录监听或后台热更新。

**V1：版本身份与发布。** 分开记录plugin_version、skill_version、manifest_schema_version、runtime_protocol_version；Skill语义版本采用major.minor.patch，发布后相同ID/version不得改变内容hash。版本号是兼容性声明，不能代替行为验证。ReleaseLock包含catalog_snapshot_id、包与Skill精确版本及资源hash、工具实现revision、参数/结果契约revision、工具schema_hash及权限标志、运行时协议版本。required_tools保留工具名称列表，锁定结果在ReleaseLock中补齐，不让模型解析版本范围。run/checkpoint/result都引用release_id和该锁；尚未选择Skill的旧运行同样固定原catalog。

发布顺序：准备独立版本产物→校验资源/工具/协议依赖→运行候选版本与受支持旧版本的契约回归→显式切换部署的默认release_id。任何门槛失败均保持原默认版本；新版本不能覆盖旧目录或修改旧锁。新运行默认采用当前部署release，旧运行及恢复沿用原release；调用方如指定skill_version，服务端只解析已发布且受支持的精确版本，不静默回退到latest。API新增可选skill_version，必须与skill_id一同提供；版本不存在返回skill_version_not_found，尚未支持的版本返回skill_version_unsupported。

**V2：工具与运行时兼容。** 升级前检查旧Skill required_tools、参数/结果契约、权限及runtime协议。相同schema不保证行为兼容，因此候选工具还必须通过旧Skill固定契约Case；报告中保留具体输入和输出断言。第一版采用保守精确锁定：工具实现或契约revision变化不自动替换旧绑定。破坏性变更必须提供可并存的显式旧版本适配器，或保留旧部署承接原release的运行／恢复；未具备其中任一项时阻止会影响旧绑定的发布。不会动态import历史任意代码来恢复。新增可选参数也必须通过契约检查，不仅凭minor版本号放行。

恢复时先解析原ReleaseLock并核验工具实现、协议和资源，再执行任何业务动作；无法满足则dependency_version_mismatch且业务handler调用0。兼容性不覆盖权限撤销：服务端权限收紧仍优先生效，应明确报告policy_revoked，不能用“旧版本兼容”绕过拒绝。

**V3：保留、回滚与退役。** 默认release从v1切到v2时，v1包、锁和依赖支持继续保留。回滚只把新运行默认值切回v1；已经绑定v2的运行不被中途改为v1。显式指定旧版本的请求在支持期内仍可创建新运行。第一版本地发布产物不提供删除／自动GC功能，保留所有已发布版本，避免引入不可靠的引用计数；占用量可统计但不以磁盘水位自动删除。未来清理必须核查持久化运行与checkpoint引用，不能仅看当前进程内活跃数。退役只能先禁止新绑定、保留未完成及可恢复运行依赖、再按明确保留策略回收，不在本期实现清理器。

拟定兼容性Case并入4.2–4.4：v1启动旧运行并保存checkpoint→发布v2→验证新运行绑定v2→恢复旧运行读取v1此前未披露资料→回滚默认v1并验证已绑定v2运行不变→尝试发布破坏工具契约版本。验收：原版本/hash错配0，旧运行恢复成功率100%，发布拒绝时原release变化0，业务越权／错版本handler调用0。各步骤仍须使用已确认的隔离Case执行，当前只是验收设计。

### 2.2 工程交付约束（用户已确认纳入：4项P0、2项P1最小接口）

**E1 / P0 开发工具链。** 新增 `capabilities/cli.py`，以 `python -m antisentinel.capabilities.cli` 提供validate、inspect、test、pack四个子命令，不做安装器。validate只检查manifest、路径元数据、依赖和预算；inspect默认只展示目录，显式 `--skill-id` 才构建该Skill的真实模型上下文预览，输出来源及估算Token；test默认只运行受信任的离线固定契约Case，不从manifest执行任意命令；pack读取全部登记字节并生成不可变版本产物。四命令调用同一套服务，禁止再写一套CLI专用校验器。结构化JSON输出，退出码0成功、2输入/契约不符、3基础设施错误；敏感配置不打印。相同源输入连续打包2次，release hash必须相同，构建时间不得参与内容身份。

**E2 / P0 激活原子性与恢复。** 新增准备态PreparedActivation，仅包含校验后的资源、预构建的规范上下文和下一版披露集合，准备期间对外状态仍未激活。读取→hash→预算→策略检查全成功后，commit_activation(expected_revision)将Skill身份、上下文、披露集合和revision作为同一个状态更新。已有available/loaded/active是审计步骤；loaded不授予工具且不能成为恢复后可执行的半状态。skill.loaded可记成功读取事实，但skill.selected/active仅在提交后记成功。默认单运行串行修改；revision检查仍防重复入口或恢复回放。提交冲突重新读取当前状态，同Skill返回幂等结果，不同Skill拒绝切换。

有checkpoint时先持久化候选状态及必要的待投递事件，再发布内存新状态；保存失败则不激活、不披露、不执行业务工具。崩溃发生在保存成功但内存更新前时，恢复以保存的revision为准。事件使用(run_id, revision, event_kind)确定性ID，重放不得重复产生业务激活；实际Span允许重试产生新Span。无持久化store时只保证进程内原子性，不能声称断电恢复。资料披露同样先校验预算后整体提交，超限失败不能残留reference_id。

**E3 / P0 运行隔离与缓存。** 不可变资源字节缓存仅按发布范围、资源hash和解码revision共享，默认单进程LRU总量≤16MiB，超过上限逐项淘汰，淘汰不改变版本身份。相同字节可以共享，选择状态、已披露reference、授权结果、pending_results与工具结果缓存必须run-scoped。读取共享缓存前仍检查当前发布范围、选择状态、资源登记和权限；缓存不保存“已授权”结论。依赖实例或工具结果若含incident/session作用域，不允许跨运行复用。最低并发Case：2个运行选择不同测试Skill，输入相同工具名/参数但不同作用域，检查状态、证据、结果均不串用。缓存命中/未命中结果内容一致，权限撤销后即使缓存命中也拒绝执行。

**E4 / P0 Skill行为契约。** 每个生产Skill随包登记 `contracts.json`（版本/hash纳入ReleaseLock），字段为case_id、input、allowed_skill_ids、should_load、should_clarify、required_tool_assertions、forbidden_tool_names、expected_result_assertions、fixture_id、verifier_id。verifier由服务端受信任代码注册，包只能引用ID，不能指定执行命令。默认固定FakeModel离线验证选择协议、工具回灌、证据关联及禁止行为；这不代表真实模型选择准确率。目录description/use_cases、正文、资料任一变化都触发该Skill契约与冻结选择集回归。diagnosis至少配1条适用、1条不适用、1条越权输入共3条契约；真实模型结果必须读取Evidence和verifier，不能靠最终文本包含某词就判任务完成。

**E5 / P1 可用性最小接口。** `check_availability(skill_id, release_id, run_policy)`返回available、reason_codes、dependency_details。按依赖/权限/只读模式/运行时协议/是否停用过滤后才构建候选目录；非授权调用方不暴露服务器路径和其他作用域能力详情。显式选择走同一检查，失败回具体原因；显式选择优先于模型自主选择，不能自行换候选。过滤只产生本运行目录视图，不改变已绑定ReleaseLock。暂不开发管理控制台或智能排序服务。

**E6 / P1 降级与停用最小接口。** 分类区分permanent、transient、revoked。unknown_skill/版本缺失/hash不符/预算/依赖错误不自动重试；本地读取遇到明确暂时性I/O错误最多重试2次，计入120秒Case和run总预算，取消立即停止。失败load同样消耗控制动作与turn预算，不能绕过max_turns=8循环加载。显式选择失败返回结构化错误；自主选择失败可以澄清或继续基础对话，但记录degraded=true，不擅自激活其他Skill。服务端发布配置可disabled_for_new_runs，保留旧绑定；安全撤销按每次执行前的policy检查阻断旧运行，返回policy_revoked。停用不修改包文件，无文件监听、无新的管理HTTP端点；通过既有注入配置/策略提供者实现。

本节只固定接口和工程门槛，不给未经测量的模型效果阈值。验收E1–E6对应下表19–24；集成与效果验证仍严格分开。

## 3. 4.1 定义、发现与装配

新增 `capabilities/manifest.py` 定义严格数据模型；`registry.py` 保存不可变索引；`bootstrap.py` 装配；`package.py` 提供本地版本产物构建／校验。源包根可配置为 `src/antisentinel/capabilities`，其manifest引用 `diagnosis/instructions.md` 等现有能力子目录，禁止另建一套编写用skills目录。

PluginManifest字段：schema_version=1、plugin_id、version、skills列表、tool_exports列表。SkillManifest字段：skill_id、version、name、description、use_cases、instructions_path、required_tools、references。skill_id严格为plugin_id/skill_name。reference条目为reference_id、description、path。发布清单另含每个登记资源的sha256与size_bytes；catalog hash覆盖规范JSON元数据、资源hash、工具名称/schema/策略元数据、schema revision。

工具导出只接受服务端可信模块及显式TOOL/TOOLS/装饰器生成的ToolDefinition；复用ToolDiscovery的提取逻辑，禁止模型提供import路径，不扫描任意函数。Python模块导入仍有代码执行效果，因此只能是服务端已信任的代码；hash不是沙箱或权限证明。代码版本升级须重启，不能原地重新import。

装配先结构校验和准备每包的临时工具集合，再依赖校验，最终一次提交可用catalog与工具集。基础工具先就绪，包工具再装配，最后Skill依赖校验。第一版不支持跨Plugin依赖另一个包独占导出的工具，所需工具必须为基础工具或本包导出，避免失败包残留工具。同一catalog snapshot内重复skill_id、plugin_id或工具名均使冲突包无效，不能按扫描顺序择一。版本存储允许同一ID的多个版本并存，但一个运行的catalog对该ID只解析到一个确定版本；历史版本用于旧运行恢复或显式版本调用，不把同名多版本同时放进模型目录。required=true的包错误阻止启动，optional包整体排除并输出诊断；错误包的工具也不可泄漏到执行集合。

资源注册仅校验元数据、路径及文件类型/大小，不解码正文。发布步骤产生真实hash，首次加载再次校验。路径拒绝绝对路径、父级跳转和符号链接；逐段受控打开、拒绝非普通文件，读取按实际字节上限而非仅依赖stat，避免检查与读取间路径替换。配置root不来自HTTP或模型。

预算建议：每包最多32个Skill，每Skill最多32个reference；单manifest≤64KiB，单正文≤64KiB、单资料≤256KiB、每包登记资源总量≤8MiB。打包用临时目录并原子发布，失败不改变现有版本；构建输出不在源资源树内。预算是拟定上限，不是性能测量。

接口：`assemble_plugins(configs, base_registry) -> CapabilityCatalog`，包含snapshot_id、skills、frozen_tools、diagnostics；`SkillRegistry.get(skill_id)`精确查找。打包接口 `build_local_package(source_root, output_root) -> PublishedPackage` 只复制清单登记资源；同版本不同内容拒绝复用版本目录，要求提升版本。

本阶段成功门槛：有效fixture登记数与清单一致率100%、错误诊断包含包/字段/原因100%、注册正文读取字节0；失败包工具泄漏0；已有tools测试及288项回归通过数不下降。

## 4. 4.2 按需读取与运行状态

新增 `capabilities/loader.py`、`runtime_state.py`、`tools.py`。`SkillLoader.load(snapshot, skill_id)`返回带版本/hash的LoadedSkill与参考目录；`read_reference(snapshot, selected_skill_id, reference_id)`只读已登记资料，不接收文件路径。LoadedSkill含规范指令、required_tools、references元数据、instruction_hash。runtime state绑定run_id、catalog_snapshot_id、selected_skill_id/version/hash、已披露reference IDs/hash、加载原因枚举及估算Token。

状态为available→loaded→active：ID及权限/资源检查→完整内容校验→按E2原子提交规范上下文与状态，下一模型请求使用新披露集合。失败记录load_failed且保持未激活；重复相同Skill幂等，其他Skill按激活顺序加入ActiveSkill集合。读reference需指定已激活Skill；只有一个活跃Skill时可省略skill_id。未知reference为unknown_reference，未激活为skill_not_active。

内置动作保留现有Task/ToolCall输出：`skill.load({skill_id, reason})`、`skill.read_reference({reference_id})`。reason限制为task_match/explicit_request/retry三个值，避免将自由文本用户输入写进Trace。snapshot由闭包绑定，不由模型选择；工具结果包含source_kind=plugin_resource、snapshot_id、skill_id、version、hash、resource_id及结构化error。指令由专用SkillContext块承载，load结果不再复制一次正文。资料进入独立reference块，摘要仅保留ID/用途。

description单项≤512字符，use_cases最多8条且各≤128字符；目录总量≤8192字符，超限显式catalog_too_large，第一版不静默丢弃条目。指令模型预算≤8192字符，超限resource_too_large而不截断；资料单次≤16384字符，累计≤32768字符，超限返回context_budget_exceeded。第一版只整份加载或报错，避免片段裁剪破坏诊断步骤。Token估算记录算法版本 `ceil(UTF8字节数/3)`，仅预算估算；与provider usage分列。

资源读到的实际hash与发布hash不同则snapshot_mismatch，拒绝替换旧缓存；已加载副本保持不变。不可变包意外损坏是失败，不自动读取新包。checkpoint保留指令/已披露资料副本、hash、选择状态及版本身份，仅存本地受控状态，不送到Trace和偏好提取。恢复校验scope/hash及服务端当前权限，允许收紧权限但禁止扩大旧上限；未加载资源仍要求旧包存在。

本阶段成功门槛：注册不读取正文、被选中指令读取1次、重复load新增激活记录0；失败指标为越界读取0；回归累计4.1及既有测试全通过。

## 5. 4.3 Runtime、Context和执行权限

保留边界：ModelPort解析 → Context/Message → RuntimeLoop → ToolRegistry → ToolExecutor → Handler。新增参数采用可选默认值，空catalog维持无Skill行为。应用层创建运行专属SkillState，禁止共享可变全局选择。

定义U为运行开始服务端允许且已注册的工具上限，B为基础工具，L为2个技能入口。未激活时 V0=(B∪{skill.load})∩U；激活后 V1=(B∪L∪required_tools)∩U。依赖中存在不在U的工具时加载失败tool_not_allowed，不能给模型展示一个不可执行的Skill。U及ToolDefinition的schema/标志必须复制冻结，不能只冻结字典键。

每次ModelRequest生成时冻结当轮Vt，并把同一集合传给Executor授权检查。一次响应包含load和一个本轮尚不可见的业务工具时，后者拒绝tool_not_disclosed；下一轮才披露新集合。检查顺序：已注册→属于U→属于Vt→只读/审批策略→参数校验→缓存查询→handler。缓存命中也不绕过授权。只读验证模式中read_only=false或requires_approval=true均拒绝，不进入waiting_approval、不调用handler。

RuntimeLoop接收运行状态及policy；内置工具通过运行专属闭包读写状态，仍经Executor。load或reference失败后本轮剩余业务调用禁止执行，结果回灌模型用于澄清或基础对话；失败不自动换Skill。不同请求不能互相激活或读取资料。

4处局部修改：

1. `messages.py` 在启用Skill时允许加载→读资料→业务工具→结论的多轮路径，移除强制一次工具后final约束；无Skill模式保持既有提示兼容。
2. `context.py` 新增目录、规范Skill指令、已披露reference块，明确source_kind与规则优先级。系统规则及服务端配置最高，包指令不能扩大工具权限。模型适配器序列化后同样核验来源与预算。
3. `loop.py` 给skill控制动作单独幂等语义，不把第二次同Skill load当业务工具重执行或立即non_convergent；持续无进展仍受max_turns=8约束。业务工具缓存既有去重保留，并先授权。
4. `checkpoint.py` 与RuntimeResult加入可选skill_state/skill_usage，旧checkpoint缺字段默认无Skill，新版本拒绝未知更高schema。显式预载发生于首次模型调用前，失败返回结构化结果且模型/业务工具调用0。

`StartSessionRequest`及`DiagnosisApplicationService.start_session`新增可选skill_id；不接受root/import/tool权限参数。API保持旧请求可用。当前 `/v1/chat/completions` 是独立直连模型路径，本期不宣称该路径已具备Runtime skill状态；验收以session启动和页面实际走该链路为准。

本阶段成功门槛：显式与自主2条路径均完成、每个后续请求规范指令块恰好1个、重复load业务重执行0；失败指标未允许/未披露handler调用0；回归288项及累计4.1–4.2通过数不下降，无Skill同fixture性能≤1.05倍。

## 6. 4.4 diagnosis、持久化与Tracing

生产示例在 `capabilities/diagnosis`：说明适用范围、证据优先步骤、参考目录和required_tools。首个真实模型Case使用固定本地/Mock只读健康状态fixture；工具返回实际fixture的Evidence，Reference只是包资料，不能冒充Incident诊断事实。现有read_health真实探针依赖Redis，未确认Redis Case前不使用它启动外部访问。

`RuntimeResult.skill_usage`只含Skill身份、资料ID/hash、加载原因、Token计数、结果引用；`entry/application.py`的结果view及持久化序列化保留它，重开后可查询。Memory记录运行使用的Skill及最终证据引用；`MemoryRecorder`的CandidateSource明确排除skill控制调用和资料正文，不能把它们当tool_summaries输入偏好分类。最终诊断仍可按既有语义记忆流程处理，不能将整个Skill正文混入其中。

Checkpoint用于中断恢复，Result/Event用于最终运行审计；沿用既有store，优先JSON字段扩展，不为了Skill新建数据库子系统。累积资料回灌从规范SkillState重建，不能既出现在tool摘要又出现在专用块。

沿用4类事件及同名Span：skill.selected、skill.loaded、skill.reference_read、skill.load_failed；另外active状态随E2激活事务提交记录。显式选择也经过同一加载服务和事件路径。Span记录request_id、run/session/turn/tool_call、版本/hash、计数和耗时；不记录正文、输入或自由文本reason。

现有TraceContext.child只是属性关联，不能单凭同名trace_id属性声称OpenTelemetry父子链成立。增加运行根Span/turn Span，model与tool为同turn子Span，skill操作嵌套在tool span内；跨先后模型/工具用turn及ToolCall因果字段关联。JSONL与SQLite导出保存真实parent_span_id，不能用parent_request_id替代。worker异步执行需要实际传播OTel context或span link；保留历史审计A3并在本阶段覆盖涉及的链路，A1/A2不顺带扩大为Memory投影重构。

页面保留现有诊断入口，自主选择可默认发生；结果区域显示选中Skill/version及资料来源，错误展示结构化码。显式选择首先由API验证，若增加页面选择器只提供服务端可用catalog，不提供路径输入框。

本阶段成功门槛：页面/HTTP最终结果与落盘Skill身份一致率100%、事件/Span因果关联完整率100%、证据引用回读成功率100%；失败指标后台异常0和正文Trace泄漏0；回归累计4.1–4.3与既有测试全通过。

## 7. 4.5 固定测评与A/B/C

新增 `evaluation/skill_integration.py`、`skillsbench_adapter.py`、`bfcl_adapter.py`，复用现有evaluation模型和报告设施；数据和依赖独立于默认CI。先离线冻结AntiSentinel用例：明确匹配、相近/干扰、信息不足、无匹配、显式指定、执行遵循、工程边界7类。建议首批14条（每类2条），这是计划数量，不是已存在数据。目录顺序用固定种子排列，标注允许集合或澄清，不强迫每例唯一正确答案。

上游子集先记录repo/commit/release、原case_id、数据hash、Skill/verifier版本、许可证、筛选排除原因，再确认真实Case。按用户2026-09-05最新要求，SkillsBench的单Skill和多Skill任务都纳入测评并分 cohort 报告，不因当前Runtime限制删除多Skill失败。先在Docker内跑参考解法及原verifier。当前用户提供ZIP包含87个任务：23个单Skill、64个多Skill；ZIP SHA-256为`2717a281b7c8a6ddc3450d24441071b59790c12f7e875a7a67b4a0151320b8a7`。由于该ZIP标记为main而非commit archive，结果身份以ZIP hash为准，不伪称固定Git commit。

单Skill cohort运行A/B/C。多Skill cohort运行官方环境A/B，并运行AntiSentinel有序多Skill激活基线；每个Skill独立版本锁、资料命名空间和指令块，工具集合取依赖并集后仍受运行上限限制。Plan负责决定子任务使用哪些Skill，DAG负责依赖调度，二者不替代Skill Runtime的ActiveSkill集合。BFCL只选允许的多候选、无需调用、简单多轮类别；协议转换标为派生评测并保留差异。

A无Skill目录/正文，显示同一业务工具上限；B显式正确Skill；C真实候选目录自主加载。业务工具权限上限、模型精确版本、采样参数、总预算、初始环境一致，Memory关闭或同只读快照。A的初始披露差异单独披露。每个适用case每组至少3个trial；正常试验次数与故障重试次数分开。无匹配/异常case不强行构造B。

报告任务verifier成功率、选择允许集合命中率、无匹配误加载率、工具选择/参数/执行正确性、输入输出和加载Token、model调用次数、耗时P50/P95及每trial波动。分母保留超时失败，基础设施错误单列不丢行。B−A和C−B同时给绝对百分点与样本数，不预写90%效果门槛。集成正确性与效果收益单独判定。

本阶段成功门槛：冻结运行记录可追溯率100%、每个适用A/B/C组trial数≥3、verifier结果可复算率100%；失败指标漏报失败trial0；回归累计4.1–4.4工程边界仍100%。单任务默认120秒，oracle/Docker若需要更多时间，先记录具体理由和新预算再确认；不启动无限时评测。

## 8. 错误语义与验收映射

必要错误码7种：unknown_skill、missing_tool_dependency、invalid_manifest、resource_missing、resource_too_large、snapshot_mismatch、skill_id_required。具体资源路径错误统一invalid_manifest附field/path原因；其他扩展码已在各节定义。装配错误带plugin/skill/字段，运行错误带版本、请求和可修复提示；资源失败不能变成成功summary。目录不猜测近似ID。

| 编号 | PRD验收项 | 阶段 | 硬门槛 |
|---|---|---|---|
| 1 | 摘要与完整内容分离 | 4.1–4.3 | 未选正文读取/披露0；激活后每请求指令块1 |
| 2 | 显式与自主选择 | 4.3–4.4 | 2条路径均有工具结果与事实依据 |
| 3 | 缺依赖/资源/重复ID/非法路径 | 4.1–4.2 | 错包登记0，handler执行0 |
| 4 | 版本快照 | 4.2–4.3 | 旧运行字节hash变化0，新部署的新运行绑定新hash，按ReleaseLock恢复 |
| 5 | 重复加载 | 4.2–4.3 | 重复激活0、重复正文块0、业务重执行0 |
| 6 | 工具越权 | 4.3 | 猜名/同轮load/缓存/恢复绕过执行0 |
| 7 | Span关联与错误复盘 | 4.4 | 同运行OTel链与request因果字段完整率100% |
| 8 | Fake与真实Loop | 4.3–4.4 | 离线可复现，真实固定Case硬门槛全部满足 |
| 9 | Domain/Runtime/Memory兼容 | 全阶段 | 288项既有测试全部通过，无Skill路径≤1.05倍 |
| 10 | 上游子集来源冻结 | 4.5 | 纳入/排除记录及版本字段完整率100% |
| 11 | 工程边界集 | 4.5 | 全部通过、禁止调用0 |
| 12 | 多候选/无匹配/澄清 | 4.5 | 7类本地用例覆盖率100% |
| 13 | A/B/C重复与成本 | 4.5 | 适用case每组≥3trial，成本字段完整率100% |
| 14 | 派生与官方区分 | 4.5 | 改动任务标注率100%，失败隐去0 |
| 15 | 不夸大效果 | 4.5 | 收益结论绑定冻结报告100%；负收益明确记录 |
| 16 | V1 多版本并存及绑定 | 4.1–4.3 | 新旧运行与显式版本调用绑定正确率100%，同版本内容覆盖0 |
| 17 | V2 依赖兼容与恢复 | 4.2–4.4 | 旧版本契约Case全部通过；不兼容发布拒绝率100%，错版本handler调用0 |
| 18 | V3 回滚与保留 | 4.2–4.4 | 回滚影响已绑定运行数0，仍被引用的旧产物删除数0 |
| 19 | E1 开发工具链 | 4.1–4.4 | 4个命令均可用；同输入2次pack hash一致，非法包发布0 |
| 20 | E2 激活事务 | 4.2–4.3 | 每个准备/提交失败点半激活状态0，恢复重复业务激活0 |
| 21 | E3 隔离缓存 | 4.2–4.3 | 2个并发运行串状态/证据0，缓存绕过授权0 |
| 22 | E4 行为契约 | 4.4–4.5 | diagnosis至少3条契约；工程契约全满足，正文/描述变更触发回归 |
| 23 | E5 可用性 | 4.1–4.3 | 不可用Skill误披露0，显式错误原因缺失0 |
| 24 | E6 降级停用 | 4.2–4.4 | 永久错误自动重试0、暂时故障重试≤2、停用新增绑定0 |

## 9. 可执行Case准备与确认边界

当前真实Case未确认，不启动服务、不读取API密钥、不写项目storage。下列是拟交付runner契约；脚本尚未实现，不能当作当前可运行证据。设计确认后先实现对应阶段及runner，再提交具体fixture和可执行命令供Case确认。

Case：C41 本地manifest登记；范围4.1；输入1个合法包（2个Skill）、6个错误包（重复ID、版本冲突、缺工具、缺资源、逃逸路径、无效字段）。运行拟为 `python scripts/validate_skill_integration.py --stage 4.1 --output <新建隔离目录>`，输出必须不存在；无外部依赖。观测catalog.json、diagnostics.json、report.json共3份，回读校验合法Skill=2、错误包=6、错误包导出=0、正文读取字节=0；任何不符或未捕获异常即失败。清理只限本Case创建目录，不自动删除失败证据。

后续命令同runner并换stage，累计执行先前阶段：C42加载/资料/hash/篡改/重复，C43真实RuntimeLoop+FakeModel显式与自主两条路径及越权、恢复，C44本地HTTP进程+真实已配置模型+固定只读fixture+页面操作+SQLite/文件/Span回读，C45冻结上游子集oracle和A/B/C。C44的模型与凭据复用方式在该阶段按实际配置确认；本轮不凭历史成功启动真实模型。

每例报告输入字节/包/事件数量、业务完成t1、持久化flush完成t2及差值、输出数量、持久化记录数、关联检查数、后台异常数、恢复一致数、重试数。case_pass仅在进程/业务成功、预期产物读回、持久化完成、关联完整、后台异常0、恢复校验均满足时为true。指标表使用基线/实际/阈值/差值比例/证据命令/结果7列；未执行的值均N/A，不填0假装实测。每例120秒、最多2次重试，同输入连续失败2次立即定位而不扩展。

验收顺序固定：针对性/累计回归→已确认真实Case→用户review。C41的3个成功指标为登记数2、明确错误数6、产物回读数3；失败指标错误包工具泄漏0；回归288项通过数不下降。其他Case使用各阶段已列出的至少3个成功指标和失败/回归指标。

## 10. 自审与待review事项

研究对照见 `../../research/prd004-skill-plugin-research.md`；参考事实与项目建议已分开。设计自审重点：冻结每轮工具集合而非load后同轮放开；缓存前授权；资源不进入Evidence/偏好；重建请求中保留单个指令块；旧checkpoint兼容；Trace属性与实际父子Span区分。

目前3项主要风险：版本产物保留及损坏恢复；现有模型提示/回灌/非收敛约束调整；SkillsBench与单Skill只读边界相容的子集可能不足。本轮按推荐A及用户确认的版本兼容、4项P0和2项P1最小接口生成实施计划。实施开始前核对本设计与计划，任何超出此范围的新接口需回到设计review；真实Case仍需独立确认。当前不宣称Case已确认或功能实现。
