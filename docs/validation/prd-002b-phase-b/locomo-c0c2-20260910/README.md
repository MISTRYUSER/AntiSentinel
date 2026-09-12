# LoCoMo10 C0 vs C2 (cross-validation)

- model: `api.deepseek.com` / `deepseek-v4-flash`
- n=20, conversations=10
- license: CC BY-NC (internal research)

| arm | correct | mean_input_tokens | gold_session_cov | overflow |
|---|---:|---:|---:|---:|
| C0 naive fill | 0.1 | 17213.35 | 0.37 | 0.8 |
| C2 ContextBudget+recall | 0.25 | 6254.05 | 0.6183 | 0.0 |

- full_history_estimated_mean: **27140.7**
- token_saving C2 vs C0: **0.6367**
- token_saving C2 vs full estimate: **0.7696**
- pass: **True**

```json
{
  "design": {
    "C0": "naive_recent_fill raw dialogue sessions to max_tokens",
    "C2": "ContextBudget + keyword recall top-k",
    "dataset": "locomo10.json (official LoCoMo10 shape)",
    "license_note": "LoCoMo is CC BY-NC — research/internal only"
  },
  "mode": "real",
  "model_host": "api.deepseek.com",
  "model_name": "deepseek-v4-flash",
  "dataset": "data/locomo10.json",
  "n": 20,
  "max_tokens": 32000,
  "top_k": 5,
  "conversations_covered": 10,
  "C0_correct_rate": 0.1,
  "C0_token_recall_mean": 0.2663,
  "C0_provider_ok_rate": 1.0,
  "C0_mean_input_tokens": 17213.35,
  "C0_mean_total_estimated": 26995.65,
  "C0_overflow_rate": 0.8,
  "C0_mean_gold_session_coverage": 0.37,
  "C2_correct_rate": 0.25,
  "C2_token_recall_mean": 0.3155,
  "C2_provider_ok_rate": 1.0,
  "C2_mean_input_tokens": 6254.05,
  "C2_mean_total_estimated": 9569.5,
  "C2_overflow_rate": 0.0,
  "C2_mean_gold_session_coverage": 0.6183,
  "mean_full_history_estimated_tokens": 27140.7,
  "full_history_overflow_rate": 0.1,
  "token_saving_C2_vs_C0": 0.6367,
  "token_saving_C2_vs_full_estimate": 0.7696,
  "delta_correct_C2_minus_C0": 0.15,
  "claims": {
    "used_real_model": true,
    "C2_provider_all_ok": true,
    "C2_saves_tokens_vs_C0": true,
    "C2_correct_not_worse_than_C0_by_5pct": true
  },
  "pass": true
}
```
