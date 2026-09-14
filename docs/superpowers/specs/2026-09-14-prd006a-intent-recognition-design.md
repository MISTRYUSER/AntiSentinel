# PRD-006A 意图识别设计

日期：2026-09-14。范围：设计供用户 review，不代表业务实现、真实运行或用户验收通过。

## 1. 范围、目标与方案

006A 接收用户消息与授权 Session 上下文，生成可审计、版本化 ResolvedIntent，分流到回答、006B、任务控制、澄清或不支持。采用同进程模块，不新建微服务，不训练专用分类模型。

非目标：取证、诊断假设生成、工具步骤、DAG、执行审批、补偿，以及实现006B。006A的模型调用不挂载工具。读取结构化会话通过服务端ContextReader，不通过模型取证。

| 方案 | 收益 | 代价/边界 |
|---|---|---|
| 规则为主 | 确定性强，显式命令方便 | 否定、多轮和模糊指代需要持续维护 |
| 模型提取+确定性校验（推荐，沿用已讨论方向） | 自然语言适应性与服务端控制分离 | 分类效果需要真实模型评估 |
| 专用分类器+实体提取 | 可独立优化成本 | 需要额外训练数据与维护，30条验收集不够作为训练依据 |

具体接口与存储选择以本文件review为准。

官方事实：Codex公开主循环由模型选择回答、追问或工具调用；App Server提供start/steer/interrupt，其中steer校验expectedTurnId。Agno Team leader可回答、用工具或委派；Workflow Router用selector选择choices。上述机制不直接提供本项目ResolvedIntent契约。本项目借鉴显式控制与分支接口，不采用带取证工具的leader作为006A。

资料本轮重新打开： https://learn.chatgpt.com/docs/app-server 与 https://docs.agno.com/reference/workflows/router-steps 。详细出处和调研限制见 `docs/research/prd006a-intent-recognition-research.md`。

## 2. 现状与基线

`entry/application.py:401` 的send_message目前是普通问答；`ports/model.py` 的ModelResponse围绕TaskPlan/FinalDiagnosis，不能让IntentCandidate伪装成TaskPlan。`entry/conversation_store.py` 的消息只有session/role/content/time，没有稳定message_id。`control/tasks.py` 是skeleton；`domain/task.py:109` 的cancel只是状态转换，不能证明运行已停止。

上一轮静态盘点：src目录209个文件，tests目录135个文件，测试函数定义匹配598处；这些不是执行通过数。当前重新盘点见本轮基线文件。分类效果、测试通过/失败数、P50/P95、吞吐、成本、后台异常数均N/A：设计阶段未运行测试或业务服务；Case执行数0。不得宣称回归通过。

历史learning转为3个待验证项：恢复关联版本、运行/落盘分别验证、控制信号/停止完成分别验证。没有复用其实现结论；本轮不新增推测性长期learning。

## 3. 模块职责与接口

以下均为拟建接口，不是现有可调用API。

| 模块/建议路径 | 接口及责任 |
|---|---|
| domain/intent.py | 不可变IntentCandidate、ResolvedIntent与封闭枚举，不依赖模型SDK |
| ports/intent_model.py | extract(IntentModelRequest)->IntentCandidate；严格解析、无工具、无权威identity字段 |
| control/intent_validation.py | validate(candidate, authorized_context)->ValidatedIntent或字段缺口；禁止读取工具与调度 |
| control/intents.py | resolve(actor,message,expected_revision)->ResolvedIntent；编排上下文、版本、澄清与持久化 |
| ports/intent_store.py | 原子保存revision、current指针、幂等映射、outbox；CAS校验expected_revision |
| adapters/storage/sqlite_intent_store.py | 隔离SQLite实现；不把事务状态追加到普通聊天JSONL代替数据库 |
| control/intent_dispatch.py | 从outbox读取，复核当前revision与授权，调用固定下游入口，记录回执 |
| entry/application.py | 新消息入口接入服务；普通回答逻辑抽取为内部answer入口，避免递归进入识别 |

AuthorizedContext由服务端提供：actor_id、session_id、incident_id、context_version、已验证字段及来源、任务候选、可用路由能力。拒绝客户端覆盖actor/权限；模型不可接触凭据。读取任务状态必须通过已有授权控制服务；若该服务缺失，相关能力不开放。

新入口请求包含message_id、content、expected_revision、可选clarification_id；session由URL/服务端关联，actor由认证层取得。response包含intent及delivery状态。旧客户端兼容入口可以生成message_id，但无稳定客户端ID时不能承诺跨网络重试幂等；完整幂等验收必须使用新契约。

