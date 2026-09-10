# Fusion 来源一致性与 Graph → chunk → Evidence

2026-09-10。上次中断只完成只读检查，本轮继续实现。保留此前改动，不调用外部模型。

## 修复

Fusion的document_id和source_identity现在来自同一个代表记录。相同document_id出现矛盾来源或已知范围时显式失败；重复候选缺少可选范围不能抹去已知范围。不同文档只有在完整Scope/commit/path相同、字节范围合法且直接重叠时才能合并；source_hash是chunk hash，不再要求不同重叠chunk的hash相等。无范围时不猜测重叠，避免把同文件或同内容的不同chunk误合并。

范围分组以最优代表为锚，不再做传递闭包：甲与乙重叠、乙与丙重叠，不会使与甲不重叠的丙贡献到甲的融合分数。各通道仍取最佳rank，RRF k和top-k配置不变。

Graph从已验证归档generation的chunk元数据生成source_candidates，包含Scope、node_id、单个chunk_id、path、source_hash、byte_start/end。按源码字节位置、末端、chunk ID稳定排序，每节点最多5个；超出标truncated/incomplete。没有可读chunk的节点仍可作为结构信息返回，但不会被search伪造成可读Graph命中。rejected_chunks/unreadable_nodes与rejected_count（边）分开统计。

graph模式把候选展开为单chunk命中，最多30候选，再按既有规则与seed交错取top-k；同节点不同chunk不再被node级去重吞掉。Graph document_id为graph:node:chunk，用于该检索路径追踪，不冒充SQLite检索投影文档ID。运行Case模型直接传递服务返回identity，不再手工取chunk_ids[0]。

Evidence读取前额外核验声明的node/path/byte范围与归档chunk一致；不一致时在存证前拒绝。新Evidence记录node_id，checkpoint恢复补充path/node校验；旧记录缺node仍由归档信息兼容恢复，但generation/commit/hash原有硬校验不放松。

## 测试

新增tests/test_retrieval_source_consistency.py共16项，覆盖代表ID/source一致、无范围不误合并、Scope/范围非法、重复ID冲突、重叠链不扩散、已知范围不被重复项抹去、Graph直接读取、多chunk排序和完整读取、节点/路径/范围不符不写Evidence、候选上限与非法chunk路径拒绝。

先观察代表合并用例失败，再修复；追加重叠链用例先失败（原来只剩甲），再改代表锚点分组。针对回归17/17、46/46、47/47、49/49逐步通过；最后重复范围边界targeted37 passed、0 failed、2.29s。更新两个Graph模拟器以提供真实接口要求的source_candidates，而非空chunk邻居。

此前临时Ragas环境路径已不存在；当前Python没有ragas包。全量使用 `/Users/xuewentao/miniconda3/bin/python -m pytest -q`，两项真实Ragas ID指标测试按其原有skipif跳过，其他测试执行；不把跳过计为通过。未安装或升级项目依赖，也未重跑付费Ragas/Embedding。

## 本地累计Case

本轮产物保存到持久目录 `/Users/xuewentao/.local/share/antisentinel/cases/source-consistency-1jfy1qnd`，避免依赖临时环境目录。

```sh
python scripts/run_embedding_worker_case.py --output <新目录>
python scripts/run_lexical_publication_case.py --output <新目录>
python scripts/run_code_retrieval_case.py --case graph --output <新目录> --timeout 120
```

六个命令分别由父进程120秒上限保护，均退出0。worker17检查/7484.53ms，publication9检查/73.04ms，keyword4检查/47.40ms，vector6检查/883.97ms，hybrid8检查/1463.44ms，graph15检查/112.02ms；均case_pass=true。Graph真实Runtime Case直接使用具体chunk完成Evidence及checkpoint，不再依赖手工挑chunk；多chunk完整读取由真实SQLite/归档blob测试覆盖。

| 关键指标 | 旧基线 | 本次 | 阈值 | 差值 | 证据 | 结果 |
|---|---:|---:|---:|---:|---|---|
| Worker恢复检查 | 17 | 17 | 17 | 0 | worker/report.json | 通过 |
| 发布检查 | 9 | 9 | 9 | 0 | publication/report.json | 通过 |
| hybrid完整性检查 | 8 | 8 | 8 | 0 | hybrid/report.json | 通过 |
| Graph完整性检查 | 15 | 15 | 15 | 0 | graph/report.json | 通过 |
| Case失败 | 0 | 0 | 0 | 0 | 六份report | 通过 |
| 外部模型调用 | 0 | 0 | 0 | 0 | 本地Case路径 | 通过 |

## 尚未完成

SQLite/Milvus检索文档仍未全量持久化byte ranges，因此其未知范围候选只按document_id去重，本轮不宣称已验证真实大语料范围去重效果。Graph每节点前5个chunk是有界候选策略，不是长节点完整遍历/分页。hybrid+graph四路评测、常驻调度/Runtime生产集成、Standalone、性能和质量验收仍未完成。

本轮改动10个实现/脚本/测试文件（Fusion、Graph、engine、Evidence、source_context、tools、Graph Case脚本及3个测试文件），另更新验证文档/开发日志。原检索质量报告不因正确性修复自动升级为通过。

最终全量：`/Users/xuewentao/miniconda3/bin/python -m pytest -q` 为638 passed、2 skipped、0 failed、7 warnings、54.49s。总收集624→640新增16项；2项Ragas依赖测试因原隔离环境已不存在而跳过，不冒充完整依赖环境通过。最后再次执行hybrid/graph CLI Case，结果记录于同一持久目录的hybrid-final和graph-final。compileall、git diff --check通过。本地正确性Case通过，当前环境执行的回归通过；Ragas可选测试完整复测、用户review及整体生产验收未通过。
