# Qwen Flash 独立真实验证 Case

2026-09-05 执行结果：远端embedding与临时SQLite链路真实运行通过，报告见 [report.json](qwen-flash-20260905/report.json)。模型为qwen3.7-text-embedding-flash、1024维，3条文档向量/3条落盘/3条版本关联，恢复校验成功，总耗时432.02ms。首次沙箱网络失败，获准网络执行后同输入重试1次；成功调用内部重试0。此Case不是检索质量实验，不宣称在线MemoryRecall已接向量检索。

- 输入：3条合成记忆、1条合成查询，真实业务文本0条。
- 依赖：百炼空间对应的OpenAI兼容base URL和API Key；不启动Redis/API服务。
- 持久化：系统新建临时目录及SQLite，正式storage写入0。
- 观测：实际模型、向量维度、记录数、索引版本关联、查询回源、重开数据库恢复、SQLite完整性、耗时。
- 成功阈值：3/3文档向量、1/1查询向量、3/3持久化记录、3/3模型/维度/版本关联、恢复后3条索引。
- 失败阈值：异常0，外部scope命中0，quick_check必须为ok。
- 回归：先执行下方63个针对性测试，失败0。真实P95和语义质量基线N/A，不据单查询推断。
- 预算：120秒，真实API每次超时30秒；本Case禁用自动重试，最多2个API请求。失败保留目录与日志，不扩展数据集。
- 清理：验证后仅清理输出中显示的临时目录；本命令不自动删除产物。

先在本机安全配置`DASHSCOPE_API_KEY`和`ANTISENTINEL_EMBEDDING_BASE_URL`到环境；不把Key放入命令正文。`ANTISENTINEL_EMBEDDING_DIMENSION`默认1024。

```sh
python3 -m pytest -q tests/test_qwen_embedding.py tests/test_memory*.py tests/test_local_memory.py tests/test_local_vector_memory.py tests/test_sqlite_memory_search.py tests/test_operator_graph.py
PYTHONPATH=src python3 - <<'PY'
import json
import tempfile
from pathlib import Path
from time import monotonic
from antisentinel.persistence.local_vector_memory import LocalVectorMemory, QwenFlashEmbedder
from antisentinel.persistence.sqlite_database import SQLiteDatabase

root = Path(tempfile.mkdtemp(prefix="antisentinel-qwen-smoke-"))
print(json.dumps({"artifact_directory": str(root)}), flush=True)
started = monotonic()
db = SQLiteDatabase(root / "memory.db")
db.initialize()
texts = ["排查服务异常时先检查日志", "请求超时可检查上游服务", "缓存命中率反映缓存利用情况"]
with QwenFlashEmbedder.from_env() as embedder:
    embedder.max_retries = 0
    document_vectors = embedder.embed_documents(texts)
    memory = LocalVectorMemory(db, embedder)
    for index, (content, embedding) in enumerate(zip(texts, document_vectors, strict=True)):
        memory.upsert({"memory_id": f"smoke-{index}", "operator_id": "smoke-owner",
                       "incident_id": "smoke-incident", "content": content,
                       "content_version": 1, "embedding": embedding})
    persisted_at = monotonic()
    matches = memory.search("服务异常怎么排查", operator_id="smoke-owner", incident_id="smoke-incident", limit=3)
    reopened = SQLiteDatabase(root / "memory.db")
    counts = reopened.query("SELECT COUNT(*) FROM memory_vectors")[0][0]
    links = reopened.query("SELECT COUNT(*) FROM memory_vectors v JOIN memory_records m USING(memory_id) WHERE v.content_version=m.content_version AND v.embedding_model=? AND v.dimension=?", (embedder.model_name, embedder.dimension))[0][0]
    integrity = reopened.query("PRAGMA quick_check")[0][0]
    elapsed = monotonic() - started
    report = {"model": embedder.model_name, "dimension": embedder.dimension,
              "input_characters": sum(map(len, texts)) + len("服务异常怎么排查"),
              "document_vectors": len(document_vectors), "query_result_count": len(matches),
              "persisted_vectors": counts, "valid_version_links": links,
              "persist_ms": round((persisted_at-started)*1000, 2),
              "total_ms": round(elapsed*1000, 2), "quick_check": integrity,
              "foreign_scope_hits": sum(item.get("operator_id") != "smoke-owner" or item.get("incident_id") != "smoke-incident" for item in matches),
              "background_workers": 0, "background_exceptions": 0, "retries": 0}
    report["case_pass"] = (len(document_vectors) == counts == links == len(matches) == 3
                           and integrity == "ok" and report["foreign_scope_hits"] == 0 and elapsed <= 120)
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert report["case_pass"], "smoke case did not meet all thresholds"
PY
```

本Case是同步链路，无异步业务/持久化间隔；persist_ms记录写入结束，total_ms记录查询/恢复验证结束。调用失败时Python异常即Case失败，不输出成功报告。排序正确性、跨租户攻击集、P95和470查询评测属于后续累计验证。
