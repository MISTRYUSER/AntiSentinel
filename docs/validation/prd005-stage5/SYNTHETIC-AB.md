# 新构造问题 A/B（2026-09-10）

结论：**发现B无法保留整类概览答案的反例，默认继续用A。** 未修改策略去迎合本次结果。

用户要求助手自行设计问题后，生成并冻结新24题。这里是synthetic challenge，非真实用户日志或独立业务留出集；held_out=false。作者知道此前实验结果和策略，因此不宣称独立泛化证明。此前获准使用的企业Go语料临时目录已不存在，本轮没有替换企业仓库或外发源码，使用本地既有Python语料和诊断向量。

## 数据与协议

问题与标签：[synthetic-v2 manifest](../../../tests/fixtures/code_retrieval/synthetic-v2/manifest.json)，冻结记录同目录freeze.json。20可回答、4无答案；含5数据结构、4整类概览、10辅助方法、1嵌套函数问题。与旧24题规范化文本交集0，gold(path,symbol)交集0；不将此等同语义独立。所有标签来自运行前源码阅读，仍待review。

manifest SHA-256 `5c280c10a43b882c2623c8edd0e7a46520bbceac7704ef1793ba230d4ce8bc03`。语料保持82文档/50833bytes。策略hash与上一轮相同，测量后再次核验；默认hybrid未改。

A=hybrid，B=hybrid_graph/replace_container。共用一个索引和同一查询向量，top-k=5；48预热后正式120对/240调用，AB/BA各60对，每题逐轮换序。每条题聚合5轮再按题bootstrap（seed42、20000次）；仅20条可回答题和4条无答案题是统计单位。正式运行1次，重试0。

## 结果

| 指标 | A | B | B−A / 95%差区间 |
|---|---:|---:|---|
| P@5 | 0.13 | 0.12 | -0.01 / [-0.03,0] |
| Recall@5 | 0.65 | 0.60 | -0.05 / [-0.15,0] |
| MRR | 0.435 | 0.425 | -0.01 / [-0.03,0] |
| 无答案误命中率 | 1 | 1 | 0 / [0,0] |
| P95检索延迟 | 28.77ms | 33.28ms | +4.51ms，约15.66% |

20条可回答问题中0条提高、1条下降、19条不变。P@5/Recall相对下降7.69%，MRR相对下降2.30%；区间包含0，不宣称B在所有场景统计显著更差。按预先固定的门槛，Recall优效、P@5不劣、MRR不劣均未通过；误命中差与P95保护门槛通过，统计门槛2/5通过。

具体退化为s06：「请找整个CodeMapQuery类的定义，以概览快照、符号、邻居和源码查询接口。」A第5条为CodeMapQuery类本身；B将其替换为CodeMapQuery.get_snapshot。四个先前结果相同，相关类定义从top-5消失。这是容器也可能是用户所需答案的实际反例，不能假设所有类都应该被细化为方法。

相同240观察另行运行Ragas ID指标，不重新检索。无LLM或外部模型调用，答案类指标未评估。测试集运行后已曝光，后续不能继续称作未见过的问题。

## 证据与状态

产物根 `/Users/xuewentao/.local/share/antisentinel/cases/synthetic-ab-53o054d8`：command.json/run.log与ab/内计划、manifest、向量、预热、逐查询结果、report、audit、ragas、SQLite和Milvus数据。有效计划hash `8285fb72511dba8b77505c7d1d9b1c1d740f9d98ac21e4dd8c77accc2653eef3`。

执行检查6/6通过，输入错误/重复候选0，两库82/82，计划/manifest/策略hash不变，实际顺序匹配冻结schedule。业务观察7983.35ms、存储读回观察8005.24ms、差21.89ms，总8448.43ms。无后台worker，异常字段null，不冒充测得0。

复现命令（必须新输出目录）：

```sh
/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python scripts/run_retrieval_ab.py --repository /Users/xuewentao/agentCode/antisentinel --manifest tests/fixtures/code_retrieval/synthetic-v2/manifest.json --output <new-directory>
```

新增CLI manifest/repository入口，--help退出0。针对性`python -m pytest tests/test_retrieval_ab.py -q`：5 passed、13.63s；本轮未改检索代码或默认配置，没有重复跑全量套件冒充新增证据。preflight输入错误0，24问题唯一，标签与源码hash可解析。execution_pass=true、statistical_gate_pass=false、promote_default=false、quality_pass=false。仍需真实业务问题、Flash/Standalone和答案验收。
