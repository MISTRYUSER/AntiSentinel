"""Batch Qwen vector/RRF evaluation on isolated LongMemEval-S storage."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
import re
from tempfile import mkdtemp
from time import monotonic

from antisentinel.evaluation.longmemeval import LongMemEvalLoader
from antisentinel.memory.models import AuthorizedMemoryScope, MemoryRecord, MemorySourceRef
from antisentinel.memory.retrieval import HybridMemoryRetriever
from antisentinel.memory.trusted_recall import TrustedMemoryRecall
from antisentinel.persistence.local_vector_memory import LocalVectorMemory, QwenFlashEmbedder
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.persistence.sqlite_stores import SQLiteMemoryCandidateStore, SQLiteMemoryStore
from antisentinel.memory.vector_projection import project_session, projection_batches


def evaluation_time(value: str) -> datetime:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value): return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    matched = re.match(r"(\d{4})[-/](\d{2})[-/](\d{2})(?:\s+\([A-Za-z]+\))?\s+(\d{2}):(\d{2})", value)
    if matched is None: raise ValueError(f"unsupported evaluation date: {value}")
    return datetime(int(matched.group(1)), int(matched.group(2)), int(matched.group(3)), int(matched.group(4)), int(matched.group(5)), tzinfo=timezone.utc)


class CachedQueryEmbedder:
    def __init__(self, base, query_vectors: dict[str, list[float]]) -> None:
        self.base, self.model_name, self.dimension = base, base.model_name, base.dimension
        self.query_vectors = query_vectors
    def embed_documents(self, texts): return self.base.embed_documents(texts)
    def embed_query(self, texts): return [self.query_vectors[text] for text in texts]
    def embed(self, texts): return self.embed_documents(texts)


def chunks(values, size=20):
    for index in range(0, len(values), size): yield values[index:index + size]


def embedding_batches(entries, *, max_items=20, max_chars=60000):
    batch, used = [], 0
    for entry in entries:
        text = entry[0][:24000]
        if batch and (len(batch) >= max_items or used + len(text) > max_chars):
            yield batch; batch, used = [], 0
        batch.append((text, entry[1])); used += len(text)
    if batch: yield batch


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    root = args.root or Path(mkdtemp(prefix="antisentinel-qwen-longmemeval-")); root.mkdir(parents=True, exist_ok=True)
    cases = [case for case in LongMemEvalLoader(args.data) if not case.question_id.endswith("_abs")]
    if args.limit: cases = cases[:args.limit]
    docs, metadata = [], []
    for case in cases:
        now = evaluation_time(case.question_date)
        for index, session in enumerate(case.sessions):
            for block_index, block in enumerate(project_session(session)):
                docs.append(block.content); metadata.append((case.question_id, index, block_index, session.session_id, now, block.turn_ids, block.truncated))
    db = SQLiteDatabase(root / "memory.db"); db.initialize(); memory = SQLiteMemoryStore(db)
    started = monotonic()
    with QwenFlashEmbedder.from_env() as base:
        document_batches = query_batches = 0
        entries = list(zip(docs, metadata, strict=True))
        for batch_entries in projection_batches(type("Block", (), {"content": text, "metadata": metadata}) for text, metadata in entries):
            batch = [item.content for item in batch_entries]
            vectors = base.embed_documents(batch); document_batches += 1
            for vector, item in zip(vectors, batch_entries, strict=True):
                text, (question_id, index, block_index, session_id, now, turn_ids, truncated) = item.content, item.metadata
                record = MemoryRecord.create(memory_id=f"qwen:{question_id}:{index}:{block_index}", memory_type="session_digest", operator_id="benchmark", incident_id=question_id, session_id=session_id, content=text, source_refs=(MemorySourceRef("session", session_id),), extraction_confidence=.9, valid_from=now)
                LocalVectorMemory(db, base).upsert({**record.to_dict(), "embedding": vector})
            if document_batches % 25 == 0:
                (root / "progress.json").write_text(json.dumps({"document_batches": document_batches, "query_batches": query_batches}, ensure_ascii=False), encoding="utf-8")
        query_vectors = {}
        for batch in chunks([case.question for case in cases]):
            query_vectors.update(zip(batch, base.embed_query(batch), strict=True)); query_batches += 1
        vector = LocalVectorMemory(db, CachedQueryEmbedder(base, query_vectors))
        retriever = HybridMemoryRetriever(SQLiteMemoryCandidateStore(db), vector_memory=vector); trusted = TrustedMemoryRecall()
        rows, latencies = [], []
        for case in cases:
            now = evaluation_time(case.question_date); scope = AuthorizedMemoryScope("benchmark", case.question_id, case.sessions[-1].session_id)
            begin = monotonic(); candidates = retriever.retrieve(case.question, scope, limit=10)
            records = tuple(memory.get_record(item.memory_id) for item in candidates)
            view = trusted.recall(scope=scope, query=case.question, token_budget=100000, now=now, records=records, evidence_lookup=None, digest="").view
            session_ids = tuple(memory.get_record(memory_id).session_id for memory_id in view.memory_ids)
            expected = set(case.answer_session_ids); latency = (monotonic() - begin) * 1000; latencies.append(latency)
            row = {"question_id": case.question_id, "latency_ms": latency, "session_ids": session_ids, "channels": [item.channels for item in candidates]}
            for k in (1, 5, 10):
                selected = session_ids[:k]; hits = len(set(selected) & expected)
                row[f"recall_at_{k}"] = hits / len(expected) if expected else 0.0
                row[f"precision_at_{k}"] = hits / k
                row[f"mrr_at_{k}"] = next((1 / rank for rank, value in enumerate(selected, 1) if value in expected), 0.0)
            rows.append(row)
    ordered = sorted(latencies)
    report = {"artifact_directory": str(root), "dataset_sha256": "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442", "model": "qwen3.7-text-embedding-flash", "dimension": 1024, "cases": len(cases), "documents": len(docs), "document_batches": document_batches, "query_batches": query_batches, "metrics": {metric: sum(row[metric] for row in rows)/len(rows) for k in (1,5,10) for metric in (f"recall_at_{k}", f"precision_at_{k}", f"mrr_at_{k}")}, "latency_ms": {"p50": ordered[(len(ordered)-1)//2], "p95": ordered[max(0, ceil(len(ordered)*.95)-1)]}, "channel_status": retriever.channel_status, "sqlite_quick_check": db.query("PRAGMA quick_check")[0][0], "duration_ms": (monotonic()-started)*1000, "rows": rows}
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key:value for key,value in report.items() if key != "rows"}, ensure_ascii=False))
    return 0


if __name__ == "__main__": raise SystemExit(main())
