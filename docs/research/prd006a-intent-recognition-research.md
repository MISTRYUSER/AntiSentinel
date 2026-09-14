# PRD-006A 意图识别调研

日期：2026-09-14。范围：概念解释、官方资料核对和仓库静态定位；不是已确认设计或实施计划。

## 意图识别是什么

把用户消息与当前授权会话上下文转成可审计的需求描述：希望什么结果、针对什么对象、有什么限制、是否补充/纠正、是否需要澄清、交给哪个入口。分类只是其中一项。006A 负责理解与路由，006B 负责规划，007/008 负责实际调用的权限与执行检查。

## 一手资料事实

1. OpenAI Structured Outputs 用 schema 约束输出格式，但官方明确说明输出仍可能有内容错误。不能把结构合法当作意图正确或授权有效。
   https://developers.openai.com/api/docs/guides/structured-outputs
2. Agno Router 通过 selector 在 choices 中选择步骤；它提供工作流分支机制。这份 API 文档不能证明其自动满足本项目的归属校验、意图 revision 或幂等契约。
   https://docs.agno.com/reference/workflows/router-steps
3. Codex app-server 当前原始 README 对实验性 userVerification/cancel 明确区分取消信号确认与原请求实际结束，并说明不能回滚已完成影响。这是控制回执语义的参考，不是本项目任务取消协议；不能据此声称 Codex 使用了本 PRD 的五类意图分类器。GitHub页面与raw抓取内容有差异，具体控制API须在实施时固定commit复核。
   https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md

资料来自可变官方页面，未固定上游 commit；进入实现时应固定版本复核。

## 仓库证据与基线

- `entry/application.py:401` 的 send_message 加载历史并调用 ModelPort，tools=[]，目前是普通对话入口。
- `entry/application.py:376` 有 get_session，但读取 Session 不等于已具备带任务归属核验的 task status 契约。
- `entry/conversation.py`、`control/tasks.py` 是 skeleton。
- `domain/task.py` 有 cancel；领域状态变更不等于运行中工具已经停止。
- `ports/model.py` 现有协议围绕 TaskPlan/FinalDiagnosis；本次在 src 中搜索 ResolvedIntent 未命中。

可复现静态命令与本轮实际值：

```sh
rg --files src/antisentinel | wc -l
# 209
rg --files tests | wc -l
# 135
rg -n '^def test_|^async def test_|^    def test_|^    async def test_' tests | wc -l
# 598：静态定义匹配数，不是 pytest collected 或 passed 数
rg -n 'def .*cancel|def .*interrupt|ResolvedIntent' src/antisentinel
sed -n '376,424p' src/antisentinel/entry/application.py
```

源码/测试目录文件数本轮基线与当前值为 209/135，目标保持不变；业务 Case 执行数 0、模型分类评测调用数 0，目标均为 0。回归失败数、分类效果、P50/P95、成本、业务完成与落盘时间差均为 N/A，因为只做静态调研。598 不构成回归证据。

历史 INDEX 已读取。相关待验证提醒：恢复时分别检查会话、运行和落盘；绑定需带版本；停止执行需要真正可终止的执行边界。它们仅是验证项，不是本轮已证明能力。

## 候选方案与建议（尚未定稿）

| 方案 | 做法 | 主要权衡 |
|---|---|---|
| 规则为主 | 关键词、显式命令与有限状态匹配 | 可解释，但否定、指代和多轮表达需要持续维护；效果待测 |
| 模型提取 + 确定性校验 | 无工具模型调用输出候选，服务端校验绑定、版本与路由 | 推荐首期；语义理解与权限边界职责清晰，但模型仍会误判 |
| 专用分类器 + 提取器 | 训练分类模型，再独立抽实体与约束 | 可作为后续成本优化实验；至少30条验收集不等于充足训练集 |

建议流程：

1. 服务端验证 actor/session/message，载入当前授权结构化上下文与 context version。
2. 用 message ID、context version 和内容摘要做幂等检查。
3. 无工具模型调用产生 IntentCandidate：类型、关系、目标、实体、限制、原文来源引用、歧义。模型不填写权威 actor/权限/归属。
4. 服务端检查 schema、字段来源、目标绑定、必需字段与业务路由硬不变量。置信度不能单独放行。
5. 服务端补齐 identity，生成 ResolvedIntent；必要时只问一个阻断路由的问题。
6. 原子保存 revision 与待路由记录；通过幂等下游入口提交，保存回执。实现可考虑事务 outbox，但下游仍必须去重。
7. 回答、规划和控制各自进入明确入口。模型/校验服务失败作为技术失败，不伪装成 unsupported。

模型提示需要说明五类意图、三类关系、不能推断修复授权、显式禁止约束必须保留、缺失字段不能编造，并提供否定/指代/补充的少量例子。调用只请求结构化输出，不开放取证工具，不进入工具循环。首期正常路径可先以一次模型调用作为待验证设计目标，不承诺已测延迟。

