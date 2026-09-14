# 24题开发评测：输入审计与离线基线

日期2026-09-14；HEAD `36e51b440b07f5738936c9350230368a3c94f6b2`。用户已确认R1/R2阶段继续，并明确选择“先整理现有24题做开发评测，正式业务验收保持待定”。本轮不调用外部模型、不修改检索实现/标签。

## 1. 冻结输入

`tests/fixtures/code_retrieval/v1/manifest.json`，SHA-256 `6473b9ae5f596fb3dc2287a747ad24f97da15165ce7fc77f7a1cd97d2adab3d0`。24个不同问题、20个有答案/4个无答案、10份文件版本、50833bytes、82个文档，两个commit各41文档。标签仍为proposed_for_user_review；现有集已经用于开发，不能称独立业务留出集。

类别：exact5、error_code5、semantic_zh4、cross_function2、semantic_en1、version3、no_answer4。有答案题金标数为：16题×1、3题×2、1题×3，因此 `P@5理论宏上限=(16+6+3)/(20×5)=0.25`；与原门槛0.80相差−0.55（−68.75%）。不增加虚假标签、不改变P@5分母。

## 2. 运行时身份核对

[input-audit.json](input-audit.json) 保留24题清单、逐题上限、旧相关ID和82条映射。运行时数据库来自已确认的R1最后成功轮。

| 指标 | 基线 | 实测 | 阈值 | 差值 | 证据 | 结果 |
|---|---|---|---|---|---|---|
| 旧/运行时文档数 | 82 | 82 | 数量及来源一致 | 0 | audit_inputs.py | 数量一致 |
| 相同document_id | 未验证 | 0/82 | 直接使用旧ID需82/82 | N/A | input-audit.json | 不能直接复用 |
| 完整来源唯一匹配 | 未验证 | 82/82 | 82/82 | N/A | 同上 | 可做版本映射 |
| 缺失/歧义映射 | 0目标 | 0/0 | 0/0 | 0 | 同上 | 满足 |
| 运行时DB写入 | 0 | 0 | 0 | 0 | 只读SQLite URI | 满足 |

旧投影为evaluation-projection-v1，运行时为utf8-slices-8192-v1，document_id按设计包含投影版本。当前82项在Scope/node/chunk/path/source_hash上完全匹配；这不证明任意长源码多切片都能自动继承相关性。不存在唯一完全一致来源时，应暂停映射并保留错误，不能只按path或者内容hash猜测。

## 3. 新跑的关键词基线

使用现有runner的keyword-only诊断模式。24题×5轮=120观测；独立问题数仍为24，质量样本中的有答案题数仍为20。新目录内SQLite全文投影，无Milvus或远程Embedding。

| 指标 | 运行前基线 | 实测 | 原门槛 | 差值/比例 | 证据 | 结果 |
|---|---|---|---|---|---|---|
| 执行观测 | 0 | 120/120 | 120 | +120 | keyword报告 | 完整 |
| 执行错误/重复 | 0目标 | 0/0 | 0/0 | 0 | 同上 | 满足 |
| P@5宏平均 | N/A：本轮首跑 | .14 | ≥.80 | 距门槛−.66 | 同上 | 未达标，且上限仅.25 |
| Recall@5宏平均 | N/A | .55 | ≥.60 | −.05 | 同上 | 未达标 |
| MRR宏平均 | N/A | .485 | ≥.80 | −.315 | 同上 | 未达标 |
| 三项逐题最小值 | N/A | 0/0/0 | 报告最小值 | N/A | 同上 | 已记录 |
| exact Hit@1 | N/A | 1.00 | 1.00 | 0 | 同上 | 满足 |
| 无答案误命中率 | N/A | .25 | 0 | +.25 | 同上 | 未达标 |
| 查询P50/P95 | N/A | 5.14/8.92ms | 同配置5轮后比较 | N/A | 同上 | 建立基线 |

5轮查询总时间分别122.98/132.81/119.90/155.48/148.49ms，中位132.81ms；runner总941.10ms。没有同配置旧基线，不宣称性能优化。不要与之前企业语料的FLASH.md成绩比较：不是同一份语料/查询/标签。

原报告保留execution_pass=true、quality_pass=false、case_pass=false；其中未测通道/标签/阈值/性能blockers不能直接当作项目全局状态。原始报告在 `/tmp/antisentinel-quality-dev.J9pmT2/keyword/report.json`，压缩归档 [keyword-report.json.gz](keyword-report.json.gz)，解压SHA-256 `fcb4debc14a71d253617138015c0be06ab647c9280113004b5a9e92879fcb368`。

## 4. 实际Ragas ID指标核对

Ragas0.4.3，对同一120观测离线执行，invalid_inputs=0、外部模型调用0。IDBasedContextPrecision均值.203571，有定义70/120、无定义50/120（检索为空）；IDBasedContextRecall=.55，有定义100/120、无定义20/120（无答案）。无定义值保留null，不能填成0或1再假装同一指标。

ID precision的分母是实际返回的唯一ID数，与固定分母5的P@5=.14不同。Recall数值一致，但只说明对这份proposed标签的覆盖。Faithfulness/Answer Relevancy本轮未评估。报告 [ragas-id-report.json.gz](ragas-id-report.json.gz) 保留全部行和分母，原始目录 `/tmp/antisentinel-quality-dev.J9pmT2/ragas-id`。

## 5. 可复现命令

```sh
cd /Users/xuewentao/.codex/worktrees/antisentinel-prd005-real-app
/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python docs/validation/prd005-quality-dev-20260914/audit_inputs.py \
  --runtime-db /Users/xuewentao/.local/share/antisentinel/cases/prd005-real-application-20260914/r1-retry2/facts.sqlite
/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python scripts/evaluate_code_retrieval.py \
  --repository /Users/xuewentao/.codex/worktrees/antisentinel-prd005-real-app \
  --manifest tests/fixtures/code_retrieval/v1/manifest.json --diagnostic \
  --output /tmp/antisentinel-quality-dev.J9pmT2/keyword --timeout 120
/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python scripts/evaluate_ragas.py \
  --repository /Users/xuewentao/.codex/worktrees/antisentinel-prd005-real-app \
  --report /tmp/antisentinel-quality-dev.J9pmT2/keyword/report.json \
  --output /tmp/antisentinel-quality-dev.J9pmT2/ragas-id
```

这些是已执行的精确命令；重跑须选择新的输出目录，不能覆盖原始结果。两条评测命令退出0、技术重试0、外部模型调用0。完整773项回归是上一阶段证据，本轮没有修改运行时代码，不重复跑全量。
