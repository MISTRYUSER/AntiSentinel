"""Actual answer/plan persistence and task-control gateway, without tool simulation."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from antisentinel.control.planning import PlanningService
from antisentinel.domain.intent import canonical, object_fields, require, text
from antisentinel.ports.intent_store import IntentConflict, IntentAccessDenied


class NewEvidenceRequired(RuntimeError):
    pass


class LiveIntentRoutes:
    def __init__(self, application, contexts, model, root):
        self.application, self.contexts, self.model = application, contexts, model
        self.path = Path(root)/'live-routes.sqlite'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS deliveries(key TEXT PRIMARY KEY,session TEXT NOT NULL,
                    request TEXT NOT NULL,receipt TEXT NOT NULL,result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS plans(plan_id TEXT PRIMARY KEY,session TEXT NOT NULL,
                    status TEXT NOT NULL,version INTEGER NOT NULL,payload TEXT NOT NULL);
            ''')

    @contextmanager
    def db(self):
        conn=sqlite3.connect(self.path,timeout=5);conn.row_factory=sqlite3.Row
        try:
            with conn:yield conn
        finally:conn.close()

    def tasks(self, session, actor):
        self.contexts.authorize(actor, session)
        with self.db() as db:
            result=[{'task_id':r['plan_id'],'run_id':None,'state_version':str(r['version'])}
                    for r in db.execute('SELECT * FROM plans WHERE session=? ORDER BY rowid',(session,))]
        if session in self.application._cancel_requests or self.application.sessions[session].status.value in {'completed','failed','cancelled'}:
            state=self.application.session_control_status(session,actor)
            result.append({k:state[k] for k in ('task_id','run_id','state_version')})
        return result

    def lookup(self,key):
        with self.db() as db:
            row=db.execute('SELECT * FROM deliveries WHERE key=?',(key,)).fetchone()
            if row is None:return None
            receipt=json.loads(row['receipt']);request=json.loads(row['request'])
            if receipt['status']=='accepted' and receipt['route']=='task_control':
                task=request['context_binding']['task']
                if task['task_id'].startswith('run:'):
                    actor=self.contexts.current_actor()
                    state=self.application.session_control_status(task['run_id'],actor)
                    if state['status'] in {'completed','failed','cancelled'}:
                        receipt['status']='completed'
                        db.execute('UPDATE deliveries SET receipt=?,result=? WHERE key=?',(canonical(receipt),canonical(state),key))
            return receipt

    def submit(self,request,validate_current):
        existing=self.lookup(request['key'])
        if existing is not None:return existing
        route=request['route'];sid=request['intent_ref']['session_id']
        if route=='direct_answer':
            data=self.model.complete_json(
                '只根据现有授权上下文回答，输出JSON {"answer":"回答", "needs_evidence":false}。'
                '如果需要读取新日志/源码/状态才能作答，needs_evidence=true，answer为空。不得编造取证结果。',
                {'request':request,'context':self.contexts.current_context()},timeout_seconds=30)
            object_fields(data,{'answer','needs_evidence'},'answer')
            require(type(data['needs_evidence']) is bool,'invalid evidence flag')
            if data['needs_evidence']:raise NewEvidenceRequired('new evidence requires plan')
            require(text(data['answer']),'empty answer')
            downstream='answer-'+str(uuid4())
        elif route=='plan':
            registry=self.application.registry_factory()
            incident=self.application.incidents[request['intent_ref']['incident_id']]
            self.application._bind_code_map_tools(registry,incident)
            if self.application.retrieval_tools is not None:
                for tool in self.application.retrieval_tools.for_incident(str(incident.incident_id)):
                    if registry.resolve(tool.name) is None:registry.register(tool)
            data=PlanningService(self.model,registry).generate(request)
            downstream=data['plan_id']
        elif route=='task_control':
            data=None;downstream='control-'+str(uuid4())
        else:raise ValueError('unsupported gateway route')
        with validate_current(),self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT receipt FROM deliveries WHERE key=?',(request['key'],)).fetchone()
            if row is not None:return json.loads(row['receipt'])
            status='completed'
            if route=='plan':
                db.execute('INSERT INTO plans VALUES (?,?,?,?,?)',(downstream,sid,data['status'],1,canonical(data)))
            if route=='task_control':
                task=request['context_binding']['task'];operation=request['control']['operation']
                if task['task_id'].startswith('run:'):
                    actor=self.contexts.current_actor()
                    method=self.application.request_session_cancel if operation=='cancel' else self.application.session_control_status
                    data=method(task['run_id'],actor)
                    if operation=='cancel' and data['status'] not in {'completed','failed','cancelled'}:status='accepted'
                else:
                    plan=db.execute('SELECT * FROM plans WHERE plan_id=? AND session=?',(task['task_id'],sid)).fetchone()
                    if plan is None:raise IntentAccessDenied('task not accessible')
                    if str(plan['version'])!=task['state_version']:raise IntentConflict('task state changed')
                    state=plan['status']
                    if operation=='cancel' and state!='cancelled':
                        state='cancelled';db.execute('UPDATE plans SET status=?,version=version+1 WHERE plan_id=?',(state,task['task_id']))
                    data={'task_id':task['task_id'],'status':state,'execution_status':'not_submitted'}
            receipt={k:request[k] for k in ('key','intent_ref','route','step')}
            receipt.update(receipt_id=str(uuid4()),receiver_kind='real',status=status,downstream_id=downstream)
            db.execute('INSERT INTO deliveries VALUES (?,?,?,?,?)',(request['key'],sid,canonical(request),canonical(receipt),canonical(data)))
        return receipt

    def artifact(self,actor,session,artifact_id):
        self.contexts.authorize(actor,session)
        with self.db() as db:
            row=db.execute('SELECT * FROM plans WHERE plan_id=? AND session=?',(artifact_id,session)).fetchone()
            if row:return {'plan_id':row['plan_id'],'definition':json.loads(row['payload']),
                           'status':row['status'],'state_version':str(row['version']),'execution_status':'not_submitted'}
            for row in db.execute('SELECT receipt,result FROM deliveries WHERE session=?',(session,)):
                if json.loads(row['receipt'])['downstream_id']==artifact_id:return json.loads(row['result'])
        raise KeyError('artifact not found')
