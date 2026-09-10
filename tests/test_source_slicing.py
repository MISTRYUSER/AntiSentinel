from dataclasses import replace
import hashlib
import json

import pytest

from antisentinel.domain.errors import DomainError
from antisentinel.retrieval.models import CodeSearchScope
from antisentinel.retrieval.slicing import slice_documents
from antisentinel.retrieval.sqlite_store import SQLiteCodeSearchStore
from antisentinel.retrieval.evidence import CodeEvidenceAssembler
from tests.test_retrieval_graph_boundaries import setup_source


def test_unicode_slices_cover_original_bytes_with_distinct_ids():
    text = '中文🌍' * 100 + '\n' + ' ' * 40
    raw = text.encode()
    scope = CodeSearchScope('repo','snap',1,'commit')
    source = {'content':text,'content_hash':hashlib.sha256(raw).hexdigest(),
        'byte_start':100,'byte_end':100+len(raw),'node_id':'node','chunk_id':'parent','path':'file.py'}
    docs = slice_documents(scope, source, 'symbol', 'python', max_bytes=17)
    assert ''.join(d.text for d in docs) == text
    assert all(len(d.text.encode()) <= 17 and d.chunk_id == 'parent' for d in docs)
    assert len({d.document_id for d in docs}) == len(docs)
    assert docs[0].byte_start == 100 and docs[-1].byte_end == source['byte_end']
    assert all(a.byte_end == b.byte_start for a,b in zip(docs, docs[1:]))


def test_incorrect_parent_bytes_are_rejected():
    source = {'content':'中','content_hash':'bad','byte_start':0,'byte_end':3}
    with pytest.raises(ValueError, match='byte-preserving'):
        slice_documents(CodeSearchScope('r','s',1,'c'),source,'s','py')


def test_multiple_slices_of_parent_read_and_restore_exact_ranges(tmp_path):
    store, scope, _, chunk, source = setup_source(tmp_path)
    parent = store.read_generation_chunk(scope['repository_id'],scope['snapshot_id'],1,chunk['chunk_id'])
    docs = slice_documents(CodeSearchScope(**scope), parent, 'symbol', 'python', max_bytes=16)
    selected = docs[:2]
    context = CodeEvidenceAssembler(source).select('incident-a', [d.source_identity for d in selected])
    assert len(context.slices) == 2
    assert [s.content for s in context.slices] == [d.text for d in selected]
    restored = source.rehydrate([{'evidence_id':s.evidence_id} for s in context.slices])
    assert [(s.content,s.byte_start,s.byte_end) for s in restored] == [(d.text,d.byte_start,d.byte_end) for d in selected]


@pytest.mark.parametrize('field,value', [('parent_source_hash','bad'),('source_hash','bad'),('byte_end',999999),('byte_start',-1)])
def test_tampered_slice_does_not_persist_evidence(tmp_path, field, value):
    store, scope, _, chunk, source = setup_source(tmp_path)
    parent = store.read_generation_chunk(scope['repository_id'],scope['snapshot_id'],1,chunk['chunk_id'])
    doc = slice_documents(CodeSearchScope(**scope),parent,'symbol','py',max_bytes=16)[0]
    with pytest.raises(DomainError):
        CodeEvidenceAssembler(source).select('incident-a',[{**doc.source_identity,field:value}])
    assert store.database.query('SELECT COUNT(*) FROM evidence')[0][0] == 0


def test_slice_ranges_survive_sqlite_and_manifest_publication(tmp_path):
    store, scope, _, chunk, _ = setup_source(tmp_path)
    parent = store.read_generation_chunk(scope['repository_id'],scope['snapshot_id'],1,chunk['chunk_id'])
    docs = slice_documents(CodeSearchScope(**scope),parent,'symbol','py',max_bytes=16)
    projection = SQLiteCodeSearchStore(store.database)
    projection.begin_manifest(CodeSearchScope(**scope),docs[0].projection_revision,docs)
    projection.upsert_documents(docs)
    projection.publish_manifest(CodeSearchScope(**scope),docs[0].projection_revision)
    reopened = SQLiteCodeSearchStore(store.database)
    hits = reopened.query_exact(CodeSearchScope(**scope),'symbol',limit=30)
    assert {h.document_id: h.source_identity for h in hits} == {d.document_id:d.source_identity for d in docs}


