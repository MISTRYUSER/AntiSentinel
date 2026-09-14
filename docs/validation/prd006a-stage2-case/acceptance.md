# 6A.1–6A.2 累计Case验收

case_pass=true（固定候选+SQLite生命周期）；stage_pass=false（人工标签仍待review，未接模型或真实控制入口）。

C：`python3 scripts/case_prd006a_lifecycle.py --output docs/validation/prd006a-stage2-case`。复跑需指定新目录。
V：`python3 -m json.tool docs/validation/prd006a-stage2-case/verification.json`。
T：`python3 -m pytest tests/test_intent_lifecycle.py tests/test_intent_contract.py tests/test_intent_fixtures.py tests/test_intent_case_report.py -q`。
P：`python3 -m json.tool docs/validation/prd006a-stage2-performance.json`。

| 指标 | 基线 | 实际值 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---|---|---|---|---|---|
| 本阶段独立Case | 0 | 1 | 1 | +1 | C | 满足 |
| 输入/输出 | N/A | 37/37 | 相等 | 100% | C | 满足 |
| SQLite版本落盘/恢复 | 0/0 | 4/4 | 4/4 | +4/+4 | C | 满足 |
| 身份关联 | 0 | 20 | 20 | 100% | V | 满足 |
| 审计/消息关联 | 0/0 | 11/4 | 11/4 | 100% | V | 满足 |
| 路由/问题关联 | 0/0 | 3/1 | 3/1 | 100% | V | 满足 |
| 当前待路由/旧失效 | 0/0 | 2/1 | 2/1 | +2/+1 | C,V | 满足 |
| 重复请求新增路由 | N/A | 0 | 0 | 0 | C | 满足 |
| 后台异常 | N/A | 0 | 0 | worker本身0 | C | 同步范围满足 |
| 业务工具调用 | 0 | 0 | 0 | 0 | C | 满足 |
| Case耗时 | N/A | 71.96 ms | <=120000ms | N/A | C | 满足 |
| 业务完成到文件落盘 | N/A | 0.35 ms | 必须明确 | N/A | C | 满足 |
| 固定候选计时中位数 | 5.70ms | 5.34ms | 劣化<=5% | -6.41%（原始精度） | P | 满足 |
| 针对性测试 | 49 | 49通过/0失败 | 全部通过 | 0 | T | 满足 |
| 重试 | 0 | 0 | <=2 | 0 | C | 满足 |

运行状态：脚本exit0。持久化状态：SQLite完整性检查ok；版本4、消息4、outbox3、问题1、事件11。独立Python进程回读的4个快照与原始hash/内容一致。不是JSON文件代替数据库恢复。

产物：intents.sqlite、fixed-candidates.json、snapshots.json、report.json、verification.json、本报告。源码本轮新增/修改0；Case新增产物6，开发记录修改1。上一轮完整回归775 passed、2 skipped、7 warnings，46.95s；本轮无代码变化，只重跑49项针对性检查，0.28s。

业务完成时间与落盘完成时间见report.json原始Unix时间，差值指SQLite事务已提交后的快照文件写入；SQLite事务为同步提交，无后台持久化。累计Case保留5次生命周期响应，其中重复请求仍返回原结果，只保存4个不同版本。

剩余范围：人工标签review仍未完成；真实模型分类、真实Plan/控制交接、停止执行及审批属于6A.3/6A.4。本阶段未发送取消命令，不声称已有执行被停止。等待用户review后进入6A.3。
