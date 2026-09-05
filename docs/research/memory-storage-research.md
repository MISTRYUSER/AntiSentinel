# Memory / Storage 设计参考

## 参考项目事实

- Agno 将 `session_id` 作为对话线程的主键，Session 中保存 runs、消息、元数据和 summary：
  [Session Storage](https://docs.agno.com/database/session-storage)
- Agno 明确区分 Session History 和长期 Memory：前者服务于当前对话连续性，后者保存跨 Session 的用户事实：
  [Memory Overview](https://docs.agno.com/memory/overview)
- Agno 支持独立控制历史消息、工具消息和媒体是否持久化，说明运行上下文与持久化内容应有明确边界：
  [Storage Control](https://docs.agno.com/sessions/persisting-sessions/storage-control)

## 对 AntiSentinel 的建议

- Incident Evidence 是排障真相层，不应被 Session 摘要覆盖。
- Session Tree 是当前工作集，可缓存、可丢失，并必须能从 Event/EvidenceRef 重建。
- Operator Graph 只保存偏好，不保存某次 Incident 的根因事实。
- 第一版用本地 JSONL/JSON 实现 Port，后续再替换 SQLite、Postgres 或 Redis。
- Memory PRD 应先完成存储、回放和范围隔离，再接入 RAG 检索。