def test_legacy_manifest_and_task_json_remain_usable_without_rewriting(tmp_path):
    from tests.test_embedding_worker import setup_queue
    from antisentinel.retrieval.embedding_jobs import EmbeddingQueue
    database, queue, run_id, scope, doc = setup_queue(tmp_path)
    fields = ('byte_start','byte_end','parent_source_hash')
    with database.transaction() as c:
        old = json.loads(c.execute('SELECT expected_json FROM code_search_manifests').fetchone()[0])
        for record in old:
            for key in fields: record.pop(key, None)
        encoded = json.dumps(old)
        c.execute('UPDATE code_search_manifests SET expected_json=?',(encoded,))
        task = json.loads(c.execute('SELECT document_json FROM code_embedding_tasks').fetchone()[0])
        for key in fields: task.pop(key, None)
        c.execute('UPDATE code_embedding_tasks SET document_json=?',(json.dumps(task),))
        for key in fields: c.execute(f'ALTER TABLE code_search_documents DROP COLUMN {key}')
    reopened = SQLiteCodeSearchStore(database)
    reopened.begin_manifest(scope, doc.projection_revision, [doc])
    assert database.query('SELECT expected_json FROM code_search_manifests')[0][0] == encoded
    queue = EmbeddingQueue(database)
    lease = queue.claim(run_id,'test',now=1)
    restored = queue.document(lease,now=1)
    assert restored.document_id == doc.document_id and restored.source_identity == doc.source_identity


def utf8_store(tmp_path, data):
    from datetime import datetime, timezone
    from antisentinel.code_map.models import MapSnapshot, RepositoryRegistration
    from antisentinel.code_map.python_parser import PythonAstParser
    from antisentinel.code_map.store import SQLiteCodeMapStore, SourceFile, SourceBlob, StagedMapRows
    from antisentinel.persistence.sqlite_database import SQLiteDatabase
    database=SQLiteDatabase(tmp_path/'utf8.sqlite');database.initialize();store=SQLiteCodeMapStore(database)
    store.register(RepositoryRegistration('repo','file:///fixture','local','main'))
    job=store.enqueue('repo','a'*40,'manual');lease=store.claim_job('test',datetime.now(timezone.utc))
    parser=PythonAstParser('python-ast-v1',max_chunk_bytes=7)
    parsed=parser.parse_file('utf.py',data,'snap')
    assert not parsed.errors
    assert store.publish(lease,MapSnapshot('snap','repo','a'*40,'python-ast-v1',job.rules_digest,file_count=1),
        StagedMapRows(nodes=parsed.symbols,chunks=parsed.chunks,
            blobs=(SourceBlob(parsed.file_hash,'object',data,parsed.encoding),),
            files=(SourceFile('file','snap','utf.py','object',parsed.file_hash,len(data)),))).ok
    store.bind_incident('incident','repo','snap')
    return store,parsed


