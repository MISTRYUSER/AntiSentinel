"""Real SQLite publication, failure preservation and reopen Case; no model calls."""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.retrieval.models import CodeSearchScope,CodeSearchDocument
from antisentinel.retrieval.sqlite_store import SQLiteCodeSearchStore


def run(output):
    output.mkdir()
    started=time.monotonic()
    database=SQLiteDatabase(output/'facts.sqlite');store=SQLiteCodeSearchStore(database)
    scope=CodeSearchScope('fixture','snapshot',1,'fixed-commit')
    docs=[]
    for i in range(2):
        text=f'def charge_{i}():\n    return {i}\n'
        digest=hashlib.sha256(text.encode()).hexdigest()
        docs.append(CodeSearchDocument('fixture','snapshot',1,'fixed-commit',f'n{i}',f'c{i}',f'{i}.py',f'charge_{i}','python',digest,digest,'v1',text))
    checks={}
    store.begin_manifest(scope,'v1',docs);store.upsert_documents(docs[:1])
    try:store.publish_manifest(scope,'v1');checks['initial_partial_rejected']=False
    except ValueError:checks['initial_partial_rejected']=store.resolve_projection(scope) is None
    store.upsert_documents(docs[1:]);store.publish_manifest(scope,'v1')
    second=[replace(d,projection_revision='v2') for d in docs]
    store.begin_manifest(scope,'v2',second);store.upsert_documents(second[:1])
    try:store.publish_manifest(scope,'v2');checks['failed_rebuild_keeps_pointer']=False
    except ValueError:checks['failed_rebuild_keeps_pointer']=store.resolve_projection(scope)=='v1'
    store.upsert_documents(second[1:]);store.publish_manifest(scope,'v2')
    expected={d.document_id for d in second}
    checks['only_new_revision_visible']={h.document_id for h in store.query_fts(scope,'charge_0 OR charge_1')}==expected
    checks['old_pin_hidden']=store.query_fts(scope,'charge_0',projection_revision='v1')==()
    try:store.publish_manifest(scope,'v1');checks['stale_publisher_rejected']=False
    except ValueError:checks['stale_publisher_rejected']=store.resolve_projection(scope)=='v2'
    try:store.upsert_documents([replace(docs[0],text='changed')]);checks['retired_immutable']=False
    except ValueError:checks['retired_immutable']=True
    third=[replace(d,projection_revision='v3') for d in docs]
    store.begin_manifest(scope,'v3',third);store.upsert_documents(third)
    with database.transaction() as c:c.execute("UPDATE code_search_documents_fts SET code='corrupt' WHERE document_id=?",(third[0].document_id,))
    try:store.publish_manifest(scope,'v3');checks['fts_corruption_rejected']=False
    except ValueError:checks['fts_corruption_rejected']=store.resolve_projection(scope)=='v2'
    reopened=SQLiteCodeSearchStore(SQLiteDatabase(output/'facts.sqlite'))
    checks['reopen_identity']={h.document_id for h in reopened.query_fts(scope,'charge_0 OR charge_1')}==expected
    checks['sqlite_integrity']=database.query('PRAGMA integrity_check')[0][0]=='ok'
    attempts=[dict(r) for r in database.query('SELECT * FROM code_search_publication_attempts ORDER BY attempt_id')]
    (output/'publication-attempts.json').write_text(json.dumps(attempts,indent=2)+'\n')
    report={'case':'lexical_publication','input_files':2,'input_bytes':sum(len(d.text.encode()) for d in docs),
        'projection_revisions':3,'expected_documents_per_revision':2,'persisted_documents':store.count_documents(scope),
        'output_count':len(expected),'active_revision':reopened.resolve_projection(scope),
        'publication_attempts':len(attempts),'failed_publications':sum(r['status']=='failed' for r in attempts),
        'checks':checks,'passed_checks':sum(checks.values()),'required_checks':len(checks),
        'elapsed_ms':(time.monotonic()-started)*1000,'external_calls':0,'background_workers':0,'retries':0,
        'case_pass':all(checks.values())}
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return 0 if report['case_pass'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    raise SystemExit(run(parser.parse_args().output))
