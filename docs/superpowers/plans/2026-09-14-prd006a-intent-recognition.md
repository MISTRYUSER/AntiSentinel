# PRD-006A Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for inline execution. The user-specified PRD stage gates take precedence: implement only one stage, then cumulative Case and user review.

**Goal:** 将用户请求转成带来源、版本和路由约束的意图契约。

**Architecture:** 无工具模型提取候选，服务端校验与绑定。版本、澄清、投递和回执由服务端持久化；真实下游能力缺失时不开放业务入口。

**Tech Stack:** Python 3.11+、标准库dataclasses/enum/json/hashlib、pytest；6A.2引入标准库SQLite。

**Spec:** `docs/superpowers/specs/2026-09-14-prd006a-intent-recognition-design.md`

## Global Constraints

- 模型不提供权威actor、授权或任务归属；意图模块不调用取证handler。
- needs_clarification只能clarify；unsupported只能unsupported；业务路由必须resolved。
- 识别含等待总预算30秒，修复最多2次；自动澄清最多3轮，问题24小时过期。
- Case每次120秒，最多重试2次；真实Case命令准备后经用户确认才运行。
- 每个测试/实现/Case步骤后更新`docs/memory-development-log.md`。
- 本次只实施6A.1；不提前实现6A.2–6A.4。

## 阶段映射

| 阶段 | 文件与接口 | 验收 |
|---|---|---|
| 6A.1 | domain/intent.py：IntentCandidate/ResolvedIntent；control/intent_validation.py：resolve_candidate(candidate,context,identity)；tests/test_intent_contract.py；tests/fixtures/intents/v1.json | 来源、约束、合法路由、非法结构拒绝，>=30条标注样本 |
| 6A.2 | ports/intent_store.py、adapters/storage/sqlite_intent_store.py、control/intents.py；resolve(actor,message,expected_revision) | 版本CAS、幂等、澄清、重启恢复，累计6A.1 |
| 6A.3 | ports/intent_model.py、control/intent_dispatch.py、entry/application.py；AnswerGateway/PlanGateway/TaskControlGateway | 5入口、限制传递、幂等回执、越权与重复副作用0，累计6A.1–2 |
| 6A.4 | evaluation/intent_effectiveness.py与累计Case runner | 冻结集真实模型效果、完整回归、累计Case、用户验收 |

后续阶段的逐测试实施细节在上一阶段review后展开，不把接口候选当作已经集成。

## 6A.1 Task 1：意图契约及校验

**Files:** 新建`src/antisentinel/domain/intent.py`、`src/antisentinel/control/intent_validation.py`、`tests/test_intent_contract.py`。

**Interfaces:** 输入严格JSON IntentCandidate（语义字段、来源、缺口）；输入服务端AuthorizedIntentContext与IntentIdentity；输出ResolvedIntent。所有对象防御性复制，序列化输出与内部状态隔离。无业务dispatch依赖。

- [ ] 先写否定约束、伪造来源、非法枚举、越权绑定和状态/路由不一致测试；观察预期失败。

```python
def test_read_only_diagnosis_routes_to_plan():
    result = resolve_candidate(candidate, context, identity)
    assert result.to_dict()['route'] == 'plan'
    assert result.to_dict()['constraints'] == candidate.to_dict()['constraints']
```

- [ ] 执行`python3 -m pytest tests/test_intent_contract.py -q`，记录红灯原因。
- [ ] 增加封闭枚举和严格解析，拒绝unknown字段、非JSON值与空目标；来源引用必须匹配当前授权消息/上下文字段；未知目标绑定不能放行。
- [ ] 增加确定性路由：普通问题直接回答；新证据问题plan；诊断/执行plan；task_control要求唯一授权绑定；缺口clarify；明确能力不支持unsupported。
- [ ] 计算规范化语义载荷hash；校验服务端identity及完整ResolvedIntent状态/路由；序列化往返与篡改拒绝。
- [ ] 执行针对性测试并立即记录实际结果。

## 6A.1 Task 2：冻结样本与可执行Case准备

**Files:** 新建`tests/fixtures/intents/v1.json`、`tests/test_intent_fixtures.py`、`scripts/case_prd006a_contract.py`。

**Interfaces:** fixture每项包含id/message/context/candidate/expected类型与route/必需字段/禁止动作；manifest保存版本和输入/context hash。候选响应为人工固定数据，不宣称真实模型分类。

- [ ] 先写fixture完整性与参数化路由测试，至少30条，5类各>=4；中文/英文、指代、补充、纠正、注入、复合目标必须覆盖。
- [ ] 固定响应只驱动真实解析器与校验器；对比人工expected而非从实现生成标签。
- [ ] Case脚本读取fixture，逐项验证并将JSON报告写入用户指定的隔离目录；报告输入/输出/耗时/落盘/关联/异常、业务/落盘完成时间差和case_pass。
- [ ] 单独准备Case命令与预期计数，不运行脚本直到用户确认；单测不替代Case。
- [ ] 执行`python3 -m pytest tests/test_intent_contract.py tests/test_intent_fixtures.py -q`，再全量`python3 -m pytest -q`；记录基线与当前结果。

## 6A.1 成功与停止条件

成功：>=30条fixture、封闭契约断言100%、固定响应显式限制保留100%。失败：非法结构放行0、越权控制路由0、意图handler调用0。回归：既有测试全部通过；性能基线不足则N/A且不称性能门槛通过。

用户确认Case后执行：`python3 scripts/case_prd006a_contract.py --output <新建隔离目录>`。只覆盖6A.1契约，不包含尚未实施的恢复/真实控制；完整累计生命周期按6A.2/3逐步扩展。所有硬门槛通过后请求本阶段review，不能继续下一阶段。


