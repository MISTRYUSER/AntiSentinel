# 6A.1 代码与样本 review

用户选择先review代码和样本，独立Case未运行。本切片为纯契约，不接应用消息入口，不运行模型或工具。

## 核心文件

- `src/antisentinel/domain/intent.py`：严格JSON契约、来源结构、身份、hash与不可变序列化。
- `src/antisentinel/control/intent_validation.py`：来源核验、字段绑定、上下文指代与确定性路由。
- `tests/fixtures/intents/v1.json`：32条固定候选，标签状态draft_requires_user_review。
- `src/antisentinel/evaluation/intent_contract.py`：固定响应比较，不测模型分类。

## 代表性样本

| 样本 | 用户消息 | 必要上下文 | 期望 |
|---|---|---|---|
| d01 | 排查订单超时，只分析，不要重启 | 当前授权Session | diagnose→plan；保留read_only与forbid_restart |
| d03 | 版本是B | 已有意图版本1，B已验证 | continuation→plan；新revision=2 |
| d04 | Do not change configuration, only analyze | 已有意图版本1 | correction→plan；保留read_only |
| t03 | 取消它 | 2个可访问任务 | clarify，不猜任务 |
| t05 | 取消别人的任务other | 当前只有t1可访问 | clarify，不绑定other，不泄露任务状态 |
| u05 | 解释连接池，再部署另一个服务 | 两个独立目标 | clarify；保留两个候选目标 |

## 评审边界

- 32条由本轮编写，未获用户人工标签验收；不能称人工标注集已冻结验收。
- provenance只核验引用存在与context值匹配，不能以字符串命中证明模型语义提取正确。
- 本切片只接收已解析候选；跨轮目标与约束合并、澄清轮数/到期、真实actor取得和持久化在后续阶段。d03当前目标仍是补充语句，尚不是端到端恢复后的诊断目标。
- ResolvedIntent.from_dict的hash校验不授予执行权限；未来dispatcher必须复核当前授权和revision。
- Case尚未运行，模型效果、性能回归和真实控制验证均N/A。
