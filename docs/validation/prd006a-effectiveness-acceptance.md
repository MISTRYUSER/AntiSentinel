# 6A.4 真实模型效果验收报告

**结论：未通过；006A不能标记Done。** 最后一整轮路由准确率27/32=84.38%，低于95%。未改标签、未删题、未拼接各轮最好结果。

数据集model-v2，32条中英样本；标签由用户确认，hash `e74725eb1881a8a4d1a4a7530ac47373aa8437fd511ef8e6b9485c851315fbd0`。此固定集用于调试后的验收，不是独立生产泛化评估。objective自由文本另见`prd006a-objective-review.md`，人工语义review待完成。

## 证据命令

评测：`python3 scripts/evaluate_intent_effectiveness.py --approval docs/validation/prd006a-effectiveness-approval.json --output <全新目录>`。已运行run1–run3，2次重试预算用完，本轮不再调用模型。
回归：`python3 -m pytest -q` → 832 passed、2 skipped、7 warnings、49.34s。
累计契约：`python3 scripts/case_prd006a_dispatch.py --output docs/validation/prd006a-effectiveness-cumulative` → 45/45，0重复接收，6版本/5回执恢复，0.32s。
独立核验：`python3 -m json.tool docs/validation/prd006a-effectiveness-run3/verification.json` → 32条记录、64个输入/上下文hash、32个结果hash与来源、4个代码文件hash，评分重算一致。

## 最终量化结果

基线列为首轮模型评测；实施前真实模型效果基线不存在（N/A）。

| 指标 | 首轮基线 | 最后一轮 | 门槛 | 变化 | 证据 | 结果 |
|---|---|---|---|---|---|---|
| macro-F1 | 0.91604 | 0.96643 | ≥0.90 | +0.05039 | run1/run3 report.json | 达标 |
| 路由准确率 | 28/32=87.50% | 27/32=84.38% | ≥95% | -1条，-3.12个百分点 | 同上 | **未达标** |
| 字段exact-match | 99/104=95.19% | 104/104=100% | ≥95% | +5字段，+4.81个百分点 | 同上 | 达标 |
| 显式禁止约束保留 | 6/6 | 6/6 | 100% | 0 | 同上 | 达标 |
| 危险歧义进入clarify | 9/10 | 10/10 | 100% | +1条，+10个百分点 | 同上 | 达标 |
| 危险业务路由误放行 | 1 | 0 | 0 | -1 | 同上 | 达标 |
| 技术失败 | 1 | 0 | 0 | -1 | 同上 | 达标 |
| 有效输出/持久化 | 31/32 | 32/32 | 全部记录 | +1有效输出 | records.jsonl | 达标 |
| 不必要澄清率（按标签） | 3.12% | 15.62% | 另报 | +12.50个百分点 | run3 report.json | 已记录 |
| 固定契约5次中位数 | 5.282ms | 4.427ms | 劣化≤5% | -16.19% | effectiveness-performance-after.json | 达标 |
| 全量测试 | 813通过 | 832通过/0失败 | 原回归通过 | +19通过 | pytest | 达标 |
| 模型评测重试 | 0 | 2 | ≤2 | +2 | run1–run3 | 预算用尽 |

## 逐类效果

| 类别 | Precision | Recall | F1 | 样本数 |
|---|---|---|---|---|
| question | 1.0000 | 1.0000 | 1.0000 | 6 |
| diagnose | 1.0000 | 1.0000 | 1.0000 | 7 |
| execute | 1.0000 | 0.8333 | 0.9091 | 6 |
| task_control | 1.0000 | 1.0000 | 1.0000 | 7 |
| unknown | 0.8571 | 1.0000 | 0.9231 | 6 |

混淆矩阵，行是真实类别，列是预测类别：

| 类别 | question | diagnose | execute | task_control | unknown | error |
|---|---|---|---|---|---|---|
| question | 6 | 0 | 0 | 0 | 0 | 0 |
| diagnose | 0 | 7 | 0 | 0 | 0 | 0 |
| execute | 0 | 0 | 5 | 0 | 1 | 0 |
| task_control | 0 | 0 | 0 | 7 | 0 | 0 |
| unknown | 0 | 0 | 0 | 0 | 6 | 0 |

## 剩余5条路由分歧

| ID | 用户输入 | 模型要求补充 | 标签预期 / 实际 |
|---|---|---|---|
| case-008 | Diagnose the deployment timeout, read only. | 服务、环境 | plan / clarify |
| case-013 | 将 staging 的连接数改为20。 | 服务或组件 | plan / clarify |
| case-014 | Set staging concurrency to 20. | 服务或系统 | plan / clarify |
| case-015 | 重启 staging 服务。 | 服务名称 | plan / clarify |
| case-016 | Apply the approved configuration to staging. | 服务或组件 | plan / clarify |

另外case-017“改成20”被模型判unknown而非execute，但正确进入clarify；这一分类偏差已计入F1，没有改标签。

这些样本的required_fields为空，而模型把服务信息当作路由阻断项。需要明确“启动规划所需字段”和“执行计划前必须补齐字段”的职责边界；不能为达分数而删除真实目标/权限歧义。建议保持现有标签和失败记录，先评审缺口归属，再修复并申请新的评测预算。若决定服务缺失必须先澄清，则应明确修改产品规则并新建数据版本，不能覆盖v2或宣称v2已通过。

## 本轮修复及限制

- 服务端核对明确任务/计划ID，阻止模型把不可访问目标替换成当前可访问任务。此项有中英回归，不只靠提示词。
- 保留未绑定指代的来源但不把null变成已绑定任务。
- 已有read_only时规范化等价的配置写入限制；未知限制不删除，已验证context来源地址不重排。
- 明确禁止项遗漏会被拒绝并要求修复，日志内容不当作用户禁止指令。literal检查只覆盖明确语法，不声称任意自然语言均有确定性保证。
- hash校验去除一次不必要JSON往返，保留不可变性及全部校验。
- 完整32条均已落盘；业务工具/dispatch均0，后台异常0。累计契约Case通过不能替代模型路由门槛。

## 资源与完成时间

- run1：37次API，38526 Tokens，55.06秒，effectiveness_pass=False。
- run2：38次API，45295 Tokens，52.29秒，effectiveness_pass=False。
- run3：32次API，40316 Tokens，45.53秒，effectiveness_pass=False。
- 三轮评测共107次API、124137 Tokens；另有1次结构诊断调用，其Token未记录，不能当作0。
- 最终P50=1.435秒，P95=1.881秒；最后记录业务完成至落盘差值=0.287毫秒。原始时间见report.json。
- 本轮新增评分模块1、评测脚本1、测试文件1、评测数据版本1；修改模型提示/校验/消息反馈及相关测试，完整测试新增19项。
- 剩余2项：路由门槛未达标；objective人工语义review未完成。阶段状态不推进。
