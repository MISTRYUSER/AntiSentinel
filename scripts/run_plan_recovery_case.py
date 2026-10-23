#!/usr/bin/env python3
"""Real process-exit/lock case, using isolated SQLite and local tools only."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from antisentinel.orchestration.runtime import PlanRuntime
from antisentinel.orchestration.store import PlanBusy, SQLitePlanStore
from antisentinel.tools.manifest import ToolDefinition
from antisentinel.tools.registry import ToolRegistry
from antisentinel.worker.execution.tool_executor import ToolExecutionScope


def build(root, mode):
    directory = root/mode
    directory.mkdir(parents=True, exist_ok=True)
    registry = ToolRegistry(auto_discover=False)

    def first(_):
        path = directory/'a-count'
        count = int(path.read_text()) if path.exists() else 0
        path.write_text(str(count + 1))
        return {'ok': 'a'}

    def interrupted(_):
        path = directory/'b-count'
        count = int(path.read_text()) if path.exists() else 0
        path.write_text(str(count + 1))
        if count == 0:
            os._exit(7)  # No finally/atexit/ThreadPool cleanup: a real abrupt exit.
        return {'ok': 'b'}

    registry.register(ToolDefinition('a', 'a', {'type': 'object'}, first, implementation_version='recovery-case-v1'))
    registry.register(ToolDefinition('b', 'b', {'type': 'object'}, interrupted,
                                    read_only=mode == 'read', implementation_version='recovery-case-v1'))
    store = SQLitePlanStore(directory/'runs.sqlite')
    runtime = PlanRuntime(registry, store, max_workers=1, max_attempts=2)
    definition = {'plan_id': 'p', 'steps': [
        {'step_id': 'a', 'tool_name': 'a', 'arguments': {}, 'depends_on': []},
        {'step_id': 'b', 'tool_name': 'b', 'arguments': {}, 'depends_on': ['a']},
    ]}
    scope = ToolExecutionScope(frozenset({'a', 'b'}), read_only_mode=mode == 'read')
    return runtime, definition, scope


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--child', choices=['read', 'write'])
    parser.add_argument('--lock-probe', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if args.lock_probe:
        store = SQLitePlanStore(output/'locks.sqlite')
        try:
            with store.exclusive('run'):
                return 1
        except PlanBusy:
            return 0
    if args.child:
        runtime, definition, scope = build(output, args.child)
        runtime.run(definition, run_id='run', scope=scope)
        return 1  # Child must have exited inside the tool.
    output.mkdir(parents=True, exist_ok=False)
    reports, exits = {}, {}
    for mode in ('read', 'write'):
        child = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--output', str(output), '--child', mode],
                               capture_output=True, timeout=10)
        exits[mode] = child.returncode
        if child.returncode != 7:
            raise RuntimeError(f'{mode} child did not reach the crash boundary: {child.returncode}')
        runtime, definition, scope = build(output, mode)
        reports[mode] = runtime.run(definition, run_id='run', scope=scope)
    store = SQLitePlanStore(output/'locks.sqlite')
    with store.exclusive('run'):
        probe = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--output', str(output), '--lock-probe'],
                               capture_output=True, timeout=10)
    counts = {mode + '_' + node: int((output/mode/(node + '-count')).read_text())
              for mode in ('read', 'write') for node in ('a', 'b')}
    passed = (reports['read']['status'] == 'completed' and reports['write']['status'] == 'needs_review'
              and counts == {'read_a': 1, 'read_b': 2, 'write_a': 1, 'write_b': 1} and probe.returncode == 0)
    report = {'pass': passed, 'scope': 'real_local_process_exit_no_model_calls', 'child_exitcodes': exits,
              'counters': counts, 'cross_process_lock_exclusive': probe.returncode == 0, **reports}
    (output/'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'pass': passed, 'counters': counts}))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
