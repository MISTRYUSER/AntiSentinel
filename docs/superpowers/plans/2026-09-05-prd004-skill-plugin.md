# PRD-004 Skill / Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本轮在当前任务内串行执行，每个阶段单独review，不自动派发子任务。

**Goal:** 在现有Runtime/ToolExecutor上交付本地可信Skill的发现、按需加载、版本兼容、诊断及固定评测。

**Architecture:** 使用不可变本地版本包及ReleaseLock；catalog注册仅读取元数据。运行专属状态管理有序ActiveSkill集合和回合披露，Executor负责实际授权。复用持久化、Tracing和evaluation。

**Tech Stack:** Python 3.11+、标准库JSON/dataclasses/pathlib、pytest、现有FastAPI/SQLite/OpenTelemetry；不新增生产依赖。

**Spec:** ../specs/2026-09-05-prd004-skill-plugin-design.md

## Global Constraints

- 单运行最多1个Skill；capabilities为唯一编写目录；无安装器/在线市场/热更新/脚本执行平台。
- ModelPort只解析；Registry只索引；Executor校验权限；skill.load不扩大运行U及当轮Vt。
- 每包≤32个Skill，每Skill≤32个reference；manifest≤64KiB、正文≤64KiB、资料≤256KiB、登记资源总量≤8MiB。
- 目录≤8192字符、指令≤8192字符、资料单次≤16384/累计≤32768字符；不得静默截断指令。
- 源码基线147文件、测试64文件/288项、0失败、1警告，历史pytest 2.86s。开始实现前在隔离工作区复测。
- 每Stage先失败测试→最小实现→针对性和累计回归→已确认真实Case→用户review。Case未确认只准备runner及fixture，不启动服务或真实模型。
- Case默认120秒、最多2次重试，同输入连续失败2次停止扩展；正常A/B/C trial次数不算故障重试。
- 每Step追加docs/memory-development-log.md，原始输出写docs/validation/prd004-skill-stage41及后续阶段目录；无数据标N/A或INSUFFICIENT_DATA。
- 生产变更在隔离工作树；只提交本阶段变更，不混入用户未提交文档。阶段review前不做下一Stage。

## 文件职责及跨阶段接口

源路径前缀为src/antisentinel/。

| Stage | 创建／修改文件 | 职责 |
|---|---|---|
| 4.1 | capabilities/manifest.py、resources.py、registry.py、bootstrap.py、availability.py、cli.py；tools/discovery.py | 严格元数据、受限文件访问、索引、原子包装配、可用性及validate/inspect目录 |
| 4.2 | capabilities/package.py、loader.py、runtime_state.py、tools.py；capabilities/cli.py | 版本包/锁、按需读取、准备/提交状态、skill工具及pack/inspect正文 |
| 4.3 | worker/runtime/context.py、messages.py、loop.py、checkpoint.py；worker/execution/tool_executor.py；tools/registry.py | 回合披露、授权前缓存、幂等、持久恢复、隔离 |
| 4.4 | capabilities/diagnosis/；entry/application.py、api/app.py、memory/recorder.py、tracing/telemetry.py；frontend/index.html及已有脚本 | 示例/契约、API显式选择、来源持久化、Span关联、页面验证 |
| 4.5 | evaluation/skill_integration.py、skillsbench_adapter.py、bfcl_adapter.py；固定fixtures与runner | 来源冻结、oracle、A/B/C及复算 |

4.1定义PluginConfig(root, required, trusted_tool_modules)，后者为服务端预先批准的模块映射，不由模型或包自行授信。assemble_plugins(configs, base_registry)返回CapabilityCatalog(snapshot_id, skills, diagnostics, tool_registry)；SkillRegistry.get(skill_id)精确查找；check_availability(catalog, skill_id, policy)返回available/reason_codes。4.1的snapshot_id只代表元数据及工具manifest身份，非完整内容快照，直到4.2绑定发布hash/ReleaseLock才可执行Skill。

4.2定义build_local_package(source_root, output_root)、SkillLoader.load(snapshot, skill_id)、read_reference(snapshot, selected_skill_id, reference_id)、prepare_activation及commit_activation(expected_revision)。4.3在run开始绑定release和U，生成每请求Vt并在executor和缓存前检查。4.4增加结果skill_usage及API skill_id/skill_version。4.5runner输出统一case/trial报告，不替换官方verifier。

## Stage 4.1 定义／发现

测试新增tests/test_skill_catalog.py、test_skill_cli.py；fixture使用pytest tmp_path创建本地包，不依赖服务。

