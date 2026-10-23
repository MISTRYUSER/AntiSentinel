import json
from pathlib import Path
import subprocess
import sys


def test_actual_process_exit_preserves_sibling_and_never_replays_write(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root/'scripts/run_plan_recovery_case.py'),
                             '--output', str(tmp_path/'case')], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path/'case'/'report.json').read_text())
    assert report['pass'] is True
    assert report['child_exitcodes'] == {'read': 7, 'write': 7}
    assert report['read']['status'] == 'completed'
    assert report['write']['status'] == 'needs_review'
    assert report['counters'] == {'read_a': 1, 'read_b': 2, 'write_a': 1, 'write_b': 1}
    assert report['cross_process_lock_exclusive'] is True
