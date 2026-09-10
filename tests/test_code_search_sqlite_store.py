from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.retrieval.models import CodeSearchDocument, CodeSearchScope
from antisentinel.retrieval.sqlite_store import SQLiteCodeSearchStore


def document(*, snapshot_id="snapshot-a", generation=1, chunk_id="chunk-1", text="charge timeout"):
    return CodeSearchDocument(
        repository_id="repo",
        snapshot_id=snapshot_id,
        published_generation=generation,
        commit_sha=f"commit-{snapshot_id}",
        node_id="node-1",
        chunk_id=chunk_id,
        path="src/service.py",
        symbol="charge",
        language="python",
        source_hash=f"source-{snapshot_id}-{chunk_id}",
        embedding_input_hash=f"input-{snapshot_id}-{chunk_id}",
        projection_revision="projection-v1",
        text=text,
    )


def test_upsert_is_idempotent_and_unpublished_generation_is_hidden(tmp_path):
    database = SQLiteDatabase(tmp_path / "facts.db")
    store = SQLiteCodeSearchStore(database)
    first = document()
    store.upsert_documents([first, first])

    scope = CodeSearchScope("repo", "snapshot-a", 1, "commit-snapshot-a")
    assert store.count_documents(scope) == 1
    assert store.query_fts(scope, "charge") == ()

    store.begin_manifest(scope, "projection-v1", [first])
    store.publish_manifest(scope, "projection-v1")
    hits = store.query_fts(scope, "charge")
    assert len(hits) == 1
    assert hits[0].document_id == first.document_id
    assert hits[0].source_identity == first.source_identity


def test_same_path_in_different_snapshot_is_not_overwritten(tmp_path):
    database = SQLiteDatabase(tmp_path / "facts.db")
    store = SQLiteCodeSearchStore(database)
    old = document(snapshot_id="snapshot-a", generation=1)
    new = document(snapshot_id="snapshot-b", generation=1)
    store.upsert_documents([old, new])

    old_scope = CodeSearchScope("repo", "snapshot-a", 1, "commit-snapshot-a")
    new_scope = CodeSearchScope("repo", "snapshot-b", 1, "commit-snapshot-b")
    store.begin_manifest(old_scope, "projection-v1", [old])
    store.begin_manifest(new_scope, "projection-v1", [new])
    store.publish_manifest(old_scope, "projection-v1")
    store.publish_manifest(new_scope, "projection-v1")

    assert store.count_documents(old_scope) == 1
    assert store.count_documents(new_scope) == 1
    assert store.query_fts(old_scope, "charge")[0].document_id == old.document_id
    assert store.query_fts(new_scope, "charge")[0].document_id == new.document_id
