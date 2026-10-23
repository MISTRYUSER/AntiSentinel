"""Deterministic node and run outcome rules."""

FAILED_DEPENDENCIES = frozenset({'failed', 'rejected', 'blocked', 'uncertain', 'cancelled'})


def run_status(nodes):
    states = {value['status'] for value in nodes.values()}
    if 'uncertain' in states:
        return 'needs_review'
    if 'cancelled' in states:
        return 'cancelled'
    if 'waiting_approval' in states:
        return 'waiting_approval'
    if states & {'failed', 'rejected', 'blocked'}:
        return 'failed'
    return 'completed' if states == {'succeeded'} else 'running'

