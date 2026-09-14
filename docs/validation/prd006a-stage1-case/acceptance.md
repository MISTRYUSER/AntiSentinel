# 6A.1 隔离契约Case验收报告

仅验证固定候选到纯解析器/校验器的契约链路。case_pass=true；stage_pass=false。未调用模型或业务控制入口。

证据命令：

- `python3 -m pytest tests/test_intent_contract.py tests/test_intent_fixtures.py tests/test_intent_case_report.py -q`：34 passed，0 failed，0.10s。
- `python3 scripts/case_prd006a_contract.py --output docs/validation/prd006a-stage1-case`：exit0；复跑须使用另一个全新目录，现有目录不会覆盖。
- `python3 -m json.tool docs/validation/prd006a-stage1-case/report.json`：Case原始数据。
- `python3 -m json.tool docs/validation/prd006a-stage1-case/verification.json`：独立进程回读与5轮计时原始数据。

下表C指Case命令及report.json，V指独立验证的verification.json。

| 指标 | 基线 | 实际值 | 阈值 | 差值/比例 | 证据命令 | 结果 |
|---|---|---|---|---|---|---|
| Case执行数 | 0 | 1 | 1 | +1 | C | 满足 |
| 输入/输出 | N/A（未运行） | 32/32 | 输出=输入 | 100% | C | 满足 |
| 路由与必需字段匹配 | N/A | 32/32 | 100% | 100% | C | 满足 |
| 持久化/回读 | 0/0 | 32/32 | 输入数 | +32/+32 | C | 满足 |
| 独立进程身份关联核验 | 0 | 160 | 32×5 | +160 | V | 满足 |
| 独立路由/约束核验 | 0/0 | 32/32 | 32/32 | +32/+32 | V | 满足 |
| 后台异常 | N/A | 0 | 0 | 后台worker本身0 | C | 仅同步范围 |
| 模型调用/业务dispatch | 0/0 | 0/0 | 0/0 | 0 | C | 满足 |
| Case耗时 | N/A | 11.05 ms | <=120000ms | N/A | C | 满足 |
| 业务完成至落盘差值 | N/A | 0.68 ms | 明确记录 | N/A | C | 满足 |
| 5轮解析计时中位数 | N/A | 5.70 ms | 首轮建基线 | N/A | V | 基线已记录 |
| 性能劣化 | N/A | N/A | <=5% | 无实施前等价链路 | V | 未验证 |
| 重试 | 0 | 0 | <=2 | 0 | C | 满足 |

本轮生产/测试代码修改0；新增Case产物4（intents.jsonl、report.json、verification.json、本报告）；修改开发记录1。针对性测试34/34、失败0。上轮完整回归760 passed/2 skipped、50.38s，本轮无代码变化未重复全量回归。

独立进程回读证明JSON产物可重新解析及关联，不代表6A.2的SQLite进程重启恢复。五轮计时为固定候选解析/校验链路，不能代表模型识别延迟。label_status仍为draft_requires_user_review；用户确认运行不等于逐条人工标签验收。

剩余2项：人工标签review；缺少实施前等价性能基线，不能宣称性能回归门槛通过。真实模型效果留至6A.4。等待本阶段review，不进入6A.2。
