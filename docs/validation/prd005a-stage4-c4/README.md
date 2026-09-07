# PRD-005A 5A.4 实现与 C4 验收

日期：2026-09-07。用户已授权直接完成5A.4及本地Case。范围：关系接入真实构建、Git变更输入、文件事实缓存、代次保留与有界关系查询。5A.5真实仓库/性能容量验收未在本报告中宣称完成。

## 结果

- 正式C4：6/6变更通过，全量/增量节点、关系、Chunk、文件及blob语义一致，双SQLite读回一致。
- 原快照变化0；partial显式重试生成1/2两代次，原Incident binding仍为1，原Evidence重新读取的内容/hash不变。
- 固定fixture人工标注6条确定关系，precision/recall均为100%；另外的回归覆盖跨文件同名、未导入同名、参数遮蔽、重复调用点。这不是任意Python程序的全语义保证。
- 最终代码回归：`python -m pytest tests -q`，431 passed、0 failed、1既有Starlette警告，52.02秒；原基线426→431，增加5项（1.17%）。[原始输出](regression-final.txt)。
- C1/C2/C3累计fixture复跑均case_pass=true。C2首次因相对output路径解析remote失败，使用绝对路径重跑成功（1次）；保留失败目录。

## 正式 C4 数字

[机器报告](final/report.json)，[Trace](final/traces.jsonl)。输入每Commit6–7文件、294–328字节；总耗时18.42秒，预算120秒；重试0；8份逻辑快照、9份发布代次、30个持久化span、608项逐行/字节hash/关联检查；后台异常0。

| 场景 | cache hit | 节点/边/Chunk | 双库完整性检查 | t2−t1(ms) | 等价及独立调用标注 |
|---|---:|---|---:|---:|---|
| add | 6 | 9/6/9 | 110 | 57.37 | 通过 |
| modify | 6 | 9/6/9 | 110 | 47.45 | 通过 |
| delete | 6 | 8/6/8 | 100 | 44.31 | 通过 |
| rename | 5 | 8/6/8 | 100 | 92.55 | 通过 |
| export | 5 | 8/3/8 | 88 | 44.07 | 通过 |
| caller | 4 | 8/6/8 | 100 | 35.45 | 通过 |

t1为构建结果产生，t2为发布后SQLite逐项读取和Trace flush完成。所有场景的原始ns时间保留在JSON。基线关系与各场景独立标注已通过；表中的确定关系包含静态imports依据与tested_by，不代表测试覆盖率。

| 指标 | 基线 | 当前 | 门槛 | 差值 | 证据命令 | 结果 |
|---|---|---|---|---|---|---|
| 六类真实Git等价 | 0 | 6/6 | 6/6 | +6 | C4命令 | 通过 |
| 固定关系标注 | N/A | 6/6 | 100% | N/A | C4命令 | 通过 |
| 原快照改变 | N/A | 0 | 0 | N/A | read_generation | 通过 |
| 旧Evidence回读 | N/A | true | true | N/A | C4命令 | 通过 |
| 完整性校验 | 0 | 608 | 全部通过 | +608 | C4命令 | 通过 |
| 全量测试失败 | 0 | 0 | 0 | 0 | pytest tests -q | 通过 |
| C4耗时 | N/A | 18.42s | ≤120s | N/A | C4命令 | 通过 |

全量中位526.42ms、增量中位577.33ms：六个不同变更输入，仅为观测数据，不是同条件性能回归基线，不宣称提速。五轮真实仓库性能基线留在5A.5。

## 实现与复现

关系解析现在按文件与词法作用域选择目标，导入使用模块路径，参数/重绑定无法唯一确定时保留unresolved；每个调用点保留行范围并生成独立edge ID。SnapshotBuilder真正调用RelationResolver；缓存复用AST树并重新绑定目标snapshot身份，关系统一重算。daemon使用真实SnapshotBuilder及进程内缓存；进程重启后冷启动全量构建，未宣称磁盘AST缓存。

SQLite新增V5 code_map_generations迁移；每个已发布代次保存不可变行快照，源码字节继续由blob hash引用。partial不会伪装ready，显式retry保留逻辑job及历史代次；SourceEvidence读取其绑定代次。V4已有快照仍可读取当前代次。

复现（新的输出目录；不会写企业仓库）：

```sh
/Users/xuewentao/miniconda3/bin/python -m pytest tests -q
C4_PARENT="$(mktemp -d /tmp/antisentinel-c4.XXXXXX)"
/Users/xuewentao/miniconda3/bin/python scripts/run_code_map_case.py --case incremental --output "$C4_PARENT/run" --timeout 120
```

本轮代码/测试/runner新增2文件、修改17文件（共19）；正式C4增强前后各执行1次且均通过；最后累计fixture3个通过。源码/数据质量风险仍限定于静态支持范围；未进入5A.5、未运行企业Git，也未宣称整个005A完成。
