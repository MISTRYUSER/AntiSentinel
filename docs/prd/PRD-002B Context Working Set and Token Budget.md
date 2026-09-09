# PRD-002B Context Working Set & Token Budget

## 基本信息

- **状态**：Draft
- **提出角色**：AntiSentinel PM / Runtime
- **目标**：把「每轮脱敏切片组装」升级为可预算、可连续、可观测的 Working Set 上下文管理
- **优先级**：P0
- **依赖**：PRD-002 Model Runtime Loop、PRD-003 Memory and Evidence Foundation、PRD-004 Skill Plugin（已落地能力）、PRD-005A Code Map Source Context（已落地能力）
- **后续依赖**：Compact 管线可被 PRD-005 RAG / PRD-006 Plan 复用；观测指标进入看板

## 0. 当前代码基线

已具备：

- `ContextBuilder` 每轮重建 `ModelRequest`：system、incident/session/turn、prior_turns 摘要、上一轮 task_results、可选 memory / source_context / skill。
- `RuntimeConfig` 用 `max_turns` / `max_tasks` / `max_tool_calls` / `max_total_tool_calls` 限制循环与工具次数。
- Memory：`recall_context(token_budget=400)`、session tree digest、trusted recall。
- Source：最多 4 片、合计约 32KiB UTF-8。
- Skill：指令 / reference 字符预算；超限拒绝加载，不静默截断。
- Checkpoint 可保存 `messages`、`pending_results`、`source_context_refs`、`skill_state`。

已知缺口（本 PRD 要解决）：

- **无全局 context token 预算与分块配额**；各通道各自截断/拒绝，互不协调。
- **工具历史断裂**：生产路径 `pending_results` 每轮覆盖，只回灌上一轮；与 PRD-002 设计「将所有 Task 结果加入下一轮」不一致。SkillsBench 路径累积历史，生产与评测行为分叉。
- **prior_turns 信息过稀**：常见摘要为 `"tool calls completed"`，缺少 plan / 工具事件要点。
- **无 assistant / plan 轨迹回灌**：模型上一轮结构化 tasks 不进入下一轮上下文。
- **Memory 中途空转**：`MemoryRecorder.record()` 在 `engine.run` 结束后才写入；同一次 run 内 timeline digest 基本不可用。
- **Token 估算不统一**：digest 用 `budget * 4` 字符；recall 用 `(len + 1) // 2`；Skill 规格写 `ceil(UTF8/3)`。
- **源码超限整片丢弃**：`ContextBuilder` 对超预算 slice 直接 `break`，无 `truncated` 标记（Code Map 设计要求有标记）。
- **Skill 每轮全量重付**：指令与已披露 reference 全文每请求注入，无 delta / 压缩策略。
- **Checkpoint 半残**：保存了 `messages` 但 resume 不消费；生产入口默认不接 `checkpoint_store`。
- **观测缺失**：设计要求记录 budget / 各块占用 / tokens_saved / truncated，代码未闭环。
- **无 AGENTS.md**：工程侧缺少上下文红线与改动检查清单。



## 1. 背景与问题

AntiSentinel 是 Incident 诊断 Agent，不是闲聊助手。模型连续性依赖的是：

1. 当前故障事实（Incident / EvidenceRef）。
2. 本 Session 已做过的计划与工具结果（Working Set）。
3. 受控披露的源码与 Skill 指令。
4. 可信记忆摘要。

当前实现用「运行时次数上限 + 局部粗糙截断」控量，导致：

- 多轮后模型看不到更早工具结论，重复调用或基于过时假设下结论。
- 局部预算互抢：Skill / 源码 / Memory 都可能把请求撑大，却没有统一淘汰顺序。
- 评测（SkillsBench）与生产 Context 策略不一致，指标不可外推。
- 超限行为不可观测：静默丢片、跳过记忆、或直接 fail session，产品侧难以区分。

本 PRD 不引入完整 chat transcript，也不做「从顶部砍最早 user/assistant」的通用对话截断；改为 **Working Set + 分块配额 + 分层保留**。

## 2. 用户与使用场景

```text
操作者提交 Incident
→ Runtime 每 Turn 组装 Working Set 上下文
→ 模型看到：sticky 信息 + 最近 K 轮工具/计划摘要 + 更早轮 digest + memory/source/skill（受配额）
→ 工具执行后，结果以摘要+EvidenceRef 进入 Working Set（非原文）
→ 预算不足时按优先级淘汰或 compact，并打 truncated / dropped 指标
→ Session 结束或中断时可从 Working Set / Checkpoint 恢复等价上下文
```

典型失败场景：

