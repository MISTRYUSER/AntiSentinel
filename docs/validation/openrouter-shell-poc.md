# OpenRouter hosted shell：独立评测 POC

评估基线：`MISTRYUSER/AntiSentinel@d1ef53a9eef9a730da5d4cea23545dc12244d028`。
核对日期：2026-10-01。邮件信息来自用户提供的 September 2026 摘要，未读取完整邮件。

## 决策

只用于独立评测 sandbox；生产 Runtime 不接入，本轮不增加 adapter 原生工具透传。

| 方案 | 判断 | 代价与边界 |
| --- | --- | --- |
| 完全不接 | 生产路径采用 | 保留本地审批、只读策略、Evidence、重试和工具调用收敛；不获得托管执行环境 |
| 独立评测 sandbox | 推荐做最小 POC | 可验证跨模型 shell 能力与远端观测质量；不等同 AntiSentinel Runtime 能力提升 |
| adapter 增加原生工具透传 | 暂缓 | 当前 transport 不支持 shell；换 API、解析远端执行、预算与追踪已超出“极薄透传” |

`OpenAICompatibleModelAdapter.complete()` 在
`src/antisentinel/adapters/llm/openai_compatible.py` 调用 `/chat/completions`，
`_provider_tool()` 把所有工具转换为 `type:function`，解析 `choices[0].message`，
`_parse_native_tool_calls()` 生成待本地执行的任务。hosted shell 在响应返回前已经执行，
不能走这条待执行任务路径，否则会把观测误当授权并可能重复执行。

`src/antisentinel/tools/manifest.py:ToolDefinition` 强制 callable handler；
`worker/execution/tool_executor.py` 做 disclosure/read_only/approval preflight；
`worker/runtime/loop.py` 管理 Attempt、失败、去重和 Evidence 引用。
这些约束无法拦住 provider 单次请求内部的命令。

code-map 的既有设计明确固定 argv、禁止 shell=True、checkout、hooks、filters、
submodules 和 LFS。参见 `docs/superpowers/specs/2026-09-07-prd005a-code-map-design.md`
和 `src/antisentinel/code_map/git_reader.py`。本 POC 不替换 GitReader、不把真实仓库交给 shell。

## 协议核对

| 工具 | 可用接口 | 请求与执行归属 | 响应要点 |
| --- | --- | --- | --- |
| `openrouter:shell` | Responses、Messages；Chat Completions 返回 400 | tools 中直接传原生 type；本 POC 显式 engine=openrouter | Responses 使用 `openrouter:shell` item；使用 OpenAI shell shape 则为 `shell_call`；结果含 output[]、stdout、stderr、outcome |
| `openrouter:shell`（Messages） | `/api/v1/messages` | 始终 hosted execution | `server_tool_use`，name=`openrouter:shell`；配对 `openrouter_shell_tool_result` |
| `openrouter:bash` | 仅 Messages | **必须 engine=openrouter** 才是托管；auto/native 默认返回客户端执行 | 原生 Bash 兼容输出 command/stdout/stderr/exitCode；不能复用 shell 的 outcome parser |

Shell 调用参数：commands[]、每条命令 timeout_ms、每流 max_output_length。
命令结果 outcome 为 `exit` + exit_code 或 `timeout`。Bash 的 timeout_ms 是批次语义，
且 restart 可重置容器；不是改名即可互换的同一个协议。

Responses API 为无状态：拒绝 store=true 和非空 previous_response_id；多轮需完整回放历史。
容器文件持久化与 Responses 对话状态是两件事。

**文档没有给出 namespaced Responses item 的完整 JSON 包装。** POC 对
`type=openrouter:shell` + 顶层 output[] 的支持明确是待真实 capture 验证的假设。
测试数据由文档拼接，不能称协议实测。若真实结果嵌套方式不同，脚本拒绝解析，
先补脱敏捕获 fixture 再补 parser，不静默丢弃 item。Messages/Bash 不在本 POC parser 范围。

## 已实现的最小边界

`scripts/openrouter_shell_probe.py` 仅依赖 Python 标准库，不导入 AntiSentinel 包。

- `request --model`：输出固定合成任务的 Responses JSON；随机 20 字符 session_id，
  engine=openrouter、container_auto、显式 network_policy=disabled，stream=false、store=false。
- 无 HTTP 客户端、密钥读取、上传/下载、环境注入、本地 shell、重试、Runtime 注册或多轮循环。
- `inspect capture.json`：读取最多 2 MiB 的本地响应；只接受 completed 状态及已识别 item。
  输出 stream 的 SHA-256/字节数、退出/超时结果、容器/文件/调用 ID、有限 usage 字段。
  不输出命令、stdout/stderr、最终回答、推理正文或原始 provider 错误。
- 原生 shell_call/output 按 call_id 与命令/结果数量配对；不匹配、未知类型、非法退出码和文件引用拒绝解析。
- tracing 输出是独立 JSON 观测，`transcript_completeness=unverified`；不是 Runtime span、
  PlannedToolCall 或 Evidence。哈希只能标识捕获内容，不能证明其真实性或来源完整性。

生成请求：

```bash
python scripts/openrouter_shell_probe.py request --model YOUR_TOOL_CALLING_MODEL > request.json
```

本轮未调用付费 API。真实验证应在专用评测工作区用其受控 HTTP 客户端，
向 `https://openrouter.ai/api/v1/responses` 发送 request.json，私密保存 capture.json，
避免 Authorization、原始响应进入 shell 历史、CI 输出或公共提交，然后执行：

```bash
python scripts/openrouter_shell_probe.py inspect capture.json > summary.json
python -m unittest discover -s tests -p test_openrouter_shell_probe.py -v
```

