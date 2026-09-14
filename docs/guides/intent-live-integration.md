# DeepSeek 意图层真实接入

## 启动与入口

运行 `./scripts/start.sh`。脚本会读取 ignored 的 `.env.local`，构建并启动本地 Compose 服务；本轮没有重启已有部署。

启用意图层后主页即为新对话界面，亦可访问 `/intent`。页面要求服务访问令牌，对应 `.env.local` 中的 `ANTISENTINEL_INTENT_API_TOKEN`，不是 DeepSeek 密钥。页面不把令牌写入浏览器存储。

服务端已有配置：`ANTISENTINEL_MODEL_BASE_URL`、`ANTISENTINEL_MODEL_NAME`、`ANTISENTINEL_MODEL_API_KEY`，本轮复用 DeepSeek，不更换模型密钥。
新增配置：`ANTISENTINEL_INTENTS_ENABLED=1`、独立的 `ANTISENTINEL_INTENT_API_TOKEN`、`ANTISENTINEL_INTENT_ACTOR`。当前为单服务端 operator 身份；客户端提交的actor或participant不能替代该身份。

`ANTISENTINEL_INTENT_BINDINGS` 可配置已授权实体，例如 `{"/entities/environment":"staging","/entities/service":"orders"}`。未配置或不匹配的目标进入澄清，不自动授权。

## HTTP接口

启用后 `/api/*` 和 `/v1/*`（公开模型配置除外）要求 `Authorization: Bearer <服务令牌>`。

| 接口 | 作用 |
|---|---|
| POST /api/intent/sessions | 创建服务端绑定actor的Incident/Session，不启动诊断执行 |
| POST /api/intent/sessions/{session_id}/messages | 识别、校验、持久化、实际回答/计划/控制交接 |
| GET /api/intent/sessions/{session_id}/artifacts/{id} | 获取本Session的回答、计划或控制结果 |
| GET /api/intent/sessions/{session_id}/tasks | 获取可访问任务绑定与状态版本 |
| GET /api/sessions/{session_id}/control | 查询实际Runtime状态 |
| POST /api/sessions/{session_id}/cancel | 请求取消实际Runtime；运行未退出时为202/cancel_requested |

消息体示例：`{"message_id":"稳定消息ID","content":"连接池是什么？"}`。补充/纠正增加 `intent_id`、`expected_revision`，可带 `clarification_id`。同ID不同内容409；重放不重复创建产物。旧 `/api/sessions/{id}/messages` 在启用时进入同一链路，旧客户端没有稳定message_id时只能保证本次生成ID内的幂等。

## 真实能力与边界

- DeepSeek通过JSON mode提取候选，严格解析；不提供tools。空内容、工具调用或非法JSON拒绝；最多2次修复，总网络请求按剩余deadline取消。
- 回答使用DeepSeek与已有授权上下文。回答入口发现需要新证据时改成新意图版本并路由Plan，不调用取证工具。
- Plan调用真实PlanningService，结合真实工具目录校验参数及只读限制，保存diagnostic/execution草案和稳定hash。计划定义与取消状态分离，不改写已保存定义hash。
- 所有计划的execution_status仍为not_submitted。写步骤需审批；本接入没有自动执行DAG，不宣称PRD-006B/007/008/010全部完成。
- 查询/取消走实际计划状态存储或Runtime控制入口。Runtime在模型返回后、工具调用边界响应取消；正在执行的外部调用结束前只承诺请求已接收，已发生副作用不回滚。
- 回执receiver_kind=real，accepted与completed分开。当前身份、Incident、Session、任务状态及意图版本在实际交接时检查。控制请求自身改变状态后，重放返回已保存回执，不重复控制。
- 正在运行的进程消失而无终态记录时保留unavailable，不猜测为已取消，也不自动重启旧运行。

## 验证

单测：`python3 -m pytest -q`。
真实Case：`python3 scripts/case_prd006a_real.py --output <全新隔离目录>`，读取既有DeepSeek配置；4条通用消息、正常6次API请求，父进程120秒上限。仅测试问答、计划、控制和恢复，业务工具调用0。

首轮、执行类型补强后、Incident权限补强后的产物分别保存在 `docs/validation/prd006a-real-case`、`prd006a-real-case-final`、`prd006a-real-case-secure`。不把这4条输入当作固定30条人工标注集的模型效果评估。
