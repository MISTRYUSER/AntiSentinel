import hashlib

import pytest

from antisentinel.evaluation.rag_answers import prepare_context, answer_request, validate_answer, disclosed_contexts
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.persistence.sqlite_stores import SQLiteEvidenceStore
from antisentinel.retrieval.models import CodeSearchDocument


def test_context_persists_hash_verified_evidence_and_resolves_answer_citation(tmp_path):
    text = 'def f():\n    return 1\n'
    doc = CodeSearchDocument('r', 's', 1, 'c', 'n', 'ch', 'a.py', 'f', 'python', hashlib.sha256(text.encode()).hexdigest(), 'input', 'p', text)
    query = {'id': 'q', 'query': 'f在哪里', 'scope': {'repository_id':'r','snapshot_id':'s','published_generation':1,'commit_sha':'c'}}
    db = SQLiteDatabase(tmp_path/'facts.db');db.initialize();store = SQLiteEvidenceStore(db)
    packet = prepare_context(query, [doc.document_id], {doc.document_id:doc}, tmp_path, store)
    assert len(packet['candidates']) == 1
    evidence_id = packet['candidates'][0]['evidence_id']
    assert store.get(evidence_id).content_hash == doc.source_hash
    raw = {'query_id':'q','status':'answered','response':'f返回1','citations':[{'document_id':doc.document_id,'start_line':2,'end_line':2}]}
    answer = validate_answer(packet, raw)
    assert answer['citations'][0]['evidence_id'] == evidence_id
    assert answer['citations'][0]['quote'] == '    return 1\n'
    assert 'max_tokens' not in answer_request(packet, 'deepseek-chat')
    baseline = answer_request(packet, 'deepseek-chat')
    focused = answer_request(packet, 'deepseek-chat', style='focused')
    assert baseline['messages'][1] == focused['messages'][1]
    assert baseline['tools'] == focused['tools']
    assert baseline['messages'][0] != focused['messages'][0]
    import json
    shown = json.loads(answer_request(packet, 'deepseek-chat')['messages'][1]['content'])['candidates']
    assert [json.loads(c) for c in disclosed_contexts(packet)] == shown
    assert json.loads(disclosed_contexts(packet)[0])['source_identity']['path'] == 'a.py'
    assert json.loads(disclosed_contexts(packet)[0])['source_lines'][1]['line'] == 2
    raw['citations'] = []
    with pytest.raises(ValueError):
        validate_answer(packet, raw)


def test_context_hash_mismatch_is_rejected_before_persistence(tmp_path):
    doc = CodeSearchDocument('r','s',1,'c','n','ch','a.py','f','python','wrong','input','p','def f(): pass')
    db=SQLiteDatabase(tmp_path/'facts.db');db.initialize();store=SQLiteEvidenceStore(db)
    with pytest.raises(ValueError, match='hash'):
        prepare_context({'id':'q','query':'f','scope':{'repository_id':'r'}}, [doc.document_id], {doc.document_id:doc}, tmp_path, store)
    assert db.query('SELECT COUNT(*) FROM evidence')[0][0] == 0


def test_context_budget_precedes_evidence_writes(tmp_path):
    from dataclasses import replace
    text = 'x' * 9000
    base = CodeSearchDocument('r','s',1,'c','n','ch','a.py','f','python',hashlib.sha256(text.encode()).hexdigest(),'input','p',text)
    docs = [replace(base, node_id=f'n{i}',chunk_id=f'ch{i}') for i in range(5)]
    db=SQLiteDatabase(tmp_path/'facts.db');db.initialize();store=SQLiteEvidenceStore(db)
    packet=prepare_context({'id':'q','query':'x','scope':{'repository_id':'r'}}, [d.document_id for d in docs], {d.document_id:d for d in docs}, tmp_path, store)
    assert len(packet['candidates']) == 3
    assert packet['disclosed_bytes'] == 27000 and packet['incomplete']
    assert db.query('SELECT COUNT(*) FROM evidence')[0][0] == 3
