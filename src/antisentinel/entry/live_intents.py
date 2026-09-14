"""Compose authenticated message handling with the actual application and DeepSeek."""
from contextvars import ContextVar
from copy import deepcopy
import hmac
import json
from pathlib import Path
from uuid import uuid4

from antisentinel.control.intent_dispatch import IntentDispatcher
from antisentinel.control.intents import IntentService
from antisentinel.control.live_intent_routes import LiveIntentRoutes, NewEvidenceRequired
from antisentinel.domain.intent import AuthorizedIntentContext, IntentCandidate, CANDIDATE_FIELDS, content_hash, canonical
from antisentinel.domain.session import Session
from antisentinel.entry.intent_application import IntentApplication
from antisentinel.persistence.intent_store import SQLiteIntentStore
from antisentinel.ports.intent_store import IntentAccessDenied, IntentConflict


class LiveIntentRuntime:
    def __init__(self,application,*,root,model,api_token,actor='operator',bindings=None):
        if not api_token or not actor:raise ValueError('intent API authentication must be configured')
        self.application,self.model=application,model
        self._api_token,self.actor=api_token,actor
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.bindings=deepcopy(bindings or {})
        self._context=ContextVar('live_intent_context',default=None)
        self.store=SQLiteIntentStore(self.root/'intents.sqlite')
        self.service=IntentService(self.store)
        self.routes=LiveIntentRoutes(application,self,model,self.root)
        self.dispatcher=IntentDispatcher(self.store,self,answer=self.routes,plan=self.routes,control=self.routes)
        self.entry=IntentApplication(self.service,self.dispatcher,model)
        with self.routes.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS input_contexts(actor TEXT,session TEXT,message_id TEXT,digest TEXT,context TEXT,PRIMARY KEY(actor,session,message_id))')
            db.execute('CREATE TABLE IF NOT EXISTS incident_owners(incident TEXT PRIMARY KEY,actor TEXT NOT NULL)')
        if application.application_store is None:
            from antisentinel.persistence.application_store import FileApplicationStore
            from antisentinel.entry.conversation_store import FileConversationStore
            application.application_store=FileApplicationStore(self.root)
            application.conversation_store=FileConversationStore(self.root)
            application._restore_application_state()

    def authenticate(self,authorization):
        if not authorization or not hmac.compare_digest(authorization,'Bearer '+self._api_token):
            raise PermissionError('authentication required')
        return self.actor

    def authorize(self,actor,session):
        value=self.application.sessions.get(session)
        if value is None or actor not in value.participant_ids:raise IntentAccessDenied('session unavailable')
        return value

    def register_incident(self,actor,incident):
        if actor!=self.actor:raise IntentAccessDenied('invalid actor')
        with self.routes.db() as db:
            db.execute('INSERT INTO incident_owners VALUES (?,?)',(str(incident),actor))

    def authorize_incident(self,actor,incident):
        value=self.application.incidents.get(incident)
        if value is None:raise IntentAccessDenied('incident unavailable')
        with self.routes.db() as db:
            row=db.execute('SELECT actor FROM incident_owners WHERE incident=?',(incident,)).fetchone()
        if row is not None:
            if row['actor']==actor:return value
        elif any(actor in self.application.sessions[str(sid)].participant_ids
                 for sid in value.session_ids if str(sid) in self.application.sessions):
            return value
        raise IntentAccessDenied('incident unavailable')

    def current_actor(self):return self.current_context()['actor_id']
    def current_context(self):
        ctx=self._context.get()
        if ctx is None:raise IntentAccessDenied('no authorized request context')
        return deepcopy(ctx)

    def read(self,actor,session,incident):
        value=self.authorize(actor,session)
        if str(value.incident_id)!=incident:raise IntentAccessDenied('incident mismatch')
        ctx=self.current_context()
        if (ctx['actor_id'],ctx['session_id'],ctx['incident_id'])!=(actor,session,incident):
            raise IntentAccessDenied('scope mismatch')
        ctx['verified_fields']=deepcopy(self.bindings)
        ctx['tasks']=self.routes.tasks(session,actor)
        return AuthorizedIntentContext.from_dict(ctx)

    def create_session(self,actor,title):
        if actor!=self.actor:raise IntentAccessDenied('invalid actor')
        incident=self.application.create_incident(title=title,summary=None,source='intent-api')
        self.register_incident(actor,incident.incident_id)
        session=Session.create(incident_id=incident.incident_id,participant_ids=[actor])
        incident.add_session(session.session_id)
        self.application.sessions[str(session.session_id)]=session
        self.application.application_store.save_incident(incident.to_dict())
        self.application.application_store.save_session(session.to_dict())
        return {'session_id':str(session.session_id),'incident_id':str(incident.incident_id),'status':'ready'}

    def prepare(self,actor,sid,message_id,content,intent_id=None):
        session=self.authorize(actor,sid)
        digest=content_hash({'message':content})
        with self.routes.db() as db:
            row=db.execute('SELECT * FROM input_contexts WHERE actor=? AND session=? AND message_id=?',(actor,sid,message_id)).fetchone()
            if row:
                if row['digest']!=digest:raise IntentConflict('message ID already used with different content')
                return json.loads(row['context'])
            ctx={'actor_id':actor,'session_id':sid,'incident_id':str(session.incident_id),
                 'message_id':message_id,'messages':{message_id:content},'verified_fields':deepcopy(self.bindings),
                 'known_fields':{},'required_fields':[],'tasks':self.routes.tasks(sid,actor),
                 'available_routes':['direct_answer','plan','task_control'],'needs_evidence':False,'previous_intent':None}
            if len(ctx['tasks'])==1:
                ctx['known_fields']['/control/target']=ctx['tasks'][0]['task_id']
                ctx['known_fields']['/entities/task_target']=ctx['tasks'][0]['task_id']
            if intent_id:
                old=self.store.get(intent_id,actor,sid,str(session.incident_id)).to_dict()
                ctx['previous_intent']={'intent_id':intent_id,'revision':old['identity']['revision']}
                ctx['known_fields']['/objective']=old['objective']
                ctx['known_fields'].update({f'/entities/{k}':v for k,v in old['entities'].items()})
                ctx['known_fields'].update({f'/constraints/{i}':v for i,v in enumerate(old['constraints'])})
            ctx['context_version']=content_hash(ctx)
            db.execute('INSERT INTO input_contexts VALUES (?,?,?,?,?)',(actor,sid,message_id,digest,canonical(ctx)))
            return ctx

    def submit(self,actor,sid,*,message_id,content,expected_revision=0,intent_id=None,clarification_id=None):
        if not isinstance(content,str) or not content.strip() or not message_id:raise ValueError('message required')
        ctx=self.prepare(actor,sid,message_id,content,intent_id)
        token=self._context.set(ctx)
        try:
            try:
                result=self.entry.submit(AuthorizedIntentContext.from_dict(ctx),expected_revision=expected_revision,
                                         intent_id=intent_id,clarification_id=clarification_id)
            except NewEvidenceRequired:
                iid=self.store.prior_message(ctx);old=self.store.get(iid,actor,sid,ctx['incident_id']).to_dict()
                ctx['needs_evidence']=True;ctx['context_version']=content_hash({'previous':ctx['context_version'],'needs_evidence':True})
                self._context.set(ctx)
                candidate={k:old[k] for k in CANDIDATE_FIELDS};candidate['relation']='continuation'
                revised=self.service.resolve(IntentCandidate.from_dict(candidate),AuthorizedIntentContext.from_dict(ctx),
                                             expected_revision=old['identity']['revision'],intent_id=iid).to_dict()
                with self.routes.db() as db:
                    db.execute('UPDATE input_contexts SET context=? WHERE actor=? AND session=? AND message_id=?',(canonical(ctx),actor,sid,message_id))
                result=self.dispatcher.dispatch(iid,revised['identity']['revision'],actor,sid,ctx['incident_id'])
            receipt=result['receipt']
            if receipt:
                data=self.routes.artifact(actor,sid,receipt['downstream_id'])
                content=data.get('answer') or ('计划已保存，尚未执行。' if receipt['route']=='plan' else
                        '取消已请求，等待运行端结束。' if receipt['status']=='accepted' else '任务状态：'+data.get('status','completed'))
            else:
                content=result['intent']['clarification']['question'] if result['delivery_status']=='clarify' else '当前不支持该请求。'
            return {**result,'role':'assistant','content':content}
        finally:self._context.reset(token)