技术错误与业务意图分离：401/403为身份权限失败，409为内容/版本冲突，503为模型、上下文或依赖不可用，504为识别总预算耗尽；这些不包装成unsupported。

## 4. 输出契约与路由规则（6A.1）

IntentCandidate仅含语义字段及候选来源引用。服务端补齐身份与验证结果后形成ResolvedIntent。新增字段必须随schema_version升级。

| 字段 | 定义 |
|---|---|
| identity | intent_id、revision（从1开始）、schema_version、content_hash、incident_id、session_id、message_id、created_at；均服务端绑定 |
| intent_type | question / diagnose / execute / task_control / unknown |
| relation | new_request / continuation / correction |
| objective | 用户结果描述，不含步骤或推测动作 |
| entities | 按需包含service/interface/environment/repository/commit/time_window/task_target |
| constraints | 类型化限制列表：read_only、forbid_restart、deadline、access_scope；未支持限制保留原文并阻断无法检查的动作 |
| provenance | 字段路径、user/context/server来源类型、message或context引用、原文区间；模型引用必须由代码验证存在 |
| resolution_status | resolved / needs_clarification / unsupported |
| route | direct_answer / plan / task_control / clarify / unsupported |
| control | task_control时为operation=status/cancel及用户目标引用；其他情况null；可用task/run绑定由服务端另行确认 |
| missing_fields / ambiguities | 字段路径、原因码、可展示说明；无则空数组 |
| clarification | 问题ID、关联revision、问题、候选、轮数、expires_at、状态；无则null |
| context_binding | context_version、前一intent/revision、task_id/run_id/state_version；无关联明确null |
| reason / confidence | 简短理由、可选分数；不存内部推理，分数不作为权限条件 |

provenance引用合法不能保证语义正确；语义提取准确率仍须独立模型评测。原文指令、日志和代码数据使用不同信任层输入，不允许数据提升权限。

硬规则：needs_clarification只能clarify，unsupported只能unsupported，三个业务路由必须resolved。所有业务路由都需结构与绑定通过。unknown不能进入业务路由。复合独立目标保存全部候选并clarify顺序，不拆DAG。

question在授权上下文足够时direct_answer；需要新证据则plan，类型仍可question。diagnose和execute进入plan，但execute不是审批。一般概念问题不强制环境/commit。task_control要求operation明确且唯一可访问绑定；多个候选clarify。服务端能力已明确不支持时unsupported；服务异常时技术失败，不能假装能力不存在。

## 5. 澄清、版本与持久化（6A.2）

每个独立目标创建intent_id；continuation/correction沿用intent_id并增加revision。新消息ID即形成新输入版本；同消息ID但context_version更新也增加revision。新目标创建新intent并记录前一关联，不静默覆盖旧目标。控制查询/取消为独立控制意图，绑定原任务，不替换诊断目标。

幂等键为actor/session/message_id/context_version，输入内容摘要另存用于冲突检查；同键同内容返回同一解析结果，同键异内容409。content_hash采用规范化JSON的SHA-256，覆盖不变语义载荷、identity关联字段和context_version，不包含hash自身、投递状态与可变回执。澄清生命周期更新作为独立记录/事件，不原地改写已hash的revision载荷。

SQLite至少存：intent_revisions（主键intent_id/revision）、intent_heads、message_resolutions、clarifications、route_outbox、route_receipts、audit_events。revision/current/outbox/事件同事务提交，使用expected_revision比较更新。两个并发纠正只有一个获当前版本，另一请求409后重新解析；旧版不会重新成为current。

每次只问一个阻断问题；保存已有字段；默认自动提问最多3轮，达到上限标waiting_user，保留needs_clarification。每个问题24小时到期标expired，不触发动作。迟到回复重新核验session/问题/当前revision；关联失效时澄清目标而不是执行旧路由。

服务重启加载head、待答问题、outbox与回执。只重试未确认投递，不把“已resolved”视作“下游完成”。识别失败保留脱敏输入引用及失败记录；同输入最多2次修复、含等待总预算30秒，到期技术失败且无dispatch。

## 6. 交接、纠正与控制边界（6A.3）

固定接口：AnswerGateway.answer(intent_ref,context_binding,idempotency_key)；PlanGateway.accept(intent_ref,objective,entities,constraints,provenance,idempotency_key)；TaskControlGateway.request(operation,authorized_task_binding,expected_state_version,idempotency_key)。三者返回Receipt(receipt_id,status,downstream_id,error_code)，status区分accepted/completed/failed。Plan回执downstream_id为plan_id，未返回前事件中的plan_id为空。

PlanGateway可先接契约接收器；回执必须标receiver_kind=contract/real。契约接收器验收不能声称Plan联调完成。006B返回字段缺口和原因，由006A产生新版澄清；006B不得重推目标。限制完整传递，并在下游计划校验器中检查。

