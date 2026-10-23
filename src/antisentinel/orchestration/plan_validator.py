"""Compile dependency declarations against the current tool catalog."""

from .dag import ExecutionDAG


def validate_plan(steps, registry, *, scope=None):
    from antisentinel.worker.execution.tool_executor import ToolExecutor

    graph = ExecutionDAG.from_steps(steps)
    executor = ToolExecutor(registry)
    for node in graph.nodes:
        rejected = executor.preflight(node.tool_name, node.arguments, scope=scope)
        if rejected is not None:
            raise ValueError(f"invalid step {node.step_id}: {rejected.error['code']}")
    return graph

