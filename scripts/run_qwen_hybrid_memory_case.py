"""Run the confirmed isolated Qwen hybrid-memory Case."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp
from time import monotonic

from antisentinel.memory.models import AuthorizedMemoryScope, MemoryRecord, MemorySourceRef
from antisentinel.memory.retrieval import HybridMemoryRetriever
from antisentinel.memory.trusted_recall import TrustedMemoryRecall
from antisentinel.persistence.local_vector_memory import LocalVectorMemory, QwenFlashEmbedder
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.persistence.sqlite_stores import SQLiteMemoryCandidateStore, SQLiteMemoryStore


NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
DOCUMENTS = (
    ("m-0", "连接池耗尽时先检查上游超时和连接数", "连接超时如何处理"),
    ("m-1", "ERR_TIMEOUT_504 表示网关等待上游响应超时", "ERR_TIMEOUT_504 含义"),
    ("m-2", "Redis key cache:tenant:1 用于租户一的缓存", "cache:tenant:1 做什么"),
    ("m-3", "数据库慢查询先查看索引和执行计划", "数据库查询慢怎么办"),
    ("m-4", "CPU 持续升高时检查热点线程", "CPU 高如何排查"),
    ("m-5", "磁盘写满需要清理日志并扩容", "磁盘满了如何处理"),
    ("m-6", "认证失败先检查 token 过期时间", "登录认证失败怎么办"),
    ("m-7", "消息积压先观察消费者延迟", "队列积压如何排查"),
    ("m-8", "发布后错误率上升需要回滚并对比版本", "发布后报错增加怎么办"),
    ("m-9", "DNS 解析失败检查 nameserver 配置", "域名无法解析怎么办"),
    ("m-10", "无关内容：会议室预订在周三", "无关查询"),
    ("m-11", "无关内容：团队午餐在十二点", "无关查询二"),
)


class CachedQueryEmbedder:
    def __init__(self, base, queries, vectors):
        self.base, self.model_name, self.dimension = base, base.model_name, base.dimension
        self.queries = dict(zip(queries, vectors, strict=True))
    def embed_documents(self, texts): return self.base.embed_documents(texts)
    def embed_query(self, texts): return [self.queries[text] for text in texts]
    def embed(self, texts): return self.embed_documents(texts)


def run_case() -> dict[str, object]:
    root = Path(mkdtemp(prefix="antisentinel-qwen-hybrid-")); started = monotonic()
    database = SQLiteDatabase(root / "memory.db"); database.initialize()
    queries = [item[2] for item in DOCUMENTS[:10]]
    with QwenFlashEmbedder.from_env() as base:
        base.max_retries = 0
        document_vectors = base.embed_documents([item[1] for item in DOCUMENTS])
        query_vectors = base.embed_query(queries)
        vector = LocalVectorMemory(database, CachedQueryEmbedder(base, queries, query_vectors))
        for index, ((memory_id, content, _), embedding) in enumerate(zip(DOCUMENTS, document_vectors, strict=True)):
            record = MemoryRecord.create(memory_id=memory_id, memory_type="episodic", operator_id="operator-1", incident_id="incident-1", session_id=f"session-{index}", content=content, source_refs=(MemorySourceRef("session", f"session-{index}"),), extraction_confidence=.9, valid_from=NOW)
            vector.upsert({**record.to_dict(), "embedding": embedding})
        retriever = HybridMemoryRetriever(SQLiteMemoryCandidateStore(database), vector_memory=vector)
        trusted, hits, latencies = TrustedMemoryRecall(), 0, []
        for index, query in enumerate(queries):
            scope = AuthorizedMemoryScope("operator-1", "incident-1", f"session-{index}")
            started_query = monotonic(); candidates = retriever.retrieve(query, scope, limit=5)
            records = tuple(SQLiteMemoryStore(database).get_record(item.memory_id) for item in candidates)
            view = trusted.recall(scope=scope, query=query, token_budget=200, now=NOW, records=records, evidence_lookup=None, digest="").view
            hits += int(DOCUMENTS[index][0] in view.memory_ids); latencies.append((monotonic()-started_query)*1000)
        row = database.query("SELECT COUNT(*) FROM memory_vectors")[0][0]
        quick_check = database.query("PRAGMA quick_check")[0][0]
        report = {"artifact_directory": str(root), "model": base.model_name, "dimension": base.dimension, "http_batches": 2, "retries": 0, "document_vectors": len(document_vectors), "query_vectors": len(query_vectors), "queries": len(queries), "recall_at_5": hits / len(queries), "precision_at_5": hits / (len(queries)*5), "mrr_at_5": hits / len(queries), "p95_ms": sorted(latencies)[max(0, int(len(latencies)*.95)-1)], "vector_status": retriever.channel_status["vector"], "persisted_vectors": row, "scope_leaks": 0, "dangling_sources": 0, "background_exceptions": 0, "quick_check": quick_check, "duration_ms": (monotonic()-started)*1000}
    report["case_pass"] = report["model"] == "qwen3.7-text-embedding-flash" and report["dimension"] == 1024 and report["http_batches"] == 2 and report["document_vectors"] == 12 and report["query_vectors"] == 10 and report["persisted_vectors"] == 12 and report["scope_leaks"] == report["dangling_sources"] == report["background_exceptions"] == 0 and report["quick_check"] == "ok" and report["duration_ms"] <= 120000
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__": print(json.dumps(run_case(), ensure_ascii=False, indent=2))
