# 候选支持性判别 Case（待确认新服务目的地）

## 取消客户端输出上限后的续跑

用户明确要求移除tokens上限。v2请求省略max_tokens，使用服务端默认值；这不代表服务端无限输出。沿用冻结prompt、temperature0、30秒单请求和600秒总体预算，无自动重试。保留前5条v1有效结果，首先重跑q06，再顺序继续。

q06返回1230 completion tokens、finish_reason=stop，JSON与引用校验通过，原1024截断问题解除。v2执行10请求，其中9有效；q15模型返回supported，但152字符的第一条引用不在披露文本中，去除空白后也不匹配；另一条684字符引用有效。严格验证拒绝整条q15，错误invalid disclosed quote，停止后续请求。未自动丢弃坏引用来制造通过。

v2耗时37256.53ms，prompt40093/completion5803/total45896 tokens，重试0。与v1累计16请求（含q06重跑），14个不同查询有效；48查询尚有34个无有效结果，不能计算完整质量指标。两轮累计90292 tokens，费用N/A，实际响应model仍deepseek-v4-flash。

v2原始记录和report位于私有语料根目录support-judge-run-v2，execution_pass=false。客户端输出限制已按要求移除，引用真实性错误仍保持失败；生产检索和默认拒答策略未修改。

## 2026-09-08 首次真实运行：失败后停止

用户明确“运行”后，向已披露的DeepSeek目的地执行冻结requests.jsonl。请求模型deepseek-chat；六个响应的实际model字段均为deepseek-v4-flash，分别保存requested/returned身份，不将别名假定为不可变模型版本。

共执行6/48请求，5个合法supported判别，第6个q06响应finish_reason=length，completion_tokens=1024，JSON被截断，因此未作为合法判别接受。按既定Case首次错误停止，剩余42条未执行，重试0。错误不是无答案，未生成整体Recall/MRR/误命中率。前5条均为精确查询，不能据此判断无答案优化效果。

运行耗时19155.57ms；服务报告prompt_tokens=41476、completion_tokens=2920、total_tokens=44396；费用N/A。5个合法响应共有9条引用，均已通过document_id和披露原文精确匹配；语义正确性不由字符串匹配自动证明。第6条原始内容2927字符，出现3个quote字段但JSON未闭合。直接原因是1024输出tokens上限；协议没有限制引用数量/长度，使输出预算控制不足。

产物在 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-baidu-source-poitl0bv/support-judge-run-v1`：responses.jsonl（逐请求原始内容、usage、finish_reason、校验结果）、report.json（execution_pass=false、valid=5、errors=1、attempted=6、planned=48）。没有自动重试、增加输出上限或改prompt继续。

后续修复方向：为引用数量、单条引用长度和reason长度设置明确预算；冻结新prompt版本，先对同一q06失败输入验证，再继续其他查询。不能把本次错误样本排除，也不能直接将max_tokens放大后与v1当作同一实验。当前阶段真实运行未通过，生产检索默认行为不变，Ragas答案指标仍未执行。

## 原始准备记录

2026-09-08。已完成输入准备与协议测试，尚未调用判别模型，不能声称拒答优化成功。

## 范围与证据边界

现有环境的文本模型为deepseek-chat，目的地api.deepseek.com；前一阶段公司源码发送授权针对阿里云Embedding服务。待用户确认后才向这个新目的地发送当前选定仓库的查询和候选文本。

当前48条查询（原开发24+已观察的留出24）全部作为本轮诊断集。不得继续将已观察过的后24条宣称为新的盲测集；后续选定策略后需要另外冻结查询进行验证。当前只判别前5个检索候选是否满足查询，不能证明整个仓库不存在功能。

协议输出supported/not_supported/insufficient_evidence、理由、document_id与原文引用。模型输入不含relevant_ids、rationale、正确答案或查询类别。JSON协议严格校验；supported至少一个引用且所有引用必须来自披露文本。引用存在只能证明可追溯，不能机械证明模型的语义判断正确，仍需与标签和人工review对照。

源码与查询标明为不可信数据，不能覆盖判别指令。每候选最多8192 UTF-8字节、每查询源码最多32768字节；保存截断标记。有截断时not_supported不合法，应返回insufficient_evidence。未知、格式错误或请求失败不计作正确拒答；默认不改变生产检索。

## 已准备产物

目录均位于 `/var/folders/82/x2z0kfxd1cl4vxwz40r4dbrw0000gn/T/antisentinel-baidu-source-poitl0bv`：

- support-judge-development-v1：24查询、120候选出现次数、189693源码字节、9查询截断。
- support-judge-heldout-v1：24查询、120候选出现次数、119623源码字节、5查询截断。目录名沿用输入来源，当前不再是未观察盲测。
- 每目录packets.json、requests.jsonl、preparation.json，共6文件。请求体已可review，合计309316源码字节（含重复），不含提示词/JSON元数据字节；token与成本N/A，未调用服务。
- prompt SHA256：f9ca97d62fbe9250420902f21f2ec054025c6d755b7273f8903d3a297cfa43b9。

可复现命令：

```sh
python scripts/prepare_code_support_judge.py --repository <repo> \
  --manifest <manifest.json> --retrieval-rows <development-raw.json或heldout-raw.json> \
  --output <不存在的新目录>
python -m pytest tests/test_code_support_judge.py tests/test_code_abstention.py tests/test_code_retrieval_evaluation.py -q
```

新测试首次collection error1（模块未实现）；实现后22 passed、0 failed、1.26s。新增4项支持性判别协议测试。本轮仅新增输入准备、响应验证与测试3个文件，不改生产检索，不运行远程模型。

## 拟运行与验收

拟使用现有deepseek-chat配置逐查询判别，一次最多48请求，每请求输出上限1024 tokens、超时30秒、自动重试0；整体预算600秒（48次生成调用不同于此前Embedding低延迟批处理，超过原120秒预算需以本Case明确记录）。任何服务/解析错误保留原始脱敏诊断并停止扩展，不自动改提示词重跑刷分。

三个执行成功指标：48个请求均有可核验记录、supported引用原文一致率100%、所有判别都能绑定冻结query/candidate身份。失败指标：无效引用、格式错误、请求错误不得伪装成no-answer。质量指标：无答案误接受应下降，同时Recall/MRR相对基线下降≤5%，单独报告有答案误拒绝、unknown比例与覆盖率，不从分母删除难例。任何指标缺失或不达标均不启用策略。延迟、输入输出tokens与成本按实际报告；有请求前为N/A。

这不是Ragas Faithfulness或答案生成Case，本轮不借判别模型输出冒充真实用户答案。待支持性判别证明有效，再设计真实答案与Ragas答案评估。
