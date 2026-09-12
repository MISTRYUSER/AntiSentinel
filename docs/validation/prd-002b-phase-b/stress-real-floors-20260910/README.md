# Phase B stress — Real DeepSeek

- pass: **True**
- host: `api.deepseek.com` model: `deepseek-v4-flash`

## Claims
```json
{
  "used_real_deepseek": true,
  "model_host": "api.deepseek.com",
  "model_name": "deepseek-v4-flash",
  "B_all_provider_ok": true,
  "B_all_within_budget": true,
  "B_no_hard_limit_exceed": true,
  "B_no_silent_source_drop": true,
  "B_no_secret_leak": true,
  "B_probe_majority_pass": true,
  "B_useful_in_budget_ge_A_when_A_exceeds": true,
  "A_may_fail_provider_or_budget": false,
  "metrics_are_budget_aligned": true
}
```

## Table
```json
[
  {
    "scenario": "S2_source_10files",
    "A_provider_ok": true,
    "B_provider_ok": true,
    "A_probe_pass": true,
    "B_probe_pass": true,
    "A_within_budget": true,
    "B_within_budget": true,
    "A_hard_limit_exceeded": false,
    "B_hard_limit_exceeded": false,
    "A_budget_fill": 0.3969,
    "B_budget_fill": 0.4633,
    "A_useful_tokens": 10206,
    "B_useful_tokens": 12051,
    "A_useful_in_budget": 10206,
    "B_useful_in_budget": 12051,
    "A_silent_discard": 8,
    "B_silent_discard": 0,
    "A_input_tokens": 5964,
    "B_input_tokens": 5927,
    "A_error": null,
    "B_error": null
  },
  {
    "scenario": "S4_tools_30",
    "A_provider_ok": true,
    "B_provider_ok": true,
    "A_probe_pass": true,
    "B_probe_pass": true,
    "A_within_budget": true,
    "B_within_budget": true,
    "A_hard_limit_exceeded": false,
    "B_hard_limit_exceeded": false,
    "A_budget_fill": 0.1796,
    "B_budget_fill": 0.1796,
    "A_useful_tokens": 4401,
    "B_useful_tokens": 4401,
    "A_useful_in_budget": 4401,
    "B_useful_in_budget": 4401,
    "A_silent_discard": 0,
    "B_silent_discard": 0,
    "A_input_tokens": 3011,
    "B_input_tokens": 3016,
    "A_error": null,
    "B_error": null
  },
  {
    "scenario": "S5_extreme_fullstack",
    "A_provider_ok": true,
    "B_provider_ok": true,
    "A_probe_pass": true,
    "B_probe_pass": true,
    "A_within_budget": true,
    "B_within_budget": true,
    "A_hard_limit_exceeded": false,
    "B_hard_limit_exceeded": false,
    "A_budget_fill": 0.7124,
    "B_budget_fill": 0.7788,
    "A_useful_tokens": 18193,
    "B_useful_tokens": 20038,
    "A_useful_in_budget": 18193,
    "B_useful_in_budget": 20038,
    "A_silent_discard": 6,
    "B_silent_discard": 0,
    "A_input_tokens": 11742,
    "B_input_tokens": 11700,
    "A_error": null,
    "B_error": null
  }
]
```