- [ ] 写失败测试：合法包2个Skill、缺工具、缺资源、路径穿越/符号链接、非法字段/JSON重复键、同snapshot版本冲突、包工具冲突、必需包失败和optional包整体排除。测试中创建ToolDefinition真实对象，handler记录调用，注册阶段调用数必须0。

```python
catalog = assemble_plugins([PluginConfig(root=package_root)], base_registry)
assert tuple(item.skill_id for item in catalog.skills.list()) == ("local/diagnosis", "local/runbook")
assert handler_calls == []
assert catalog.skills.get("local/missing") is None
```

- [ ] 运行pytest tests/test_skill_catalog.py -q，确认断言失败源于未提供catalog接口，而非fixture拼写问题；保存red输出并追加日志。
- [ ] 实现严格manifest字段、标识符/版本、资源元数据限制，复用ToolDiscovery显式提取；临时装配每包工具，依赖校验失败整个包排除，最后一次构造catalog。冻结对外索引与工具元数据，用户修改返回副本不得影响catalog。
- [ ] 增加运行policy可用性：missing、缺权限、只读拒绝、disabled；提供cli validate与inspect目录，未交付的子命令不伪造成功。

```python
result = check_availability(catalog, "local/diagnosis", RunPolicy(allowed_tools=frozenset()))
assert result.available is False
assert "tool_not_allowed" in result.reason_codes
```

- [ ] pytest tests/test_skill_catalog.py tests/test_skill_cli.py tests/test_tools.py -q；再pytest -q，保存输出；新增成功指标登记数一致率100%、错误原因完整100%、正文读取0，失败指标错包工具泄漏0，回归288项全部通过。
- [ ] 交付scripts/validate_skill_integration.py的stage 4.1真实文件Case入口，创建独立fixture目录，合法包2Skill、6种错误包，写catalog.json/diagnostics.json/report.json并回读；具体命令和清理范围提交Case确认。未确认不运行。
- [ ] 已确认Case通过后记录运行状态、持久化状态、输出3份、数量关联校验、异常0、恢复和case_pass，暂停用户review。通过后可单独提交feat(capabilities): validate and assemble local skill catalogs。

## Stage 4.2 加载／版本包

测试新增tests/test_skill_loader.py、test_skill_release.py、test_skill_activation.py；扩展test_skill_cli.py。

- [ ] 先写发布v1/v2并存、同版本异内容拒绝、两次pack确定性、范围/大小/hash拒绝、未披露资料按需读取、重复load幂等、错误不重试和暂时失败≤2的失败测试。

```python
first = build_local_package(source_root, output_root)
second = build_local_package(source_root, output_root)
assert first.release_id == second.release_id
assert first.manifest_bytes == second.manifest_bytes
```

- [ ] 实现登记资源原子发布、ReleaseLock、旧版本支持检查，不写安装/下载/GC。prepare阶段不修改Skill状态，持久化成功才commit，reference披露复用事务规则。全量I/O只在pack，注册正文I/O保持0。
- [ ] 注入资源读取、hash、预算和checkpoint save失败；逐点检查未激活/未披露。2个run复用只读资源缓存，状态与工具结果必须隔离，LRU≤16MiB。

```python
before = state.revision
outcome = load_with_failing_checkpoint(state)
assert outcome.error["code"] == "checkpoint_write_failed"
assert state.revision == before
assert state.selected_skill_id is None
```

测试辅助load_with_failing_checkpoint由tests中的fixture封装实际loader和抛OSError的CheckpointStore.save，不进入生产接口。

- [ ] pytest tests/test_skill_loader.py tests/test_skill_release.py tests/test_skill_activation.py tests/test_skill_catalog.py tests/test_skill_cli.py tests/test_tools.py -q；再pytest -q。完成cli pack及inspect正文，validate仍只元数据。
- [ ] stage4.2 Case累计4.1，增加旧版本未读资料、回滚默认值、保留原绑定、激活失败和重新打开checkpoint。成功指标hash一致100%、读取副本1、恢复一致100%；失败半激活/越界读取0。确认Case后运行并停在review。

## Stage 4.3 Runtime集成

测试扩展tests/test_runtime_loop.py、test_runtime_context.py、test_checkpoint.py、test_tools.py；新增test_skill_runtime.py。

- [ ] 先用FakeModel固定响应验证显式/自主2路径：load→reference→业务工具→final，最多8turn；第三次继续重复load最终受预算终止。增加同轮load+猜名调用、缓存越权及旧checkpoint缺Skill字段的失败测试。

```python
assert report["explicit"]["final_evidence_count"] == 1
assert report["autonomous"]["instruction_blocks_per_request"] == [0, 1, 1, 1]
assert report["same_turn_guess"]["business_handler_calls"] == 0
```

