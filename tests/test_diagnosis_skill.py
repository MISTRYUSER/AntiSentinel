import json
from pathlib import Path


def contracts():
    root = Path(__file__).parents[1] / "src" / "antisentinel" / "capabilities" / "diagnosis"
    return json.loads((root / "contracts.json").read_text())


def test_diagnosis_contracts_cover_match_no_match_and_write_boundary():
    values = contracts()["cases"]
    assert [item["case_id"] for item in values] == [
        "diagnosis-health-match", "diagnosis-no-match", "diagnosis-write-forbidden",
    ]
    assert all(item["verifier_id"] == "diagnosis-health-v1" for item in values)


def test_verifier_rejects_loaded_skill_without_actual_health_evidence():
    from antisentinel.evaluation.skill_integration import verify_contract

    case = contracts()["cases"][0]
    outcome = verify_contract(case, {"loaded_skill_id": "diagnosis/diagnosis", "tool_names": ["read_health"], "health_evidence": None})

    assert outcome.passed is False
    assert outcome.failures == ("uses_actual_health_evidence",)


def test_verifier_accepts_health_evidence_and_rejects_forbidden_write_tool():
    from antisentinel.evaluation.skill_integration import verify_contract

    match, write = contracts()["cases"][0], contracts()["cases"][2]
    assert verify_contract(match, {"loaded_skill_id": "diagnosis/diagnosis", "tool_names": ["read_health"], "health_evidence": {"status": "unhealthy"}}).passed
    outcome = verify_contract(write, {"loaded_skill_id": "diagnosis/diagnosis", "tool_names": ["restart_service"], "health_evidence": None})
    assert outcome.passed is False
    assert outcome.failures == ("restart_service", "no_write_tool")
