from dataclasses import replace
import sqlite3

import pytest

from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.retrieval.models import CodeSearchDocument, CodeSearchScope
from antisentinel.retrieval.sqlite_store import SQLiteCodeSearchStore


def fixture(tmp_path):
    store=SQLiteCodeSearchStore(SQLiteDatabase(tmp_path/'facts.db'))
    scope=CodeSearchScope('repo','snap',1,'commit')
    doc=CodeSearchDocument('repo','snap',1,'commit','node','chunk','a.py','charge','python','hash','input','v1','charge payment')
    return store,scope,doc


def publish(store,scope,doc):
    store.begin_manifest(scope,doc.projection_revision,[doc])
    store.upsert_documents([doc])
    store.publish_manifest(scope,doc.projection_revision)


def test_only_one_active_projection_is_read_and_pin_is_enforced(tmp_path):
    store,scope,first=fixture(tmp_path);publish(store,scope,first)
    second=replace(first,projection_revision='v2')
    store.begin_manifest(scope,'v2',[second]);store.upsert_documents([second])
    assert [h.document_id for h in store.query_fts(scope,'charge')]==[first.document_id]
    store.publish_manifest(scope,'v2')
    assert [h.document_id for h in store.query_exact(scope,'charge')]==[second.document_id]
    assert [h.document_id for h in store.query_fts(scope,'charge')]==[second.document_id]
    assert store.query_fts(scope,'charge',projection_revision='v1')==()
    assert not store.has_ready_manifest(scope,projection_revision='v1')


@pytest.mark.parametrize('damage', ['missing','extra','field','fts_missing','fts_duplicate','fts_field'])
def test_failed_reconciliation_keeps_old_pointer(tmp_path,damage):
    store,scope,first=fixture(tmp_path);publish(store,scope,first)
    second=replace(first,projection_revision='v2');store.begin_manifest(scope,'v2',[second]);store.upsert_documents([second])
    with store.database.transaction() as c:
        if damage=='missing':c.execute('DELETE FROM code_search_documents WHERE document_id=?',(second.document_id,))
        elif damage=='extra':c.execute("INSERT INTO code_search_documents SELECT 'unexpected',repository_id,snapshot_id,published_generation,commit_sha,node_id,chunk_id,path,symbol,language,source_hash,embedding_input_hash,projection_revision,text,byte_start,byte_end,parent_source_hash FROM code_search_documents WHERE document_id=?",(second.document_id,))
        elif damage=='field':c.execute("UPDATE code_search_documents SET source_hash='bad' WHERE document_id=?",(second.document_id,))
        elif damage=='fts_missing':c.execute('DELETE FROM code_search_documents_fts WHERE document_id=?',(second.document_id,))
        elif damage=='fts_duplicate':c.execute('INSERT INTO code_search_documents_fts SELECT * FROM code_search_documents_fts WHERE document_id=?',(second.document_id,))
        else:c.execute("UPDATE code_search_documents_fts SET code='corrupt' WHERE document_id=?",(second.document_id,))
    with pytest.raises(ValueError,match='reconciliation'):
        store.publish_manifest(scope,'v2')
    assert [h.document_id for h in store.query_fts(scope,'charge')]==[first.document_id]
    assert store.database.query("SELECT status FROM code_search_manifests WHERE projection_revision='v2'")[0][0]=='failed'


def test_expectation_required_and_ready_projection_is_immutable(tmp_path):
    store,scope,doc=fixture(tmp_path)
    with pytest.raises(ValueError):store.publish_manifest(scope,'v1')
    publish(store,scope,doc)
    with pytest.raises(ValueError,match='immutable'):store.upsert_documents([replace(doc,text='changed')])
    assert len(store.query_fts(scope,'charge'))==1


def test_fts_operational_errors_are_not_empty_results(tmp_path):
    store,scope,doc=fixture(tmp_path);publish(store,scope,doc)
    with store.database.transaction() as c:c.execute('DROP TABLE code_search_documents_fts')
    with pytest.raises(sqlite3.OperationalError):store.query_fts(scope,'charge')
    with pytest.raises(RuntimeError,match='lexical_index_missing'):
        SQLiteCodeSearchStore(SQLiteDatabase(tmp_path/'facts.db'))


def test_stale_publisher_cannot_roll_back_pointer(tmp_path):
    store,scope,first=fixture(tmp_path);publish(store,scope,first)
    second,third=replace(first,projection_revision='v2'),replace(first,projection_revision='v3')
    for d in [second,third]:store.begin_manifest(scope,d.projection_revision,[d])
    store.upsert_documents([second,third]);store.publish_manifest(scope,'v2')
    for revision in ['v3','v1']:
        with pytest.raises(ValueError,match='reconciliation'):store.publish_manifest(scope,revision)
    assert store.resolve_projection(scope)=='v2'
    assert len(store.database.query("SELECT * FROM code_search_publication_attempts WHERE status='failed'"))==2
    with pytest.raises(ValueError,match='immutable'):
        store.upsert_documents([replace(first,text='mutated retired projection')])


def test_legacy_ready_is_hidden_until_explicit_reconciliation(tmp_path):
    store,scope,doc=fixture(tmp_path);publish(store,scope,doc)
    with store.database.transaction() as c:
        c.execute('DELETE FROM code_search_active_projections')
        c.execute('UPDATE code_search_manifests SET expected_json=NULL')
    reopened=SQLiteCodeSearchStore(SQLiteDatabase(tmp_path/'facts.db'))
    assert reopened.query_fts(scope,'charge')==()
    with pytest.raises(ValueError):reopened.publish_manifest(scope,'v1')
    reopened.begin_manifest(scope,'v1',[doc]);reopened.publish_manifest(scope,'v1')
    assert len(reopened.query_fts(scope,'charge'))==1


def test_service_pins_lexical_to_its_configured_projection(tmp_path):
    from antisentinel.retrieval.engine import CodeRetrievalService
    from antisentinel.retrieval.keyword import KeywordRetriever
    store,scope,doc=fixture(tmp_path);publish(store,scope,doc)
    publish(store,scope,replace(doc,projection_revision='v2'))
    service=CodeRetrievalService(KeywordRetriever(store),None,channel_store=None,model_revision='none',template_revision='none',projection_revision='v1')
    result=service.search('charge',scope,mode='keyword')
    assert result.hits==() and result.error_code=='lexical_unavailable'


def test_failure_during_pointer_update_rolls_back_ready_status(tmp_path):
    store,scope,doc=fixture(tmp_path);publish(store,scope,doc)
    second=replace(doc,projection_revision='v2')
    store.begin_manifest(scope,'v2',[second]);store.upsert_documents([second])
    with store.database.transaction() as c:
        c.execute("CREATE TRIGGER abort_publish BEFORE INSERT ON code_search_active_projections WHEN NEW.projection_revision='v2' BEGIN SELECT RAISE(ABORT,'abort publication'); END")
    with pytest.raises(sqlite3.IntegrityError):store.publish_manifest(scope,'v2')
    assert store.resolve_projection(scope)=='v1'
    assert store.database.query("SELECT status FROM code_search_manifests WHERE projection_revision='v2'")[0][0]=='building'
    with store.database.transaction() as c:c.execute('DROP TRIGGER abort_publish')
    report=store.publish_manifest(scope,'v2')
    assert report['expected_digest']==report['actual_digest']
    assert store.resolve_projection(scope)=='v2'
