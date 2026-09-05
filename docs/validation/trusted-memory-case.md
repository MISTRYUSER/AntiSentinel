# 可信记忆隔离真实 Case

状态：真实运行通过（2026-09-05）。

- 范围：可信记忆 A 阶段累计验证，不接入 Redis、外部模型或正式 storage。
- 输入：2 个 operator、2 个 incident、4 个 session；4 条有效记忆、1 条未来记忆、1 条 Evidence hash 不匹配记忆、1 个失败后重试 job。
- 运行：`PYTHONPATH=src python3 scripts/run_trusted_memory_case.py`。脚本只创建 `antisentinel-trusted-memory-*` 临时目录和 SQLite。
- 观测：candidate ID 数、持久化记录、来源数、跨 scope/未来/无效来源注入、retry、重开恢复、耗时、report.json。
- 硬门槛：sessions=4；unique_candidate_ids=4；persisted_records>=4；verified_source_refs>=4；cross_scope/future/provenance_invalid/background_exceptions 全为0；恢复记录数等于持久化数；retry_attempts=1；时长<=120000ms。
- 失败：任一硬门槛不满足、脚本异常、SQLite 产物缺失。
- 清理：仅删除脚本输出的 artifact_directory；不触碰仓库 `storage/`。
- 基线：可信度探针原始 0/5；当前真实 Case 尚未运行。重试预算最多 2 次。

## 实际结果

- 累计离线回归：104/104 通过，0 失败，1.49 秒。
- 真实 Case：`case_pass=true`，总耗时 22.62 ms，重试 0 次；内部 retry job 成功重试 1 次。
- 产物：`/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-trusted-memory-rqntafi4/trusted-memory.db` 与同目录 `report.json`。

| 指标 | 基线 | 实际 | 阈值 | 结果 |
|---|---:|---:|---:|---|
| Session 数 | 0 | 4 | 4 | 通过 |
| 唯一 Candidate ID | 0 | 4 | 4 | 通过 |
| 持久化记录 | 0 | 6 | >=4 | 通过 |
| 有效来源关联 | 0 | 6 | >=4 | 通过 |
| 跨 scope 注入 | N/A | 0 | 0 | 通过 |
| 未来记忆注入 | N/A | 0 | 0 | 通过 |
| 无效来源注入 | N/A | 0 | 0 | 通过 |
| 重开恢复记录 | 0 | 6 | 6 | 通过 |
| 后台异常 | 0 | 0 | 0 | 通过 |
| 总耗时 | N/A | 22.62 ms | <=120000 ms | 通过 |
