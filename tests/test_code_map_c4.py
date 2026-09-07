def test_six_real_git_changes_preserve_incremental_semantics_and_history(tmp_path):
    from scripts.code_map_c4 import run_c4
    report = run_c4(tmp_path, timeout=120)
    assert report['case_pass'], report
    assert report['scenarios_passed'] == 6
    assert report['partial_generations'] == [1, 2]
    assert report['old_snapshot_changed'] == 0
    assert all(s['cache_hits'] > 0 for s in report['scenarios'])