outbox唯一键intent_id/revision/route/operation；下游必须支持同键去重和回执查询。发送后崩溃重放仍返回原回执。仅outbox不能保证下游不重复执行；不支持幂等的控制入口不得启用。dispatch前复核current revision；下游接收及实际动作也必须复核版本，避免检查后纠正的竞态。此接收检查是006B/007/008交接契约，不能只靠006A本地锁。

“只分析”纠正：先原子保存新revision、使旧版待发路由失效并记录停止请求outbox；通过TaskControlGateway请求已有执行停止，再把收敛信息交006B。停止失败则保持新限制和待处理状态，禁止自动启动替代运行；执行端未确认时只展示“停止已请求/失败”，不能声称撤销。已发生副作用不因意图更新回滚。新计划不继承旧审批。

当前控制入口与认证绑定未证明贯通：首期契约Case使用独立接收器验证协议；生产task_control及涉及已有执行停止的能力保持关闭，直到真实入口验证。若必须完成生产取消，这是6A.3的硬依赖，不能把6A.1或领域cancel当作替代。

事件至少intent.received/resolved/clarification_requested/revised/routed/failed，均带intent/revision/message/session；Plan回执得到plan_id后关联写入。事件不保存敏感原文或内部推理。

## 7. 评估与验收映射（6A.4）

冻结至少30条中英人工标注fixture，5类各至少4条，其余覆盖异常；每项含输入/context hash、期望类型/route/必需字段/禁止动作。标注集与prompt示例分离，不能测后删题；修改标签需新版本。真实模型效果与固定模型响应的契约测试分别报告。

| 阶段 | 3项成功指标 | 失败指标 | 回归指标 |
|---|---|---|---|
| 6A.1 | fixture>=30；契约合法性断言100%；显式约束保留100% | 非法结构dispatch=0 | 原相关测试失败0；固定fixture中位耗时劣化<=5% |
| 6A.2 | 重启回读100%；纠正关联100%；澄清状态关联100% | 旧revision路由=0 | 累计6A.1行为断言100%；同上性能门槛 |
| 6A.3 | 5路分流正确100%（固定响应）；回执关联100%；限制传递100% | 越权/重复逻辑控制/取证handler调用均0 | 累计6A.1–2断言100%；同上性能门槛 |
| 6A.4 | macro-F1>=0.90；route>=95%；字段exact-match>=95% | 危险歧义误放行0 | 既有测试全部通过；固定集重复5次中位耗时劣化<=5% |

另报逐类precision/recall、混淆矩阵、禁止约束100%、危险歧义clarify100%、跨Incident混用0、回执完整率100%、P50/P95、调用/Token、澄清轮数、不必要澄清率。缺少可比性能基线则N/A且性能门槛未验证；不以小样本成绩声称生产效果。

## 8. 累计Case草案与执行边界

Case名称：C6A-intent-lifecycle。采用固定模型响应、临时SQLite、固定时钟、契约Plan与Control接收器；执行实现后提供的Case命令，当前命令尚不存在，因此本轮不执行。不得将本草案当作已确认的真实运行Case；实施阶段提供可执行命令后再确认。

输入序列：诊断缺commit→补充B→Plan接收→以测试接收器声明运行→用户改只读→停止请求及收敛回执→重启回读；另设查询、取消、重复取消、异任务访问、24小时过期、模型非法、发送后崩溃重放、并发纠正子场景。

观测：revision/head、问题状态、outbox/回执、plan_id/task_id、来源/约束、审计事件、模型调用数、handler调用数。输入/输出/持久化/关联检查数量按固定脚本报告实际值；恢复后hash与原值完全一致；重复控制新增逻辑命令0，越权0，后台异常0。

每次上限120秒，最多重试2次，同输入连续失败2次停止扩展。仅清理该Case新建临时目录。同步数据库事务完成为落盘时间；异步回执分别记录业务完成与持久化完成，报告差值。所有硬指标和恢复关联满足才case_pass=true；真实生产控制和真实006B联调另设Case，不能用契约接收器结果替代。

阶段执行顺序：针对性测试→累计Case→用户review。实施前先记录既有全回归基线。本轮仅设计，测试、Case、性能和模型效果均未验证。

## 9. Review重点与后续

本设计需要确认的3项取舍：同进程独立IntentModelPort；SQLite事务存意图/outbox；控制入口不满足授权、幂等和停止语义时不开放对应能力。确认后按6A.1–6A.4编制实施计划，每次只实现一个阶段。

可选学习练习：以“只分析”的纠正为例，区分意图版本失效与实际执行停止；用户愿意时再开展，不阻塞设计review。