POC 提示词中的“一次命令”不是强制权限控制，max_output_tokens 也不是 sandbox 硬预算。
客户端断开或超时不保证远端取消。未验证服务端调用次数/总时限限制前，只跑微小合成任务。

## 权限、密钥、网络与文件风险

| 风险 | 本轮约束 / 后续要求 |
| --- | --- |
| 绕过 approval/read_only | 授权的是整个合成评测环境；不能宣称逐命令审批。生产诊断、修复和只读任务禁接 |
| 密钥泄露 | 仅用独立、限额、限定工作区的评测 API key；只给 HTTP Authorization，不放进提示词、env、文件或 shell 参数；禁止生产/BYOK/数据库密钥 |
| 数据外泄 | 无外网仅阻断容器网络；模型响应、输出文件、provider 日志仍可带出数据。不得上传私有源码、日志、个人数据或 .git/config/凭据 |
| 网络扩大 | 显式 disabled，不接 web_fetch/web_search 等容器外工具，不用通配 allowlist；后续域名放行必须独立评测，warm 容器变更 policy 会返回 409 |
| 容器串数据 | 工作区隔离不等于 incident/session 隔离；复用 container_id 共享文件。session_id 超过 20 字符仅取尾部；本 POC 每次随机 20 字符，不回放历史、不复用 prompt_cache_key |
| 持久残留 | 5 分钟 idle 是休眠，不是删除；home 文件保留至最后使用后 30 天，workspace 文件不自动过期。建立显式删除/到期巡检；“ephemeral”不能理解为立刻销毁 |
| 文件行为 | 附件为可写副本，不是只读挂载；最多 20 个附件，名称有 file-id 尾部前缀；10 GiB 是工作区总存储，不是容器内存/磁盘保证；单文件最多 100 MiB |
| 输出不完整 | 单条结果最多列 10 个变更文件；deleted 文件不列，最终回答也不带文件 annotations；后续通过 container files 分页清单核对完整性 |
| 下载危害 | 不自动信任 filename/路径、符号链接、文件类型或可执行内容；下载需固定目的目录、限字节、校验 hash、拒绝路径逃逸；不自动执行产物 |
| 重试与计费 | HTTP 超时/5xx 后执行状态不明，不自动重发；重发可能重复副作用与费用。隔离新任务仍不代表原任务已经停止 |

## 解析与 tracing 后续门槛

真实 capture 至少验证：成功、非零 exit、timeout、未使用工具、多个 shell batch、
输出截断、HTTP 400/403/409/429/5xx、连接断开及 malformed response。
需要补齐的内容只放在独立评测模块：

1. 核实 namespaced Responses item 的完整 envelope、call/result 对应关系；
   Messages 后续单独解析 server_tool_use/tool-result blocks，Bash 单独解析 exitCode。
2. request/response/call/container/file ID、模型实际 provider、session/run/attempt 与固定源码 commit
   的对应；记录本地请求起止和 provider 给出的远端时间，不能把本地解析时间当执行时间。
3. 区分 token cost、sandbox metered time、冷启动最低计费；保留缺失字段，不用 token
   估算 sandbox 费用。可把 span 命名为 provider.server_tool.observed，标记执行归属。
4. 记录截断/缺失/未知 item 数、结果清单完整性、文件 SHA-256 和隔离配置指纹。
   Trace 默认只存长度与摘要；需要正文时只保存在受控短期 artifact 中。
5. 预算、取消、服务端重试、审计可见性验证完成前，不转换为内部 Evidence，也不进入 Runtime。

## 费用

公开价格约 $0.0001/s，冷启动或唤醒最低 30 秒，即至少 $0.003/次（另加模型 token）。
60 秒约 $0.006；1000 次冷启动最低约 $3（均仅 sandbox 部分）。
官方 shell guide 写“第一次命令开始至响应完成”，发布博客写“至最后一条命令”；
两处计费窗口有差异，实际以 provider 账单/日志为准，不能仅累加命令耗时。
idle 请求间不计费。Files API 无独立费用。

## 验证状态

- 新增 11 个标准库 unittest：请求隔离、ID 唯一、脱敏观测、三种 outcome、
  原生 call 配对、协议漂移拒绝、非法 ID/usage、未完成/无 shell 结果、输入不变。
- 先运行测试看到缺少 probe 的 10 个失败；审查补充命令/结果数量不匹配测试，
  先验证两个子案例失败再修复；最终 11/11 通过。
- 仓库默认 `python -m pytest -q` 未能启动：当前运行环境缺少 pytest；
  默认 Python 也缺少 httpx。未安装产品依赖、未运行完整回归，不声称全仓通过。
- 未真实调用 OpenRouter、未上传文件、未确认服务端完整 transcript 或实际账单。
- 生产 src/、依赖、Runtime、ToolRegistry、adapter 和 code-map 没有改动。

要判断值不值得继续：先补真实 capture，再用小型公开/合成任务比较固定模型与预算下的
成功率、P95、总费用、trace 完整性与隔离性。对 code-map 真值检索没有直接收益；
可尝试合成脚本/数据处理/评测产物生成。收益不明显就保留文档，删除 POC。

## 官方来源

- https://openrouter.ai/docs/guides/features/server-tools/shell
- https://openrouter.ai/docs/guides/features/server-tools/bash
- https://openrouter.ai/docs/guides/features/containers
- https://openrouter.ai/docs/guides/features/files-api
- https://openrouter.ai/docs/api_reference/responses/overview
- https://openrouter.ai/blog/announcements/shell-tool/
