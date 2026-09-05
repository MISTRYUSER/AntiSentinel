import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from tests.test_skill_catalog import package


def cli():
    assert importlib.util.find_spec("antisentinel.capabilities.cli") is not None, "skill CLI missing"
    from antisentinel.capabilities.cli import main
    return main


def test_validate_and_inspect_metadata(tmp_path, capsys):
    main = cli()
    root = tmp_path / "pkg"
    package(root)
    for command in ("validate", "inspect"):
        assert main([command, "--root", str(root)]) == 0
        value = json.loads(capsys.readouterr().out)
        assert len(value["skills"]) == 2
        assert value["executable"] is False
        assert "PRIVATE BODY" not in json.dumps(value)


def test_invalid_cli_input_returns_structured_error(tmp_path, capsys):
    main = cli()
    assert main(["validate", "--root", str(tmp_path / "missing")]) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["diagnostics"][0]["code"] == "resource_missing"


def test_pack_and_explicit_inspect_load_only_requested_instruction(tmp_path, capsys):
    main = cli()
    root = tmp_path / "pkg"
    package(root)
    output = tmp_path / "published"

    assert main(["pack", "--root", str(root), "--output", str(output)]) == 0
    packed = json.loads(capsys.readouterr().out)
    assert packed["release_id"]
    assert main(["inspect", "--release", packed["root"], "--skill-id", "local/diagnosis"]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["instruction"] == "PRIVATE BODY NOT FOR REGISTRATION"


def test_stage41_runner_writes_and_reads_three_isolated_artifacts(tmp_path):
    output = tmp_path / "stage41"
    script = Path(__file__).parents[1] / "scripts" / "validate_skill_integration.py"
    result = subprocess.run(
        [sys.executable, str(script), "--stage", "4.1", "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    artifacts = {name: json.loads((output / name).read_text()) for name in ("catalog.json", "diagnostics.json", "report.json")}
    assert artifacts["catalog.json"]["skill_count"] == 2
    assert artifacts["diagnostics.json"]["diagnostic_count"] == 6
    assert artifacts["report.json"] == {
        "case_pass": True,
        "body_reads": 0,
        "diagnostic_count": 6,
        "artifact_count": 3,
        "skill_count": 2,
        "tool_leaks": 0,
    }


def test_stage42_runner_preserves_old_release_and_atomic_state(tmp_path):
    output = tmp_path / "stage42"
    script = Path(__file__).parents[1] / "scripts" / "validate_skill_integration.py"
    result = subprocess.run(
        [sys.executable, str(script), "--stage", "4.2", "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((output / "report.json").read_text())
    assert json.loads((output / "stage41" / "report.json").read_text())["case_pass"] is True
    assert report == {
        "case_pass": True,
        "activation_failures": 0,
        "old_reference_content": "version one reference",
        "release_count": 2,
        "state_revision": 2,
    }
