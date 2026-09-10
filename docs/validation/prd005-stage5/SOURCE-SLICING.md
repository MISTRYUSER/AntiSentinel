# 长源码切片与范围去重：限定范围验证（2026-09-10）

状态：**真实运行通过、回归通过；用户 review 待完成**。仅覆盖 UTF-8 派生投影、字节范围权威校验、去重和恢复，不代表生产就绪或 RAG 质量通过。默认 hybrid 保持，图选择优化继续 pending。

## 实现边界

新增显式投影版本 `utf8-slices-8192-v1`，单片最多 8192 个源码字节（不是模型 token 数）；优先换行切片，保持字符完整。document_id 包含绝对字节范围及父 hash；chunk_id 保留原始归档父块 ID。SQLite/Milvus 同步保存范围与父 hash，Evidence 存证和 checkpoint 恢复重新核验原始归档、父 hash、切片 hash 及当前 incident binding。

原 Code Map 按字节分块可能切断 UTF-8 字符。本实现将派生视图的相邻边界共同向前推进到下一字符起点，最多 3 字节，原始归档不修改；极小尾块可以派生为空，字符由前块持有。服务器根据完整文件计算允许范围，不能依靠客户端自行扩展父块范围。

旧记录的新范围列为 NULL，旧 manifest/task JSON 作兼容归一化比较，原始 JSON 不重写。新版本必须显式发布完整 manifest，并使用匹配的新 collection schema；不自动迁移已有 active projection，不覆盖旧 collection。旧投影伪造范围字段会被拒绝。Graph 使用投影 document_id；范围重叠不再只依赖父 chunk ID 去重。

## 可复现命令与证据

运行环境：`/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python`（下称 PY）。完整产物根：`/Users/xuewentao/.local/share/antisentinel/cases/source-slicing-3ki805yl`（下称 ROOT）。重跑请使用新输出目录，避免混合历史产物。

```sh
$PY -m pytest -q
$PY scripts/run_source_slicing_case.py --output <新目录>/lite
$PY scripts/run_source_slicing_case.py --output <新目录>/standalone --milvus-uri http://127.0.0.1:29530
```

Standalone 需先按既有独立 Compose 配置启动，将私有 token 注入环境变量；不要把凭证写入命令或报告。已运行命令见 ROOT/standalone-command.json，10 个累计 Case 精确命令及退出码见 ROOT/cumulative-commands.json。报告见各子目录 report.json，全量原始日志见 ROOT/pytest-full.log。

## 数字验收

| 指标 | 基线 | 实际值 | 阈值 | 差值/比例 | 证据 | 结果 |
|---|---|---|---|---|---|---|
| 全量回归 | 711 通过 | 728 通过，0 跳过，0 失败，61.92s | 失败/跳过 0 | +17，+2.39% | pytest-full.log | 通过 |
| 长文件投影 | 18 原始父块 | Lite/Standalone 各 38 文档、38 向量、38 任务 | 三存储数量一致 | 新增 20 个检索片段 | 两个 report.json | 通过 |
| 单片源码预算 | 原块最多 16KiB | 所有切片 ≤8192 bytes | ≤8192 | 上限减半 | slice_size | 通过 |
| UTF-8 完整性 | 3 个原块不能独立解码 | 节点字节完整覆盖，原归档不变 | 遗失/归档修改 0 | 3 个切断边界得到可读派生视图 | complete_node_coverage/archive_unchanged | 通过 |
| 重开编码 | 首次各 38 次 | 第二次各 0 次 | 0 | 减少 38 次，100% | embedding_calls_per_start | 通过 |
| Evidence | 首次各 4 条 | 各 4 条按范围恢复 | 4/4、≤32KiB | 恢复率 100% | rehydrated_slices/evidence_budget | 通过 |
| 新切片 Case | 无同 fixture 历史时延基线 | Lite 15/15；Standalone 15/15 | 检查全部通过、单 Case ≤120s | 100% | lite-final/standalone | 通过 |
| 累计 Case | 既有 9 类 | 10/10、115/115 检查 | 失败 0 | 增加切片 Case 1 类 | cumulative-commands.json/各 report | 通过 |

切片输入各 2 文件、150121 bytes、18 父块；输出各 5 个 hybrid hit、4 个 Evidence。Lite 耗时 2557.28ms，业务完成 2427.98ms，存储/关闭核验差 129.30ms；Standalone 耗时 28217.38ms，业务完成 28014.49ms，核验差 202.89ms。该差值是观察/关闭成本，不能当作纯异步写入延迟。两者 embedding attempts 各 38、重试 0、外部模型调用 0、后台异常 0；使用确定性本地 Encoder，未验证真实 Flash。

累计 10 Case 命令墙钟总计 23.75s，全部退出 0；包含 worker 故障注入恢复、manifest 发布、keyword/vector/hybrid/graph、生命周期、关闭和查询预算。Worker 的注入重试是测试输入，不能把其 7 attempts 写成无业务重试。预算 Case 分段响应 7 次后取消，降级 342.71ms/400ms 配置，锁等待 85.13ms/80ms 配置及 300ms 调度容差；这仍不等于生产硬时限。

Standalone 启动前空间预检 17983459328 bytes、Docker 29.4.1；复用已验证 v3.0.1 镜像，不拉取镜像。使用独立新 collection `slice_case_ba0511480c7a_7df4ab8c1fcab2ee`；结束后本次测试栈 stop 退出 0，旧数据保留。

## 剩余验收

只支持声明 UTF-8/UTF-8-sig/ASCII 且完整文件确为 UTF-8 的源码；其他编码显式拒绝。大语料吞吐/并发/容量、Graph 完整分页及检索效果尚未验收。当前小 fixture 不能证明质量提升，未与旧版在相同长文件 fixture 上建立性能基线。

真实 Flash＋正常 Session LLM 完整链路、最小权限/TLS、生产资源配置、故障硬时限、用量运维及业务质量门槛仍待完成。下一轮优先真实模型完整链路；不恢复图策略调参。
