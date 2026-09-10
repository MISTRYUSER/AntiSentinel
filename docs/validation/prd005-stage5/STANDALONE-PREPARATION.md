# Standalone 准备与环境阻塞（2026-09-10）

后续用户释放空间，恢复过程及真实Case已完成，见[Standalone真实验证](STANDALONE-VERIFIED.md)。本文保留当时的失败与准备记录，不代表最新阻塞状态。

**真实Standalone验收尚未运行。** 已准备配置、连接鉴权和验收脚本；镜像下载遇到磁盘不足与Docker I/O错误，停止继续下载。图策略保持用户指定pending，默认hybrid未改。

## 已完成的准备

依据[官方安装说明](https://milvus.io/docs/install_standalone-docker-compose.md)与[v3.0.1 Compose](https://github.com/milvus-io/milvus/releases/download/v3.0.1/milvus-standalone-docker-compose.yml)，新增deploy/milvus独立验收栈：Milvus v3.0.1、etcd v3.5.25、MinIO RELEASE.2024-12-18T13-15-44Z。仅本机RPC/health端口映射，内部依赖不映射宿主机端口，启用鉴权，随机对象存储凭证放Case目录0600文件。

MilvusAdapter新增token和连接timeout透传，应用配置支持ANTISENTINEL_CODE_RETRIEVAL_MILVUS_TOKEN。已有生命周期Case可连接显式服务器URI。run_standalone_case.py准备验证实际版本、错误凭证、真实常驻投影/Session/Evidence、停服连接失败、服务器重启和数据恢复；只使用合成源码与本地模型替身。这些远程服务路径尚未通过真实Case，不能标为生产接入已验收。

服务CPU上限4、内存上限8g。Docker VM观测8CPU、8321994752bytes内存，低于完整生产资源承诺加配套开销；未做50GiB SSD、容量或吞吐验收。[官方资源说明](https://milvus.io/docs/prerequisite-docker.md)要求Standalone至少8G，资源上限不能替代实测容量。

## 验证与失败证据

| 项目 | 基线 | 实际 | 目标 | 证据 | 结果 |
|---|---|---|---|---|---|
| Compose语法 | 无独立栈 | config --quiet退出0 | 退出0 | 本轮命令 | 通过 |
| 连接/恢复相关测试 | 既有回归 | 40 passed，8.24s；25 passed，6.63s | 0失败 | pytest | 通过 |
| 全量回归 | 693通过 | 694 passed，0 skipped，0 failed，55.55s | 原测试无失败 | pytest-full.log | 通过 |
| 追加磁盘预检测试 | 未覆盖 | 3 passed，0.10s | 低空间不触碰Docker | pytest | 通过 |
| 镜像下载 | 本轮缺3镜像 | etcd/MinIO完成，Milvus未完成 | 全完成 | pull-retry1.log | 未通过 |
| 本地磁盘预检 | 下载前漏测 | 约2.17GiB空闲 | 本地下载余量≥10GiB | preflight-final.json | 未通过 |
| Standalone业务Case | 0 | 0 | 1次完整Case | 无runtime-case报告 | 未运行 |

首次下载预算600秒超时；随后仅重试Milvus主镜像，预算300秒。重试日志显示程序层提交失败：containerd sync input/output error；写重试摘要又收到宿主机ENOSPC。检查时宿主机只剩601MiB，随后回升至约2.1–2.2GiB；Docker Engine返回unable to start。未声称Docker已修复。

下载前遗漏磁盘余量预检。已新增scripts/preflight_standalone.py，并将其设为操作说明中的pull/up前置步骤：低于10GiB本地余量立即失败，不继续访问Docker；余量满足后再查Engine、CPU、内存。此10GiB只是本地下载保护值，不是生产50GiB容量验收，也不能替代VM内部磁盘核验。

未执行全局prune、Docker数据重置或删除其他项目。Daemon不可用，无法安全通过Docker清理单个未完成镜像层；不直接修改Docker虚拟磁盘。Compose栈尚未up，没有启动本轮业务服务或改变其root凭证，只有本轮新建私有env文件。

## 产物与恢复条件

根目录：`/Users/xuewentao/.local/share/antisentinel/cases/milvus-standalone-_8k570ob`。包含control.json、私有case.env（勿分享）、官方Compose副本与hash、pull-attempt1.json、pull-retry1.log、blocked.json、preflight-final.json、pytest-full.log。重试摘要写入曾失败，日志与blocked.json补记了事实，没有补造成功输出。

恢复前先释放磁盘空间并恢复Docker Engine，再运行：

```sh
python scripts/preflight_standalone.py --data-root /Users/xuewentao/.local/share/antisentinel/cases/milvus-standalone-_8k570ob
```

预检未通过不得继续pull/up。通过后使用该control.json中唯一project和env文件恢复镜像下载、记录实际镜像digest、启动服务，再执行run_standalone_case.py。操作方法见deploy/milvus/README.md。当前没有固定已拉取的Milvus镜像digest，也没有伪造server版本、鉴权、重启或容量通过结论。

本轮外部模型调用0、业务Case0、下载尝试2。全量694通过发生在补磁盘预检之前，后续3预检测试单独通过，未宣称全量697已运行。最小权限角色、TLS、Standalone容量和真实Flash完整链路继续待验收。
