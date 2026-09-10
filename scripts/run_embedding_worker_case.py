"""Real process-exit and Milvus recovery Case with a local embedding substitute."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.retrieval.models import CodeSearchDocument,CodeSearchScope
from antisentinel.retrieval.sqlite_store import SQLiteCodeSearchStore
from antisentinel.retrieval.vector import EmbeddingTaskStore
from antisentinel.retrieval.embedding_worker import EmbeddingWorker
from antisentinel.retrieval.milvus_adapter import MilvusAdapter

SCOPE=CodeSearchScope('fixture','snapshot',1,'fixed-commit')
TEMPLATE='path-symbol-source-v1'


class LocalEmbedder:
    dimension=3
    def __init__(self,output,model,denied=False):self.output,self.model_name,self.denied=output,model,denied
    def embed_documents(self,texts):
        with (self.output/'embedding-calls.jsonl').open('a') as f:f.write(json.dumps({'model':self.model_name,'inputs':len(texts),'denied':self.denied})+'\n')
        if self.denied:raise RuntimeError('embedding_http_401')
        return [[1.,0.,0.] for _ in texts]


def index(output,model):
    return MilvusAdapter(output/'vectors.db','worker_case',dimension=3,model_revision=model,template_revision=TEMPLATE,projection_revision='p',rpc_timeout=10)


def enqueue(queue,model):
    return queue.enqueue(SCOPE,model_revision=model,dimension=3,template_revision=TEMPLATE,projection_revision='p')


def wait_due(queue,rid):
    due=max(float(t['next_attempt_at']) for t in queue.task_rows(rid))
    time.sleep(max(0,min(5,due-time.time()+.05)))


def child(output):
    queue=EmbeddingTaskStore(SQLiteDatabase(output/'facts.sqlite')).queue
    rid=enqueue(queue,'m1');base=index(output,'m1')
    class CrashAfterWrite:
        def __getattr__(self,name):return getattr(base,name)
        def upsert(self,points):
            base.upsert(points)
            os._exit(17)
    EmbeddingWorker(queue,CrashAfterWrite(),LocalEmbedder(output,'m1'),owner='crashed-process',lease_seconds=2).run_once(rid)
    raise RuntimeError('crash hook was not reached')


def run(output):
    output.mkdir();started=time.monotonic()
    db=SQLiteDatabase(output/'facts.sqlite');store=SQLiteCodeSearchStore(db)
    text='def charge():\n    return 1\n';payload='a.py\ncharge\n'+text
    doc=CodeSearchDocument('fixture','snapshot',1,'fixed-commit','node','chunk','a.py','charge','python',hashlib.sha256(text.encode()).hexdigest(),hashlib.sha256(payload.encode()).hexdigest(),'p',text)
    store.begin_manifest(SCOPE,'p',[doc]);store.upsert_documents([doc]);store.publish_manifest(SCOPE,'p')
    tasks=EmbeddingTaskStore(db);queue=tasks.queue;first=enqueue(queue,'m1')
    enqueue(queue,'m2');enqueue(queue,'m3')
    business_done=time.monotonic()
    crashed=subprocess.run([sys.executable,__file__,'--child','--output',str(output)],capture_output=True,text=True,timeout=30)
    (output/'child.stdout').write_text(crashed.stdout);(output/'child.stderr').write_text(crashed.stderr)
    if crashed.returncode!=17:raise RuntimeError('child did not reach post-write crash')
    row=queue.task_rows(first)[0]
    checks={'real_process_exit':crashed.returncode==17,'crash_left_running':row['status']=='running','vector_cached_before_write':row['vector_json'] is not None}
    time.sleep(max(0,min(5,row['lease_expires_at']-time.time()+.05)))
    queue=EmbeddingTaskStore(SQLiteDatabase(output/'facts.sqlite')).queue
    indices=[]
    try:
        i1=index(output,'m1');indices.append(i1)
        w1=EmbeddingWorker(queue,i1,LocalEmbedder(output,'m1'),owner='restarted-process')
        checks['restart_ready']=w1.run_once(first)['status']=='ready'
        checks['restart_reused_embedding']=len((output/'embedding-calls.jsonl').read_text().splitlines())==1
        second=enqueue(queue,'m2');i2=index(output,'m2');indices.append(i2)
        class FailOnce:
            failed=False
            def __getattr__(self,name):return getattr(i2,name)
            def upsert(self,points):
                if not self.failed:self.failed=True;raise ConnectionError('injected transport failure')
                return i2.upsert(points)
        w2=EmbeddingWorker(queue,FailOnce(),LocalEmbedder(output,'m2'),owner='retry-worker')
        checks['transient_wait']=w2.run_once(second)['error_code']=='transport' and queue.task_rows(second)[0]['status']=='retry_wait'
        wait_due(queue,second);checks['transient_recovered']=w2.run_once(second)['status']=='ready'
        third=enqueue(queue,'m3');i3=index(output,'m3');indices.append(i3)
        denied=EmbeddingWorker(queue,i3,LocalEmbedder(output,'m3',denied=True),owner='auth-worker')
        checks['auth_blocked']=denied.run_once(third)['status']=='blocked'
        checks['blocked_not_retried']=denied.run_once(third)['worked'] is False
        checks['model_isolation']=queue.run(first)['status']=='ready' and queue.run(second)['status']=='ready' and len(i1.search([1,0,0],SCOPE,model_revision='m1',template_revision=TEMPLATE,projection_revision='p',channel_store=tasks))==1
        queue.unblock(third,now=time.time())
        checks['explicit_unblock']=EmbeddingWorker(queue,i3,LocalEmbedder(output,'m3'),owner='fixed-auth').run_once(third)['status']=='ready'
        point_id=queue.task_rows(first)[0]['point_id']
        i1.client.delete(collection_name=i1.collection_name,ids=[point_id],timeout=10)
        checks['missing_detected']=w1.reconcile(first)=='degraded'
        wait_due(queue,first);checks['missing_repaired']=w1.run_once(first)['status']=='ready'
        checks['only_four_model_calls']=len((output/'embedding-calls.jsonl').read_text().splitlines())==4
        output_counts=[len(adapter.search([1,0,0],SCOPE,model_revision=model,template_revision=TEMPLATE,projection_revision='p',channel_store=tasks)) for adapter,model in zip(indices,['m1','m2','m3'])]
        checks['versioned_search']=output_counts==[1,1,1]
        persisted_vectors=sum(len(adapter.get([queue.task_rows(rid)[0]['point_id']])) for adapter,rid in zip(indices,[first,second,third]))
    finally:
        for adapter in indices:adapter.close()
    queue.set_run_state(first,'degraded','publish_checkpoint')
    reopened=index(output,'m1')
    try:checks['ready_checkpoint_reconciled_on_reopen']=EmbeddingWorker(queue,reopened,LocalEmbedder(output,'m1'),owner='reopened').run_once(first)['status']=='ready'
    finally:reopened.close()
    attempts=[dict(r) for r in db.query('SELECT * FROM code_embedding_attempts ORDER BY task_id,attempt')]
    (output/'attempts.json').write_text(json.dumps(attempts,indent=2)+'\n')
    checks['sqlite_integrity']=db.query('PRAGMA integrity_check')[0][0]=='ok'
    persistence_done=time.monotonic()
    report={'case':'embedding_worker_recovery','input_files':1,'input_bytes':len(text.encode()),'documents':1,
        'model_versions':3,'persisted_tasks':db.query('SELECT COUNT(*) FROM code_embedding_tasks')[0][0],
        'ready_tasks':db.query("SELECT COUNT(*) FROM code_embedding_tasks WHERE status='ready'")[0][0],
        'ready_version_channels':db.query("SELECT COUNT(*) FROM code_embedding_projection_runs WHERE status='ready'")[0][0],
        'attempts':len(attempts),'embedding_calls':len((output/'embedding-calls.jsonl').read_text().splitlines()),'external_model_calls':0,'injected_process_exits':1,
        'output_count':sum(output_counts),'persisted_vectors':persisted_vectors,
        'business_completed_ms':(business_done-started)*1000,'persistence_completed_ms':(persistence_done-started)*1000,
        'persistence_lag_ms':(persistence_done-business_done)*1000,
        'checks':checks,'passed_checks':sum(checks.values()),'required_checks':len(checks),
        'elapsed_ms':(time.monotonic()-started)*1000,'case_pass':all(checks.values())}
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    return 0 if report['case_pass'] else 1


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--child',action='store_true');a=p.parse_args()
    if a.child:child(a.output)
    else:raise SystemExit(run(a.output))
