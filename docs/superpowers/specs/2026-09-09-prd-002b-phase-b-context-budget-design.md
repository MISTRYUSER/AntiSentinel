# PRD-002B Phase B — 全局预算 Pack 设计

- **状态**：Implemented（核心路径已落地；SoftBudget 兼容 shim 待删除；Real 校准可选跑）
- **日期**：2026-09-09
- **上游**：`docs/prd/PRD-002B Context Working Set and Token Budget.md` §5.2 / §5.4 / §5.7 / Phase B；`docs/superpowers/specs/2026-09-09-prd-002b-context-working-set-design.md`
- **依赖**：Phase A 已落地（Working Set 累积、共享 pack、去空洞、OFF/ON 对照、digest 软帽）
- **非本阶段**：Memory 中途写入与动态 recall（C）；生产强制 checkpoint resume（C）；Skill delta 协议（C）；`AGENTS.md`（C）；LLM async compact；正式 tokenizer SDK

## 0. 评审增补（已采纳，实现必须遵守）

### 0.1 估算校准（不为换 SDK，只为量误差）

- 启发式仍为唯一组装前真值：`ceil(utf8_bytes/3)` / `utf8_ceil_div3_v1`。
- Phase B 增加 **校准脚本/Case**：少量真实 `model.complete` 后对比 `BudgetReport.total_estimated` vs `usage.input_tokens`，产出误差分布（mean / p50 / p95 / max），写入 `docs/validation/prd-002b-phase-b/calibration-*`。
- 校准 **不** 改 pack 行为；仅决定后续是否需要模型表系数或 tokenizer。

### 0.2 Source 窗口可配置

- `source_window_radius`：**可配置**（RuntimeConfig / ContextBudget），默认 40。
- 增加可选 `source_window_by_suffix: dict[str, int]`（如 `.py→40`, `.json→20`, `.md→30`）；未命中用默认。
- Phase B **不做**完整 AST 符号块裁剪（留给 Code Map 深化）；无命中行时仍用头+尾窗口 + 模型可读标记。

### 0.3 Skill 降级要「晚、软、可观测」

优先级（从先到后，尽量晚动 active 正文）：

1. 截断 **未激活** catalog 的长 description（末尾 `[truncated]`）
2. 压缩/摘除 **最旧已披露 reference**（改为 `id+hash+short` 或 drop，记 report）
3. 最后才考虑截断 active instructions（尽量避免）
4. **禁止** 为 skill 删除 `working.recent` tool_events

单测：多 skill catalog + 大 reference 时，仍能在紧预算下保留至少 1 轮完整 tool summary。

### 0.4 BudgetReport 存在哪

| 通道 | 用途 | 存储 |
|------|------|------|
| Runtime Event `context.built` | 主真相、SSE、可审计 | 经现有 `event_bus`；持久化开启时走 **SQLite EventStore**（与其它 runtime event 相同），**不**单独 Redis key |
| Prometheus counters/gauges | 聚合观测 | 进程内 registry（`/metrics`） |
| `GET .../observability/sessions/{id}` | 最近一次摘要 | 读事件/结果视图，不新表 |

不做：Redis 专表存 BudgetReport；不做独立 `context_budgets` SQLite 表（除非 C 看板需要再议）。

### 0.5 超限策略修正

| 规则 | 决定 |
|------|------|
| K 下限 | **`min_recent_turns = 1`**，禁止降到 0（避免「只剩 digest」退化成旧逻辑一样差） |
| digest/memory 截断标记 | **模型可读**：正文末尾追加 ` [truncated]`；source 中间插入 `# ... [truncated] ...`；slice 字段 `truncated=true` 给程序/观测双消费 |
| tools 软底线 | `tools_min_share` 默认 **0.08**（allocate 时 tools = max(remainder, floor)），并从其它弹性块（优先 skill catalog / source 额外片）借；另：`tools_core_names` 或「注册表前 N 个」完整保留 description，其余可截 |
| system 超限 | **启动/配置预检** `preflight_system_budget(system_text, budget)`：超限 → `InvalidInputError` / 配置错误，**不**等到诊断中途 |
| source 15% | 保持；大文件靠窗口+dropped，不靠抬份额 |

Working 块内淘汰更新为：

```text
while working.used > working.limit:
  1. compact_older(更小)
  2. 若 recent_turns > min_recent_turns(1): 降 K 并 fold
  3. 截断单条 summary（保 tool_name:status 前缀 + 末尾 [truncated]）
  永不删光 recent；K 停在 1
```

## 1. Phase A 基线与 Phase B 要关掉的缺口

