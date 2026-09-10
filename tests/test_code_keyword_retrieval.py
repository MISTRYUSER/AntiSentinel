from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.retrieval.keyword import KeywordRetriever
from antisentinel.retrieval.models import CodeSearchDocument, CodeSearchScope
from antisentinel.retrieval.sqlite_store import SQLiteCodeSearchStore


def make_document(*, snapshot_id="snapshot-a", generation=1, node_id="node-1", symbol="process", text="payment"):
    return CodeSearchDocument(
        repository_id="repo",
        snapshot_id=snapshot_id,
        published_generation=generation,
        commit_sha=f"commit-{snapshot_id}",
        node_id=node_id,
        chunk_id=f"chunk-{node_id}",
        path="src/service.py",
        symbol=symbol,
        language="python",
        source_hash=f"source-{snapshot_id}-{node_id}",
        embedding_input_hash=f"input-{snapshot_id}-{node_id}",
        projection_revision="projection-v1",
        text=text,
    )


def test_exact_identifier_is_prioritized_and_scope_is_enforced(tmp_path):
    database = SQLiteDatabase(tmp_path / "facts.db")
    store = SQLiteCodeSearchStore(database)
    exact = make_document(node_id="exact", symbol="process_payment", text="ERR_PAYMENT_TIMEOUT")
    fuzzy = make_document(node_id="fuzzy", symbol="process", text="process payment")
    other_snapshot = make_document(snapshot_id="snapshot-b", node_id="other", symbol="process_payment")
    store.upsert_documents([exact, fuzzy, other_snapshot])
    scope = CodeSearchScope("repo", "snapshot-a", 1, "commit-snapshot-a")
    store.begin_manifest(scope, "projection-v1", [exact, fuzzy])
    store.publish_manifest(scope, "projection-v1")

    hits = KeywordRetriever(store).search(scope, "process_payment")

    assert [hit.document_id for hit in hits[:2]] == [exact.document_id, fuzzy.document_id]
    assert all(hit.source_identity["snapshot_id"] == "snapshot-a" for hit in hits)
    assert all(hit.channel == "keyword" for hit in hits)


def test_chinese_error_code_and_fts_special_characters_are_searchable(tmp_path):
    database = SQLiteDatabase(tmp_path / "facts.db")
    store = SQLiteCodeSearchStore(database)
    chinese = make_document(node_id="zh", symbol="handle", text="支付 超时")
    error = make_document(node_id="error", symbol="charge", text="ERR_PAYMENT_TIMEOUT")
    store.upsert_documents([chinese, error])
    scope = CodeSearchScope("repo", "snapshot-a", 1, "commit-snapshot-a")
    store.begin_manifest(scope, "projection-v1", [chinese, error])
    store.publish_manifest(scope, "projection-v1")
    retriever = KeywordRetriever(store)

    assert retriever.search(scope, "支付 超时")[0].document_id == chinese.document_id
    assert retriever.search(scope, "ERR_PAYMENT_TIMEOUT")[0].document_id == error.document_id
    assert retriever.search(scope, 'ERR_PAYMENT_TIMEOUT" OR *')[0].document_id == error.document_id
