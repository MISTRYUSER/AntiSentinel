# 常驻检索关闭边界专项（2026-09-10）

本轮修复三项边界：收到停止信号后继续后续投影/任务；停止期间真实调度异常被忽略；客户端关闭失败却报告 stopped。协调器现在显式呈现 stopping，归档 chunk、投影及任务之间检查停止信号；内部取消不记作损坏投影，真实异常始终记录 failed。关闭失败保留错误类型，stop 向调用者报错；超时不会强关仍在使用的客户端。无效 timeout 在修改状态前拒绝。

仅保证协作式停止：已经开始的编码/索引操作可以完成。未增加线程强杀或 RPC 中途取消，也不将本轮等同于多进程生产验收。

## 复现与回归

基线 659 passed、2 skipped，41.87s。首轮新用例命令 `python -m pytest tests/test_retrieval_coordinator.py -q -k 'stop_during or close_failure or stop_signal'` 产生 3 个失败；第三个复现初版在继续处理后因缺少 queue 替身报错，最终补全替身，直接断言只处理第一个投影。

- 针对性：`python -m pytest tests/test_retrieval_coordinator.py tests/test_incident_retrieval_tools.py tests/test_embedding_worker.py -q`：45 passed，0 failed，6.96s。
- 全量：`/Users/xuewentao/miniconda3/bin/python -m pytest -q`：668 passed、2 skipped、0 failed、7 warnings，34.86s。新增9测试；Ragas依赖仍缺失，2项不计入通过。
- 最终停止投影单项验证：1 passed，15 deselected，2.14s。
- compileall 与 git diff --check 退出0。

## 真实 Case

命令：`/Users/xuewentao/miniconda3/bin/python scripts/run_retrieval_coordinator_case.py --output <new-output> --shutdown-probe`。父进程120秒上限，本轮CLI重试0。

产物：`/Users/xuewentao/.local/share/antisentinel/cases/retrieval-shutdown-a4foevrv`。shutdown/report.json、source-refs.json、facts.sqlite、Milvus Lite数据均保留。根目录commands.json保留8条实际命令及父进程耗时，8份CLI日志和8份report对应同轮验证。

输入1文件、52bytes、2chunks。本地确定性编码器在第一项编码中受控等待；stop(timeout=.01)产生1次预期超时，客户端保持打开。释放等待后当前项完成并关闭。重建客户端后编码剩余1项，再次重建编码0；随后验证正常Session、来源引用、Evidence存储和恢复。编码器/模型为本地替身，SQLite与Milvus为真实实现，外部模型调用0。

| 指标 | 基线 | 实际 | 阈值 | 变化 | 证据 | 结果 |
|---|---:|---:|---:|---:|---|---|
| 关闭边界复现失败 | 3 | 0 | 0 | -3 | pytest | 通过 |
| 全量通过数 | 659 | 668 | 原用例无失败 | +9 | pytest | 通过 |
| shutdown检查 | 未覆盖 | 17/17 | 全部通过 | 新增17 | shutdown/report.json | 通过 |
| 3次启动编码量 | 未覆盖 | 1/1/0 | 总计2 | 无重复编码 | report编码字段 | 通过 |
| 持久任务 | 0 | 2 | 2 | +2 | SQLite读回 | 通过 |
| Evidence存储/恢复 | 0 | 1/1 | 各1 | +1 | report、source-refs及hash | 通过 |
| 后台未处理异常 | 0 | 0 | 0 | 0 | shutdown检查 | 通过 |
| 外部模型调用 | 0 | 0 | 0 | 0 | 本地替身 | 通过 |

shutdown业务完成观察1135.04ms，持久化完成观察1162.00ms，差26.96ms；最终读回与重建校验完成1231.03ms。观察差受轮询调度影响，不是精确写入延迟，未用其证明生产P95。

累计8 Case shutdown/worker/publication/keyword/vector/hybrid/graph/lifecycle全部case_pass=true、退出0，检查17/17/9/4/6/8/17/12，总90项；内部耗时1231.03/6928.04/111.67/68.37/840.83/1298.78/370.97/1086.00ms，总11935.69ms。进程崩溃恢复仍由worker Case覆盖；shutdown Case是同进程关闭和客户端重建。

本轮可报告本地真实运行通过、回归通过，未标用户review通过或生产就绪。仍待远程Flash Worker、Standalone与容量部署、四路质量验收、完整Ragas依赖复测。
