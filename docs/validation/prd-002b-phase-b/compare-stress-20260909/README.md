# PRD-002B Phase B stress compare

- pass: **True**
- groups: B=ContextBudget, A=SoftBudget baseline, A_rep=negative control
- Fake model / pack-level; Real DeepSeek task quality is separate

## Claims
```json
{
  "token_not_blown_on_B": true,
  "short_term_memory_protected_on_B": true,
  "less_silent_source_drop_on_B": true,
  "skill_late_degrade_or_core_kept_on_B": true,
  "tools_core_floor_on_B": true,
  "bill_observability_on_B": true,
  "baseline_stable_A_vs_A_rep": true,
  "ai_task_quality_real_llm": null,
  "ai_task_quality_note": "Fake stress cannot score diagnosis quality; needs annotated Real DeepSeek set",
  "extreme_B_within_A_may_blow": true
}
```