- 连续 5+ 轮工具调用后，模型仍需引用第 1 轮读到的错误码摘要。
- 已 load Skill + 多份 reference + 4 片源码时，总输入逼近模型窗口。
- 重复 tool 调用被拒绝时，模型必须仍能在上下文中看到「已有结果摘要」。



## 3. 目标

1. 定义 **Working Set** 作为模型跨轮连续性的唯一短期载体（非完整对话 transcript）。
2. 引入 **全局** `max_context_tokens` **+ 分块配额**，统一估算算法与版本号。
3. 生产路径与 SkillsBench 对齐：**累积**工具/计划摘要，但受 K 与 token 配额约束。
4. 提升 prior_turns / plan 摘要信息密度；禁止无信息占位摘要作为唯一历史。
5. 源码超限改为行级/窗口裁剪并标记 `truncated`；禁止静默整片丢弃且不留痕。
6. Session run 中途增量写入 timeline，使 memory digest 在同一次诊断内可用。
7. Checkpoint / resume 能重建与中断前等价的 Working Set（至少摘要级等价）。
8. 每次 `context.build` 产出可观测的 budget 报告；沉淀 Event / 指标。
9. 仓库增加 `AGENTS.md`，固化脱敏红线与上下文改动检查清单。



## 4. 非目标（明确不做）

- 不做完整 user/assistant chat transcript 回灌。
- 不把 `Attempt.result`、Evidence 本体、密钥、隐藏 thinking 注入模型（`source_context` 受控通道除外）。
- 第一版不做依赖外部 LLM 的异步 compact 服务（可预留接口；首版允许规则/模板 compact）。
- 不在本 PRD 扩大 `max_turns` 产品默认值，也不重做 Tool Policy。
- 不把 Skill 权限边界改为「可由 Skill 扩大工具权限」。
- 不要求第一版接入真实 tokenizer SDK；允许可校准的启发式，但必须单一版本。



## 5. 解决方案



### 5.1 Working Set 模型

```text
WorkingSet
  ├── sticky
  │     ├── open_questions / active_hypotheses（可选，首版可空）
  │     ├── active_skill 元数据（id/version；正文仍走 skill 块）
  │     └── source_slices（受配额）
  ├── recent_turns[0..K]          # 默认 K=3
  │     ├── plan_summary          # 本轮模型 tasks/objectives 摘要
  │     ├── tool_events[]         # tool_name, args_fingerprint, status, result_summary, evidence_refs
  │     └── outcome               # turn 结果一句话
  ├── older_digest                # K 以外轮次的压缩摘要
  └── memory_view                 # TrustedMemoryRecall 输出（受配额）
```

组装进 `ModelRequest` 时仍保持结构化 role 块，不伪装成自由聊天。

### 5.2 全局预算与分块配额

引入 `ContextBudget`（名称可调整）：


| 块                                  | 建议默认份额（可配置） | 超限策略                                                       |
| ---------------------------------- | ----------- | ---------------------------------------------------------- |
| system + schema                    | 固定 / ~5%    | 不裁；超则视为配置错误                                                |
| incident + session + prior/working | ~35–40%     | 先缩 older_digest，再降 K，再缩短单条 summary                         |
| memory                             | ~15%        | 跳过低分记忆；digest 字符截断并标记                                      |
| source_context                     | ~15%        | 窗口裁剪 + `truncated=true`；再不够则按相关度丢最旧/最低优先片                  |
| skill                              | ~10–15%     | 目录保持；已激活指令优先；reference 按披露顺序保留，超额不加载新 ref（沿用拒绝语义）或后续 delta |
| 预留 / 工具清单 JSON                     | 剩余          | tools schema 可截断描述字段（需显式标记）                                |


- 配置项：`max_context_tokens`（或字符预算等价物）、各块份额、`recent_turn_limit(K)`。
- **统一估算**：选定一种算法并声明 `estimate_version`（建议与 Skill 规格对齐为 `ceil(utf8_bytes/3)`，或明确迁移路径）；禁止 digest/recall/skill 三套并存而不标注。
- Provider 返回的 `usage.input_tokens` 用于校准，不直接作为组装前真值。



### 5.3 跨轮连续性（替代「只留上一轮」）

1. `pending_results` 改为写入 Working Set 的 `tool_events` / `recent_turns`，**累积**而非覆盖。
2. 超出 K 的轮次折叠进 `older_digest`（规则模板首版即可，例如拼接 plan_summary + 关键 evidence_id 列表）。
3. 每轮模型若输出 tasks，落盘 `plan_summary`（截断到配额内），供下一轮 prior/working 使用。
4. `build_task_results_message` 扩展为可输出 recent tool_events（仍只含 summary + refs，不含 raw payload）。
5. 生产 `ContextBuilder` 与 `BenchmarkContextBuilder` 共享同一 Working Set / pack 实现，避免双轨。



