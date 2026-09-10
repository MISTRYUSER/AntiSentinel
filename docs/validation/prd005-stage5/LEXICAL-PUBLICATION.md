# FTS 投影隔离与 manifest 对账发布

2026-09-08。本轮处理生产边界中的词法投影发布，不调用外部模型，不改变检索排序算法或回答策略。

## 改动与语义

- 每个CodeSearchScope以code_search_active_projections指向一个revision。KeywordRetriever一次搜索先固定该revision，exact/FTS都使用同一revision；CodeRetrievalService传入配置中的projection_revision，配置与活动版本不符时lexical_unavailable，不默默选另一个版本。CodeSearchScope仍表示源码身份，投影版本作为独立查询配置，不混入Evidence源码绑定。
- begin_manifest接收调用者独立提供的完整expected documents，验证Scope/revision/唯一ID并保存不可变expected_json。构建可以分批，未发布文档不可见。publish_manifest必须已有expectation，不允许直接写ready。
- 发布事务内核验实际IDs、全部文档字段、每个FTS行的唯一性和字段，并执行FTS5 integrity-check；记录expected/actual digest、缺失/额外ID、字段差异、发布冲突。成功才把manifest ready和活动指针一起提交。
- begin捕获base_revision，publish按其校验当前指针，过期发布者和旧版本重发不能回滚新版本。发布检查失败提交failed记录但保留原活动指针；SQL事务本身失败则整笔回滚，不能残留ready。code_search_publication_attempts保留每次已提交的对账结果，重新begin不会抹去失败历史。
- 已发布过的投影文档不可修改，即使已退役或后续尝试失败；同值upsert幂等。需要变更时使用新revision。显式回滚旧revision不是本次支持的操作。
- 向量候选的SQLite权威验证也必须匹配活动投影，避免词法已切换而旧向量仍被返回。Embedding通道本身仍是Scope级记录，完整按模型/模板/维度区分的就绪与Worker生命周期尚未实现，本轮不宣称该部分完成。
- query_fts不再吞OperationalError。FTS表丢失且有既存文档时，重开store拒绝自动建立空FTS，显式要求重建，避免伪装成零命中。

原有Case和索引构建脚本已改为先提交expected set，再upsert并对账发布。测试中也使用源对象提供expectation，不从实际DB行反推预期。

## 兼容与限制

旧库增加expected_json/reconciliation_json/base_revision/published_once和两张发布表；旧ready记录不自动填充活动指针。只有显式从可信冻结源提供expected set并成功对账，才能重新发布。未修改此前私有模型评测的历史索引与报告；它们重开后不应被视为自动迁移成功。

预期源集合由调用者提供，仍需生产CodeMap/构建器保证来自指定发布generation；本模块不独立重新读取Git。发布中的失败有详细审计；整个SQL事务被中断时，状态和指针回滚，失败异常由调用者保留。没有新增后台Worker。

本轮保证返回候选不混projection，不承诺构建期间BM25分值完全稳定：FTS仍共用全表统计。并发吞吐、生产容量、Standalone、分模型通道就绪和真实Worker恢复另行验证，不能以本Case宣称性能或整体RAG生产通过。

## 测试

新增tests/test_lexical_publication.py（13例）与1个退役向量投影测试，共14项。覆盖：双revision隔离/显式pin、6种文档/FTS损坏、不可变投影、运行时与重开FTS缺失、旧发布者、legacy ready迁移、service版本配置、指针提交中断回滚与重试。先见begin_manifest缺失红灯；初始9/9通过，逐步扩展。首次全量606 passed；加入边界后609 passed。最后FTS重开拒绝分支targeted71 passed、0 failed、4.77s。

## 真实落盘 Case

产物根目录：`/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-publication-case-kp5s1l2p`。

```sh
python scripts/run_lexical_publication_case.py --output <新目录>
python scripts/run_code_retrieval_case.py --case keyword --output <新目录> --timeout 120
python scripts/run_code_retrieval_case.py --case vector --output <新目录> --timeout 120
python scripts/run_code_retrieval_case.py --case hybrid --output <新目录> --timeout 120
python scripts/run_code_retrieval_case.py --case graph --output <新目录> --timeout 120
```

全部在父进程120秒硬超时约束下串行执行，每个退出0。新publication Case为2源文件/58字节、3投影×2文档、实际持久化6文档、当前输出2；6次发布尝试含4次预期失败、2次成功。9/9断言通过，重开active=v2，75.08ms。预期失败不等于未处理异常；代码不因这些失败错误地切换指针。

| Case/指标 | 历史fixture基线 | 本次 | 阈值 | 差值 | 证据 | 结果 |
|---|---:|---:|---:|---:|---|---|
| publication检查 | N/A（新） | 9/9 | 9 | N/A | publication/report.json | 通过 |
| keyword完整性 | 4 | 4 | 4 | 0 | keyword/report.json | 通过 |
| vector完整性 | 6 | 6 | 6 | 0 | vector/report.json | 通过 |
| hybrid完整性 | 8 | 8 | 8 | 0 | hybrid/report.json | 通过 |
| graph完整性 | 15 | 15 | 15 | 0 | graph/report.json | 通过 |
| 既有4 Case执行错误 | 0 | 0 | 0 | 0 | 各report.json | 通过 |

keyword输入2文件68字节，输出/持久化2/2，65.16ms；vector输入2文件68字节，SQLite/Milvus2/2，1230.81ms；hybrid输入4文件194字节，SQLite/Milvus4/4，1466.61ms；graph输入1文件220字节、输出1、持久化6，124.83ms。既有4Case均后台异常0；新Case无后台worker。外部模型请求0，重试0。五个子目录实际22文件，另10个stdout/stderr文件；Milvus内部文件也计入实际数量。独立查询publication库确认active v2、失败4/成功2、integrity_check=ok。

本轮5个Case的case_pass均true，范围为本地正确性与回归，未判定检索质量或生产性能通过。实现/脚本/测试涉及15文件，另有验证文档与开发日志。

最终累计回归：`/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-ragas-clean-wxav1le_/bin/python -m pytest -q` 为609 passed、0 failed、6 warnings、39.90s；595→609新增14项，通过率100%。compileall与git diff --check通过。当前本阶段真实运行通过、回归通过；用户review、生产性能与整体PRD验收未通过。
