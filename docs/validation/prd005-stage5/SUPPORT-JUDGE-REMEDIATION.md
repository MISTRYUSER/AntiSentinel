# 支持性判别专项整改

2026-09-08。整改对象是评测协议和执行路径，未启用生产检索过滤。

## 根因与处理

1. q15引用中模型插入了`...`，不是空白差异，也不是来源截断。原精确引用校验正确拒绝。新协议由模型只选择document_id及披露片段内的起止行，程序从原文本提取引用；禁止模型自写quote。行号不是仓库文件绝对行号，原始source identity随请求保留。
2. q01返回额外且正确的query_id。允许这个明确已知的元数据字段，但必须匹配当前查询；其他未知字段仍拒绝。保存响应离线重验，不额外调用模型。
3. q13遗漏必填status。不能从reason猜填状态；JSON mode只保证语法，改为DeepSeek `/beta/chat/completions` 的strict function schema，并强制submit_support工具返回。这个工具只承载判别结果，不执行外部动作。字段结构和枚举由服务约束，本地继续验证引用、状态与上下文。官方依据：[Tool Calls strict模式](https://api-docs.deepseek.com/guides/tool_calls/)、[JSON Output](https://api-docs.deepseek.com/guides/json_mode/)。
4. h06假设“数值转换失败使用默认值”，源码实际只对空输入替换默认值，ParseInt/ParseFloat失败返回nil。相关代码可以纠正错误前提，不能机械当成无答案过滤。增加contradicted状态，必须有明确反驳行为的引用；缺少某功能的提及不算反证。supported/contradicted保留候选，not_supported才过滤；insufficient_evidence保留原候选并单列unknown，不算正确拒答。

语义判断仍由模型做，行号可校验不等于语义一定正确。所有原标签、原失败记录和旧分数保持不变，不通过重新标注刷分。当前48条均已观察，只作为诊断，后续泛化需新盲测。

## 实现与测试

- evaluation/support_judge.py增加行号协议、服务器原文提取、严格schema请求与响应解析、query_id绑定、错误前提状态。
- prepare_code_support_judge.py默认准备ranges格式，保留quotes旧协议复现选项。
- run_code_support_judge.py提供可复现的strict服务执行入口，固定已授权DeepSeek主机、1–48独立查询、600秒预算、逐条落盘、失败即停、自动重试0、不设置max_tokens。服务端仍有自身限制，单请求30秒；总预算为请求前/后检查，不宣称网络读超时是硬wall-clock截止。调用方真实Case另使用630秒父进程终止上限。
- tests/test_code_support_judge.py新增10项用例：7项range相关、1项元数据绑定、1项strict结构、1项反证引用。新增字段红灯与缺失函数红灯已观测；最终targeted命令 `python -m pytest tests/test_code_support_judge.py tests/test_code_abstention.py tests/test_code_retrieval_evaluation.py -q` 为32 passed、0 failed、1.49s。compileall、git diff --check退出0。

## 运行轨迹（不隐藏失败）

私有产物根目录 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-baidu-source-poitl0bv`：

- v3：q15修复通过；q01额外query_id触发旧schema，2请求1有效、23858 tokens。原始输出后经绑定校验重验有效，不修改原失败report。
- v4：离线复用v3两条，新增12请求，其中11有效；q13漏status失败。新增96671 tokens。
- v5：strict协议优先重跑q13，再完成其余，共35新请求全有效，与重验13条组成48诊断结果，新增241881 tokens、69530.21ms。这个混合传输版本不是最终同协议结果。原口径下h06被拒导致Recall下降，保留quality-analysis.json。
- v6：新语义协议与strict传输统一重跑48条，所有输入/请求/响应/用量/协议hash保存于support-judge-run-v6。最终结果另记下方与开发日志。

可复现运行（需由进程环境提供已批准密钥，不打印密钥）：

```sh
python scripts/run_code_support_judge.py \
  --packets <私有根目录>/support-judge-development-v1/packets.json \
            <私有根目录>/support-judge-heldout-v1/packets.json \
  --output <不存在的新目录> --timeout 600
```

模型请求名为deepseek-chat，实际响应版本逐条记录。Ragas答案指标不在此Case范围内，模型判别reason不能冒充真实用户答案。

## 最终v6结果

统一strict传输与最终语义协议48/48有效，错误0，58条引用全部通过本地提取校验；usage48/48，prompt360062/completion11292/total371354tokens，费用N/A。总99272.50ms，平均每查询2062.80ms。所有实际model字段为deepseek-v4-flash，prompt SHA256为05773c1f0a8f2ae2b11f0818dada1a7bce17321cbc2c44098c4330ae6e59ebdd。客户端token上限未设置，自动重试0。report.json execution_pass=true，仅表示本Case执行与协议通过。

| 指标 | 基线 | v6 | 变化 | 阈值 | 证据 | 判定 |
|---|---:|---:|---:|---|---|---|
| 诊断组A无答案误命中 | 4/4 | 0/4 | -4 / -100% | 下降 | quality-analysis.json | 改善 |
| 诊断组B无答案误命中 | 12/12 | 2/12 | -10 / -83.33% | 下降 | quality-analysis.json | 改善但未清零 |
| A Recall / MRR | .925 / .816667 | .925 / .816667 | 0 / 0 | 相对退化≤5% | quality-analysis.json | 通过 |
| B Recall / MRR | .916667 / .833333 | .916667 / .833333 | 0 / 0 | 相对退化≤5% | quality-analysis.json | 通过 |
| 有答案误拒绝 | 0/32 | 0/32 | 0 | ≤5% | quality-analysis.json | 通过 |
| 格式/引用错误 | v4为1 | v6为0 | -1 | 0 | responses.jsonl | 通过 |

合计误命中16/16→2/16（100%→12.5%）；B覆盖率22/24，两个insufficient_evidence分别为一个有答案和一个无答案，不计成功拒答。剩余误命中为h16（contradicted，保留）、h21（insufficient_evidence，回退原候选）。h06最终返回supported并保留候选，不能声称本次一定通过contradicted标签修复了它；新版语义协议下查询结果恢复是观测事实，需后续盲测验证稳定性。

检索相关性口径与“请求中的行为是否存在”仍有差别，尤其h16同样可能提供解释型反证；本次不改标签，按原no-answer标注计误命中。原P@5上限问题、用户标签review、跨仓库泛化、未知比例、Ragas真实答案指标都未完成。新增约2.06秒判别延迟远高于此前毫秒级检索，未通过端到端性能护栏；不能称为整体生产优化通过。

本轮专项总请求97（v3 2 + v4 12 + v5 35 + v6 48），共733764 tokens；包含中途失败及新协议完整复测，未隐藏额外开销。各轮独立保留报告，不把它们混为一次48条成功请求。最终产物requests.jsonl、responses.jsonl、report.json、quality-analysis.json共4文件。

最终累计回归命令 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-ragas-clean-wxav1le_/bin/python -m pytest -q`：581 passed、0 failed、6 warnings、52.17s；相对最近全量562增加19（含前两轮离线实验新增9项，本轮新增10项）。判别运行结束后才执行全量回归，未与最终API延迟采样并发。状态为本次协议真实运行通过、回归通过；用户review与生产质量/性能验收尚未通过。
