"""Run inspectable component regressions without inventing model quality results."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from time import monotonic
import xml.etree.ElementTree as ET


GROUPS = {
    'runtime': ['tests/test_checkpoint.py', 'tests/test_runtime_loop.py', 'tests/test_working_set.py',
                'tests/test_context_pack_budget.py', 'tests/test_prd002b_phase_b_stress.py',
                'tests/test_source_window.py', 'tests/test_runtime_cancel.py', 'tests/test_application_persistence.py'],
    'memory': ['tests/test_memory_pipeline.py', 'tests/test_memory_jobs.py', 'tests/test_memory_resolution.py',
               'tests/test_memory_namespace_isolation.py', 'tests/test_memory_isolation_e2e.py',
               'tests/test_memory_candidate_retrieval.py', 'tests/test_hybrid_ranker.py',
               'tests/test_trusted_memory_recall.py', 'tests/test_operator_graph.py'],
    'skill': ['tests/test_skill_catalog.py', 'tests/test_skill_loader.py', 'tests/test_skill_release.py',
              'tests/test_skill_runtime.py', 'tests/test_skill_activation.py',
              'tests/test_tracing_metrics.py', 'tests/test_cross_process_tracing.py'],
    'rag': ['tests/test_code_map_incremental.py', 'tests/test_code_map_source_context.py',
            'tests/test_code_keyword_retrieval.py', 'tests/test_code_retrieval_hybrid.py',
            'tests/test_code_retrieval_evaluation.py', 'tests/test_ragas_evaluation.py',
            'tests/test_retrieval_source_consistency.py', 'tests/test_retrieval_recovery.py'],
    'dag': ['tests/test_plan_dag.py', 'tests/test_dag_runtime.py', 'tests/test_plan_execution_api.py', 'tests/test_plan_recovery_case.py',
            'tests/test_intent_contract.py', 'tests/test_intent_dispatch.py', 'tests/test_live_intent_http.py'],
}

# User-supplied goals, deliberately separate from measured values.
TARGETS = {
    'runtime': {'max_context_tokens': 32000, 'overflow_count': 0},
    'memory': {'evaluated_cases': 470, 'recall_at_5': 0.9148, 'mrr_at_5': 0.9156, 'p95_ms': 4.28},
    'skill': {'cold_start_and_first_plan_seconds': 15},
    'rag': {'human_labeled_queries': 24, 'recall_at_5': 0.925, 'mrr': 0.82},
    'dag': {'composite_queries': 50, 'p90_seconds': 8.6, 'macro_f1': 0.966, 'unsafe_releases': 0},
}


def source_fingerprint(root):
    root = Path(root)
    files = [p for directory in ('src', 'tests', 'scripts') for p in (root/directory).rglob('*.py')]
    if (root/'pyproject.toml').exists():
        files.append(root/'pyproject.toml')
    records = [(str(p.relative_to(root)), hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(files)]
    return hashlib.sha256(json.dumps(records, separators=(',', ':')).encode()).hexdigest()


def read_junit(path, *, exitcode):
    result = {'passed': False, 'tests': 0, 'failures': 0, 'errors': 0, 'skipped': 0, 'exitcode': exitcode}
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        result['report_error'] = type(exc).__name__
        return result
    cases = list(root.iter('testcase'))
    result.update(tests=len(cases), failures=sum(c.find('failure') is not None for c in cases),
                  errors=sum(c.find('error') is not None for c in cases),
                  skipped=sum(c.find('skipped') is not None for c in cases))
    result['passed'] = exitcode == 0 and result['tests'] > 0 and not any(result[k] for k in ('failures', 'errors', 'skipped'))
    result['unsuccessful_cases'] = [c.get('classname', '') + '::' + c.get('name', '')
                                  for c in cases if any(c.find(k) is not None for k in ('failure', 'error', 'skipped'))]
    return result


def run_group(name, paths, *, root, output, timeout=180):
    root, output = Path(root).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    xml = output/(name + '.xml')
    log = output/(name + '.log')
    command = [sys.executable, '-m', 'pytest', *paths, '-q', '-m', 'not integration', '--junitxml=' + str(xml)]
    env = os.environ.copy()
    env['OTEL_TRACES_SAMPLER'] = 'always_on'  # Test assertions must not depend on host sampling.
    started = monotonic()
    # Never consume an earlier success report when the current process produces none.
    xml.unlink(missing_ok=True)
    with log.open('wb') as stream:
        try:
            completed = subprocess.run(command, cwd=root, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
            code = completed.returncode
        except subprocess.TimeoutExpired:
            code = 124
    return {**read_junit(xml, exitcode=code), 'command': command, 'duration_seconds': monotonic() - started,
            'junit_path': str(xml), 'log_path': str(log)}


def build_report(groups, *, source_fingerprint):
    complete = {name: deepcopy(groups.get(name, {'passed': False, 'status': 'not_run'})) for name in GROUPS}
    return {
        'schema_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(),
        'source_fingerprint': source_fingerprint,
        'environment': {'python': platform.python_version(), 'platform': platform.platform()},
        'groups': complete,
        'regression_pass': all(r.get('passed') is True for r in complete.values()),
        'acceptance_pass': False,
        'effectiveness': {name: {'status': 'not_evaluated', 'target': deepcopy(goal), 'measured': None}
                          for name, goal in TARGETS.items()},
        'limitations': [
            'Component tests do not establish real-model quality or production latency.',
            'Historical reports retain their original dataset/model/host scope; they are not rerun results.',
            'Real-model paired evaluation, reviewed labels and full LongMemEval-S input are still required.',
        ],
    }
