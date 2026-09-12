# PRD-002B Phase A 测评 Case 目录

## 安全

- `.env.local` 由 `.gitignore` 的 `.env.*` 规则忽略，**不会**被 `git push` 到 GitHub。
- 可用 `git check-ignore -v .env.local` 自检。
- 测评报告禁止写入 API Key / Evidence 原文 / tool raw payload。

## 测评流程（固定）

```text
1. 基线（Baseline）
   → 跑本目录冻结 Case + 回归门禁，生成 report.json
2. 改代码（本轮已是 Phase A Working Set）
3. 复跑同一套 Case
   → 对比基线：新增通过项 / 回归失败项
4. 每一步写入 steps.jsonl（case_id, ok, detail, duration_ms）
5. 全链路 Case 与 pytest 合起来看「合起来怎么样」
```

Phase A 首次跑通的结果即作为 **Phase A 验收基线**；Phase B/C 改动必须复跑本套，不得静默降级。

## Case 一览（每一个长什么样）

| ID | 类型 | 输入长什么样 | 期望 | 失败长什么样 |
|----|------|--------------|------|--------------|
| `WS-01` | 引擎 Fake 多轮 | 3 次不同工具计划 + 第 4 轮 final；工具 summary 分别为 E42 / cpu / span | 第 4 次 `ModelRequest` 仍含 turn1 的 `error code E42 from turn1`；无 `SECRET-` | 只剩最后一轮 summary，或含 raw |
| `WS-02` | WorkingSet 单测 | K=2，连续 append 4 轮 | recent 仅 3–4；older_digest 含 turn=1 摘要与 evidence_id | digest 空或 early turn 从一切历史消失 |
| `WS-02b` | WorkingSet 压力 | append 50 / 100 轮，`max_older_digest_tokens=512` | `len(recent)==K`；digest token ≤ cap；<500ms | recent 膨胀或 digest 无界 / 超时 |
| `WS-03` | 脱敏 | task_results / tool result 带 `raw` / SECRET | messages / working_set 无 SECRET、无 vault 原文 | 上下文出现 raw |
| `WS-04` | 去空洞 | outcome/summary 为空或 `tool calls completed` | 生成含 tool_name+status 的非空洞文本 | 历史里只剩空洞占位 |
| `WS-05` | Checkpoint | 工具成功后中断 | snapshot.working_set 含 summary，无 secret | 无 working_set 或含 raw |
| `WS-06` | HTTP 全链路 | POST incident → session(fake) 多轮脚本模型 | session completed；第 4 次请求含 turn1 summary；API JSON 无 SECRET | 链路失败或上下文断裂 |
| `WS-07` | 生产/Bench 同 pack | `ContextBuilder` vs `make_benchmark_context_builder` | 同一 `build` 实现；Bench 无独立 `_result_history` | 双轨累积语义 |
| `REG-01` | pytest 门禁 | 固定测试文件列表 | 全部 passed | 任一 failed |
| `REAL-01` | 可选真实 DeepSeek | `.env.local` 已配 real + deepseek-v4-flash；一条短诊断 | status=completed 或明确失败码；报告只记 status/turns/tokens，不记 Key | 缺 Key 则 **skip**（不计失败） |

### WS-01 输入样例（概念）

```json
[
  {"tasks":[{"task_id":"t1","objective":"logs","tool_calls":[{"tool_name":"read_logs","arguments":{"q":"1"}}]}]},
  {"tasks":[{"task_id":"t2","objective":"metrics","tool_calls":[{"tool_name":"read_metrics","arguments":{"q":"2"}}]}]},
  {"tasks":[{"task_id":"t3","objective":"traces","tool_calls":[{"tool_name":"read_traces","arguments":{"q":"3"}}]}]},
  {"final":{"summary":"done","diagnosis":"E42 root cause","confidence":0.9,"evidence_refs":[]}}
]
```

工具 handler 返回：`result_summary="error code E42 from turn1"`，`result={"raw":"SECRET-..."}`（不得进模型）。

### WS-06 全链路形态

```text
POST /api/incidents {title, summary, source}
POST /api/incidents/{id}/sessions {participant_ids, model_mode:fake}
→ RuntimeEngine 多轮
→ GET /api/sessions/{id} status=completed
旁路检查：FakeProvider.requests[3].messages 含 turn1 summary
```

## 如何跑

```bash
# 建议先 source 本地配置（real 可选）；不要把 Key 打进命令行历史以外的日志
set -a && source .env.local && set +a

# 输出目录必须不存在
OUT=docs/validation/prd-002b-phase-a/baseline-$(date +%Y%m%d-%H%M%S)
PYTHONPATH=src python scripts/validate_prd002b_context.py --output "$OUT"

# 含真实 DeepSeek（需 ANTISENTINEL_MODEL_MODE=real 且 Key 已配置）
PYTHONPATH=src python scripts/validate_prd002b_context.py --output "$OUT-real" --real
```

产物：

- `report.json` — 总表、通过数、逐步结果
- `steps.jsonl` — 每一步一行
- `pytest.txt` — 门禁原始输出
- `README.md` — 人工可读摘要（脚本生成）

## 与「修改提升」的对比法

| 指标 | 基线含义 | 提升判定 |
|------|----------|----------|
| `WS-01`..`WS-07` | Phase A 必须全过 | 后续改动不得变红 |
| `REG-01` failed 数 | 0 | 不得增加（与本次无关的既有红另记） |
| `REAL-01` | skip 或 completed | 有 Key 时应 completed；失败要记 error.code |
| turn1 summary 可见 | true | Phase B 预算裁剪后：预算内仍可见或进 older_digest |

**OFF/ON 对照（证明改动有效，不是只验收）：**

```bash
PYTHONPATH=src python3 scripts/compare_prd002b_phase_a.py \
  --output docs/validation/prd-002b-phase-a/compare-off-on-$(date +%Y%m%d-%H%M%S)
```

- OFF：`RuntimeConfig(enable_working_set=False)` 模拟改造前「只留上一轮 + 空洞 summary」
- ON：默认 Working Set
- 产物：`compare.json` + `README.md` 指标表

历史对照（改造前行为，不自动跑）：生产路径「只留上一轮」会使 `WS-01` / `WS-06` 失败——这是 Phase A 要修掉的缺口。
