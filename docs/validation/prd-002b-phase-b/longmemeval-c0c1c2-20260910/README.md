# LongMemEval-S C0 / C1 / C2 (correct contrast)

- model: `api.deepseek.com` / `deepseek-v4-flash`
- n=10, max_tokens=32000

| arm | correct | mean_input_tokens | gold_session_cov | overflow |
|---|---:|---:|---:|---:|
| C0 naive fill | 0.2 | 14368.7 | 0.3 | 1.0 |
| C1 SoftBudget+recall | 0.7 | 7935.7 | 1.0 | 0.0 |
| C2 ContextBudget+recall | 0.6 | 7931.4 | 1.0 | 0.0 |

- full_history_estimated_mean: **164147.9**
- token_saving C2 vs C0: **0.448**
- token_saving C2 vs full estimate: **0.9517**
- pass: **True**

```json
{
  "design": {
    "C0": "naive_recent_fill raw haystack to max_tokens (no retrieval)",
    "C1": "SoftBudget + keyword recall top-k",
    "C2": "ContextBudget floors_degrade + keyword recall top-k"
  },
  "mode": "real",
  "model_host": "api.deepseek.com",
  "model_name": "deepseek-v4-flash",
  "dataset": "data/longmemeval_s_cleaned.json",
  "sha256": "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442",
  "sha256_match": true,
  "n": 10,
  "max_tokens": 32000,
  "top_k": 5,
  "C0_correct_rate": 0.2,
  "C0_token_recall_mean": 0.3083,
  "C0_provider_ok_rate": 1.0,
  "C0_mean_input_tokens": 14368.7,
  "C0_mean_total_estimated": 26703.1,
  "C0_mean_digest_estimated": 21470.9,
  "C0_overflow_rate": 1.0,
  "C0_mean_gold_session_coverage": 0.3,
  "C1_correct_rate": 0.7,
  "C1_token_recall_mean": 0.725,
  "C1_provider_ok_rate": 1.0,
  "C1_mean_input_tokens": 7935.7,
  "C1_mean_total_estimated": 13851.8,
  "C1_mean_digest_estimated": 10976.7,
  "C1_overflow_rate": 0.0,
  "C1_mean_gold_session_coverage": 1.0,
  "C2_correct_rate": 0.6,
  "C2_token_recall_mean": 0.625,
  "C2_provider_ok_rate": 1.0,
  "C2_mean_input_tokens": 7931.4,
  "C2_mean_total_estimated": 13851.8,
  "C2_mean_digest_estimated": 10976.7,
  "C2_overflow_rate": 0.0,
  "C2_mean_gold_session_coverage": 1.0,
  "mean_full_history_estimated_tokens": 164147.9,
  "full_history_overflow_rate": 1.0,
  "token_saving_C2_vs_C0": 0.448,
  "token_saving_C2_vs_full_estimate": 0.9517,
  "delta_correct_C2_minus_C0": 0.4,
  "delta_correct_C2_minus_C1": -0.1,
  "claims": {
    "used_real_model": true,
    "C2_provider_all_ok": true,
    "C2_saves_tokens_vs_C0": true,
    "C2_correct_not_worse_than_C0_by_5pct": true,
    "full_history_does_not_fit": true
  },
  "pass": true
}
```
