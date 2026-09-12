# PRD-002B Phase B — validation cases (updated)

## Strategy answers (short)

| Question | Answer |
|----------|--------|
| Strategy reasonable? | Yes for **budget governance + observability**; incomplete for global cross-block second-pass eviction and Real task-quality proof |
| Source truncated/dropped → can still edit code? | **Not from missing body alone**. B keeps window/`truncated` so model knows gap and can **re-read via tools**; A silent-break was worse |
| What is Memory? | **Long-term fact view**: session digest + evidence_refs (not short-term loop continuity) |
| Short-term continuity? | **Working Set** `recent_turns` + `older_digest` |

## Groups

| Group | Meaning |
|-------|---------|
| **B** | Phase B `ContextBudget` (experimental) |
| **A** | Phase A SoftBudget replay (control) |
| **A_rep** | SoftBudget again (negative control / stability) |

## Pressure scenarios

| ID | Pressure | KPI |
|----|----------|-----|
| S1 | 20-turn Working Set | `recent_turn_completion_rate` (+ tight 2048) |
| S2 | 10 large source files | silent discard ↓; truncated/dropped observable |
| S3 | 5-skill catalog | late degrade + keep tool history marker |
| S4 | 30 tools | core tools intact |
| S5 | full stack extreme | B `within_budget`; A may blow; payload ratio |

## Commands

```bash
PYTHONPATH=src python -m pytest -q \
  tests/test_context_budget.py tests/test_context_pack_budget.py \
  tests/test_prd002b_phase_b_stress.py tests/test_runtime_loop.py \
  tests/test_source_window.py

PYTHONPATH=src python scripts/compare_prd002b_phase_b_stress.py \
  --out docs/validation/prd-002b-phase-b/compare-stress-20260909

# optional Real
PYTHONPATH=src python scripts/smoke_prd002b_phase_b_deepseek.py \
  --out docs/validation/prd-002b-phase-b/real-smoke-20260909

PYTHONPATH=src python scripts/calibrate_context_estimate.py --mode real --samples 3 \
  --out docs/validation/prd-002b-phase-b/calibration-20260909
```

## Budget-aligned Real compare

Primary metrics (not old payload_ratio):

| Metric | Meaning |
|--------|---------|
| `hard_limit_exceeded` | total > max（A 常 true，B 应 false） |
| `budget_fill` | 窗口填充率；超限记 1.0 并标 exceed |
| `useful_tokens` | 任务通道绝对 token |
| `useful_in_budget` | 仅预算内可计的有效 token（超限按比例折算） |

```bash
PYTHONPATH=src python scripts/compare_prd002b_phase_b_stress_real.py \
  --out docs/validation/prd-002b-phase-b/compare-stress-real-budget-$(date +%Y%m%d)

PYTHONPATH=src python scripts/calibrate_context_estimate.py --real --samples 2 \
  --output docs/validation/prd-002b-phase-b/calibration-wire-$(date +%Y%m%d)
```

## Strategy update (2026-09-09 evening)

- Default `max_context_tokens`: **32000**
- Source share: **0.22** (was 0.15)
- Pack **refill**: after block pack, spend leftover budget on source→skill toward `target_fill_ratio=0.75`
- Eval dataset quality scoring: **later** (not in this stress harness)

```bash
PYTHONPATH=src python scripts/compare_prd002b_phase_b_stress_real.py \
  --out docs/validation/prd-002b-phase-b/compare-stress-real-refill-32000-$(date +%Y%m%d)
```
