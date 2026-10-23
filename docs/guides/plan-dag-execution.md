# 计划 DAG 执行

## 使用现有意图入口

启用已有的鉴权意图入口后，消息生成的计划仍只保存，不会自动执行。检查计划产物与 `state_version`，再显式提交：

```http
POST /api/intent/sessions/{session_id}/plans/{plan_id}/execute
Authorization: Bearer <已配置的服务端访问令牌>
Content-Type: application/json

{"expected_version":1}
```

响应包含 `run_id`、运行状态与逐节点 `status/attempts/result/error/evidences`。
通过已有的 `GET /api/intent/sessions/{session_id}/artifacts/{plan_id}` 回读计划与执行状态。
执行是同步 HTTP 请求；生产长任务应由已有任务系统接管，不能假定断开连接会中断工具。

- 此 HTTP 入口只执行当前工具目录允许的只读工具。计划声明自己只读不能覆盖注册表权限。
- 用户、会话、目标绑定、计划哈希、工具目录和提交版本均重新检查。
- 重复提交相同版本映射到同一个运行，成功结果不重复执行；并发提交返回409。
- 计划内容、执行权限或工具实现版本变化后，不会沿用旧运行；重新规划生成新计划。
- 计划取消复用已有的意图任务控制入口。停止提交新节点，等待已运行线程协作结束；未知写入结果必须人工核对。

## 模型计划格式

```json
{
  "hypotheses": ["日志与资源指标共同验证超时原因"],
  "checks": [
    {"step_id":"logs","purpose":"读取日志","tool_name":"实际目录中的读日志工具","arguments":{},"depends_on":[]},
    {"step_id":"metrics","purpose":"读取指标","tool_name":"实际目录中的读指标工具","arguments":{},"depends_on":[]},
    {"step_id":"verify","purpose":"验证候选原因","tool_name":"实际目录中的验证工具","arguments":{},"depends_on":["logs","metrics"]}
  ]
}
```

示例工具名仅说明格式，执行前必须与实际注册目录匹配。`depends_on: []` 表示可以独立调度；省略则兼容旧格式，依赖上一节点。重复ID、缺失依赖、循环和无效参数在调用工具前被拒绝。
依赖边只规定执行次序；本版不把上游任意输出自动拼入后继参数。需要新参数时，由规划层读取结果并生成新计划。

## 持久化与版本

SQLite 对节点开始、结果及事件同步提交。恢复复用成功节点；被中断的只读节点在最多两次尝试预算内恢复，写节点进入 `uncertain/needs_review`。
系统无法把外部工具副作用和本地 SQLite 提交组成原子事务，因此不承诺任意写工具的一次且仅一次执行。

运行绑定计划定义、权限、工具 manifest 和 Python handler 代码身份。工具的远程协议、闭包配置或依赖版本变化无法仅靠 Python 字节码识别，工具作者须指定不可变的 `implementation_version`：

```python
ToolDefinition(
    name="health", description="读取健康状态", argument_schema={"type": "object"},
    handler=read_health, read_only=True,
    implementation_version="health-plugin-release-sha256",
)
```

快照 schema 不认识时拒绝恢复。文件锁用于单机、多进程互斥，要求所有进程共享同一路径与锁文件系统；它不是跨主机分布式锁。不要删除仍可能被使用的锁文件。
通用 `PlanRuntime` 会保存审批等待状态，但尚未提供审批决策恢复协议；HTTP 入口因此不开放写入或审批执行。

## 离线验证

```bash
OTEL_TRACES_SAMPLER=always_on python -m pytest -q
python scripts/validate_five_capabilities.py --output /tmp/antisentinel-validation-new
python scripts/run_plan_recovery_case.py --output /tmp/antisentinel-recovery-new
```

统一验收保留 pytest XML、日志、命令和代码指纹。缺失结果、异常、跳过或缺少分组均不能标为全部通过。它不会因为环境中有模型凭据就运行付费测试。
进程恢复 Case 在工具中直接调用 `os._exit(7)`：成功兄弟节点只执行一次，中断的读工具恢复一次，中断的写工具不重放；另以独立进程验证同一运行锁互斥。

OTel 定向测试需要确定性采样；命令中的 `always_on` 仅用于测试。生产采样策略由部署配置决定。
