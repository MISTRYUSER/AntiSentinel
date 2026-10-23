from copy import deepcopy
from threading import Barrier

import pytest

from antisentinel.control.planning import PlanningService
from antisentinel.tools.manifest import ToolDefinition
from antisentinel.tools.registry import ToolRegistry
from tests.test_live_intent_http import rig


def prepared(tmp_path):
    service, runtime, model, client, headers = rig(tmp_path)
    sid = client.post('/api/intent/sessions', headers=headers, json={'title': 'DAG'}).json()['session_id']
    calls = []
    def registry():
        reg = ToolRegistry(auto_discover=False)
        for name in ('a', 'b', 'c'):
            reg.register(ToolDefinition(name, name, {'type': 'object'}, lambda _, name=name: calls.append(name) or {'name': name}))
        return reg
    service.registry_factory = registry
    class Planner:
        def complete_json(self, *args, **kwargs):
            return {'hypotheses': ['timeout'], 'checks': [
                {'step_id': 'a', 'purpose': 'logs', 'tool_name': 'a', 'arguments': {}, 'depends_on': []},
                {'step_id': 'b', 'purpose': 'metrics', 'tool_name': 'b', 'arguments': {}, 'depends_on': []},
                {'step_id': 'c', 'purpose': 'join', 'tool_name': 'c', 'arguments': {}, 'depends_on': ['a', 'b']},
            ]}
    incident_id = str(service.sessions[sid].incident_id)
    definition = PlanningService(Planner(), registry()).generate({
        'intent_ref': {'session_id': sid, 'incident_id': incident_id},
        'objective': '排查', 'entities': {}, 'constraints': [], 'provenance': [],
    })
    with runtime.routes.db() as db:
        db.execute('INSERT INTO plans VALUES (?,?,?,?,?)', (definition['plan_id'], sid, 'draft', 1, __import__('json').dumps(definition)))
    url = f"/api/intent/sessions/{sid}/plans/{definition['plan_id']}/execute"
    return service, runtime, client, headers, sid, definition, calls, url


def test_explicit_execution_is_persisted_and_replay_does_not_repeat_tools(tmp_path):
    _, runtime, client, headers, sid, definition, calls, url = prepared(tmp_path)
    response = client.post(url, headers=headers, json={'expected_version': 1})
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'completed'
    assert sorted(calls) == ['a', 'b', 'c'] and calls[-1] == 'c'
    again = client.post(url, headers=headers, json={'expected_version': 1})
    assert again.status_code == 200, again.text
    assert again.json()['nodes'] == response.json()['nodes']
    assert len(calls) == 3
    artifact = runtime.routes.artifact('owner', sid, definition['plan_id'])
    assert artifact['execution_status'] == 'completed'
    assert artifact['execution']['nodes'] == response.json()['nodes']


def test_execution_requires_auth_and_a_matching_plan_version(tmp_path):
    _, _, client, headers, _, _, calls, url = prepared(tmp_path)
    assert client.post(url, json={'expected_version': 1}).status_code == 401
    assert client.post(url, headers=headers, json={'expected_version': 2}).status_code == 409
    assert client.post(url, headers=headers, json={'expected_version': True}).status_code == 422
    assert calls == []


def test_other_session_or_actor_cannot_execute_plan(tmp_path):
    _, runtime, client, headers, sid, definition, calls, _ = prepared(tmp_path)
    other = client.post('/api/intent/sessions', headers=headers, json={'title': 'other'}).json()['session_id']
    response = client.post(f"/api/intent/sessions/{other}/plans/{definition['plan_id']}/execute", headers=headers, json={'expected_version': 1})
    assert response.status_code == 404
    assert hasattr(runtime.routes, 'execute_plan'), 'plan execution service is missing'
    with pytest.raises(ValueError):
        runtime.routes.execute_plan('intruder', sid, definition['plan_id'], 1)
    assert calls == []


def test_changed_tool_catalog_cannot_silently_execute_a_saved_plan(tmp_path):
    service, _, client, headers, _, _, calls, url = prepared(tmp_path)
    old = service.registry_factory
    def changed():
        reg = old()
        reg.register(ToolDefinition('added', 'changed catalog', {'type': 'object'}, lambda _: {}))
        return reg
    service.registry_factory = changed
    response = client.post(url, headers=headers, json={'expected_version': 1})
    assert response.status_code == 409
    assert calls == []


def test_tampered_plan_or_rebound_target_is_rejected(tmp_path):
    _, runtime, client, headers, sid, definition, calls, url = prepared(tmp_path)
    modified = deepcopy(definition)
    modified['steps'][0]['tool_name'] = 'c'
    with runtime.routes.db() as db:
        db.execute('UPDATE plans SET payload=? WHERE plan_id=?', (__import__('json').dumps(modified), definition['plan_id']))
    response = client.post(url, headers=headers, json={'expected_version': 1})
    assert response.status_code == 409
    assert calls == []
