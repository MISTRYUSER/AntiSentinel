# 6A.1–6A.3 累计契约Case验收

case_pass=true；stage_pass=false。验证范围是固定模型响应、真实SQLite、持久化契约接收器，未接真实模型、HTTP认证、现有问答入口、006B或实际任务取消。

C：`python3 scripts/case_prd006a_dispatch.py --output docs/validation/prd006a-stage3-case`，exit0。复跑需全新目录。
V：`python3 -m json.tool docs/validation/prd006a-stage3-case/verification.json`。
T：`python3 -m pytest -q`：797 passed、2 skipped、7 warnings、66.30s；阶段基线775 passed、46.95s。
P：`python3 -m json.tool docs/validation/prd006a-stage3-performance-recheck.json`。

| 指标 | 基线 | 实际值 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---|---|---|---|---|---|
| 累计输入/输出 | N/A | 45/45 | 相等 | 100% | C | 满足 |
| 本阶段版本恢复 | 0 | 6 | 6 | +6 | C,V | 满足 |
| 接收/回执恢复 | 0/0 | 5/5 | 5/5 | 100% | C,V | 满足 |
| 审计事件关联 | 0 | 20 | 20 | 100% | V | 满足 |
| 限制交接完整 | N/A | 5/5 | 100% | 100% | V | 满足 |
| 重复逻辑接收 | N/A | 0 | 0 | 0 | C | 满足 |
| 旧版/撤权拒绝 | N/A | 1/1 | 1/1 | 100% | C | 满足 |
| 停止等待/后续Plan顺序 | N/A | 2项断言 | 2 | 100% | V | 满足 |
| 后台异常 | N/A | 0 | 0 | worker本身0 | C | 同步范围满足 |
| 真实模型/业务调用 | 0/0 | 0/0 | 0/0 | 0 | C | 满足 |
| Case耗时 | N/A | 527.08 ms | <=120000ms | N/A | C | 满足 |
| 业务完成至文件落盘 | N/A | 0.96 ms | 明确记录 | N/A | C | 满足 |
| 固定候选5次中位数 | 5.34ms | 5.45ms | 劣化<=5% | +2.06%（原始精度） | P | 复核满足 |
| 完整测试通过数 | 775 | 797 | 原测试全通过 | +22 | T | 满足 |
| 完整测试失败数 | 0 | 0 | 0 | 0 | T | 满足 |
| Case重试 | 0 | 0 | <=2 | 0 | C | 满足 |

首次性能检查与全量回归并发，记录为6.46ms/+21.00%，未达门槛；原始数据保留在`docs/validation/prd006a-stage3-performance.json`。回归结束后复核5.45ms/+2.06%。不将一次复核推广为生产性能保证。全suite耗时由46.95s到66.30s升高，未声称全suite耗时改善；PRD性能门槛使用固定fixture五次中位数。

Case运行状态：exit0；SQLite完整性ok，6版本/6消息/4outbox/1问题/20事件，本地回执5；接收器逻辑记录5。独立解释器恢复6版本与5回执，另独立检查20审计关联及5次约束交接，错误0。业务完成与持久化完成Unix时间及差值见report.json。回执accepted与completed分开保存；停止完成只由契约接收器模拟，不能称真实任务已取消。

本阶段新增生产文件5、修改生产文件2、新增测试文件2、Case脚本1。最终针对性71/71（0.85s），完整797 passed/2 skipped（66.30s）；主累计Case1，内部包含前两阶段子Case1。真实provider/HTTP授权/真实下游/人工标签共4项待集成或验收。模型Port只传递剩余timeout_seconds，实际硬超时必须由真实provider适配器实现并验证。

核心代码：`control/intent_dispatch.py`、`entry/intent_application.py`、`ports/intent_routes.py`、`ports/intent_model.py`、`persistence/intent_store.py`；接收器位于`evaluation/intent_receiver.py`，明确标记contract。SQLite revision_guard只用于短接收事务，不得包裹长期业务执行或无界网络调用。

本轮复用了项目learning中的“历史事实不等于当前授权”及发送前复核建议，已写usage signal。等待用户review；6A.3契约验证不等于真实Plan联调完成。
