from concurrent.futures import ThreadPoolExecutor
import hashlib

import pytest

from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.retrieval.models import CodeSearchDocument,CodeSearchScope
from antisentinel.retrieval.sqlite_store import SQLiteCodeSearchStore
from antisentinel.retrieval.vector import EmbeddingTaskStore
from antisentinel.retrieval.embedding_jobs import EmbeddingQueue,LeaseLost


def setup_queue(tmp_path):
    db=SQLiteDatabase(tmp_path/'facts.sqlite');store=SQLiteCodeSearchStore(db);EmbeddingTaskStore(db)
    text='def charge():\n    return 1\n';payload='a.py\ncharge\n'+text
    doc=CodeSearchDocument('repo','snap',1,'commit','node','chunk','a.py','charge','python',hashlib.sha256(text.encode()).hexdigest(),hashlib.sha256(payload.encode()).hexdigest(),'p',text)
    scope=CodeSearchScope('repo','snap',1,'commit');store.begin_manifest(scope,'p',[doc]);store.upsert_documents([doc]);store.publish_manifest(scope,'p')
    queue=EmbeddingQueue(db)
    run=queue.enqueue(scope,model_revision='m',dimension=3,template_revision='path-symbol-source-v1',projection_revision='p',max_attempts=3)
    return db,queue,run,scope,doc


def test_concurrent_claim_and_stale_completion_are_fenced(tmp_path):
    db,q,run,_,_=setup_queue(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda owner:q.claim(run,owner,now=10,lease_seconds=5),['a','b']))
    assert sum(r is not None for r in results)==1
    old=next(r for r in results if r)
    new=q.claim(run,old['lease_owner'],now=16,lease_seconds=5)
    assert new['attempt']==2 and new['lease_token']!=old['lease_token']
    with pytest.raises(LeaseLost):q.complete(old,now=16)
    q.cache_vector(new,[1,0,0],now=16);q.complete(new,now=17)


def test_retry_wait_survives_reopen_and_has_an_attempt_limit(tmp_path):
    db,q,run,_,_=setup_queue(tmp_path)
    for attempt in range(1,4):
        lease=q.claim(run,'worker',now=attempt*10,lease_seconds=5)
        assert lease['attempt']==attempt
        q.fail(lease,'transport',now=attempt*10,retryable=True,backoff_seconds=2)
        q=EmbeddingQueue(SQLiteDatabase(tmp_path/'facts.sqlite'))
        assert q.claim(run,'early',now=attempt*10+1,lease_seconds=5) is None
    assert q.task_rows(run)[0]['status']=='failed'
    assert q.task_rows(run)[0]['retryable']==0
    assert q.claim(run,'later',now=100,lease_seconds=5) is None


def test_cached_vector_survives_lease_recovery_and_cannot_be_replaced(tmp_path):
    _,q,run,_,_=setup_queue(tmp_path)
    first=q.claim(run,'a',now=0,lease_seconds=5);q.cache_vector(first,[1,0,0],now=1)
    second=q.claim(run,'b',now=6,lease_seconds=5)
    assert q.cache_vector(second,[0,1,0],now=7)==[1.,0.,0.]
    with pytest.raises(LeaseLost):q.cache_vector(first,[0,0,1],now=7)


def test_blocked_run_requires_explicit_resume_and_does_not_block_other_model(tmp_path):
    _,q,run,scope,_=setup_queue(tmp_path)
    lease=q.claim(run,'a',now=0,lease_seconds=5);q.fail(lease,'auth',now=1,blocked=True)
    assert q.claim(run,'b',now=2,lease_seconds=5) is None
    other=q.enqueue(scope,model_revision='m2',dimension=3,template_revision='path-symbol-source-v1',projection_revision='p')
    assert q.claim(other,'b',now=2,lease_seconds=5) is not None
    q.unblock(run,now=3)
    assert q.claim(run,'a',now=3,lease_seconds=5) is not None


class FakeEmbedder:
    model_name='m'
    dimension=3
    def __init__(self):self.calls=0
    def embed_documents(self,texts):
        self.calls+=1
        return [[1.,0.,0.] for _ in texts]


class FakeIndex:
    model_revision='m'
    dimension=3
    template_revision='path-symbol-source-v1'
    projection_revision='p'
    def __init__(self):self.points={}
    def upsert(self,points):
        self.points.update({p.point_id:p for p in points})
        return len(points)
    def reconcile(self,points):
        from antisentinel.retrieval.vector import ReconciliationReport
        expected=frozenset(p.point_id for p in points);actual=frozenset(self.points)
        return ReconciliationReport(expected,actual,expected-actual,actual-expected,{})


