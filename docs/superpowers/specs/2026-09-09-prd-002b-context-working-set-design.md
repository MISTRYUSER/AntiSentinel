# PRD-002B Context Working Set & Token Budget 设计

## 背景与范围

PRD-002 已提供 Runtime Loop、`ContextBuilder`、脱敏消息组装与内存 Checkpoint。PRD-003 / Skill / Code Map 已分别提供 Memory recall、Skill 注入与源码切片通道。当前缺口是：无全局 token 预算、生产路径只回灌上一轮工具结果、prior/plan 摘要过稀、Memory 仅在 run 结束后写入、源码超限静默丢片、生产与 SkillsBench 上下文策略分叉。

本设计把「每轮脱敏切片组装」升级为 **Working Set + 分块配额 Pack**，并与 Evidence 职责划清边界。

**本设计采用的产品决策（默认建议，已确认）**

| # | 决策 |
|---|------|
| 1 | `ContextBuilder.build` 保持 `→ ModelRequest`；预算报告经可选回调 `on_budget_report` 旁路 |
| 2 | 统一估算 `ceil(utf8_bytes/3)`，版本号 `utf8_ceil_div3_v1`；迁移 memory/skill/context 旧算法 |
| 3 | `max_context_tokens` 可配置；Fake 默认 `8192`，real 默认 `32000`（可按模型表覆盖） |
| 4 | Phase A：Working Set 可序列化；生产 checkpoint **不强制**开启。Phase C 再接生产 resume |
| 5 | Skill 首版仍每请求全文重带，但计入 skill 块配额；超限优先压 catalog/旧 reference，不挤掉 recent tool_events |
| 6 | 不新增 `/api/context/*`；仅扩展 runtime config、observability、Prometheus、SSE Event |
| 7 | Loop 内 Working Set **原地 mutate** |

**非目标（同 PRD）**：完整 chat transcript；LLM 异步 compact；扩大 `max_turns`；改 Tool Policy；首版 tokenizer SDK。

## 真相源与数据边界

```text
Evidence（事实仓库）
  └── 工具原文 / 日志 / 受控源码切片本体
        ↑ 写入        ↓ 仅通过 evidence_id 再取
Working Set（本 Session 短期工作台）
  └── plan/tool 摘要 + evidence_refs + sticky 元数据
        ↓ Pack（受 ContextBudget）
ModelRequest.messages（本轮模型可见视图）
        ↓ 可选存档
Checkpoint.messages（审计对照，非恢复真相）
Checkpoint.working_set（恢复真相，Phase A 可写、Phase C 生产消费）
```

规则：

- 组装模型请求时，**短期连续性以 Working Set 为准**。
- Evidence 本体默认不进模型；`source_context` 仍为唯一受控原文通道。
- Resume 以 `working_set`（或规范重建算法）为准，不以保存的 `messages` 为准。

## 方案比较

### 方案 A：加长 prior_turns 字符串 + 继续覆盖 `pending_results`

改动最小，但无法统一配额、无法与 Benchmark 对齐、摘要仍易空洞。不采用。

### 方案 B：回灌完整 user/assistant transcript 再从顶部截断

与 PRD 非目标冲突，易泄漏 raw payload / thinking，且与结构化 role 块不一致。不采用。

### 方案 C：Working Set + 共享 Pack + ContextBudget（推荐）

新增小型模块，Loop 累积摘要，生产与 Benchmark 共用 `pack_context`。分阶段交付：A 连续性，B 全局预算，C Memory/Checkpoint/Skill 深化。采用。

## 组件与职责

### 模块布局

| 模块 | 职责 |
|------|------|
| `worker/runtime/working_set.py` | `WorkingSet` / `TurnRecord` / `ToolEvent` / sticky；`append_turn`、规则 compact |
| `worker/runtime/budget.py` | `estimate_tokens`、`ContextBudget`、`BudgetReport`、块配额分配 |
| `worker/runtime/pack.py` | 纯函数 `pack_context`：WorkingSet + 各通道 → messages + report |
| `worker/runtime/context.py` | 薄封装：调 pack → `ModelRequest`；转发 `on_budget_report` |
| `worker/runtime/messages.py` | system；working-set / tool-events / plan 摘要构建；去空洞化 |
| `worker/runtime/loop.py` | 持有可变 WorkingSet；每轮写 plan/tool；发 `context.built` |
| `worker/runtime/checkpoint.py` | Snapshot 增加可选 `working_set`；`rebuild_working_set` |
| `evaluation/skillsbench_session.py` | 删除自建历史累积；注入共享 pack（可 `system_override`） |
| `tracing/metrics.py` | context 分段计数器 |
| 仓库根 `AGENTS.md` | Phase C：脱敏红线与改 `context/messages/loop` 检查清单 |