### 5.4 源码与 Skill

- **Source**：超预算时对单片做行窗口裁剪（命中行 ±N 或文件头+符号块），设置 `truncated`；整片无法放入时记录 dropped，而不是无日志 `break`。
- **Skill（首版）**：维持「激活后指令可重建」；允许规格修订为「正文可从 SkillState 重建，上下文可只带 id/hash + 变更 delta」。若首版仍全量重带，则必须计入全局 skill 配额并在超总预算时优先压缩 catalog / 旧 reference 摘要，而不是挤掉 recent tool_events。



### 5.5 Memory 中途可用

- Turn 或工具批次结束后，增量 `append_event` / 更新 session tree，使同一次 `engine.run` 内 `recall_context` 能看到本会话进展。
- `recall` 的 query 不得仅固定 `title+summary`；至少纳入最近 tool_events 的短摘要或当前 turn objective。
- Memory 通道服从全局 pack，局部 `token_budget` 变为「块内上限」，不得突破全局剩余预算。



### 5.6 Checkpoint 与恢复

- Snapshot 增加或明确序列化 Working Set（或可从 turns/tasks/attempts + pending 重建的规范算法）。
- Resume 必须重建等价 Working Set；保存的 `messages` 可作为审计对照，但组装以 Working Set → ContextBuilder 为准（避免双源真相）。
- 生产诊断路径应可开启 checkpoint（至少 waiting_approval / 失败前）；`max_turns` 恢复时扣除已消耗 turn。



### 5.7 观测与审计

每次 `context.build` 记录：

- `estimate_version`、`max_context_tokens`、各块 `used` / `limit`
- `truncated_slices`、`dropped_memories`、`compacted_turns`
- `recent_turn_count`、`tool_event_count`

禁止在指标/日志中写入 Evidence 原文、密钥、完整 tool payload。

### 5.8 `AGENTS.md`

仓库根目录新增 `AGENTS.md`，至少包含：

- Context / 脱敏红线（什么能进模型、什么不能）
- Working Set 与预算策略摘要（指向本 PRD）
- 生产与 Benchmark 必须共享 pack 实现
- 改 `context.py` / `messages.py` / `loop.py` 的检查清单（checkpoint、指标、测试）

运行时 system prompt 仍在 `messages.py`；`AGENTS.md` 服务工程 Agent，不复制整份用户可见 prompt。

## 6. 分阶段交付



### Phase A — 连续性修复（最小可用）

- Working Set 累积 recent tool/plan 摘要（K 可配置）
- 替换生产「只留上一轮」行为
- prior/plan 摘要去空洞化
- 生产与 Benchmark 共用组装核心
- 基础单测 + 更新 PRD-002 行为说明



### Phase B — 全局预算 Pack

- `ContextBudget` + 统一 `estimate_version`
- 分块 pack / 淘汰顺序
- 源码 `truncated` 标记与窗口裁剪
- `context.build` 预算指标



### Phase C — Memory / Checkpoint / Skill 深化

- run 中增量 timeline + 动态 recall query
- Checkpoint 恢复 Working Set 与 turn 预算修正
- Skill delta 或配额联动（若规格修订）
- `AGENTS.md` 落地
- 看板展示 context token 分段（可与现有 observability 对齐）



## 7. 验收标准

必须同时满足：

1. 多轮（≥3）工具成功后，第 N 轮模型请求仍包含第 1 轮关键 tool `result_summary` 或其 compact 痕迹（在预算允许时）；不得仅因「不是上一轮」而消失。
2. 任意一次 `context.build` 的估算占用 ≤ `max_context_tokens`（允许配置关闭严格模式仅用于调试，默认开启）。
3. ModelRequest 中仍无 raw tool payload / Evidence 本体（受控 source_context 除外）；红线测试继续通过。
4. 源码超预算时，若仍保留该 path，则必须带 `truncated=true` 或显式 dropped 记录，禁止静默整片消失且无观测。
5. SkillsBench 与生产 Context 组装走同一 pack；允许 fixture 不同，不允许「一边累积一边覆盖」的双语义。
6. 同一次 Session run 内，第二轮及以后的 memory digest 至少能反映本 session 已发生的 turn/tool 事件（在 recorder 启用时）。
7. Resume 后下一轮请求的 Working Set 关键字段与中断前一致（摘要级）。
8. 新增/更新测试覆盖 §8；全量 `pytest` 通过。



## 8. 测试策略



### 8.1 单测（必做）


