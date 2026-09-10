"""Durable version-scoped embedding work with fenced leases and cached vectors."""
from dataclasses import asdict
import hashlib
import json
import math
import uuid
import time

from .models import CodeSearchDocument, normalize_document_record


class LeaseLost(RuntimeError):
    pass


def projection_id(scope, model_revision, dimension, template_revision, projection_revision):
    return hashlib.sha256(json.dumps([*scope.key,model_revision,dimension,template_revision,projection_revision]).encode()).hexdigest()


class EmbeddingQueue:
    def __init__(self,database):
        self.database=database
        with database.transaction() as c:
            fields={r['name'] for r in c.execute('PRAGMA table_info(code_embedding_tasks)')}
            for name,kind in [('run_id','TEXT'),('document_id','TEXT'),('document_json','TEXT'),('lease_token','TEXT'),('lease_expires_at','REAL'),('next_attempt_at','REAL NOT NULL DEFAULT 0'),('max_attempts','INTEGER NOT NULL DEFAULT 3'),('vector_json','TEXT')]:
                if name not in fields:c.execute(f'ALTER TABLE code_embedding_tasks ADD COLUMN {name} {kind}')
            c.execute('''CREATE TABLE IF NOT EXISTS code_embedding_projection_runs (
                run_id TEXT PRIMARY KEY, repository_id TEXT NOT NULL,snapshot_id TEXT NOT NULL,
                published_generation INTEGER NOT NULL,commit_sha TEXT NOT NULL,
                model_revision TEXT NOT NULL,dimension INTEGER NOT NULL,template_revision TEXT NOT NULL,
                projection_revision TEXT NOT NULL,expected_ids_json TEXT NOT NULL,status TEXT NOT NULL,
                mode TEXT NOT NULL,report_json TEXT,error_code TEXT)''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_embedding_claim ON code_embedding_tasks(run_id,status,next_attempt_at,lease_expires_at)')
            c.execute('''CREATE TABLE IF NOT EXISTS code_embedding_attempts (
                task_id TEXT NOT NULL,attempt INTEGER NOT NULL,owner TEXT NOT NULL,lease_token TEXT NOT NULL,
                started_at REAL NOT NULL,finished_at REAL,status TEXT NOT NULL,error_code TEXT,
                PRIMARY KEY(task_id,attempt))''')

    def enqueue(self,scope,*,model_revision,dimension,template_revision,projection_revision,max_attempts=3):
        from .vector import canonical_point_id
        if type(dimension) is not int or dimension<1 or type(max_attempts) is not int or not 1<=max_attempts<=3 or not all(isinstance(v,str) and v.strip() for v in (model_revision,template_revision,projection_revision)):
            raise ValueError('invalid embedding job configuration')
        rid=projection_id(scope,model_revision,dimension,template_revision,projection_revision)
        with self.database.transaction() as c:
            manifest=self._active(c,scope.key,projection_revision)
            docs=list(c.execute('SELECT * FROM code_search_documents WHERE repository_id=? AND snapshot_id=? AND published_generation=? AND commit_sha=? AND projection_revision=?',(*scope.key,projection_revision)))
            if not docs:raise ValueError('empty embedding projection')
            if sorted((normalize_document_record(d) for d in docs),key=lambda d:d['document_id'])!=sorted((normalize_document_record(d) for d in json.loads(manifest['expected_json'])),key=lambda d:d['document_id']):
                raise ValueError('published source projection mismatch')
            expected=sorted(canonical_point_id(d['document_id'],model_revision,dimension,template_revision,projection_revision) for d in docs)
            old=c.execute('SELECT * FROM code_embedding_projection_runs WHERE run_id=?',(rid,)).fetchone()
            if old:
                if old['mode']!='worker' or json.loads(old['expected_ids_json'])!=expected:raise ValueError('projection identity conflict')
                limits={r[0] for r in c.execute('SELECT max_attempts FROM code_embedding_tasks WHERE run_id=?',(rid,))}
                if limits!={max_attempts}:raise ValueError('attempt budget is immutable')
                return rid
            c.execute('INSERT INTO code_embedding_projection_runs VALUES(?,?,?,?,?,?,?,?,?,?,?, ?,NULL,NULL)',(rid,*scope.key,model_revision,dimension,template_revision,projection_revision,json.dumps(expected),'building','worker'))
            for d in docs:
                pid=canonical_point_id(d['document_id'],model_revision,dimension,template_revision,projection_revision)
                if c.execute('SELECT 1 FROM code_embedding_tasks WHERE point_id=?',(pid,)).fetchone():raise ValueError('existing tasks require explicit migration')
                c.execute('''INSERT INTO code_embedding_tasks(task_id,point_id,input_hash,model_revision,dimension,template_revision,projection_revision,status,run_id,document_id,document_json,max_attempts)
                    VALUES(?,?,?,?,?,?,?,'pending',?,?,?,?)''',(pid,pid,d['embedding_input_hash'],model_revision,dimension,template_revision,projection_revision,rid,d['document_id'],json.dumps(dict(d),sort_keys=True),max_attempts))
        return rid

    @staticmethod
    def _active(c,scope_key,revision):
        row=c.execute('''SELECT m.status,m.expected_json FROM code_search_active_projections a JOIN code_search_manifests m
            USING(repository_id,snapshot_id,published_generation,commit_sha,projection_revision)
            WHERE a.repository_id=? AND a.snapshot_id=? AND a.published_generation=? AND a.commit_sha=? AND a.projection_revision=? AND m.expected_json IS NOT NULL''',(*scope_key,revision)).fetchone()
        if row is None or row['status']!='ready':raise ValueError('embedding projection is not active')
        return row

    def run(self,rid):
        rows=self.database.query('SELECT * FROM code_embedding_projection_runs WHERE run_id=?',(rid,))
        if not rows:raise KeyError('unknown embedding run')
        return dict(rows[0])

    def task_rows(self,rid):
        return [dict(r) for r in self.database.query('SELECT * FROM code_embedding_tasks WHERE run_id=? ORDER BY task_id',(rid,))]

    def claim(self,rid,owner,*,now,lease_seconds=120):
        if not owner or not math.isfinite(now) or not math.isfinite(lease_seconds) or lease_seconds<=0:raise ValueError('invalid lease')
        with self.database.transaction() as c:
            run=c.execute('SELECT * FROM code_embedding_projection_runs WHERE run_id=?',(rid,)).fetchone()
            if run is None or run['mode']!='worker':raise ValueError('unknown worker projection')
            if run['status'] in ('blocked','ready'):return None
            self._active(c,tuple(run[k] for k in ('repository_id','snapshot_id','published_generation','commit_sha')),run['projection_revision'])
            c.execute("UPDATE code_embedding_attempts SET status='lease_expired',finished_at=?,error_code='lease_expired' WHERE (task_id,attempt) IN (SELECT task_id,attempt FROM code_embedding_tasks WHERE run_id=? AND status='running' AND lease_expires_at<=?)",(now,rid,now))
            c.execute("UPDATE code_embedding_tasks SET status=CASE WHEN attempt>=max_attempts THEN 'failed' ELSE 'pending' END,retryable=CASE WHEN attempt>=max_attempts THEN 0 ELSE 1 END,error_code='lease_expired',lease_owner=NULL,lease_token=NULL,updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND status='running' AND lease_expires_at<=?",(rid,now))
            if c.execute("SELECT 1 FROM code_embedding_tasks WHERE run_id=? AND status='failed'",(rid,)).fetchone():
                c.execute("UPDATE code_embedding_projection_runs SET status='degraded' WHERE run_id=?",(rid,))
            row=c.execute("SELECT * FROM code_embedding_tasks WHERE run_id=? AND status IN ('pending','retry_wait') AND next_attempt_at<=? AND attempt<max_attempts ORDER BY task_id LIMIT 1",(rid,now)).fetchone()
            if row is None:return None
            token=uuid.uuid4().hex
            c.execute("UPDATE code_embedding_tasks SET status='running',attempt=attempt+1,lease_owner=?,lease_token=?,lease_expires_at=?,started_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP,completed_at=NULL,duration_ms=NULL,error_code=NULL,retryable=1 WHERE task_id=?",(owner,token,now+lease_seconds,row['task_id']))
            c.execute("INSERT INTO code_embedding_attempts VALUES(?,?,?,?,?,NULL,'running',NULL)",(row['task_id'],row['attempt']+1,owner,token,now))
            return dict(c.execute('SELECT * FROM code_embedding_tasks WHERE task_id=?',(row['task_id'],)).fetchone())

    @staticmethod
    def _owned(c,lease,now):
        row=c.execute("SELECT * FROM code_embedding_tasks WHERE task_id=? AND status='running' AND lease_owner=? AND lease_token=? AND lease_expires_at>?",(lease['task_id'],lease['lease_owner'],lease['lease_token'],now)).fetchone()
        if row is None:raise LeaseLost('embedding lease lost')
        return row

    def document(self,lease,*,now):
        with self.database.transaction() as c:
            row=self._owned(c,lease,now)
            current=c.execute('SELECT * FROM code_search_documents WHERE document_id=?',(row['document_id'],)).fetchone()
            expected=normalize_document_record(json.loads(row['document_json']))
            if current is None or normalize_document_record(current)!=expected:raise ValueError('embedding source changed')
            if row['input_hash']!=expected['embedding_input_hash']:raise ValueError('embedding task input changed')
            self._active(c,tuple(expected[k] for k in ('repository_id','snapshot_id','published_generation','commit_sha')),row['projection_revision'])
            return CodeSearchDocument(**{k:expected[k] for k in CodeSearchDocument.__dataclass_fields__})

    def renew(self,lease,*,now,lease_seconds=120):
        if not math.isfinite(lease_seconds) or lease_seconds<=0:raise ValueError('invalid lease duration')
        with self.database.transaction() as c:
            self._owned(c,lease,now)
            c.execute('UPDATE code_embedding_tasks SET lease_expires_at=?,updated_at=CURRENT_TIMESTAMP WHERE task_id=?',(now+lease_seconds,lease['task_id']))

    def cache_vector(self,lease,values,*,now):
        with self.database.transaction() as c:
            row=self._owned(c,lease,now)
            cached=row['vector_json'] is not None
            if cached:values=json.loads(row['vector_json'])
            if len(values)!=row['dimension'] or any(type(v) not in (int,float) or not math.isfinite(v) for v in values) or not any(values):raise ValueError('invalid embedding vector')
            vector=[float(v) for v in values]
            if not cached:c.execute('UPDATE code_embedding_tasks SET vector_json=?,updated_at=CURRENT_TIMESTAMP WHERE task_id=?',(json.dumps(vector),row['task_id']))
            return vector

    def complete(self,lease,*,now):
        with self.database.transaction() as c:
            row=self._owned(c,lease,now)
            if row['vector_json'] is None:raise ValueError('cannot complete without cached vector')
            began=c.execute('SELECT started_at FROM code_embedding_attempts WHERE task_id=? AND attempt=?',(row['task_id'],row['attempt'])).fetchone()[0]
            c.execute("UPDATE code_embedding_tasks SET status='ready',completed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP,duration_ms=?,lease_owner=NULL,lease_token=NULL,error_code=NULL,retryable=0 WHERE task_id=?",(max(0,now-began)*1000,row['task_id']))
            c.execute("UPDATE code_embedding_attempts SET status='ready',finished_at=? WHERE task_id=? AND attempt=?",(now,row['task_id'],row['attempt']))

    def fail(self,lease,error_code,*,now,retryable=False,blocked=False,backoff_seconds=1):
        if not math.isfinite(backoff_seconds) or backoff_seconds<0:raise ValueError('invalid backoff')
        with self.database.transaction() as c:
            row=self._owned(c,lease,now)
            status='blocked' if blocked else ('retry_wait' if retryable and row['attempt']<row['max_attempts'] else 'failed')
            began=c.execute('SELECT started_at FROM code_embedding_attempts WHERE task_id=? AND attempt=?',(row['task_id'],row['attempt'])).fetchone()[0]
            c.execute('UPDATE code_embedding_tasks SET status=?,next_attempt_at=?,error_code=?,duration_ms=?,retryable=?,completed_at=CURRENT_TIMESTAMP,lease_owner=NULL,lease_token=NULL,updated_at=CURRENT_TIMESTAMP WHERE task_id=?',(status,now+backoff_seconds*2**(row['attempt']-1),error_code,max(0,now-began)*1000,int(status=='retry_wait'),row['task_id']))
            c.execute('UPDATE code_embedding_projection_runs SET status=?,error_code=? WHERE run_id=?',('blocked' if blocked else ('degraded' if status=='failed' else 'building'),error_code,row['run_id']))
            c.execute('UPDATE code_embedding_attempts SET status=?,error_code=?,finished_at=? WHERE task_id=? AND attempt=?',(status,error_code,now,row['task_id'],row['attempt']))

    def unblock(self,rid,*,now):
        with self.database.transaction() as c:
            c.execute("UPDATE code_embedding_tasks SET status=CASE WHEN attempt<max_attempts THEN 'pending' ELSE 'failed' END,retryable=CASE WHEN attempt<max_attempts THEN 1 ELSE 0 END,next_attempt_at=?,updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND status='blocked'",(now,rid))
            c.execute("UPDATE code_embedding_projection_runs SET status='building',error_code=NULL WHERE run_id=? AND status='blocked'",(rid,))

    def set_run_state(self,rid,status,error_code):
        if status not in ('blocked','degraded'):raise ValueError('invalid operational run state')
        with self.database.transaction() as c:
            c.execute('UPDATE code_embedding_projection_runs SET status=?,error_code=? WHERE run_id=?',(status,error_code,rid))

    def publish(self,rid,report,*,now=None):
        now=time.time() if now is None else now
        with self.database.transaction() as c:
            run=c.execute('SELECT * FROM code_embedding_projection_runs WHERE run_id=?',(rid,)).fetchone()
            if run is None or run['status']=='blocked':return False
            expected=set(json.loads(run['expected_ids_json']))
            tasks=list(c.execute('SELECT * FROM code_embedding_tasks WHERE run_id=?',(rid,)))
            complete={t['point_id'] for t in tasks if t['status']=='ready'}==expected and len(tasks)==len(expected)
            for task in tasks:
                doc=normalize_document_record(json.loads(task['document_json']))
                actual=c.execute('SELECT * FROM code_search_documents WHERE document_id=?',(task['document_id'],)).fetchone()
                complete=complete and actual is not None and normalize_document_record(actual)==doc and task['input_hash']==doc['embedding_input_hash'] and all(task[k]==run[k] for k in ('model_revision','dimension','template_revision','projection_revision'))
            valid=complete and report.is_consistent and set(report.expected_ids)==expected and set(report.actual_ids)==expected
            self._active(c,tuple(run[k] for k in ('repository_id','snapshot_id','published_generation','commit_sha')),run['projection_revision'])
            details={'expected_ids':sorted(report.expected_ids),'actual_ids':sorted(report.actual_ids),'missing_ids':sorted(report.missing_ids),'extra_ids':sorted(report.extra_ids),'field_mismatches':report.field_mismatches}
            repair=set(report.missing_ids)|set(report.field_mismatches)
            for task in tasks:
                if task['point_id'] in repair and task['status']=='ready':
                    status='retry_wait' if task['attempt']<task['max_attempts'] else 'failed'
                    c.execute('UPDATE code_embedding_tasks SET status=?,next_attempt_at=?,error_code=?,retryable=?,updated_at=CURRENT_TIMESTAMP WHERE task_id=?',(status,now+1,'index_reconciliation',int(status=='retry_wait'),task['task_id']))
                    c.execute('UPDATE code_embedding_attempts SET status=?,error_code=? WHERE task_id=? AND attempt=?',(status,'index_reconciliation',task['task_id'],task['attempt']))
            c.execute('UPDATE code_embedding_projection_runs SET status=?,report_json=?,error_code=? WHERE run_id=?',('ready' if valid else 'degraded',json.dumps(details),None if valid else 'index_reconciliation',rid))
            return valid
