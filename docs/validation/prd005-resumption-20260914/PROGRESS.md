# PRD-005 当前进度核对（2026-09-14）

结论：5.1–5.5 都有代码和局部验证记录；当前是补齐真实入口、业务质量与生产边界的验收阶段，不是从零开发。原 PRD 的“retrieval 骨架”属于历史基线，不代表当前代码。不能将 5 个阶段都有文件换算为完成率 100%。

## 本轮证据与基线

HEAD `16d83bf35209918005a46a4cfd54ba599763decb`。工作区已有其他文档改动，本轮保留。项目 learning store 不存在，cold start；历史依据来自 PRD/开发记录，不称作 learning recall。

```sh
git rev-parse HEAD
rg --files src -g '*.py' | wc -l
rg --files src/antisentinel/retrieval -g '*.py' | wc -l
rg --files tests -g 'test_*.py' | wc -l
/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python -m pytest -q
```

| 指标 | 基线 | 当前值 | 阈值 | 差值/比例 | 证据命令/产物 | 结果 |
|---|---:|---:|---|---|---|---|
| src Python 文件 | 本轮开始204 | 204 | 本轮源码改动0 | 0/0% | rg计数；baseline.json | 一致 |
| retrieval Python 文件 | 21 | 21 | 不重写架构 | 0/0% | rg计数 | 一致 |
| 测试文件 | 127 | 127 | 本轮新增0 | 0/0% | rg计数 | 一致 |
| 测试通过数 | 历史728 | 728 | 728/728 | 0/0% | pytest.txt | 100% |
| 测试失败/跳过 | 0/0 | 0/0 | 0/0 | 0 | pytest.txt | 满足 |
| 警告 | 7 | 7 | 无新增 | 0/0% | pytest.txt | 无新增 |
| 单轮pytest耗时 | 历史48.65s | 51.82s | 同配置5轮中位≤1.05倍 | +3.17s/+6.52%（仅描述） | pytest.txt | 样本不足，性能待验证 |
| 外部完整Case | 0 | 0 | 下一阶段≥1 | 0 | 本轮未启动 | 待验证 |
| 外部模型调用 | 0 | 0 | 文档阶段0 | 0 | 本轮操作记录 | 未调用 |

[baseline.json](baseline.json)、[pytest-run.json](pytest-run.json)、[pytest.txt](pytest.txt) 保留原始数字。命令墙钟54.26s，技术重试0。无真实Case，后台异常/吞吐/生产P95本轮N/A，不填0。

## 原阶段逐项核对

| PRD阶段 | 当前代码位置 | 历史证据 | 当前缺口 |
|---|---|---|---|
| 5.1 文本投影/FTS | retrieval/keyword.py、sqlite_store.py、slicing.py | SOURCE-SLICING：18父块→38文档，2种存储形态各15检查 | 其他编码/大语料/完整图分页未验 |
| 5.2 Embedding/向量 | embedding_jobs.py、embedding_worker.py、milvus_adapter.py | STANDALONE-VERIFIED：服务重启与Strong读回；切片38向量/38任务 | 真实Flash常驻完整入口、权限/TLS、容量仍待验 |
| 5.3 hybrid | engine.py、fusion.py | FLASH：Recall .50→.925，P@5 .22、MRR .81667 | 开发集非业务质量通过；无答案检索FPR100% |
| 5.4 图/Evidence | graph.py、evidence.py、runtime.py、coordinator.py | 生命周期替身、Evidence恢复；图配对实验未过主门槛 | 默认hybrid不改；图优化pending；正常真实Session待验 |
| 5.5 累计评测 | evaluation/、评测与Case脚本 | 10本地Case115检查+Standalone15检查；本轮728测试通过 | 质量和生产总验收未通过，不标Done |

历史数字引用 [OPEN-ITEMS](../prd005-stage5/OPEN-ITEMS.md)、[FLASH](../prd005-stage5/FLASH.md)、[源码切片](../prd005-stage5/SOURCE-SLICING.md)、[答案评测](../prd005-stage5/RAG-ANSWERS.md)。历史真实 Case 本轮复跑0，原始私有产物可用性未逐项验证。

## 剩余8项与优先级

1. 正常服务完整链路：优先。当前生命周期Case用 Encoder/Model 替身，真实答案脚本绕开正常Session；两种证据不能拼成一体化通过。
2. Standalone生产验收：基础正确性有记录，最小权限/TLS/容量/并发仍缺。
3. 大源码边界：UTF-8切片有限范围有记录，其他编码/规模与图分页未验。
4. 真实故障总预算：本地取消已测，不承诺同步SDK/DNS硬截止。
5. 运维用量闭环：管理员操作、token/调用配额暂停待做。
6. 默认hybrid业务质量与Session答案：先冻结标签再测，P@5不可达问题须事前处理；不测后改门槛。
7. 最终操作文档和用户review：本轮提交草案，不宣称已review。
8. 图选择策略：保留pending，不恢复实验。

配置核对：config.py默认旧投影；coordinator._project拒绝隐式迁移。本轮推荐真实Case显式新切片版本+隔离数据，详见设计。

## 本轮交付与状态边界

新增主文档3份：本进度、[调研](../../research/prd005-resumption-research.md)、[设计草案](../../superpowers/specs/2026-09-14-prd005-resumption-design.md)。开发记录追加；没有改运行时代码、测试、模型配置或默认排名。

本轮只可报告测试层“回归通过”；设计为待用户确认的草案，真实运行、Case确认及用户review不推进。下一步确认方案A后，才编制第一阶段实施计划和精确可执行Case，不自动重开所有pending事项。
