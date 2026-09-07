# PRD-005A Code Map：一手资料与接入调研

## 范围修订

真实业务仓库是多语言的；`global-compass-aegis` 固定 Commit 扫描得到 371 个文件、0 个 Python 文件，证明 Python-only 不可作为 005A 交付假设。后续优先验证 Go/TypeScript，并保留 SCIP/Sourcegraph 可选增强。

日期：2026-09-07。研究输入以用户附件为准；`cmp` 确认附件与仓库 PRD 字节一致。HEAD `55ea620e54f876e3c564b6fb7c5daf02b09226cd`。这是一份设计输入，不是代码地图功能验收。

## 1. 可复现基线

`/Users/xuewentao/miniconda3/bin/python -m pytest -q`：363 passed、0 failed、1 warning，pytest 耗时3.55秒，包装命令墙钟4.58秒。历史363/0→当前363/0，测试通过率100%，失败阈值0；160个生产Python文件、74个测试文件、code_map文件0。单次测试耗时3.74→3.55秒（-0.19秒，-5.08%）仅供记录，不能据此声明性能提升。

统计命令：`python -c "from pathlib import Path; print(len(list(Path('src').rglob('*.py'))),len(list(Path('tests').glob('test_*.py'))),len(list(Path('src/antisentinel/code_map').rglob('*.py'))))"`。

原始证据见 `../validation/prd005a-design-baseline/{baseline.json,pytest.txt}`。真实地图Case=0；扫描吞吐、查询延迟、后台异常与t2-t1=N/A（模块未实现）；本轮没有运行企业Git、模型或后台服务。

## 2. 参考项目事实

本轮重新查询2个官方仓库，固定2个Commit、4个文件。源码保存于 `../validation/prd005a-design-baseline/sources/`；manifest记录URL、Commit、字节数与SHA256。以下结论限定于所读文件，不能推出整个参考项目存在或不存在某项能力。

| 来源 | 所读事实 | 不能据此推导 |
|---|---|---|
| [Codex file-search](https://github.com/openai/codex/blob/455318c2020d75ae7d66d6ccf19defda97edec34/codex-rs/file-search/src/lib.rs) | 使用ignore WalkBuilder、nucleo匹配；结果包含路径和评分，有limit与取消控制 | 文件搜索并不证明提供固定Commit符号图或持久化扫描队列 |
| [Codex handlers](https://github.com/openai/codex/blob/455318c2020d75ae7d66d6ccf19defda97edec34/codex-rs/core/src/tools/handlers/mod.rs) | 显式导出处理器，parse_arguments统一处理JSON解析错误 | 不能直接承担本项目的仓库登记、租约和源码证据职责 |
| [Agno Knowledge](https://github.com/agno-agi/agno/blob/8f36eaf2d18e91afa7b327eec66a3cd3685dcb87/libs/agno/agno/knowledge/knowledge.py) | insert/ainsert经reader加载，支持upsert/skip_if_exists；Content身份由_build_content_hash产生。该函数对URL/path可纳入名称、描述、metadata、owner | async函数不等于跨进程持久化任务；其content_hash不能直接当作Git blob字节hash |
| [Agno LocalFileSystemTools](https://github.com/agno-agi/agno/blob/8f36eaf2d18e91afa7b327eec66a3cd3685dcb87/libs/agno/agno/tools/local_file_system.py) | 读写工具按开关加入工具列表；check_escape委托Toolkit._check_path。默认读写均启用 | 不能把该工具直接当作只读、Commit固定、hash核验的源码入口；本轮未审计委托函数的全部实现 |

补充一手规范：[Git ls-tree](https://git-scm.com/docs/git-ls-tree)、[Git cat-file](https://git-scm.com/docs/git-cat-file)、[Python AST](https://docs.python.org/3/library/ast.html)、[SQLite transaction](https://www.sqlite.org/lang_transaction.html)。Git按对象读取；AST行号从1开始、列为UTF-8字节偏移；SQLite只允许一个同时写事务，BEGIN IMMEDIATE可能返回BUSY。这些是约束，不是容量实验。

证据复核：`rg -n 'WalkBuilder|pub limit|cancel_flag|parse_arguments|def _build_content_hash|skip_if_exists|def check_escape' docs/validation/prd005a-design-baseline/sources/*`。来源目标2个官方项目/至少4个文件，本轮2/4（100%）；本轮新增来源快照0→4。未对两项目进行性能比较。

## 3. 当前项目的具体接缝

| 文件 | 现状证据 | 本项目建议/推断 |
|---|---|---|
| persistence/sqlite_database.py | SCHEMA_VERSION=1；initialize仅执行SCHEMA_V1并记版本；transaction使用BEGIN IMMEDIATE；busy_timeout=5000 | 复用连接与事务，新建有顺序、有失败回滚的迁移执行器；不能只改版本常量 |
| worker/runtime/context.py、messages.py | task_results仅summary/evidence_refs进入模型 | 新增显式SourceContextSlice，由Loop验证后交ContextBuilder；不是把所有result序列化进摘要 |
| tools/manifest.py、worker/execution/tool_executor.py | ToolExecutionResult能携带一个Evidence；普通结果默认str截断至1000字符 | 源码工具显式返回受控摘要、引用及结构化选择信息；禁止让隐式摘要决定源码披露范围 |
| persistence/sqlite_stores.py | SQLiteEvidenceStore保存content_ref与metadata，get按ID读取 | 原始字节由地图blob存储回读；SourceEvidenceService在取Evidence后重新校验Incident、snapshot和hash |
| tracing/telemetry.py | 自定义TraceContext字段写attributes；start_as_current_span未恢复序列化OTel父上下文；parent_request_id可作为导出父ID后备 | 增加inject/extract和显式flush；标准span父ID不使用request_id代替；构建没有Session也应可追踪 |
| memory/projection.py | allowed_turns仍取完整输入；LLM路径调用RuleMemoryProjector._item | 历史A1/A2代码模式仍存在，5A.3之前隔离修复并复测；不是本轮重新跑过漏洞Case |

复核命令：`rg -n 'SCHEMA_VERSION|executescript|BEGIN IMMEDIATE|busy_timeout|task_results|source_context|start_as_current_span|parent_request_id|allowed_turns|RuleMemoryProjector._item' src/antisentinel/{persistence/sqlite_database.py,worker/runtime/context.py,worker/runtime/messages.py,tracing/telemetry.py,memory/projection.py}`。

3类接缝（迁移、源码披露、跨进程追踪）目前缺口3，目标0；尚未改代码，当前仍3/3待处理。A1/A2/A3共3条历史审计项保留，不因363项回归全绿而注销。

## 4. 推荐与备选

推荐同机独立code-map服务进程，内部调度器+单Worker，SQLite作为任务与快照唯一事实源；解析在可终止子进程中运行，数据库写事务只覆盖短暂状态变更/分批暂存/发布。变化文件缓存AST事实，首期全量重新解析关系。

备选B增加Redis作为任务派送，仍需SQLite意图和发布防护，增加1个派送依赖及跨存储恢复路径；备选C使用外部索引服务，新增1个服务及其适配层，首期仍须自建版本/Evidence映射。三方案的性能与运维成本实测均N/A；推荐A的依据是当前单机、并发1与已有SQLite，不是预写的性能优势。

首期不用BM25，不声称RAG优化成功；精确符号与路径查询按固定fixture验收。完整RAG评测留给PRD-005。
