# PRD-004 Skill / Plugin 一手资料研究

日期：2026-09-05。本轮输入是 Skill / Plugin Integration，旧 Code Map 研究不作为本期设计依据。页面为本轮实际查询的官方文档及官方仓库；浮动文档不是冻结评测数据版本。

## 参考项目事实

1. [Codex Skills 官方入口](https://developers.openai.com/codex/skills/) 当前重定向至 [Build skills](https://learn.chatgpt.com/docs/build-skills)。文档说明初始披露名称、描述，选中后读取完整指令；Codex 初始列表另带路径，列表有独立预算；支持显式和隐式选择。这只能支持渐进披露设计，不能证明它实现了本项目要求的每运行单Skill、权限集合或不可变版本快照。
2. [Agno Skills overview](https://docs.agno.com/skills/overview) 描述 Browse、Load、Reference、Execute 四层，并展示 `get_skill_instructions`、`get_skill_reference`、`get_skill_script` 三个工具。
3. [Agno Creating Skills](https://docs.agno.com/skills/creating-skills) 描述元数据、Markdown 指令、可选 scripts/references，列出元数据校验规则；允许工具声明不应推导为本项目的执行授权。
4. [SkillsBench 官方仓库](https://github.com/benchflow-ai/skillsbench) 当前 README 强调组合多个 Skill 的任务，支持先运行 oracle，默认 Modal 并可指定 Docker；这给本期单Skill、只读任务子集带来适配风险。没有运行 oracle、没有冻结 commit、没有计算可用子集数量。
5. [BFCL 官方仓库](https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard) 提供独立评测工具，PyPI 包名为 `bfcl-eval`，需要独立配置及结果目录。本项目 Task/ToolCall 转换后的测试须保留差异，不能直接声称官方成绩。

查询证据：本轮 web open 上述5个官方页面；重点阅读 Codex progressive disclosure、Agno overview 163–189行、Creating Skills 145–181及281–348行、SkillsBench README Quick Start。没有引用二手文章，也没有声称完成源码级安全审计。

## 本项目建议／推断

- 保留 `capabilities/` 唯一编写入口；JSON manifest 与 Markdown 正文分离。2个内置只读动作 `skill.load` / `skill.read_reference`，不暴露脚本执行工具。
- 本地不可变版本包是为满足“真正按需读取”和“运行固定快照”提出的项目设计，不是上述参考项目的已核实特性。构建产物仅复制登记资源，服务端配置指向已发布版本；不引入在线安装、市场或热更新。
- ToolRegistry 维持静态上限，Context 只披露本模型回合的子集；Executor 独立验证相同范围。一次模型响应中先load后调用尚未披露工具也不放行。
- skill工具结果须有专用结构化通道：当前摘要回灌无法保留资料文本，且不得使其进入用户偏好抽取。
- 评测需要先筛选和冻结，再形成每组至少3次的A/B/C试验。数据来源记录、许可证审核、oracle验证属于4.5工作，当前数量N/A，不能伪填case_id或commit。

## 本地接口证据与数字

`pytest -q`=288 passed、0 failed、1 warning、2.86s；`rg --files src -g '*.py' | wc -l`=147；测试文件64。详情见 `../validation/prd004-skill-design-baseline/pytest.txt`。

复现读取：`cat src/antisentinel/worker/runtime/messages.py src/antisentinel/worker/execution/tool_executor.py`；`rg -n 'manifests|cached|non_convergent|pending_results' src/antisentinel/worker/runtime/loop.py`。

发现：全量manifests进入每轮Context；Executor尚不检查read_only字段；缓存命中先于执行校验；messages要求工具后直接final，回灌仅摘要；checkpoint不含Skill状态。当前生产能力目录只有3个包初始化文件。研究识别边界，不代表已修复。

## 2026-09-05 版本兼容补充

本次实质更新前重新打开Codex Build skills与Agno Creating Skills两份官方文档。Agno展示metadata.version，不能据此推导多版本保留、依赖兼容检查或回滚保证；上述资料未建立本项目需要的发布兼容性承诺。新增ReleaseLock、精确依赖绑定、旧版本恢复及保留策略均为本项目建议。

本地复查命令：`rg -n 'class RuntimeSnapshot|completed_invocations|schema' src/antisentinel/worker/runtime/checkpoint.py`；当前checkpoint没有Skill发布锁。设计新增V1/V2/V3三组规则，后续必须用真实Case证明，不能用文档计数替代功能验收。

## 2026-09-05 工程约束纳入实施计划

用户确认4项P0（开发工具链、激活原子性、运行隔离缓存、行为契约）及2项P1最小接口（可用性、降级停用）。本次实质更新前再次读取Codex官方Build skills的描述匹配/目录预算说明，以及Agno官方Creating Skills的元数据校验章节。其事实支持描述作为选择输入、静态校验应明确；事务、缓存隔离、命令退出码及停用语义是本项目设计，不能归为上游已实现保证。

研究来源仍为上文已记录官方链接；未启动模型、服务或评测，本次不新增效果数据。