report由test fixture调用真实RuntimeLoop并读取FakeModel收到的请求、handler记录和RuntimeResult生成，禁止测试直接写预期报告。

- [ ] 修改消息约束和规范上下文；U运行冻结，Vt请求冻结；Executor先校验注册/权限/披露/只读/参数再缓存执行；skill控制动作走已有ToolCall协议。
- [ ] checkpoint序列化SkillState/ReleaseLock/待投递事件及幂等ID；旧格式兼容、新格式拒绝未知版本；恢复必须读取对应发布锁，不能用latest替换。每步日志包含持久化结果。
- [ ] pytest tests/test_skill_runtime.py tests/test_runtime_loop.py tests/test_runtime_context.py tests/test_checkpoint.py tests/test_tools.py -q；再pytest -q。无Skill路径同fixture连续5次，记录基线和当前中位数，≤1.05倍才能声明性能回归满足。
- [ ] stage4.3 Case累计4.1–4.2，2run并发、checkpoint恢复、同轮授权、重复加载。成功两路径100%、每请求指令≤1、旧版本恢复100%；失败串状态/错误执行0。确认后运行并停在review。

## Stage 4.4 示例／追踪

创建capabilities/diagnosis/instructions.md、references/health.md、contracts.json及本地包清单；新增tests/test_diagnosis_skill.py，扩展tests/test_application_persistence.py、test_tracing_metrics.py、test_dashboard.py。

- [ ] diagnosis至少3个契约（适用、不适用、越权），固定工具Evidence，verifier_id由受信任注册表解析。cli test仅离线契约；先测缺Evidence即失败，不能以load成功判诊断成功。

```python
assert verify_diagnosis(no_evidence_result).passed is False
assert verify_diagnosis(valid_result).passed is True
```

verify_diagnosis由evaluation/skill_integration.py提供，校验实际Evidence可回读、scope正确及工具断言，不接受全文字符串匹配替代。

- [ ] API和应用增加skill_id/skill_version；结果持久化skill_usage，候选抽取排除skill控制与资料正文；页面展示版本/资料来源。运行/turn根Span建立真实父子关系，导出parent_span_id，异步链路传播上下文并读取后台错误。
- [ ] pytest tests/test_diagnosis_skill.py tests/test_application_persistence.py tests/test_tracing_metrics.py tests/test_dashboard.py -q；再pytest -q。
- [ ] stage4.4 Case累计4.1–4.3，具体固定模型/只读fixture/API地址/独立目录提交确认后运行，浏览器验证页面真实链路，读回SQLite/文件/事件/Span。成功版本一致100%、证据关联100%、恢复100%；失败后台异常及正文泄漏0。业务完成t1与持久化完成t2分开；review前不进入测评。

## Stage 4.5 冻结测评（单Skill与多Skill分组）

测试新增tests/test_skill_evaluation.py，创建evaluation/skillsbench_adapter.py、bfcl_adapter.py及固定数据清单；保留现有evaluation设施。

- [ ] 固定7类本地输入、计划14条；先测试报告不能丢失超时trial、版本字段缺失应拒绝冻结、无匹配不能构造虚假正确Skill B组。

```python
assert summary.total_trials == 3
assert summary.successful_trials == 2
assert summary.timeouts == 1
```

上述输入为tests中的三条真实runner结果fixture：2条verifier成功、1条超时；分母不可过滤失败。

- [ ] 读取上游具体commit及许可证、筛选单Skill/只读可适配任务，冻结原case_id/hash/排除理由；oracle先过原verifier。没有合适SkillsBench任务则记录0条和原因并回到范围review，不引入shell绕过权限。
- [ ] 对SkillsBench全部任务按skill_count分为single/multi cohort；single运行A/B/C，multi运行官方环境A/B并把AntiSentinel C明确记为`unsupported_skill_composition`。不得从多Skill分母删除此工程缺口，也不得把多个Skill静默合并。
- [ ] 实现接口映射并标注派生差异；A/B/C同模型/预算/工具上限，Memory关闭或同只读快照，每适用组至少3trial，记录usage来源及所有基础设施错误。
- [ ] pytest tests/test_skill_evaluation.py -q；再pytest -q；真实模型/Docker评测单独提交预算和Case确认，依赖独立于CI。
- [ ] 报告任务成功、选择、无匹配、工具正确性、Token、调用次数、P50/P95及波动；成功来源完整100%、trial≥3、评分可复算100%；失败漏报trial0，累计工程回归全满足。效果无收益必须如实写，不用工程测试冒充效果。

## 执行记录

当前开始Stage4.1。设计增加E1–E6共6项工程映射，计24项；真实Case尚未确认。后续Stage代码不提前编写，只有当前Stage实现／回归及Case报告供review。