### Working Set（`working_set.py`）

```text
WorkingSet
  sticky
    active_skill: {id, version} | None
    source_slice_refs: [...]          # 元数据；正文仍走 source 通道
    open_questions / active_hypotheses  # 首版可空列表
  recent_turns: list[TurnRecord]      # 长度 ≤ K，默认 K=3
  older_digest: str                   # K 以外轮次规则压缩
  memory_view: MemoryContextView | None  # 组装前由 Loop 填入；服从 pack
```

`TurnRecord`：`turn_index`、`plan_summary`、`tool_events[]`、`outcome`。

`ToolEvent`：`tool_name`、`args_fingerprint`、`status`、`result_summary`、`evidence_refs`。禁止 raw arguments / raw result。

API（原地更新）：

- `WorkingSet(recent_turn_limit=3)`
- `append_turn(turn: TurnRecord) -> None` — 超出 K 的最旧轮折叠进 `older_digest`
- `compact_older(max_digest_tokens: int) -> None`
- `update_sticky(...)` / `to_dict()` / `from_dict()`

折叠模板（首版规则 compact）：

```text
turn={i} plan={plan_summary}; tools=[{name}:{status}:{short_summary}|refs=...]; outcome={outcome}
```

多轮用换行或 `; ` 拼接，再按 digest token 上限截断并记 `compacted_turns`。

**去空洞化**：`outcome` / `result_summary` 不得仅使用无信息占位（如单独的 `"tool calls completed"`）。Loop 在写 TurnRecord 时用 `summarize_plan` / `summarize_tool_result` 生成至少含 tool_name + status + 截断摘要的文本；若业务摘要为空，回退为结构化短句，不得留空占位当唯一历史。

### Token 估算与预算（`budget.py`）

```python
ESTIMATE_VERSION = "utf8_ceil_div3_v1"

def estimate_tokens(text: str, *, version: str = ESTIMATE_VERSION) -> int:
    # ceil(utf8_byte_length / 3)
```

所有 digest / recall / skill / pack 路径只允许经此函数（或声明同一 version 的包装）。禁止模块内私有第二套公式。

`ContextBudget` 字段：

- `max_context_tokens`
- `recent_turn_limit`（K）
- `shares`：各块份额
- `estimate_version`
- `strict`（默认 True：pack 后总估算 ≤ max，否则视为实现错误或触发更深裁剪）

默认份额（可配置）：

| 块 | 默认份额 | 超限策略 |
|----|----------|----------|
| `system` | ~5% 或按实际固定占用预留 | 不裁；超则配置错误 |
| `working` | 38% | 缩 older_digest → 降 K → 缩短单条 summary |
| `memory` | 15% | 跳过低分；digest 截断并标记 |
| `source` | 15% | 行窗口裁剪 + `truncated=true`；再丢低优先片并记 dropped |
| `skill` | 12% | 目录保留；指令优先；旧 reference 摘要化或拒载新 ref |
| `tools` | 剩余 | 可截断工具描述字段并显式标记 |

默认 `max_context_tokens`：

- Fake / 测试：`8192`
- Real（无模型表时）：`32000`
- 严格模式可关，仅调试。

`BudgetReport`：`estimate_version`、`max_context_tokens`、各块 `used/limit`、`truncated_slices`、`dropped[]`、`compacted_turns`、`recent_turn_count`、`tool_event_count`。报告与日志禁止 Evidence 原文、密钥、完整 tool payload。

### Pack（`pack.py`）

```python
@dataclass
class PackInput:
    incident, session, turn
    working_set: WorkingSet
    tools: list[dict]
    memory_context: MemoryContextView | None
    skill_context: dict | None
    source_context: list | None
    budget: ContextBudget
    system_override: str | None = None  # SkillsBench

@dataclass
class PackResult:
    messages: list[dict]
    report: BudgetReport

def pack_context(inp: PackInput) -> PackResult: ...
```