## 6A.2 展开：SQLite事务与澄清生命周期

前置证据：6A.1针对性复核34/34通过；完整回归最近760 passed/2 skipped；独立Case32/32。人工标签与实施前性能比较仍未验收，不因此宣称6A.1全部门槛通过。用户明确继续6A.2。

- [ ] 新建`ports/intent_store.py`：IntentStore协议与IntentConflict/IntentAccessDenied错误；以actor/session/incident作用域读取，不由模型覆盖。
- [ ] 新建`persistence/intent_store.py`中的SQLiteIntentStore（沿用仓库存储目录）：revision/head/message_resolution/clarification/outbox/audit同事务，BEGIN IMMEDIATE + expected_revision CAS；同键异内容409语义；重放返回原快照且不增outbox。
- [ ] 新建`control/intents.py`：IntentService.resolve(candidate,context,expected_revision,intent_id=None,clarification_id=None)，固定响应调用方提供candidate，模型接入留给6A.3。continuation保留旧目标与限制；correction使用新目标、合并新限制；实体按最新明确字段覆盖并重新绑定。
- [ ] 先写测试验证补充commit、只读纠正、跨作用域拒绝、同消息重放/冲突、上下文更新产生新revision、两个连接并发CAS只有一个成功、三轮澄清暂停、24小时关闭与迟到回答重验。
- [ ] 记录红灯；实现最小事务与服务；执行`python3 -m pytest tests/test_intent_lifecycle.py -q`，记录绿灯。
- [ ] 累计运行6A.1测试与完整回归；准备`python3 scripts/case_prd006a_lifecycle.py --output <全新隔离目录>`。Case用固定候选与SQLite，在独立进程读取；不dispatch，仅检查持久化pending/superseded路由。
- [ ] Case命令就绪后呈现输入、观测、计数和清理边界，用户确认后执行；上限120秒、最多重试2次。

成功指标：重放结果一致100%、纠正旧outbox失效100%、独立进程回读关联100%；失败：跨作用域读取/重复逻辑路由/CAS双成功均0；回归既有测试失败0，6A.1固定候选5次计时中位数对已建基线劣化<=5%。运行停止、真实控制回执与模型超时不属于本阶段。


## 6A.3 展开：路由交接与回执

已核实Plan/Control为skeleton，问答入口没有幂等契约；首期使用明确标记contract的持久化接收器验收，不声称真实Plan或生产控制集成。模型Port仅定义无工具提取契约；真实provider配置与效果测评不在本阶段运行。

- [ ] `ports/intent_routes.py`：ContextReader.read(actor,session,incident)->AuthorizedIntentContext；RouteGateway.submit(request,validate_current)->RouteReceipt，lookup(key)->RouteReceipt|None。网关必须实现幂等、接收时版本检查、实际动作独立权限检查；协议不赋予权限。
- [ ] `control/intent_dispatch.py`：dispatch(intent_id,revision,actor,session,incident)。重新读取授权、最新head、目标绑定和任务state_version；业务路由按白名单进入单个入口。clarify/unsupported只返回本地结果。回执先按key查询，缺少时submit；异常保留待办，恢复重试同key。
- [ ] `persistence/intent_store.py`：read_receipt/save_receipt原子保存route_receipts及intent.routed事件，接受回执保留accepted状态，不冒充completed。回执含intent/revision/hash、route、receiver_kind和下游ID。
- [ ] 先写路由分流、越权、旧版本、状态变化、重复投递、发送后崩溃恢复、错误回执与接收前纠正竞态的失败测试；实现后累计回归。
- [ ] 只读纠正遇到已有写运行：要求服务端fresh context提供active_write_task，先幂等stop控制；完成回执到达前不提交新Plan。真实运行收敛仍属于006B。
- [ ] `ports/intent_model.py`与`entry/intent_application.py`：无工具ModelRequest、严格解析、最多2次修复、30秒总deadline（provider必须遵守）；预查消息重放；接入现有IntentService和Dispatcher。真实HTTP/认证绑定未就绪时不开放网络入口。
- [ ] 准备累计Case，沿用6A.1/2并新增持久化contract接收器与回执恢复；命令提供后经用户确认运行，120秒/2次重试。

成功：5路正确分流100%、回执关联100%、限制传递100%；失败：旧版本投递/越权控制/重复逻辑接受均0；回归原测试失败0。生产入口不可用保留为明确集成限制，不因fake通过关闭该项。


## 6A.4 展开：真实模型效果评估

保留v1契约样本不变，新建model-v2用于真实模型评估。原样本有缺失的代码/结论上下文、补充版本未绑定原目标等问题，需在模型评测前修订并由用户确认。类别标签和预期结果不得发送给模型。

- [ ] 提供32条匿名ID样本、完整授权上下文、单一类别/route、关键字段、显式限制和危险歧义标记；生成可读review表及SHA-256。
- [ ] 先写评分器失败测试：逐类precision/recall/F1、macro-F1、混淆矩阵、路由率、字段exact-match、约束保留、危险放行；技术失败保留在分母，未完成样本不得删除。
- [ ] 关键字段为预先标注的实体、operation/任务绑定及relation；constraints按无序语义列表规范化后比较。自由文本objective单独人工语义review，不能以字符串同义改写当作字段错误，也不能因此声称目标语义已通过。
- [ ] 编写真实DeepSeek runner，仅调用提取器+确定性验证器，禁止dispatch；每输入最多2次修复、30秒预算，整个run由父进程限时120秒，增量落盘。标签获确认后才能做正式验收；未确认结果只能预评估。
- [ ] 固定版本评测，完整保存原始候选/错误与输入/context/dataset hash、调用次数、Token和P50/P95；全部门槛满足后再累计Case与用户review。
