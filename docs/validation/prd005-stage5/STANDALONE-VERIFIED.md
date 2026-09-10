# Standalone 真实服务验证（2026-09-10）

**本轮Standalone基础正确性Case通过；生产容量、最小权限/TLS及真实Flash链路仍未验收。** 图策略继续pending，默认hybrid未变。

## 镜像、版本和环境

用户释放空间后，预检观测40405377024bytes空闲，Docker29.4.1恢复，8CPU/8321994752bytes VM内存。原生拉取仍在旧未完成层遇到digest不一致；没有绕过校验或全局prune。独立BuildKit缓存因容器访问Docker Hub鉴权端点超时失败，临时builder随后移除。

改由宿主机从官方registry取得arm64镜像，逐层校验压缩digest及官方config内的解压diff ID，10/10层通过后构造Docker archive导入。最后空层因匿名凭证超时返回401，刷新凭证后复核已有9层并补齐，未重新下载大层或跳过校验。

- 发行标签：[Milvus v3.0.1](https://github.com/milvus-io/milvus/releases/tag/v3.0.1)。
- 官方tag commit：`658cbd16899bb715a17d4d6f727531a376678ca6`，原始GitHub响应保存在release-tag.json。
- arm64源manifest：`sha256:d0f1645d57e341701f80b92b8d455017c739db89732a5ec3ffb3ba605dee13cd`。
- 源config：`sha256:f78faf2b0d62f0c5dd57e107856544b1a98ee61545c0cd3af29c5f1bdc910709`。
- 本地导入描述符/镜像ID：`sha256:dbc112bfcf4353721b4c70c2db7c2e2c25a9d007f5a43a8b32f01af5f9bb0544`。它不是源config digest；导入后的运行Config及10个RootFS diff ID与官方源逐项相同，见image-receipt.json。
- 实际RPC返回：`3.0-20260902-658cbd1689`，与tag commit对应，未改写为另一个版本号。

以`up --pull never --wait`使用已核验镜像；etcd/MinIO/Standalone三容器健康。仅本机29530/29091端口，鉴权开启，数据和网络使用唯一Compose project。资源基于[官方要求](https://milvus.io/docs/prerequisite-docker.md)，服务上限4CPU/8g；VM余量不足以承诺完整生产资源，50GiB SSD/容量尚未验证。

## 真实 Case 与发现

执行入口：

```sh
python scripts/run_standalone_case.py --control <case-root>/control.json
```

业务Case预算120秒。两次bootstrap校验技术失败均保留：首次错误地把RPC构建号直接比为3.0.1；第二次只检查SDK最外层字符串，漏识别异常链中的UNAUTHENTICATED。核对官方tag后固定镜像/commit/精确构建号，并增加异常链识别。显式`--resume-bootstrap`复用已生成的测试凭证，没有重置密码或测试数据；技术重试2次后完成第一个完整Case。

### 鉴权、停服及服务器重启

外层6检查和正常应用12检查全部通过：

- 正确凭证连接成功，错误凭证确认为UNAUTHENTICATED；网络UNAVAILABLE不会被当作鉴权拒绝。
- 正常应用Session通过常驻Worker完成投影、hybrid检索、Evidence披露与最终引用。
- 停止Standalone后连接失败，探测10.90ms，满足本Case≤5s边界；这不是黑洞网络超时或应用降级验收。
- 启动同一服务器后，在恢复Worker运行前，直接核验2个point ID及其文档、Scope、hash、模型/模板/projection字段，全部保留，避免自动修复掩盖数据丢失。
- 恢复客户端后检索/Evidence回读成功，编码数量2→0。

输入1文件52bytes、2chunks；2条任务/向量，1条持久Evidence/最终引用，后台异常0，外部模型调用0。核心Case15484.70ms，业务/持久化读回观察差1.92ms。这是干净停启，不替代服务kill、容器网络故障、多进程或容量测试。

### Strong读取修正

审计发现原Case有一次index_reconciliation重试，但旧runner把retries固定写成0。保留原始report，在original-retry-audit.json明确记录实际1次，不掩盖错误。

适配器search原先使用Strong，但get/reconcile没有显式指定；已为这两个读取补上Strong，避免陈旧读取被视为缺失向量。runner改为从持久attempt计算重试数。

以新collection `lifecycle_strong`和新SQLite文件再次运行真实Standalone生命周期Case，未删除原Case数据：12/12检查通过，2任务均attempt=1，真实重试0，编码2→0，1Evidence/引用，5003.64ms，业务/读回观察差2.33ms。该补充Case只重开客户端，不重复服务器重启；两次耗时不能直接比较为加速。

## 验收摘要

| 指标 | 基线 | 本次 | 门槛 | 变化 | 证据 | 结果 |
|---|---:|---:|---:|---:|---|---|
| Standalone完整Case | 0 | 1 + Strong补充1 | 完整通过 | +2 | standalone-report及两runtime报告 | 通过 |
| 服务器重启前后point | 2 | 2 | 身份/字段相同 | 0丢失 | persisted_before_worker_repair | 通过 |
| Strong新任务重试 | 原Case实际1 | 0 | 0 | -1 | runtime-strong-case/facts.sqlite | 通过 |
| Evidence恢复 | 1 | 1 | hash/引用匹配 | 0丢失 | runtime报告 | 通过 |
| 外部模型调用 | 0 | 0 | 0 | 0 | 本地替身 | 通过 |
| 最终回归 | 此前完整694 | 701通过/0跳过/0失败 | 原用例无失败 | +7 | pytest-strong-final.log | 通过 |
| 容量/生产就绪 | 未验收 | 未验收 | 独立容量与真实链路验证 | N/A | production_capacity_verified=false | 未通过 |

针对性12通过；鉴权链与连接相关15通过/3.09s；Strong修正后44通过/7.12s。中间全量700通过/66.64s，最终701 passed、7 warnings、40.03s。不同测试集合/负载不能当作性能改善证明。

## 产物、清理与剩余项

根目录：`/Users/xuewentao/.local/share/antisentinel/cases/milvus-standalone-_8k570ob`。重点包括standalone-report.json、runtime-case/report.json、runtime-strong-case/report.json、original-retry-audit.json、image-receipt.json、verified-image/proof.json/source-manifest.json、release-tag.json、container/cleanup/final-state和完整pytest日志。原失败及恢复记录均保留；不要分享私有case.env或milvus-token。

本轮测试栈已停止，运行容器0，命名卷/数据库保留。只删除本轮创建的约6196828160bytes下载临时tar文件，未清理其他项目，最终空闲约25.4GB（23.66GiB）。临时builder已移除；校验记录和源manifest保留。

后续仍需生产最小权限角色、TLS/网络边界、镜像在生产平台的固定与部署、容量/并发/磁盘、真实Flash常驻Worker与真实答案评测。未标整体PRD Done或用户review通过。
