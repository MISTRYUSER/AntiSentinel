# BFCL 派生子集报告

数据来自 `bfcl-eval==2026.3.23`。冻结子集文件：`bfcl-subset.json`；选择规则是每个官方类别按源文件顺序取前5条，不按模型成绩选择。映射把 BFCL 的 `dict/float/list/tuple/bool` schema 类型转换为 JSON Schema 的 `object/number/array/array/boolean`，并把不符合 DeepSeek函数命名规则的名称映射为可逆 provider alias。因此这是 AntiSentinel Task/ToolCall 的派生评测，不能与官方 BFCL leaderboard 直接比较。

## 固定范围

| 类别 | 冻结数 | 评分方式 |
|---|---:|---|
| simple_python | 5 | 函数名和参数落入官方 ground truth 允许集合 |
| irrelevance | 5 | 不应产生任何工具调用 |
| multi_turn_base | 5 | 当前有状态文件工具环境未适配，标记 unsupported |

共15条：10条可评分、5条未支持。每条保留官方 case ID、源行 hash、原问题、函数定义和 ground truth。

## DeepSeek 实际运行

v1 正式产物：`docs/validation/bfcl-deepseek-report/`。v2 使用通用“仅在工具直接满足请求时调用；候选工具均无关时不调用”的系统规则，产物：`docs/validation/bfcl-deepseek-report-v2/`。

| 指标 | 实际值 |
|---|---:|
| 记录数 | 15 |
| 可评分 | 10 |
| v1通过 / 准确率 | 7 / 70.00% |
| v2通过 / 准确率 | 8 / 80.00% |
| 有状态多轮未支持 | 5 |
| v1 / v2 输入 token | 3566 / 3896 |
| v1 / v2 输出 token | 1109 / 1113 |
| v1 / v2 模型耗时 | 10.898s / 13.505s |
| 基础设施错误 | 0 |

simple_python 与 irrelevance 的正式结果均包含在 `results.jsonl`。v2 仍有2条无关工具误调用，说明模型在不应调用工具的场景仍会调用；不能将其归为协议转换失败。v2比v1多通过1条，提升10个百分点，代价是输入增加330 token（9.25%）和耗时增加2.607s（23.92%）。

同一冻结集的另一轮运行 `/tmp/antisentinel-bfcl-r6` 得到9/10（90.00%），表明当前模型输出存在波动。报告以仓库内正式产物的7/10为准，不挑选较高分数。

## 未完成

`multi_turn_base` 需要可回滚的有状态文件工具环境，当前只读诊断工具无法执行。实现该环境后，必须重新跑全部15条，并将unsupported改为可评分的通过/失败结果。
