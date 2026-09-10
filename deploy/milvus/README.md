# Milvus Standalone 本地验收栈

这是独立的验收Compose，不会启动或替换主应用。Milvus版本标签固定v3.0.1，etcd为v3.5.25，MinIO为RELEASE.2024-12-18T13-15-44Z，依据[官方v3.0.1 Compose](https://github.com/milvus-io/milvus/releases/download/v3.0.1/milvus-standalone-docker-compose.yml)。不是Distributed。

使用唯一Compose project name隔离容器、网络和命名卷。只有Milvus的RPC/health端口映射到127.0.0.1；etcd/MinIO不暴露宿主机端口。server配置启用鉴权。对象存储用户名/密码必须通过私有env文件提供，不使用仓库内默认密码。

必需环境：MILVUS_OBJECT_USER、MILVUS_OBJECT_PASSWORD。可选：MILVUS_CASE_PORT（19530）、MILVUS_CASE_HEALTH_PORT（9091）、MILVUS_MEMORY_LIMIT（8g）。CPU上限4，内存上限不代表宿主机实际可供资源，更不代表容量验收通过。[官方资源要求](https://milvus.io/docs/prerequisite-docker.md)要求单机8G，推荐16G，部署前需要单独核对。

```sh
python scripts/preflight_standalone.py --data-root <case-data-root>
docker compose --project-name <unique-case-project> --env-file <private-env-file> -f deploy/milvus/compose.yaml config --quiet
docker compose --project-name <unique-case-project> --env-file <private-env-file> -f deploy/milvus/compose.yaml pull
docker compose --project-name <unique-case-project> --env-file <private-env-file> -f deploy/milvus/compose.yaml up -d --wait --wait-timeout 180
```

预检失败时停止，不执行后续pull/up。默认至少保留10GiB宿主机空闲空间作为本地下载余量，同时检查Docker Engine、CPU和VM内存；这个余量不等于PRD的50GiB生产SSD容量目标，也不能替代Docker VM内部磁盘检查。

不要将含凭证的`config`完整展开结果粘贴到日志；使用`config --quiet`。私有env文件权限应为0600。

## 正确性 Case

`scripts/run_standalone_case.py --control <case-root>/control.json`只针对明确准备的本地空验收实例，固定RPC29530/health29091及antisentinel-milvus-前缀project。control.json需要project、compose绝对路径、env_file绝对路径、uri=http://127.0.0.1:29530。

Case检查固定发行镜像、release commit及空collection列表。该v3.0.1镜像的RPC版本字符串实际为`3.0-20260902-658cbd1689`，与官方tag commit对应，不能直接比较成字符串`3.0.1`。Case根目录还需经过核验的image-receipt.json与release-tag.json；本轮来源/内容核验见[验证记录](../../docs/validation/prd005-stage5/STANDALONE-VERIFIED.md)。脚本固定本轮arm64来源，不接受任意版本。

Case将该空实例初始root凭证改为随机值，写入0600的milvus-token，绝不输出。参考[官方鉴权](https://milvus.io/docs/authenticate.md)与[用户管理](https://milvus.io/docs/users_and_roles.md)。此root仅用于独立验收，不是生产最小权限方案。如果仅bootstrap阶段中断、还未创建runtime-case，可显式使用`--resume-bootstrap`复用已有凭证；不会再次重置密码。

随后检查错误凭证拒绝、真实SQLite/Milvus投影、正常Session检索/Evidence、停止服务器时连接失败，以及启动同一服务器后的数据恢复和不重复编码。业务代码/Embedding用确定性本地替身，无外部模型调用。服务会被Case停启，不能指向用户既有实例；脚本拒绝重复初始化已有Case凭证。

重启后先直接核验服务器中的point ID及完整来源/版本字段，再启动恢复Worker，防止自动补写掩盖持久化丢失。SDK的UNAUTHENTICATED可能位于异常链内，不能只匹配最外层错误字符串。

应用连接启用鉴权的Milvus时，可配置ANTISENTINEL_CODE_RETRIEVAL_MILVUS_TOKEN；它只传给SDK，连接与RPC均使用既有10秒超时。是否启用检索、仓库白名单和语料外发范围仍按既有配置管理。

完成后只停止本Case project，保留卷和报告以便复核；不要运行全局docker prune或删除其他项目卷。生产还需要镜像digest记录/固定、最小权限、TLS/网络边界、资源/磁盘与容量、真实Flash链路验收。本文件不表示这些已经通过。
