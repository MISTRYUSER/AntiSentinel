# 持久 Ragas 环境与累计验证（2026-09-10）

本轮解决临时环境消失造成的2项Ragas测试长期跳过。新环境位于 `/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3`，主Python未修改；后续完整评测应使用该环境的Python。Ragas0.4.3与现有optional extra一致，本轮没有修改检索实现或标签。

## 环境复建

已解析并验证的依赖版本记录在 [ragas-requirements-py313.txt](ragas-requirements-py313.txt)。这是macOS arm64/Python3.13的运行依赖版本快照，不是跨平台或带制品hash的锁文件。

在仓库根目录，用新建的隔离环境执行：

```sh
/Users/xuewentao/miniconda3/bin/python -m venv /Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3
/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python -m pip install -r docs/validation/prd005-stage5/ragas-requirements-py313.txt
/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python -m pip install --no-deps -e .
/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python -m pip check
/Users/xuewentao/.local/share/antisentinel/venvs/ragas-0.4.3/bin/python -m pytest -q
```

本轮首次安装实际采用 `pip install -e '.[ragas-evaluation]' pytest`，随后记录122个包元数据与解析版本。pip check无冲突。版本快照用于避免后续再次浮动安装；首次安装与上述按快照重建是不同命令，未另做第二次空环境重建。

## 测试与 Case

产物根：`/Users/xuewentao/.local/share/antisentinel/cases/ragas-restored-z3qwhwym`，包括pytest-full.log、pip-check.log、packages.json、依赖快照、检索与Ragas报告、samples.jsonl、两库文件及累计Case日志。

| 指标 | 基线 | 本次 | 阈值 | 变化 | 证据 | 结果 |
|---|---:|---:|---:|---:|---|---|
| 全量通过 | 668 | 670 | 原用例无失败 | +2 | pytest-full.log | 通过 |
| 跳过项 | 2 | 0 | 0 | -2 | pytest-full.log | 通过 |
| 测试失败 | 0 | 0 | 0 | 0 | pytest-full.log | 通过 |
| 依赖冲突 | 未测 | 0 | 0 | N/A | pip-check.log | 通过 |
| Ragas评分行数 | 本轮未测 | 360 | 360 | N/A | ragas/report.json | 通过 |
| Ragas输入错误 | N/A | 0 | 0 | N/A | ragas/report.json | 通过 |
| 累计恢复Case | 8 | 8 | 全部通过 | 0 | cumulative-commands.json | 通过 |

`python -m pytest tests/test_ragas_evaluation.py -q`：4 passed，0 skip，24.29s（冷启动）。其中socket.connect拦截验证ID指标离线执行。全量670 passed、0 skipped、0 failed、7 warnings，43.13s。基线主环境34.86s；本轮环境/依赖及并发评测负载不同，不据此宣称性能改善或满足同负载5%性能回归门槛。

新环境累计shutdown/worker/publication/keyword/vector/hybrid/graph/lifecycle共8 Case、90检查全部通过，内部耗时1248.57/6549.83/66.85/53.69/842.53/1321.46/670.47/1217.72ms；CLI重试0，每条父进程timeout120秒。全量测试父进程上限180秒，用于容纳新环境首次导入，与单Case预算区分。

## 固定语料 Ragas 结果

命令使用上述隔离Python：

```sh
python scripts/evaluate_code_retrieval.py --diagnostic --diagnostic-vectors --output <new-root>/retrieval --timeout 120
python scripts/evaluate_ragas.py --report <new-root>/retrieval/report.json --output <new-root>/ragas
```

固定manifest SHA-256 `6473b9ae5f596fb3dc2287a747ad24f97da15165ce7fc77f7a1cd97d2adab3d0`；82文档、50833bytes、24独立查询、3模式各5轮，共360观察。SQLite/Milvus分别读回82/82，身份验证通过；检索运行9594.47ms，业务/存储观察差9.87ms。该诊断程序没有后台worker，background_exceptions字段为null，不冒充测得0。

| 模式 | Ragas ID precision（有定义行均值） | precision有定义行 | Ragas ID recall（有定义行均值） | recall有定义行 |
|---|---:|---:|---:|---:|
| keyword | 0.203571 | 70/120 | 0.55 | 100/120 |
| vector | 0.066667 | 120/120 | 0.283333 | 100/120 |
| hybrid | 0.10 | 120/120 | 0.483333 | 100/120 |

空检索的precision、无参考答案的recall保留未定义，未当作满分。ID precision分母是实际检索的唯一ID数，不能与固定分母P@5混用。

诊断P@5/Recall@5/MRR分别为keyword 0.14/0.55/0.485、vector 0.08/0.283333/0.1825、hybrid 0.12/0.483333/0.328333；每模式逐查询最小值均0。P95分别9.55/11.45/21.95ms，仅是本轮观察，无同环境性能对照。

两个评测CLI execution_pass=true；quality_pass=false、case_pass=false保持不变。诊断向量为未训练hash特征，不能代表Qwen Flash表现。标签仍待review、P@5理论上限0.25；本轮未调用LLM，Faithfulness、Answer Relevancy等答案指标未评估，未证明优化或整体RAG质量达标。

本轮环境/回归及本地执行验证完成，未标用户review或生产就绪。剩余远程Flash常驻Worker、Standalone容量部署、四路冻结质量与答案评测验收。