| 区域             | 建议文件                                                          | 断言要点                               |
| -------------- | ------------------------------------------------------------- | ---------------------------------- |
| Working Set 累积 | `tests/test_runtime_context.py`、新 `tests/test_working_set.py` | 多轮后仍含早期 tool summary；超 K 进入 digest |
| 脱敏红线           | 现有 runtime context 测试扩展                                       | 无 raw result / evidence body       |
| 全局 pack        | 新 `tests/test_context_budget.py`                              | 超预算时按优先级裁；总估算 ≤ cap                |
| 源码截断           | `tests/test_code_map_source_context.py`、runtime context       | `truncated` / dropped 行为           |
| Memory 中途      | `tests/test_memory_recall.py` 等                               | run 内第二轮 digest 非空（在写入路径启用时）       |
| Checkpoint     | `tests/test_checkpoint.py`                                    | resume 重建 Working Set；turn 计数正确    |
| Skill 配额       | `tests/test_skill_runtime.py`、loader 测试                       | 不突破全局/局部约定                         |
| Token 估算       | 新单测                                                           | 同一 `estimate_version` 跨模块一致        |




### 8.2 集成 / 循环

- `tests/test_runtime_loop.py`：多 turn 工具 → final；断言第 N 次 `model.complete` 请求 messages 含历史 working 摘要。
- 重复 tool 调用场景：拒绝信息与可见历史一致。
- `max_turns` / `max_total_tool_calls` 与预算同时存在时，失败码语义不变。



### 8.3 评测对齐

- SkillsBench session 改为调用共享 Context pack。
- 对比改造前后：相同 fixture 下工具重复率、达 final 轮次、context 估算分布。



### 8.4 全量回归门禁

```bash
# 开发迭代
pytest -q tests/test_runtime_context.py tests/test_working_set.py tests/test_context_budget.py \
  tests/test_runtime_loop.py tests/test_checkpoint.py tests/test_code_map_source_context.py \
  tests/test_memory_recall.py tests/test_trusted_memory_recall.py tests/test_skill_runtime.py

# 合并前全量
pytest -q
```

可选：Docker 启动烟测（`docs/validation/docker-startup.md`）验证真实入口仍能完成一次 Fake Provider 诊断。

### 8.5 手工 / 观测验收

- 打开一次多轮诊断，确认指标或日志中出现分块 budget 字段。
- 人为注入超大 source / skill reference，确认行为是截断标记或拒绝，而非不可解释失败。



## 9. 失败、重试、超时与权限

- **预算不足**：优先降级 Working Set / memory / source；不得为了塞源码而丢掉全部 tool 历史。
- **Skill / reference 超局部硬帽**：维持现有拒绝加载语义（`context_budget_exceeded`），并计入观测。
- **模型窗口仍被 provider 拒绝**：Session 失败码需可区分（例如 `context_overflow`），保留 budget 报告于 Event/错误详情（脱敏）。
- **Checkpoint 损坏**：不得用半套 messages 静默继续；失败并要求从头或明确 resume 错误。
- **权限 / 脱敏**：任何「为了连续性」的改动不得绕过 Evidence 原文禁入规则。



## 10. 需要沉淀的 Event、Evidence 与指标

**Event（建议）**

- `context.built`：budget 摘要、truncated/dropped 计数、estimate_version
- `context.compacted`：被折叠的 turn 数与 digest 长度
- 沿用现有 `model.*` / `tool.*`；不新增 Evidence 类型仅因 compact

**指标**

- `antisentinel_context_tokens_estimated`
- `antisentinel_context_block_tokens{block=...}`
- `antisentinel_context_truncated_total`
- `antisentinel_context_dropped_total{reason=...}`
- 与现有 `observe_tokens`（provider 真实 usage）对照，不互相覆盖

**文档**

- 本 PRD
- 根目录 `AGENTS.md`
- 必要时补充 `docs/superpowers/specs/` 详细设计（Ready 后）



## 11. 风险与开放问题

1. Skill 规格「每请求必须重带全文」与全局预算冲突时，以哪条为更高优先级？（建议：安全可重建前提下允许 delta，但需修订 PRD-004 规格）
2. `max_context_tokens` 默认值如何按 Fake / 真实模型配置？（建议按模型配置表，Fake 用较小值便于测裁剪）
3. older_digest 首版规则 compact 是否足够，还是 Phase C 再引入 LLM compact？
4. 是否在 Phase A 就强制生产启用 checkpoint？



## 12. 完成定义（DoD）

- [ ] 本文档状态推进到 Ready，并有对应 technical design（如需要）
- [ ] Phase A/B 代码合并，§7 验收项勾选完成
- [ ] `AGENTS.md` 已合并
- [ ] 相关单测/集成/全量 pytest 通过
- [ ] PRD 索引状态更新；与 PRD-002 / PRD-003 / Skill / Code Map 文档交叉引用已修正「只留上一轮」等过时描述