def test_worker_recovers_after_write_without_second_embedding(tmp_path):
    from antisentinel.retrieval.embedding_worker import EmbeddingWorker
    db,q,run,scope,_=setup_queue(tmp_path);clock=[0.];embed=FakeEmbedder()
    class Crash(BaseException):pass
    class CrashIndex(FakeIndex):
        crashed=False
        def upsert(self,points):
            n=super().upsert(points)
            if not self.crashed:self.crashed=True;raise Crash()
            return n
    index=CrashIndex();worker=EmbeddingWorker(q,index,embed,owner='a',clock=lambda:clock[0],lease_seconds=5)
    with pytest.raises(Crash):worker.run_once(run)
    assert q.task_rows(run)[0]['status']=='running'
    clock[0]=6
    restarted=EmbeddingWorker(EmbeddingTaskStore(SQLiteDatabase(tmp_path/'facts.sqlite')).queue,index,embed,owner='b',clock=lambda:clock[0],lease_seconds=5)
    assert restarted.run_once(run)['status']=='ready'
    assert embed.calls==1 and len(index.points)==1
    tasks=EmbeddingTaskStore(db)
    tasks.set_channel_status(*scope.key,'ready')
    assert tasks.get_channel_status(*scope.key,model_revision='m',dimension=3,template_revision='path-symbol-source-v1',projection_revision='p')=='ready'
    assert tasks.get_channel_status(*scope.key,model_revision='different',dimension=3,template_revision='path-symbol-source-v1',projection_revision='p') is None
    assert [r[0] for r in db.query('SELECT status FROM code_embedding_attempts ORDER BY attempt')]==['lease_expired','ready']


def test_worker_repairs_missing_index_entry_from_cache(tmp_path):
    from antisentinel.retrieval.embedding_worker import EmbeddingWorker
    _,q,run,_,_=setup_queue(tmp_path);clock=[0.];embed=FakeEmbedder();index=FakeIndex()
    worker=EmbeddingWorker(q,index,embed,owner='a',clock=lambda:clock[0])
    assert worker.run_once(run)['status']=='ready'
    index.points.clear()
    assert worker.reconcile(run)=='degraded'
    clock[0]=2
    assert worker.run_once(run)['status']=='ready'
    assert embed.calls==1
    assert q.run(run)['error_code'] is None


def test_worker_auth_failure_blocks_and_manual_setter_cannot_bypass_lease(tmp_path):
    from antisentinel.retrieval.embedding_worker import EmbeddingWorker
    db,q,run,_,_=setup_queue(tmp_path)
    class Denied(FakeEmbedder):
        def embed_documents(self,texts):raise RuntimeError('embedding_http_401')
    result=EmbeddingWorker(q,FakeIndex(),Denied(),owner='a',clock=lambda:0).run_once(run)
    assert result['status']=='blocked'
    task=q.task_rows(run)[0]
    with pytest.raises(ValueError,match='fenced lease'):
        EmbeddingTaskStore(db).set_task_status(task['task_id'],'ready')


def test_renewal_and_expiry_exhaustion(tmp_path):
    _,q,run,_,_=setup_queue(tmp_path)
    first=q.claim(run,'a',now=0,lease_seconds=5);q.renew(first,now=4,lease_seconds=10)
    assert q.claim(run,'b',now=6,lease_seconds=5) is None
    q.claim(run,'b',now=15,lease_seconds=5)
    q.claim(run,'c',now=21,lease_seconds=5)
    assert q.claim(run,'d',now=27,lease_seconds=5) is None
    assert q.task_rows(run)[0]['status']=='failed'
    assert q.run(run)['status']=='degraded'


@pytest.mark.parametrize('code,expected', [('UNAVAILABLE',('milvus_transient',True,False)),('DEADLINE_EXCEEDED',('milvus_transient',True,False)),('PERMISSION_DENIED',('milvus_auth',False,True))])
def test_real_sdk_rpc_error_codes_are_classified(code,expected):
    import grpc
    from pymilvus.exceptions import MilvusException
    from antisentinel.retrieval.embedding_worker import classify_failure
    assert classify_failure(MilvusException(code=getattr(grpc.StatusCode,code),message='redacted'))==expected


def test_ready_tasks_reconcile_after_worker_restart_without_embedding(tmp_path):
    from antisentinel.retrieval.embedding_worker import EmbeddingWorker
    from antisentinel.retrieval.vector import VectorPoint
    _,q,run,_,doc=setup_queue(tmp_path)
    lease=q.claim(run,'old',now=0,lease_seconds=5);q.cache_vector(lease,[1,0,0],now=1)
    index=FakeIndex();index.upsert([VectorPoint('derived',(1,0,0),doc.source_identity,doc.embedding_input_hash,'m',3,'path-symbol-source-v1','p',doc.document_id)])
    q.complete(lease,now=2)
    embed=FakeEmbedder()
    assert EmbeddingWorker(q,index,embed,owner='new',clock=lambda:3).run_once(run)=={'worked':False,'status':'ready'}
    assert embed.calls==0


@pytest.mark.parametrize('vector', [[1,0],[float('nan'),0,1],[0,0,0]])
def test_invalid_embeddings_never_reach_index_or_ready(tmp_path,vector):
    from antisentinel.retrieval.embedding_worker import EmbeddingWorker
    _,q,run,_,_=setup_queue(tmp_path);index=FakeIndex()
    class Invalid(FakeEmbedder):
        def embed_documents(self,texts):return [vector]
    assert EmbeddingWorker(q,index,Invalid(),owner='a',clock=lambda:0).run_once(run)['status']=='blocked'
    assert not index.points and q.task_rows(run)[0]['vector_json'] is None
