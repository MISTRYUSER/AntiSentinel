# DeepSeek真实接入验收

本轮接入真实DeepSeek、受认证HTTP入口、真实问答、计划草案生成/持久化、计划查询/取消与Runtime取消。模型密钥沿用现有DeepSeek配置；另配置服务访问令牌。没有重启既有部署。

C：`python3 scripts/case_prd006a_real.py --output docs/validation/prd006a-real-case-secure`，exit0，case_pass=true。
T：`python3 -m pytest -q`。
V：`python3 -m json.tool docs/validation/prd006a-real-case-secure/verification.json`。
配置与脚本：`docker compose --env-file .env.local config --quiet`、`bash -n scripts/start.sh`、`node --check frontend/intent.js`均exit0。

| 指标 | 基线 | 实际值 | 阈值 | 差值/比例 | 证据 | 结果 |
|---|---|---|---|---|---|---|
| 完整测试通过数 | 797 | 813 | 原回归全通过 | +16 | T | 满足 |
| 完整测试失败数 | 0 | 0 | 0 | 0 | T | 满足 |
| 测试跳过/警告 | 2/7 | 2/7 | 无新增 | 0/0 | T | 满足 |
| 真实用户消息输入/输出 | N/A | 4/4 | 相等 | 100% | C | 满足 |
| 真实回执/意图恢复 | 0/0 | 4/4 | 4/4 | +4/+4 | C | 满足 |
| 独立进程关联 | 0 | 8 | 8 | 100% | V | 满足 |
| 计划定义hash校验 | 0 | 1 | 1 | 100% | V | 满足 |
| 已取消Runtime恢复 | 0 | 1 | 1 | 100% | C,V | 满足 |
| Incident归属持久化 | 0 | 2 | 2 | 100% | V | 满足 |
| 重放请求 | 0 | 1 | 新增调用/产物0 | 满足 | C | 满足 |
| 业务工具handler调用 | 0 | 0 | 0 | 0 | C | 满足 |
| 后台异常 | N/A | 0 | 0 | 0 | C | 满足 |
| 本次DeepSeek调用 | N/A | 6 | Case预算内 | N/A | C | 已记录 |
| 本次Token总量 | N/A | 5987 | 明确记录 | N/A | C | 已记录 |
| Case耗时 | N/A | 10.71秒 | <=120秒 | N/A | C | 满足 |
| 业务至文件落盘差值 | N/A | 0.23毫秒 | 明确记录 | N/A | C | 满足 |

最终完整回归813 passed/2 skipped，41.06秒；针对性权限/旧API20 passed，0.88秒。本轮新增生产文件3、修改生产文件8、新增测试文件4、修改测试文件1、新增Case脚本1、新增前端文件2；Compose与启动脚本已连接新配置。密钥配置文件ignored且权限0600，Docker构建上下文不包含.env.local。

真实Case共运行3次（首轮、执行类型补强后、Incident权限补强后），均成功；不是故障重试。总DeepSeek调用19次，Token合计18695；每次原始产物保留。最终Case业务/落盘Unix时间见report.json，不隐藏落盘时间差。

本Case创建的计划有1个可执行检查步骤；工具不足时为needs_capability。计划定义与任务取消状态分离，定义hash不因取消而改变。execution_status=not_submitted，不自动执行任何工具或DAG，不继承审批。执行意图以plan_type=execution保留，与诊断草案区分。

Runtime取消用受控阻塞模型验证真实线程与HTTP控制路径：先202/cancel_requested，调用返回后确认cancelled，持久化后可恢复。没有宣称DeepSeek或外部工具可瞬时中断。

独立核验在新Python进程中只读恢复4个意图/回执、1个计划、1个已取消Runtime和2条Incident归属，模型调用0，错误0。

限制：当前为单服务端operator认证，不是企业IAM；本轮4条真实输入不代替30条人工标注效果评估，不报告macro-F1或生产准确率。计划草案入口已接入，但不等于PRD-006B/007/008/010全部完成。已记录既有Code Map gRPC/fork间歇性超时：单项隔离通过、后续完整回归通过，未修改无关Code Map代码。

使用说明：`docs/guides/intent-live-integration.md`。启动后主页即意图对话；服务令牌与DeepSeek密钥不同。
