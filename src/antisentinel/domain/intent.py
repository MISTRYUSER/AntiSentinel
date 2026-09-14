"""Immutable JSON contracts for intent recognition, with no execution dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any


class IntentValidationError(ValueError):
    """Malformed or unverifiable intent data; callers must not dispatch it."""


INTENT_TYPES = frozenset({'question', 'diagnose', 'execute', 'task_control', 'unknown'})
RELATIONS = frozenset({'new_request', 'continuation', 'correction'})
BUSINESS_ROUTES = frozenset({'direct_answer', 'plan', 'task_control'})
ENTITY_NAMES = frozenset({'service', 'interface', 'environment', 'repository', 'commit', 'time_window', 'task_target'})
CANDIDATE_FIELDS = frozenset({'intent_type', 'relation', 'objective', 'entities', 'constraints',
                              'provenance', 'missing_fields', 'ambiguities', 'control', 'reason', 'confidence'})
IDENTITY_FIELDS = frozenset({'intent_id', 'revision', 'schema_version', 'session_id',
                            'incident_id', 'message_id', 'created_at'})


def require(condition: bool, message: str) -> None:
    if not condition:
        raise IntentValidationError(message)


def text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def object_fields(value: Any, fields: set | frozenset, name: str) -> None:
    require(isinstance(value, dict) and set(value) == fields, f'{name}: unexpected or missing fields')


def canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise IntentValidationError('expected finite JSON data') from exc


def content_hash(value: dict) -> str:
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def validate_identity(value: dict) -> None:
    object_fields(value, IDENTITY_FIELDS, 'identity')
    require(all(text(value[k]) for k in IDENTITY_FIELDS - {'revision'}), 'identity strings required')
    require(type(value['revision']) is int and value['revision'] > 0, 'positive revision required')
    require(value['schema_version'] == '1', 'unsupported schema version')
    try:
        stamp = datetime.fromisoformat(value['created_at'].replace('Z', '+00:00'))
        require(stamp.tzinfo is not None, 'timezone required')
    except ValueError as exc:
        raise IntentValidationError('invalid created_at') from exc


def validate_gaps(value: Any) -> None:
    require(isinstance(value, list), 'gap list required')
    for gap in value:
        require(isinstance(gap, dict), 'gap object required')
        object_fields(gap, {'path', 'code', 'reason'} | ({'candidates'} if 'candidates' in gap else set()), 'gap')
        require(all(text(gap[k]) for k in ('path', 'code', 'reason')), 'gap values required')
        require(gap['path'].startswith('/'), 'gap path must be JSON pointer')
        if 'candidates' in gap:
            require(isinstance(gap['candidates'], list) and all(text(v) for v in gap['candidates']), 'invalid candidates')


def validate_task_binding(task: dict) -> None:
    object_fields(task, {'task_id', 'run_id', 'state_version'}, 'task binding')
    require(text(task['task_id']) and text(task['state_version']), 'invalid task binding')
    require(task['run_id'] is None or text(task['run_id']), 'invalid run binding')


def validate_previous(previous: dict | None) -> None:
    if previous is not None:
        object_fields(previous, {'intent_id', 'revision'}, 'previous_intent')
        require(text(previous['intent_id']) and type(previous['revision']) is int and previous['revision'] > 0, 'invalid previous revision')


def validate_candidate(value: dict) -> None:
    object_fields(value, CANDIDATE_FIELDS, 'candidate')
    require(isinstance(value['intent_type'], str) and value['intent_type'] in INTENT_TYPES, 'invalid intent_type')
    require(isinstance(value['relation'], str) and value['relation'] in RELATIONS, 'invalid relation')
    require(text(value['objective']) and text(value['reason']), 'objective and reason required')
    entities = value['entities']
    require(isinstance(entities, dict) and set(entities) <= ENTITY_NAMES, 'invalid entities')
    require(all(text(v) for v in entities.values()), 'entity values must be nonempty strings')
    constraints = value['constraints']
    require(isinstance(constraints, list), 'constraints list required')
    kinds = set()
    for constraint in constraints:
        object_fields(constraint, {'kind', 'value'}, 'constraint')
        kind = constraint['kind']
        require(isinstance(kind, str) and kind in {'read_only', 'forbid_restart', 'deadline', 'access_scope', 'other'}, 'invalid constraint kind')
        if kind != 'other':
            require(kind not in kinds, 'duplicate constraint kind')
        kinds.add(kind)
        if kind in {'read_only', 'forbid_restart'}:
            require(constraint['value'] is True, 'prohibitions must be true')
        else:
            require(text(constraint['value']), 'constraint text required')
    sources = value['provenance']
    require(isinstance(sources, list), 'provenance list required')
    for source in sources:
        object_fields(source, {'path', 'source', 'ref', 'quote'}, 'provenance')
        require(text(source['path']) and text(source['ref']), 'source path/ref required')
        require(source['source'] in ('user', 'context'), 'model cannot claim server provenance')
        require(text(source['quote']) if source['source'] == 'user' else source['quote'] is None, 'invalid source quote')
    validate_gaps(value['missing_fields'])
    validate_gaps(value['ambiguities'])
    control = value['control']
    if value['intent_type'] == 'task_control':
        object_fields(control, {'operation', 'target'}, 'control')
        require(control['operation'] in ('status', 'cancel'), 'invalid control operation')
        require(control['target'] is None or text(control['target']), 'invalid control target')
    else:
        require(control is None, 'control requires task_control intent')
    confidence = value['confidence']
    require(confidence is None or (type(confidence) in (int, float) and 0 <= confidence <= 1), 'invalid confidence')


@dataclass(frozen=True)
class JsonContract:
    """Keep only a canonical string so nested caller mutations cannot affect a contract."""
    _json: str

    def __post_init__(self) -> None:
        try:
            value = json.loads(self._json)
        except (ValueError, TypeError) as exc:
            raise IntentValidationError('invalid JSON') from exc
        self.validate(value)
        object.__setattr__(self, '_json', canonical(value))

    @classmethod
    def from_dict(cls, value: dict):
        return cls(canonical(value))

    def to_dict(self) -> dict:
        return json.loads(self._json)

    @staticmethod
    def validate(value: dict) -> None:
        require(isinstance(value, dict), 'object required')


class IntentCandidate(JsonContract):
    validate = staticmethod(validate_candidate)


class IntentIdentity(JsonContract):
    validate = staticmethod(validate_identity)


class AuthorizedIntentContext(JsonContract):
    """Server-only snapshot. Never construct from model output or untrusted request fields."""

    @staticmethod
    def validate(value: dict) -> None:
        object_fields(value, {'actor_id', 'session_id', 'incident_id', 'context_version', 'message_id',
                             'messages', 'verified_fields', 'required_fields', 'tasks', 'available_routes',
                             'needs_evidence', 'previous_intent'} | (set(value) & {'known_fields', 'active_write_task', 'active_write_intent_id'}), 'context')
        require(isinstance(value.get('known_fields', {}), dict), 'known_fields object required')
        if value.get('active_write_task') is not None:
            validate_task_binding(value['active_write_task'])
        if value.get('active_write_intent_id') is not None:
            require(text(value['active_write_intent_id']), 'invalid active write intent')
        require(all(text(value[k]) for k in ('actor_id', 'session_id', 'incident_id', 'context_version', 'message_id')), 'context identity required')
        messages = value['messages']
        require(isinstance(messages, dict) and all(text(k) and text(v) for k, v in messages.items()), 'invalid authorized messages')
        require(value['message_id'] in messages, 'input message missing')
        require(isinstance(value['verified_fields'], dict), 'verified_fields object required')
        require(isinstance(value['required_fields'], list) and all(text(v) and v.startswith('/') for v in value['required_fields']), 'invalid required fields')
        require(isinstance(value['available_routes'], list) and all(isinstance(v, str) and v in BUSINESS_ROUTES for v in value['available_routes']), 'invalid capabilities')
        require(type(value['needs_evidence']) is bool, 'needs_evidence must be server boolean')
        require(isinstance(value['tasks'], list), 'tasks list required')
        ids = []
        for task in value['tasks']:
            validate_task_binding(task)
            ids.append(task['task_id'])
        require(len(ids) == len(set(ids)), 'duplicate task binding')
        validate_previous(value['previous_intent'])


class ResolvedIntent(JsonContract):
    """Validated envelope, not a dispatch permission. Consumers must recheck authorization."""

    @staticmethod
    def validate(value: dict) -> None:
        object_fields(value, CANDIDATE_FIELDS | {'identity', 'resolution_status', 'route', 'clarification', 'context_binding'}, 'resolved intent')
        validate_candidate({key: value[key] for key in CANDIDATE_FIELDS})
        identity = value['identity']
        object_fields(identity, IDENTITY_FIELDS | {'content_hash'}, 'resolved identity')
        validate_identity({key: identity[key] for key in IDENTITY_FIELDS})
        # Only identity is changed below. Copying it avoids a full JSON round trip
        # while keeping the caller's payload untouched.
        unhashed = {**value, 'identity': dict(identity)}
        supplied = unhashed['identity'].pop('content_hash')
        require(supplied == content_hash(unhashed), 'intent hash mismatch')
        status, route = value['resolution_status'], value['route']
        require((status == 'needs_clarification' and route == 'clarify') or
                (status == 'unsupported' and route == 'unsupported') or
                (status == 'resolved' and isinstance(route, str) and route in BUSINESS_ROUTES), 'invalid status/route combination')
        binding = value['context_binding']
        object_fields(binding, {'context_version', 'previous_intent', 'task'}, 'context_binding')
        require(text(binding['context_version']), 'context version required')
        validate_previous(binding['previous_intent'])
        if binding['task'] is not None:
            validate_task_binding(binding['task'])
        if status == 'resolved':
            require(not value['missing_fields'] and not value['ambiguities'], 'unresolved fields cannot dispatch')
            require(value['intent_type'] != 'unknown', 'unknown cannot dispatch')
            require(route != 'direct_answer' or value['intent_type'] == 'question', 'only questions can answer directly')
            require((route == 'task_control') == (value['intent_type'] == 'task_control'), 'invalid control routing')
            if route == 'task_control':
                require(isinstance(binding['task'], dict), 'control binding required')
        clarification = value['clarification']
        if status == 'needs_clarification':
            object_fields(clarification, {'question', 'candidates', 'revision'}, 'clarification')
            require(text(clarification['question']) and isinstance(clarification['candidates'], list), 'invalid clarification')
            require(clarification['revision'] == identity['revision'], 'stale clarification revision')
        else:
            require(clarification is None, 'unexpected clarification')