组装顺序（role 块，非自由聊天）：

1. `system`（或 override）
2. `user`：incident / session / turn + working 视图（sticky 摘要、recent_turns、older_digest）；**不再依赖稀薄的 prior_turns 字符串作为唯一历史**
3. `tool`：由 working set 的 recent tool_events（及必要时兼容层）生成
4. `memory` / `source_context` / `skill*`：按块配额裁剪后附加
5. tools schema 进入 `ModelRequest.tools`，占用计入 `tools` 块

**块间优先级（总预算不足时）**：保护 `working.recent` tool/plan 摘要优先于塞满 source/skill；不得为源码清空全部 tool 历史。

**源码**：单片超块内剩余预算时行窗口裁剪（命中行 ±N 或文件头+符号块），设 `truncated=true`；整片放不下则 `dropped` 并打点，禁止无日志 `break`。

**Skill（首版）**：全文重带但受 skill 块与全局剩余约束；超额不加载新 reference（保留 `context_budget_exceeded` 语义）或压缩 catalog / 旧 ref 摘要。

### ContextBuilder（`context.py`）

保持对外鸭子类型：

```python
def build(..., on_budget_report: Callable[[BudgetReport], None] | None = None) -> ModelRequest
```

内部构造 `PackInput` → `pack_context` → `ModelRequest(messages=..., tools=...)`；若提供回调则调用。Benchmark 与生产共用此类（或共享同一 `pack_fn`）；Benchmark 仅传 `system_override` / 不同 fixture 数据。

过渡期：`task_results` 参数可保留，由 Loop 改为「从 WorkingSet 导出的近期事件」；最终以 WorkingSet 为准。

### Loop 集成（`loop.py`）

状态变更：

- 新增可变 `working_set: WorkingSet`
- **废除**生产路径「`pending_results = turn_results` 整表覆盖」作为唯一连续性；改为每轮结束后：
  1. 从模型 tasks 写 `plan_summary`
  2. 从工具结果写 `tool_events`（summary + refs）
  3. `working_set.append_turn(...)`
  4. 下一轮 `build(..., working_set=...)`（或由 builder 从 loop 状态读取）

`RuntimeConfig` 扩展：

- `max_context_tokens: int`
- `recent_turn_limit: int = 3`
- `context_budget_shares: dict[str, float] | None = None`
- `context_strict: bool = True`

每轮 `build` 后：`event_sink` 发 `context.built`（BudgetReport 摘要字段）；超 K 折叠时发 `context.compacted`。OTel：可选 `context.build` span，属性为 used/limit/truncated 计数，不含 payload。

Checkpoint：保存时写入 `working_set=working_set.to_dict()`；`pending_results` 可暂留兼容字段（由 working set 导出最近工具摘要），避免旧测试瞬间全挂。Phase A 生产入口仍可不传 `checkpoint_store`。

### Memory（Phase C，接口 Phase A 预留）

- Turn / 工具批次结束后 `MemoryRecorder.append_turn_events(...)`，使同一次 `engine.run` 内 timeline 可用。
- `recall_context` 的 query 纳入最近 tool_events 短摘要或 turn objective；块内 `token_budget` 不得超过 pack 分配的 memory 上限。
- Phase A：可不改写入时机，但 pack 已能接纳 memory_view。

### Checkpoint（`checkpoint.py`）

`RuntimeSnapshot` 增加可选字段 `working_set: dict | None = None`（`from_dict` 用 `.get`，旧快照可缺省）。

```python
def rebuild_working_set(snapshot: RuntimeSnapshot, *, recent_turn_limit: int) -> WorkingSet:
    if snapshot.working_set:
        return WorkingSet.from_dict(snapshot.working_set)
    # 规范重建：从 turns/tasks/attempts 摘要生成（Phase C 补全；Phase A 可只支持显式字段）
```

损坏或半套数据：失败并明确 resume 错误，禁止用残缺 `messages` 静默继续。

### 观测与 HTTP

**Event**

- `context.built`：estimate_version、max、blocks used/limit、truncated/dropped 计数、K、tool_event_count
- `context.compacted`：compacted_turns、digest 估算 token