### 1.1 Phase A 已具备

| 能力 | 位置 |
|------|------|
| Working Set 累积 / K 折叠 / digest 软帽 | `working_set.py`、`loop.py` |
| 共享 `pack_context`（生产 + SkillsBench） | `pack.py`、`context.py` |
| `PackReport`（轻量计数，无分块配额） | `pack.py` |
| SoftBudget：源码仍按 4 片 / 32KiB，超限 `break` | `pack.py` |
| OFF/ON 对照与验收 Case | `scripts/compare_*`、`docs/validation/prd-002b-phase-a/` |

### 1.2 Phase B 必须关闭的缺口（对图 / PRD）

| 缺口 | Phase B 交付 |
|------|----------------|
| 无全局 context token 预算与分块配额 | `ContextBudget` + 分块 pack |
| Token 估算混乱 | 唯一 `estimate_tokens` + `estimate_version` |
| 源码超限粗暴丢片 | 行窗口裁剪 + `truncated=true` / 显式 dropped |
| 缺乏观测 | `BudgetReport`、`context.built`、Prometheus 分块指标 |
| Skill 每轮全文（本阶段不改协议） | **计入 skill 块配额**；超限压 catalog/旧 ref，**不挤** recent tool |

明确 **不做**（留给 C 或以后）：Memory 中途可用、Checkpoint 生产强制、Skill id/hash delta、看板 UI 深改。

## 2. 目标与验收（Phase B 子集）

对齐 PRD §7 中与预算相关的项：

1. 任意一次 `context.build`（strict 默认开）估算占用 **≤ `max_context_tokens`**。
2. 源码：若仍保留某 path，必须 `truncated=true` **或** report 中有显式 dropped；禁止静默整片消失。
3. 红线：无 raw tool payload / Evidence 本体（受控 source 除外）——回归既有测试。
4. 生产与 Bench 仍走同一 pack；预算逻辑不得再开双轨。
5. 预算不足时：**不得为塞 source/skill 清空全部 recent tool 历史**。
6. 每次 build 产出可观测 `BudgetReport`（Event + metrics）；日志无密钥/原文。
7. 统一估算：memory digest/recall/skill/pack/working_set 均声明或调用同一 `estimate_version`。

## 3. 方案选择

| 方案 | 说明 | 结论 |
|------|------|------|
| A. 各通道继续局部截断，只加日志 | 不满足「全局协调」 | 否 |
| B. 引入真实 tokenizer SDK | PRD 非目标；首版过重 | 否 |
| C. `ContextBudget` + 启发式 `ceil(utf8/3)` + 分块淘汰状态机 | 与已确认决策一致 | **采用** |

## 4. 组件与 API

### 4.1 新模块 `worker/runtime/budget.py`

```python
ESTIMATE_VERSION = "utf8_ceil_div3_v1"

def estimate_tokens(text: str, *, version: str = ESTIMATE_VERSION) -> int:
    """ceil(utf8_byte_length / 3). Unknown version → ValueError."""

def estimate_json(value: object, *, version: str = ESTIMATE_VERSION) -> int:
    """Stable JSON dump (sort_keys, ensure_ascii=False) then estimate_tokens."""

DEFAULT_SHARES: dict[str, float] = {
    "system": 0.05,
    "working": 0.38,
    "memory": 0.15,
    "source": 0.15,
    "skill": 0.12,
    # tools = remainder after allocate()
}

@dataclass(frozen=True)
class ContextBudget:
    max_context_tokens: int
    recent_turn_limit: int = 3
    min_recent_turns: int = 1              # K 下限，禁止 0
    shares: dict[str, float] | None = None   # None → DEFAULT_SHARES
    estimate_version: str = ESTIMATE_VERSION
    strict: bool = True
    max_source_slices: int = 4
    source_window_radius: int = 40
    source_window_by_suffix: dict[str, int] | None = None
    max_older_digest_tokens: int | None = None
    tools_min_share: float = 0.08
    tools_core_limit: int = 8              # 前 N 个工具保留完整 description
    truncation_marker: str = " [truncated]"

    def allocate(self) -> dict[str, int]: ...
    def window_radius_for_path(self, path: str) -> int: ...
    def preflight_system(self, system_text: str) -> None:
        """Raise if system alone exceeds system block limit."""

@dataclass
class BlockUsage:
    used: int
    limit: int
    truncated: bool = False
    dropped: int = 0

@dataclass
class DroppedItem:
    kind: str          # source|memory|skill_ref|tool_desc|working_summary
    reason: str        # over_block|over_global|low_priority|window_failed
    ref: str           # path / memory_id / skill_id — 无正文

@dataclass
class BudgetReport:
    estimate_version: str
    max_context_tokens: int
    strict: bool
    blocks: dict[str, BlockUsage]
    truncated_slices: list[dict]   # {path, evidence_id, before_tokens, after_tokens}
    dropped: list[DroppedItem]
    compacted_turns: int
    recent_turn_count: int
    tool_event_count: int
    total_estimated: int
    within_budget: bool
```

