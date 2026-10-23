from importlib import import_module
from pathlib import Path

import pytest


def api():
    from importlib.util import find_spec
    assert find_spec('antisentinel.evaluation.five_capabilities'), 'five-capability acceptance is missing'
    return import_module('antisentinel.evaluation.five_capabilities')


def junit(tmp_path, status='passed'):
    path = tmp_path / 'results.xml'
    detail = {'passed': '', 'failed': '<failure message="broken"/>',
              'skipped': '<skipped message="dependency missing"/>',
              'error': '<error message="collection failed"/>'}[status]
    path.write_text(f'<testsuites><testsuite tests="1"><testcase name="real_test" classname="tests.real">{detail}</testcase></testsuite></testsuites>')
    return path


@pytest.mark.parametrize('status,exitcode,expected', [
    ('passed', 0, True), ('failed', 1, False), ('skipped', 0, False),
    ('error', 2, False), ('passed', 1, False),
])
def test_acceptance_never_turns_failure_skip_or_process_error_into_success(tmp_path, status, exitcode, expected):
    result = api().read_junit(junit(tmp_path, status), exitcode=exitcode)
    assert result['passed'] is expected


def test_absent_or_empty_result_is_not_a_success(tmp_path):
    module = api()
    assert module.read_junit(tmp_path/'absent.xml', exitcode=0)['passed'] is False
    p = tmp_path/'empty.xml'
    p.write_text('<testsuites/>')
    assert module.read_junit(p, exitcode=0)['passed'] is False


def test_regression_success_does_not_assert_unmeasured_effectiveness(tmp_path):
    module = api()
    groups = {name: {'passed': True, 'tests': 2} for name in module.GROUPS}
    report = module.build_report(groups, source_fingerprint='abc')
    assert report['regression_pass'] is True
    assert report['acceptance_pass'] is False
    assert report['effectiveness']['memory']['status'] == 'not_evaluated'
    assert report['effectiveness']['memory']['target']['evaluated_cases'] == 470
    assert report['effectiveness']['memory']['measured'] is None


def test_a_partial_group_run_cannot_pass_the_full_regression():
    report = api().build_report({'runtime': {'passed': True, 'tests': 2}}, source_fingerprint='abc')
    assert report['regression_pass'] is False
    assert report['groups']['dag']['passed'] is False


def test_source_fingerprint_changes_when_code_changes(tmp_path):
    module = api()
    path = tmp_path/'src'/'main.py'
    path.parent.mkdir()
    path.write_text('a=1\n')
    first = module.source_fingerprint(tmp_path)
    path.write_text('a=2\n')
    assert module.source_fingerprint(tmp_path) != first


def test_real_pytest_subprocess_and_cli_report(tmp_path):
    module = api()
    test = tmp_path/'test_sample.py'
    test.write_text('import pytest\ndef test_actual():\n    assert 2+2 == 4\n'
                    '@pytest.mark.integration\ndef test_paid_provider():\n    raise AssertionError("offline runner must not invoke provider")\n')
    result = module.run_group('sample', [str(test)], root=tmp_path, output=tmp_path/'out', timeout=10)
    assert result['passed'] is True
    assert result['tests'] == 1
    assert Path(result['junit_path']).is_file()
    assert result['command'][1:3] == ['-m', 'pytest']
