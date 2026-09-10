from antisentinel.retrieval.models import CodeSearchDocument, CodeSearchScope


def test_code_search_document_has_stable_identity_and_scope():
    document = CodeSearchDocument(
        repository_id="repo",
        snapshot_id="snapshot-a",
        published_generation=2,
        commit_sha="abc123",
        node_id="node-1",
        chunk_id="chunk-1",
        path="src/a.py",
        symbol="charge",
        language="python",
        source_hash="source-hash",
        embedding_input_hash="input-hash",
        projection_revision="projection-v1",
        text="def charge(): return 1",
    )

    assert document.document_id == CodeSearchDocument(**document.__dict__).document_id
    assert document.source_identity == {
        "repository_id": "repo",
        "snapshot_id": "snapshot-a",
        "published_generation": 2,
        "commit_sha": "abc123",
        "node_id": "node-1",
        "chunk_id": "chunk-1",
        "path": "src/a.py",
        "source_hash": "source-hash",
    }
    assert CodeSearchScope("repo", "snapshot-a", 2, "abc123").key == (
        "repo",
        "snapshot-a",
        2,
        "abc123",
    )