**配置默认值**（进 `RuntimeConfig`）：

| 字段 | Fake / 测试 | Real（无模型表） |
|------|-------------|------------------|
| `max_context_tokens` | `8192` | `32000` |
| `context_strict` | `True` | `True` |
| `context_budget_shares` | `None`（用 DEFAULT） | `None` |
| `recent_turn_limit` | `3`（A 已有） | `3` |
| `max_older_digest_tokens` | `2048`（A 已有，B 可由 working 块覆盖更严） | 同左 |

环境变量（可选，与现有风格一致）：

- `ANTISENTINEL_MAX_CONTEXT_TOKENS`
- `ANTISENTINEL_CONTEXT_STRICT=0|1`

### 4.2 迁移统一估算

| 现状 | Phase B |
|------|---------|
| `working_set._estimate_tokens` | 改为调用 `budget.estimate_tokens`（可留薄包装） |
| `session_tree.digest`: `token_budget * 4` 字符 | 改为按 `estimate_tokens` 累加行，直到 ≥ 块内/调用方预算 |
| `trusted_recall` / retrieval：`(len+1)//2` 或 `token_budget*2` 字符 | 改为 `estimate_tokens`；保留「块内上限」语义 |
| Skill loader：字符硬帽 | **保留**字符硬帽作为局部安全阀；pack 层另用 token 配额。报告中同时可观测两者 |
| SoftBudget 字节帽 | 删除为权威路径；`max_source_slices` 保留为片数硬帽 |

迁移原则：对外函数签名尽量不变（如 `token_budget: int` 仍表示「本通道允许的估算 token」），只换内部度量。

### 4.3 Pack 升级（`pack.py`）

替换 `SoftBudget` / `PackReport`：

```python
@dataclass
class PackInput:
    ...
    budget: ContextBudget
    # soft_budget 删除或仅测试兼容期映射到 ContextBudget

@dataclass
class PackResult:
    messages: list[dict]
    report: BudgetReport
    tools: list[dict]          # 可能已截断 description
```

`ContextBuilder`：

- 构造 `ContextBudget`（来自 ctor / RuntimeConfig）
- `on_budget_report: Callable[[BudgetReport], None] | None`（A 已有 PackReport 回调，B 换类型）
- `build` 仍返回 `ModelRequest`；`tools` 使用 pack 返回的可能截断清单

### 4.4 Loop / 观测

每轮 `build` 后：

1. 回调收集 `BudgetReport`
2. `event_sink` → `context.built`（脱敏摘要字段，见 §6）
3. 若本轮 `compacted_turns` 增加 → `context.compacted`
4. `observability_metrics.observe_context_budget(report)`（新方法）

`strict=True` 且 pack 结束后 `total_estimated > max`：视为实现错误——先再跑一轮更深裁剪；仍超则 `ModelRequest` 不发出，Session 失败码 `context_pack_exceeded`（与 provider 的 `context_overflow` 区分）。

## 5. 分块组装与淘汰状态机

### 5.1 组装顺序（不变）

1. system（或 override）  
2. user：incident/session/turn + working_set 视图  
3. tool：recent tool_events  
4. memory / source_context / skill*  
5. `ModelRequest.tools`（计入 tools 块）

先 **按块独立装到块上限**，再做 **全局校验**；全局仍超则按块间优先级继续压。

### 5.2 Working 块内淘汰（保护连续性）

```text
while working.used > working.limit:
  1. compact_older(更小 digest_tokens)     # 已有 API
  2. 若 recent_turns > min_recent_turns(1): 降 K 并 fold
  3. 截断单条 result_summary / plan_summary
     （保 tool_name+status 前缀，末尾追加 truncation_marker）
  永不删除「最后一条成功 tool_event」；K 不得低于 1
```

**块间**：总预算不足时，裁剪顺序为：

```text
tools 非核心 description 截断（核心 tools_core_limit 个完整保留）
  → skill：未激活 catalog → 旧 reference →（尽量不动）active instructions
    → memory 低分跳过 + digest 截断 + [truncated]
      → source 窗口裁 / drop 片
        → working 按上表（最后动；K≥1）
```