def test_split_utf8_parent_boundaries_are_repaired_without_changing_archive(tmp_path):
    from antisentinel.code_map.source_context import SourceEvidenceService
    from antisentinel.persistence.sqlite_stores import SQLiteEvidenceStore
    data=('def f():\n    return "X'+'中🌍'*12+'"\n').encode()
    store,parsed=utf8_store(tmp_path,data)
    before=store.read_generation('snap',1)
    invalid=0;aligned=[];docs=[]
    for chunk in parsed.chunks:
        try:store.read_generation_chunk('repo','snap',1,chunk.chunk_id)
        except UnicodeDecodeError:invalid+=1
        source=store.read_generation_utf8_chunk('repo','snap',1,chunk.chunk_id);aligned.append(source)
        assert source['parent_source_hash']==chunk.content_hash
        assert 0<=source['byte_start']-chunk.byte_start<=3 and 0<=source['byte_end']-chunk.byte_end<=3
        docs.extend(slice_documents(CodeSearchScope('repo','snap',1,'a'*40),source,'f','py',max_bytes=4))
    assert invalid>0 and ''.join(s['content'] for s in aligned).encode()==data
    assert all(a['byte_end']==b['byte_start'] for a,b in zip(aligned,aligned[1:]))
    chosen=next(d for d in docs if '🌍' in d.text)
    evidence=SourceEvidenceService(None,store,SQLiteEvidenceStore(store.database))
    result=CodeEvidenceAssembler(evidence).select('incident',[chosen.source_identity])
    restored=evidence.rehydrate([{'evidence_id':result.slices[0].evidence_id}])
    assert restored[0].content==chosen.text and restored[0].content_hash==chosen.source_hash
    assert store.read_generation('snap',1)==before


def test_non_utf8_source_is_rejected_without_reencoding(tmp_path):
    store,parsed=utf8_store(tmp_path,b'# coding: latin-1\ndef f():\n    return "\xe9"\n')
    with pytest.raises(DomainError,match='unsupported_source_encoding'):
        store.read_generation_utf8_chunk('repo','snap',1,parsed.chunks[0].chunk_id)


def test_empty_aligned_tail_is_owned_by_preceding_parent(tmp_path):
    data='def f():\n    return 中'.encode()
    store,parsed=utf8_store(tmp_path,data)
    sources=[store.read_generation_utf8_chunk('repo','snap',1,c.chunk_id) for c in parsed.chunks]
    assert sources[-1]['byte_start']==sources[-1]['byte_end']
    assert ''.join(s['content'] for s in sources).encode()==data
    assert slice_documents(CodeSearchScope('repo','snap',1,'a'*40),sources[-1],'f','py')==()


def test_real_slicing_case(tmp_path):
    from scripts.run_source_slicing_case import run
    result=run(tmp_path/'case')
    assert result['case_pass'] and result['counts']['vectors']==result['counts']['documents']
    assert result['embedding_calls_per_start'][1]==0


def test_explicit_slice_publication_keeps_old_projection_until_complete(tmp_path):
    from antisentinel.retrieval.models import CodeSearchDocument
    store,scope,_,chunk,_=setup_source(tmp_path)
    parent=store.read_generation_chunk(scope['repository_id'],scope['snapshot_id'],1,chunk['chunk_id'])
    source_scope=CodeSearchScope(**scope)
    legacy=CodeSearchDocument(*source_scope.key,parent['node_id'],parent['chunk_id'],parent['path'],
        'symbol','py',parent['content_hash'],'input','legacy',parent['content'])
    projection=SQLiteCodeSearchStore(store.database)
    projection.begin_manifest(source_scope,'legacy',[legacy]);projection.upsert_documents([legacy]);projection.publish_manifest(source_scope,'legacy')
    docs=slice_documents(source_scope,parent,'symbol','py',max_bytes=16)
    projection.begin_manifest(source_scope,docs[0].projection_revision,docs)
    projection.upsert_documents(docs[:1])
    with pytest.raises(ValueError,match='reconciliation failed'):
        projection.publish_manifest(source_scope,docs[0].projection_revision)
    assert projection.resolve_projection(source_scope)=='legacy'
    projection.upsert_documents(docs[1:]);projection.publish_manifest(source_scope,docs[0].projection_revision)
    assert projection.resolve_projection(source_scope)==docs[0].projection_revision
    assert not projection.has_ready_manifest(source_scope,projection_revision='legacy')