**Prometheus**

- `antisentinel_context_tokens_estimated`
- `antisentinel_context_block_tokens{block=...}`
- `antisentinel_context_truncated_total`
- `antisentinel_context_dropped_total{reason=...}`

与 `observe_tokens`（provider usage）对照，不互相覆盖。

**HTTP**

- `GET /api/runtime/config`：增加 `max_context_tokens`、`recent_turn_limit`、`estimate_version`
- `GET /api/observability/sessions/{id}`：附最近 `context.built` 摘要（若有）
- `GET /metrics`：上述指标
- 不新增 context 写接口

### SkillsBench 对齐

`BenchmarkContextBuilder` 改为委托共享 `pack_context`（或删除类、直接用 `ContextBuilder` + override）。禁止再维护独立 `_result_history` 累积语义；连续性只来自 WorkingSet（由 Loop 统一维护时，Benchmark 路径同样走 Loop）。

## 分阶段实现顺序

### Phase A — 连续性 ✅（已实现）

1. `working_set.py` + 摘要 helpers（`messages.py`）
2. Loop 累积 + 去空洞化；替换覆盖式 `pending_results` 主路径
3. `pack.py` 最小版（软配额 SoftBudget；生产/Bench 共用 `pack_context`）
4. `ContextBuilder` 改调 pack；SkillsBench 经 `make_benchmark_context_builder()` 共享实现
5. Snapshot 可选 `working_set` 字段 + `rebuild_working_set`
6. 测试：`test_working_set.py`、扩展 `test_runtime_context.py` / `test_runtime_loop.py` / `test_checkpoint.py`

### Phase B — 全局预算

详细设计见：`docs/superpowers/specs/2026-09-09-prd-002b-phase-b-context-budget-design.md`。

1. `budget.py` 统一 `estimate_tokens`；迁移 memory/skill 旧估算
2. 分块 pack + 淘汰顺序；source `truncated` / dropped
3. `BudgetReport` + `context.built` + metrics
4. 测试：`test_context_budget.py`、源码截断、估算一致性；紧/宽预算对照
5. 删除 SoftBudget 权威路径

### Phase C — 深化

1. Memory 中途 append + 动态 recall query
2. 生产可开启 checkpoint；resume 重建 Working Set；`max_turns` 扣已消耗
3. Skill 配额联动（全文仍重带或规格修订 delta）
4. `AGENTS.md`；看板分块展示
5. 更新 PRD-002 过时「只留上一轮」描述；PRD-002B → Ready

## 失败语义

| 场景 | 行为 |
|------|------|
| 预算不足 | 按块策略降级；保护 tool 历史优先于 source |
| Skill/reference 局部硬帽 | 保持拒绝加载 + `context_budget_exceeded` + 观测 |
| Provider 仍拒窗口 | Session 失败码可区分（如 `context_overflow`），附脱敏 budget 报告 |
| Checkpoint 损坏 | resume 失败，不静默用半套 messages |
| 脱敏 | 任何连续性改动不得让 Evidence 原文进入模型（source 通道除外） |

## 测试要点（对齐 PRD §8）

- 多轮 ≥3 后第 N 次 `model.complete` 仍含第 1 轮 tool `result_summary` 或 compact 痕迹（预算允许时）
- `context.build` 估算 ≤ `max_context_tokens`（strict）
- 红线：无 raw tool payload / Evidence body
- 源码：保留 path 则 `truncated=true` 或显式 dropped
- 生产与 Bench 同一 pack 语义
- Phase C：同 run 内第二轮 memory digest 可见本 session 事件；resume Working Set 摘要级一致
- 门禁命令见 PRD-002B §8.4

## 开放问题（已关闭）

见文首「本设计采用的产品决策」。Skill 全文 vs delta 若后续修订 PRD-004，在 Phase C 再开变更，不阻塞 A/B。

## 完成定义

- [ ] 本设计评审通过
- [ ] Phase A/B 按序合并，PRD §7 验收勾选
- [ ] Phase C 与 `AGENTS.md` 合并
- [ ] 相关 pytest 与全量回归通过
- [ ] PRD-002B 状态 → Ready；交叉引用过时描述已修正
