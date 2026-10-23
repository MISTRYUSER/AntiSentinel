from copy import deepcopy

import pytest

from antisentinel.orchestration import dag
from antisentinel.control.planning import PlanningService
from antisentinel.tools.manifest import ToolDefinition
from antisentinel.tools.registry import ToolRegistry


def compile_steps(steps):
    assert hasattr(dag, 'ExecutionDAG'), 'plan DAG compiler is missing'
    return dag.ExecutionDAG.from_steps(steps)


def step(sid, deps=()):
    return {'step_id': sid, 'tool_name': 'health', 'arguments': {'nested': [1]},
            'depends_on': list(deps), 'purpose': sid}


def test_dag_topological_order_and_explicit_parallel_roots():
    graph = compile_steps([step('c', ('a', 'b')), step('a'), step('b')])
    assert graph.topological_order() == ('a', 'b', 'c')
    assert graph.ready({'a': 'succeeded'}) == ('b',)
    assert graph.ready({'a': 'succeeded', 'b': 'succeeded'}) == ('c',)


@pytest.mark.parametrize('steps', [
    [step('a'), step('a')], [step('a', ('missing',))],
    [step('a', ('b',)), step('b', ('a',))], [step('a', ('a',))],
    [step('a', ('b', 'b')), step('b')],
    [{**step('a'), 'depends_on': 'b'}], [{**step('a'), 'arguments': []}],
    [{**step('a'), 'arguments': {'x': float('nan')}}], [],
])
def test_invalid_dag_is_rejected_before_any_execution(steps):
    assert hasattr(dag, 'ExecutionDAG'), 'plan DAG compiler is missing'
    with pytest.raises(ValueError):
        dag.ExecutionDAG.from_steps(steps)


def test_legacy_missing_dependencies_preserve_step_order():
    steps = [step('a'), step('b'), step('c')]
    for s in steps:
        del s['depends_on']
    graph = compile_steps(steps)
    assert graph.nodes[1].depends_on == ('a',)
    assert graph.ready({}) == ('a',)


def test_plan_is_immutable_across_input_and_export_mutations():
    original = [step('a')]
    graph = compile_steps(original)
    digest = graph.content_hash
    original[0]['arguments']['nested'].append(2)
    graph.nodes[0].arguments['nested'].append(3)
    exported = graph.to_dict()
    exported['steps'][0]['arguments']['nested'].append(4)
    assert graph.content_hash == digest
    assert graph.nodes[0].arguments == {'nested': [1]}


class Model:
    def __init__(self, checks):
        self.checks = checks

    def complete_json(self, *args, **kwargs):
        return {'hypotheses': ['timeout'], 'checks': deepcopy(self.checks)}


def request():
    return {'intent_ref': {'session_id': 's', 'incident_id': 'i'}, 'objective': '排查',
            'entities': {}, 'constraints': [], 'provenance': []}


def registry():
    reg = ToolRegistry(auto_discover=False)
    reg.register(ToolDefinition('health', 'read', {'type': 'object'}, lambda _: {}))
    return reg


def test_planner_accepts_dependencies_and_legacy_checks():
    checks = [step('a'), step('b'), step('c', ('a', 'b'))]
    plan = PlanningService(Model(checks), registry()).generate(request())
    assert plan['steps'][0]['depends_on'] == []
    assert plan['steps'][2]['depends_on'] == ['a', 'b']
    assert compile_steps(plan['steps']).ready({}) == ('a', 'b')
    old = PlanningService(Model([{'purpose': 'read', 'tool_name': 'health', 'arguments': {}}] * 2), registry()).generate(request())
    assert old['steps'][1]['depends_on'] == ['step-1']


def test_planner_rejects_invalid_graph_from_model():
    with pytest.raises(ValueError):
        PlanningService(Model([step('a', ('b',)), step('b', ('a',))]), registry()).generate(request())
