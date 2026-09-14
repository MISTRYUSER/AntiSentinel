"""Durable intent orchestration for already-extracted candidates, without dispatch."""
from datetime import datetime, timezone
from time import time
from uuid import uuid4

from antisentinel.control.intent_validation import resolve_candidate, validate_sources
from antisentinel.domain.intent import AuthorizedIntentContext, IntentCandidate, IntentIdentity, content_hash
from antisentinel.ports.intent_store import IntentConflict, IntentStore


class IntentService:
    def __init__(self, store: IntentStore, *, clock=time):
        self.store = store
        self.clock = clock

    def resolve(self, candidate: IntentCandidate, context: AuthorizedIntentContext, *, expected_revision: int,
                intent_id: str | None = None, clarification_id: str | None = None):
        if type(expected_revision) is not int or expected_revision < 0:
            raise IntentConflict('invalid expected revision')
        ctx, current = context.to_dict(), candidate.to_dict()
        digest = content_hash({'message': ctx['messages'][ctx['message_id']]})
        replay = self.store.replay(ctx, digest)
        if replay is not None:
            return replay
        validate_sources(current, ctx)
        same_message = self.store.prior_message(ctx)
        if same_message and intent_id and same_message != intent_id:
            raise IntentConflict('message already belongs to another intent')
        iid = intent_id or same_message
        previous = None
        now = self.clock()
        if iid:
            previous = self.store.get(iid, ctx['actor_id'], ctx['session_id'], ctx['incident_id']).to_dict()
            if previous['identity']['revision'] != expected_revision:
                raise IntentConflict('stale expected revision')
            if current['relation'] == 'new_request' and not same_message:
                raise IntentConflict('new requests require a new intent identity')
            ctx['previous_intent'] = {'intent_id': iid, 'revision': expected_revision}
            if current['relation'] in ('continuation', 'correction'):
                current, ctx = _merge(previous, current, ctx)
                for gap in previous['missing_fields']:
                    if gap['code'] == 'required' and gap['path'] not in ctx['required_fields']:
                        ctx['required_fields'].append(gap['path'])
            question = self.store.question(iid, ctx['actor_id'], ctx['session_id'], ctx['incident_id'], now=now)
            stale = question is not None and question['state'] == 'expired'
            stale = stale or (clarification_id is not None and (question is None or
                     question['question_id'] != clarification_id or question['state'] not in ('open', 'waiting_user')))
            if stale:
                current['missing_fields'].append({'path': '/context_binding/previous_intent',
                                                 'code': 'stale_clarification', 'reason': '澄清关联已失效，请重新确认当前目标。'})
        else:
            if expected_revision:
                raise IntentConflict('expected revision requires an intent identity')
            iid = str(uuid4())
            # Caller hints never establish a binding without scoped storage lookup.
            ctx['previous_intent'] = None
            if clarification_id:
                current['missing_fields'].append({'path': '/clarification', 'code': 'stale_clarification',
                                                 'reason': '请确认要回复的请求。'})
        identity = IntentIdentity.from_dict({
            'intent_id': iid, 'revision': expected_revision + 1, 'schema_version': '1',
            'session_id': ctx['session_id'], 'incident_id': ctx['incident_id'], 'message_id': ctx['message_id'],
            'created_at': datetime.fromtimestamp(now, timezone.utc).isoformat(),
        })
        resolved = resolve_candidate(IntentCandidate.from_dict(current), AuthorizedIntentContext.from_dict(ctx), identity)
        return self.store.commit(resolved, ctx, digest, expected_revision, now=now)


def _merge(previous, current, ctx):
    """Carry verified conversational facts, never carry stale target authorization."""
    sources = {p['path']: p for p in current['provenance']}
    ctx.setdefault('known_fields', {})

    def inherited(path, value):
        ctx['known_fields'][path] = value
        return {'path': path, 'source': 'context', 'ref': ctx['context_version'], 'quote': None}

    if current['relation'] == 'continuation':
        current['objective'] = previous['objective']
        current['intent_type'] = previous['intent_type']
        current['control'] = previous['control']
        sources['/objective'] = inherited('/objective', current['objective'])
        if current['control'] and current['control']['target'] is not None:
            sources['/control/target'] = inherited('/control/target', current['control']['target'])
    for key, value in previous['entities'].items():
        if key not in current['entities']:
            current['entities'][key] = value
            sources[f'/entities/{key}'] = inherited(f'/entities/{key}', value)
    constraints = []
    incoming = {c['kind'] for c in current['constraints']}
    new_sources = {key: value for key, value in sources.items() if not key.startswith('/constraints/')}
    for constraint in previous['constraints']:
        if constraint['kind'] not in incoming:
            path = f'/constraints/{len(constraints)}'
            constraints.append(constraint)
            new_sources[path] = inherited(path, constraint)
    for index, constraint in enumerate(current['constraints']):
        path = f'/constraints/{len(constraints)}'
        constraints.append(constraint)
        source = sources[f'/constraints/{index}'].copy()
        source['path'] = path
        new_sources[path] = source
    current['constraints'] = constraints
    current['provenance'] = list(new_sources.values())
    return current, ctx