禁止：为保留 4 片满源码而清空 `recent_turns` 的全部 tool_events。
allocate 时保证 `tools >= max(remainder, floor(max * tools_min_share))`；不足则从 skill/source 份额下调弹性部分。
### 5.3 Source 窗口裁剪

对每个 `SourceContextSlice`（按输入顺序，片数 ≤ `max_source_slices`）：

1. 估算整片 tokens；若 ≤ 剩余 source 块预算 → 原样纳入。  
2. 否则尝试窗口：
   - 若 metadata/内容可解析「命中行」→ 取 `[hit-R, hit+R]`（`source_window_radius`）  
   - 否则：文件头 `H` 行 + 尾 `T` 行（默认各 30 行），中间插入标记行 `# ... truncated ...`  
3. 裁后设 `truncated=true` 写入 message 中该 slice 字段；记入 `truncated_slices`。  
4. 若窗口后仍 > 剩余预算 → **dropped**（`kind=source`, `reason=over_block`, `ref=path`），**不**静默 `break` 且不把半残片无标记塞进 messages。  
5. 超出 `max_source_slices` 的后续片一律 dropped（`reason=slice_cap`）。

Message 形态示例：

```json
{
  "role": "source_context",
  "slices": [{
    "evidence_id": "...",
    "path": "a.py",
    "content": "...",
    "content_hash": "...",
    "truncated": true
  }]
}
```

### 5.4 Memory 块（B 只做 pack 侧）

- 输入仍是 `MemoryContextView`；B **不改** recorder 写入时机。  
- 若 `estimate_tokens(digest) > memory.limit`：截断 digest 字符串并在 report 中 `blocks.memory.truncated=True`。  
- 若未来 view 带多条候选：按已有召回分从低到高 drop，记 `dropped_memories`（C 可加分数字段；B 可用顺序近似）。

### 5.5 Skill 块（全文仍重带）

- catalog / active instructions / references 全部计入 skill 块。  
- 超限策略：
  1. 截断或省略未激活 catalog 条目的长 description（标记）  
  2. 已披露 reference：从最旧开始改为「id+hash+短摘要」或移出 messages（与现有拒载语义对齐时记 `context_budget_exceeded` 类 dropped，**不**在 pack 内抛到中断 Session，除非 skill 通道硬错误已存在）  
  3. **绝不**为 skill 删除 working.recent tool_events  

局部 SkillLoader 字符硬帽继续生效（双保险）。

### 5.6 Tools 块

- 对 `tools[].description`（及过长 schema 描述字段）可截断并加 `description_truncated: true`（若结构允许）或仅在 report.dropped 记录。  
- 工具 **name** 与参数 schema 的 required 结构不删，避免模型无法调用。

## 6. 观测

### Event `context.built` payload（脱敏）

```json
{
  "estimate_version": "utf8_ceil_div3_v1",
  "max_context_tokens": 8192,
  "total_estimated": 6400,
  "within_budget": true,
  "blocks": {"working": {"used": 2000, "limit": 3110}, "...": "..."},
  "truncated_slice_count": 1,
  "dropped_count": 2,
  "compacted_turns": 4,
  "recent_turn_count": 3,
  "tool_event_count": 5
}
```

### Event `context.compacted`

`compacted_turns`, `digest_tokens_after`

### Prometheus（`tracing/metrics.py`）

```text
antisentinel_context_tokens_estimated
antisentinel_context_block_tokens{block}
antisentinel_context_truncated_total
antisentinel_context_dropped_total{reason}
```

与 `observe_tokens`（provider 真实 usage）并存；可用后续看板做校准比，B 不强制写校准逻辑。

### HTTP

- `GET /api/runtime/config` 增加：`max_context_tokens`, `recent_turn_limit`, `estimate_version`, `context_strict`
- `GET /api/observability/sessions/{id}`：若事件流已有 `context.built`，附最近一次摘要
- 不新增 `/api/context/*`

## 7. 实现顺序（建议 PR 切分）

| Step | 内容 | 测试 |
|------|------|------|
| B1 | `budget.py`：estimate / allocate / preflight / BudgetReport | `test_context_budget.py` |
| B2 | 统一估算迁移 + truncation_marker 工具函数 | recall/skill/working_set |
| B3 | pack 接 ContextBudget；K≥1；tools 软底线；skill 晚降级 | pack 优先级测 |
| B4 | Source 可配置 radius + 可读 truncated 标记 | source 测 |
| B5 | Loop `context.built`→event_bus；metrics；config；启动 preflight | 集成 |
| B6 | SoftBudget 删除 | A 回归 |
| B7 | 紧/宽对照 + **校准脚本**（estimate vs usage） | `prd-002b-phase-b/` |

