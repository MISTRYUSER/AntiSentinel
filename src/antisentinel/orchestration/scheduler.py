"""Bounded, completion-driven scheduling; all state commits happen on one thread."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context

from .state_machine import FAILED_DEPENDENCIES


class DAGScheduler:
    def __init__(self, *, max_workers=4, max_attempts=2):
        if type(max_workers) is not int or not 1 <= max_workers <= 32:
            raise ValueError('max_workers must be between 1 and 32')
        if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
            raise ValueError('max_attempts must be between 1 and 3')
        self.max_workers = max_workers
        self.max_attempts = max_attempts

    def run(self, graph, records, execute, persist, *, cancel_requested=None):
        nodes = {n.step_id: n for n in graph.nodes}
        inflight = {}
        cancelled = False
        with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix='plan') as pool:
            while True:
                cancelled = cancelled or bool(cancel_requested and cancel_requested())
                if cancelled:
                    for sid, record in records.items():
                        if record['status'] in {'pending', 'waiting_approval'}:
                            record['status'] = 'cancelled'
                            persist('node.cancelled', sid)
                # Iterate to a fixed point: descendants may precede their parents in JSON.
                changed = True
                while changed:
                    changed = False
                    for node in graph.nodes:
                        record = records[node.step_id]
                        if record['status'] == 'pending' and any(records[d]['status'] in FAILED_DEPENDENCIES for d in node.depends_on):
                            record.update(status='blocked', error={'code': 'dependency_failed'})
                            persist('node.blocked', node.step_id)
                            changed = True
                if not cancelled:
                    for sid in graph.ready({sid: r['status'] for sid, r in records.items()}):
                        if len(inflight) >= self.max_workers:
                            break
                        if cancel_requested and cancel_requested():
                            cancelled = True
                            break
                        record = records[sid]
                        record['status'] = 'running'
                        record['attempts'] += 1
                        persist('node.started', sid)  # Commit BEFORE submitting any side effect.
                        future = pool.submit(copy_context().run, execute, nodes[sid])
                        inflight[future] = sid
                if not inflight:
                    if cancelled and any(r['status'] == 'pending' for r in records.values()):
                        continue
                    break
                done, _ = wait(inflight, timeout=0.05, return_when=FIRST_COMPLETED)
                for future in done:
                    sid = inflight.pop(future)
                    result = future.result()  # Infrastructure/persistence errors stop the run; do not hide them.
                    record = records[sid]
                    record.update(result)
                    persist('node.' + record['status'], sid)
                    if (record['status'] == 'failed' and record['read_only']
                            and record['attempts'] < self.max_attempts
                            and (record.get('error') or {}).get('code') == 'tool_execution_failed'):
                        record['status'] = 'pending'
                        persist('node.retry_scheduled', sid)
        return records

