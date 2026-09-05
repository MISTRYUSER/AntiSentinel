# PRD-002 工具动态发现调研

## 结论

目录动态扫描适合作为工具发现机制，但不应让 `ToolRegistry` 直接承担模块导入、依赖加载和运行时刷新。推荐拆成：

```text
ToolDiscovery（扫描/导入/提取）
        ↓
ToolRegistry（名称索引/重复检测/manifest）
        ↓
ToolExecutor（校验/审批/执行）
        ↓
RuntimeLoop（领域对象、事件、checkpoint 和模型回灌）
```

工具模块使用显式导出约定：单个工具导出 `TOOL`，多个工具导出 `TOOLS`。启动时扫描一次并构造 registry；运行过程中不隐式重新扫描。这样模型在一轮开始时拿到稳定的工具清单，也避免导入副作用影响正在执行的 Session。

## Agno 的实现方式

Agno 的 `Toolkit` 接收显式的 callable 或 `Function` 列表，并在初始化时依据 `auto_register` 自动调用 `register()`；工具还携带 JSON Schema 参数、确认要求、外部执行、缓存、超时和展示策略等元数据。[Agno Toolkit 源码](https://github.com/agno-agi/agno/blob/main/libs/agno/agno/tools/toolkit.py#L1386-L1546)

Agno 的 `Function` 是模型可见工具和实际 entrypoint 的封装，包含名称、描述、参数 JSON Schema、entrypoint、确认和外部执行等字段。[Agno Function 源码](https://github.com/agno-agi/agno/blob/main/libs/agno/agno/tools/function.py)

这说明“显式工具定义 + 自动注册 + 丰富元数据”是成熟做法；但 Agno 的自动注册发生在 Toolkit 构造阶段，而不是盲目扫描所有 Python 文件。

Agno 还有一个与本项目相关的公开问题：执行循环开始前建立的 `_functions` 不会看到执行期间通过 `add_tool()` 新增的工具，建议是在每轮重建函数清单。[Agno 动态工具问题](https://github.com/agno-agi/agno/issues/6299)

对 AntiSentinel 的启示是：启动扫描可以自动注册；如果以后支持运行中加载工具，需要显式的 `refresh()` 或“下一轮生效”规则，不能让当前请求隐式改变工具集合。

## Codex 的实现方式

Codex 使用显式的 `ToolRegistry` 保存 `RegisteredTool`，内部按 `ToolName` 建立有序索引；受信任工具通过 `add()`/`register_trusted()` 加入，外部工具通过 `register_external()` 加入。[Codex ToolRegistry 源码](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/registry.rs#L2315-L2498)

Codex 对未知工具先返回可回灌模型的错误，而不是执行；执行前还会进行工具类型匹配、pre-tool hooks 和权限/沙箱相关处理。[Codex 工具分发源码](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/registry.rs#L2676-L2908)

Codex 对重复外部工具不会覆盖已有注册，而是跳过并记录第一次 collision；受信任工具重复注册则直接报错。[Codex 重复注册源码](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/registry.rs#L2374-L2498)

对 AntiSentinel 的启示是：工具名称必须是稳定的索引键，重复名称不能静默覆盖；未知工具和不可执行工具需要结构化反馈；工具的 exposure、审批和权限元数据应保留在定义中。

## 本项目的设计决策

- `ToolDiscovery` 负责 `pkgutil.iter_modules()`、`importlib.import_module()` 和 `TOOL/TOOLS` 提取。
- `ToolRegistry` 只负责注册、解析、manifest 和 schema 校验。
- 发现过程按模块名排序，保证 manifest 稳定。
- 模块导入失败、导出类型错误、重复工具名都显式报错并包含模块名。
- 扫描默认只发生在启动/组装阶段；RuntimeLoop 不在每次 ToolCall 时扫描文件系统。
- 测试通过临时 package 或可注入 package name 验证发现逻辑，不依赖修改正式 tools 目录。
- 后续若需要运行中加载，新增显式 `registry.refresh()`，并规定从下一轮模型调用开始生效。
