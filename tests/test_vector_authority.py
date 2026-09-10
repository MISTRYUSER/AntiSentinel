import pytest

from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.retrieval.models import CodeSearchDocument, CodeSearchScope
from antisentinel.retrieval.sqlite_store import SQLiteCodeSearchStore
from antisentinel.retrieval.vector import EmbeddingTaskStore, VectorPoint, ReconciliationReport
from antisentinel.retrieval.milvus_adapter import _point_row


def setup_authority(tmp_path):
    db = SQLiteDatabase(tmp_path/'facts.db')
    store = SQLiteCodeSearchStore(db)
    tasks = EmbeddingTaskStore(db)
    doc = CodeSearchDocument('repo', 'snap', 1, 'commit', 'node', 'chunk', 'a.py', 'fn', 'python', 'hash', 'input', 'projection', 'def fn(): pass')
    scope = CodeSearchScope('repo', 'snap', 1, 'commit')
    store.begin_manifest(scope, 'projection', [doc])
    store.upsert_documents([doc])
    store.publish_manifest(scope, 'projection')
    point = VectorPoint('derived', (1., 0.), doc.source_identity, 'input', 'model', 2, 'template', 'projection', doc.document_id)
    tasks.create_task('task', point.point_id, 'input', 'model', 2, 'template', 'projection')
    tasks.set_task_status('task', 'ready')
    tasks.set_channel_status(*scope.key, 'ready')
    ids=frozenset({point.point_id})
    assert tasks.mark_channel_ready(scope,ReconciliationReport(ids,ids,frozenset(),frozenset(),{}))
    return db, tasks, scope, _point_row(point)


def verify(tasks, scope, row):
    return tasks.verify_vector_rows([row], scope, model_revision='model', dimension=2, template_revision='template', projection_revision='projection')


def test_authoritative_vector_accepts_complete_identity(tmp_path):
    db, tasks, scope, row = setup_authority(tmp_path)
    verify(tasks, scope, row)


@pytest.mark.parametrize('field,value', [('byte_start',0),('byte_end',10),('parent_source_hash','a'*64)])
def test_legacy_document_cannot_gain_ranges_from_vector_payload(tmp_path,field,value):
    _,tasks,scope,row=setup_authority(tmp_path)
    with pytest.raises(ValueError,match='unexpected_range'):
        verify(tasks,scope,{**row,field:value})


def test_vector_authority_rejects_retired_projection(tmp_path):
    from dataclasses import replace
    db,tasks,scope,row=setup_authority(tmp_path)
    store=SQLiteCodeSearchStore(db)
    values=dict(db.query('SELECT * FROM code_search_documents')[0])
    values.pop('document_id')
    second=replace(CodeSearchDocument(**values),projection_revision='v2')
    store.begin_manifest(scope,'v2',[second]);store.upsert_documents([second]);store.publish_manifest(scope,'v2')
    with pytest.raises(ValueError,match='vector_authority_mismatch'):
        verify(tasks,scope,row)


@pytest.mark.parametrize('field', ['document_id', 'point_id', 'repository_id', 'snapshot_id', 'published_generation', 'commit_sha', 'node_id', 'chunk_id', 'path', 'source_hash', 'input_hash', 'model_revision', 'dimension', 'template_revision', 'projection_revision'])
@pytest.mark.parametrize('mutation', ['missing', 'wrong'])
def test_authoritative_vector_rejects_missing_or_corrupt_payload(tmp_path, field, mutation):
    db, tasks, scope, row = setup_authority(tmp_path)
    if mutation == 'missing':
        row.pop(field)
    else:
        row[field] = 'wrong'
    with pytest.raises(ValueError, match='vector_authority_mismatch'):
        verify(tasks, scope, row)


@pytest.mark.parametrize('sql', [
    'DELETE FROM code_search_documents',
    "UPDATE code_search_manifests SET status='building'",
    "UPDATE code_embedding_tasks SET status='pending'",
    "UPDATE code_embedding_tasks SET input_hash='changed'",
    "UPDATE code_embedding_projection_runs SET status='blocked'",
])
def test_authoritative_vector_rechecks_sqlite_state(tmp_path, sql):
    db, tasks, scope, row = setup_authority(tmp_path)
    with db.transaction() as conn:
        conn.execute(sql)
    with pytest.raises(ValueError, match='vector_authority_mismatch'):
        verify(tasks, scope, row)


def test_real_milvus_payload_tampering_cannot_reach_service(tmp_path):
    pytest.importorskip('milvus_lite')
    from antisentinel.retrieval.milvus_adapter import MilvusAdapter
    from antisentinel.retrieval.keyword import KeywordRetriever
    from antisentinel.retrieval.engine import CodeRetrievalService
    db, tasks, scope, row = setup_authority(tmp_path)
    adapter = MilvusAdapter(tmp_path/'vectors.db', 'authority_case', dimension=2, model_revision='model', template_revision='template', projection_revision='projection')
    try:
        adapter.ensure_collection()
        adapter.client.upsert(collection_name=adapter.collection_name, data=[row])
        service = CodeRetrievalService(KeywordRetriever(SQLiteCodeSearchStore(db)), adapter, channel_store=tasks, model_revision='model', template_revision='template', projection_revision='projection')
        assert len(service.search('fn', scope, mode='vector', query_vector=[1., 0.]).hits) == 1
        row['path'] = 'forged.py'
        adapter.client.upsert(collection_name=adapter.collection_name, data=[row])
        with pytest.raises(ValueError, match='vector_authority_mismatch'):
            service.search('fn', scope, mode='hybrid', query_vector=[1., 0.])
    finally:
        adapter.close()