## 8. 对照实验（证明 B「有效」）

不同于 A 的 WorkingSet OFF/ON，B 建议：

| 条件 | 含义 |
|------|------|
| **WIDE** | `max_context_tokens` 很大（如 1e6），近似不裁 |
| **TIGHT** | Fake 默认 `8192` 或故意更小（如 `2048`） |
| **LEGACY_SOFT**（可选） | 临时开关走旧 SoftBudget 路径（迁移期） |

指标：

| 指标 | 期望 |
|------|------|
| `within_budget` | TIGHT 下恒 true（strict） |
| `early_summary_coverage`（同 A 的 turn1 marker） | TIGHT 下仍 ≥ 门槛（如仍见 turn1 或 digest 痕迹）；优先于 source 全保留 |
| `truncated_slice_count` / `dropped_count` | 注入超大 source 时 >0 且可观测 |
| `secret_leaked` | 恒 false |
| `total_estimated` | TIGHT ≤ max；WIDE 可更大 |
| provider `usage.input_tokens`（可选 Real） | 仅校准笔记，不作门禁 |

产物目录建议：`docs/validation/prd-002b-phase-b/`（`cases.md`、`baseline-*`、`compare-tight-wide-*`）。

## 9. 测试清单

| 文件 | 要点 |
|------|------|
| `tests/test_context_budget.py` | 估算版本、allocate 份额、strict 超限 |
| `tests/test_runtime_context.py` | 超大 source → truncated 或 dropped；无静默消失 |
| `tests/test_code_map_source_context.py` | 窗口行为与 hash/红线 |
| `tests/test_working_set.py` | 仍用统一 estimate |
| `tests/test_memory_recall.py` / trusted | token_budget 语义换度量后行为 |
| `tests/test_skill_runtime.py` | 局部硬帽 + pack 不挤掉 tool 历史 |
| `tests/test_runtime_loop.py` | `context.built` 出现；多轮连续性在紧预算下仍成立 |
| `tests/test_integration_smoke.py` | config 字段；全链路紧预算不炸 |

门禁示例：

```bash
pytest -q tests/test_context_budget.py tests/test_working_set.py \
  tests/test_runtime_context.py tests/test_runtime_loop.py \
  tests/test_checkpoint.py tests/test_code_map_source_context.py \
  tests/test_memory_recall.py tests/test_skill_runtime.py \
  tests/test_integration_smoke.py
```

## 10. 失败语义（B 增补）

| 场景 | 码 / 行为 |
|------|-----------|
| pack strict 仍超 | `context_pack_exceeded`，附 BudgetReport 摘要 |
| Skill 局部字符硬帽 | 现有 `context_budget_exceeded`（loader） |
| Provider 拒窗口 | `context_overflow`（若可区分）+ 保留上次 built 报告 |
| 源码整片不可放入 | dropped + 指标；Session 可继续 |

## 11. 风险与开放点（B 内）

1. **启发式 vs 真 tokenizer**：Fake/Real 校准误差可能大；报告同时留 `total_estimated` 与 provider usage，B 不做自动调参。  
2. **降 K 与 A 的连续性验收**：紧预算下 turn1 可能仅在 digest；验收改为「summary 或 compact 痕迹」——与 PRD §7.1 一致。  
3. **Skill 全文 + 小窗口**：极端 Skill 可能导致 skill 块占满；必须单测「working.recent 仍在」。  
4. **SoftBudget 迁移窗口**：允许一两个 PR 内双路径，合并前删除 SoftBudget，避免长期双轨。

## 12. 完成定义（Phase B）

- [ ] 本设计评审通过  
- [ ] `budget.py` 落地；统一 `estimate_version`  
- [ ] pack 分块淘汰 + source truncated/dropped  
- [ ] `context.built` + Prometheus 指标 + runtime config 字段  
- [ ] §9 测试与 Phase A 回归通过  
- [ ] `docs/validation/prd-002b-phase-b/` 含紧/宽对照报告  
- [ ] 总设计文档 Phase B 勾选；PRD-002B 缺口列表中 B 项标注已关闭  

## 13. 与总设计文档的关系

实现时以本文为 Phase B 详细规范；总文档 `2026-09-09-prd-002b-context-working-set-design.md` 仅保留跨阶段决策与模块图，Phase B 章节指向本文。
