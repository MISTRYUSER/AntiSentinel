def test_trusted_memory_case_report_meets_hard_gates(tmp_path):
    from scripts.run_trusted_memory_case import run_case

    report = run_case(tmp_path)

    assert report["sessions"] == 4
    assert report["unique_candidate_ids"] == 4
    assert report["persisted_records"] >= 4
    assert report["verified_source_refs"] >= 4
    assert report["cross_scope_injected"] == 0
    assert report["future_injected"] == 0
    assert report["provenance_invalid_injected"] == 0
    assert report["background_exceptions"] == 0
    assert report["case_pass"] is True