## 示例

输入：排查 staging 订单服务部署后超时，只分析，不要重启。

候选结果：intent_type=diagnose；relation=new_request；objective=查明订单服务部署后超时原因；entities 包含 staging 和订单服务；constraints 包含只读、禁止重启；来源指向用户原文。

若服务端已绑定唯一可访问目标且当前规划所需字段足够，则 resolved/plan；否则 needs_clarification/clarify。不要仅凭句子就断言所有绑定已通过。

后续“版本是 B”：延续 diagnose，核验版本绑定并产生内容更新 revision；后续“别改配置，只分析”：correction，保存新限制，使旧路由版本失效。若已有写操作运行，经过控制入口请求停止并由006B收敛；收到执行端结果前仅报告停止请求状态。

## 需要在设计时补齐的契约缺口

1. task_control.operation=status/cancel 在表中有要求，但结构化字段表没有明确存放位置；建议增加受枚举约束的 control 字段。
2. question 需要新证据时也要 plan，应明确 intent_type 与 route 不强制一对一，避免只为路由改写用户目标。
3. 补充改变输入内容也需要新 revision/hash；应明确 relation=continuation 不意味着复用旧 hash。
4. 自动澄清最多3轮后需明确“暂停提问”与24小时“问题关闭”的持久化状态；这不是 unsupported。
5. 先保存新 revision 并拦截旧版本继续 dispatch，再提交幂等停止请求；停止失败也不能恢复旧写权限。
6. 必须核实取消入口、归属核验和执行端停止能力；缺失时不得用任意工具或直接改 Task 状态冒充取消。

## 验证建议

按用户既定6A.1–6A.4推进。真实模型分类与固定响应契约测试分开。固定集至少30条、每类至少4条；macro-F1>=0.90、路由准确率>=95%、必需字段exact-match>=95%、禁止约束保留率100%。危险歧义误放行、越权控制、意图层handler调用和旧revision路由均为0。原回归先通过，固定fixture重复5次的耗时中位数劣化<=5%。所有当前效果值为N/A；不得标case_pass=true。

本轮不推进开发阶段。后置 learning 判断：事实已保留在此调研记录；未完成实现或恢复验证，不将上述设计建议升级为已验证长期经验。

## 补充：Codex 与 Agno 实际如何决策

用户追问后的官方资料核对：

- Codex 官方工程文章说明：用户消息、历史、指令和工具定义进入模型；模型产生回答或工具调用；工具结果回灌，再次推理；最终消息也可以是追问。这里的语义判断融入主模型循环，文章没有展示独立五类意图分类器。不能由此推断全部Codex产品内部都没有其他分类机制。
  https://openai.com/index/unrolling-the-codex-agent-loop/
- Codex App Server 官方文档确认 turn/start 启动回合，turn/steer 向运行中回合补充输入且校验 expectedTurnId，turn/interrupt 请求取消，随后以 interrupted 结束。这是明确的协议控制，不证明自然语言“取消它”自动完成可靠归属解析。
  https://learn.chatgpt.com/docs/app-server
- Agno Team 的 leader 可以回答、调用自身工具或委派成员。route 模式在发生委派时选择一个成员并直接返回成员结果，省略leader汇总。determine_input_for_members=False 可透传用户输入；这不等于完整绑定和授权校验。
  https://docs.agno.com/teams/delegation
- Agno Workflow Router 的 selector 在 choices 中选分支，selector 可由代码实现；自然语言理解逻辑需由应用提供，Router 不自动实现006A契约。
  https://docs.agno.com/reference/workflows/router-steps

本项目推断：006A 更接近“无取证工具的结构化模型提取 + 确定性Router”，006B可参考Codex式循环。若直接采用拥有工具的Team leader，会越过006A边界。独立模块不意味着必须有独立模型或微服务。

验证限制：一次通过GitHub API解析main并下载源码的命令返回HTTP 404，未获得固定commit源码证据；以上结论依赖已打开的官方文章和API文档，不声称完成源码审计。补充业务Case数0，分类效果和延迟N/A；研究文件仍为1份。


## 真实DeepSeek接入补充（2026-09-14）

用户明确复用现有DeepSeek配置。官方JSON Output文档要求response_format=json_object、提示词包含json和示例，并提示可能返回空content；适配器因此单独拒绝空输出、工具调用及截断响应，再交严格IntentCandidate解析。出处：https://api-docs.deepseek.com/zh-cn/guides/json_mode/ 。使用现有deepseek-chat配置，不创建OpenAI密钥。

真实Case首轮4条请求、7次DeepSeek调用、6896 Tokens、9.83秒，问答/Plan草案/状态/取消均使用real回执。受控Runtime线程取消走真实HTTP入口与RuntimeLoop调用边界；这不证明能立即中断正在执行的外部工具。计划生成只读目录校验后落盘，不自动执行DAG或继承审批。
