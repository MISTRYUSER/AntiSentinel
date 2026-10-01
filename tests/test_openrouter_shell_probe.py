"""Offline tests; fixtures are documentation-derived, not captured live responses."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/openrouter_shell_probe.py"


class ShellProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assert_script_exists = SCRIPT.exists()
        if cls.assert_script_exists:
            spec = importlib.util.spec_from_file_location("shell_probe", SCRIPT)
            cls.probe = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.probe)

    def setUp(self):
        self.assertTrue(self.assert_script_exists, "isolated probe is missing")

    def response(self, kind="openrouter:shell"):
        return {"id": "resp_fixture", "status": "completed", "usage": {"input_tokens": 12, "output_tokens": 8}, "output": [
            {"type": kind, "container_id": "gen_fixture", "output": [
                {"stdout": "secret=value\n", "stderr": "", "outcome": {"type": "exit", "exit_code": 0}},
                {"stdout": "", "stderr": "failed", "outcome": {"type": "exit", "exit_code": 2}},
                {"stdout": "", "stderr": "", "outcome": {"type": "timeout"}},
            ], "files": [{"type": "container_file_citation", "container_id": "gen_fixture", "file_id": "cfile_fixture", "filename": "out/a.txt"}]},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "secret answer"}]},
        ]}

    def test_request_is_fresh_network_disabled_and_has_no_file_uploads(self):
        first = self.probe.build_request("vendor/model")
        second = self.probe.build_request("vendor/model")
        self.assertNotEqual(first["session_id"], second["session_id"])
        self.assertEqual(len(first["session_id"]), 20)
        self.assertFalse(first["stream"])
        self.assertFalse(first["store"])
        params = first["tools"][0]["parameters"]
        self.assertEqual(first["tools"][0]["type"], "openrouter:shell")
        self.assertEqual(params["engine"], "openrouter")
        self.assertEqual(params["environment"], {"type": "container_auto", "network_policy": {"type": "disabled"}})
        self.assertNotIn("previous_response_id", first)
        self.assertNotIn("file_ids", json.dumps(first))

    def test_invalid_model_rejected(self):
        for model in ("", " ", None, 1):
            with self.subTest(model=model), self.assertRaises(ValueError):
                self.probe.build_request(model)

    def test_results_are_observations_with_no_raw_text_or_evidence(self):
        result = self.probe.inspect_response(self.response())
        encoded = json.dumps(result)
        self.assertNotIn("secret", encoded)
        self.assertNotIn("evidence_id", encoded)
        self.assertEqual(result["command_outcomes"], ["exit:0", "exit:2", "timeout"])
        self.assertEqual(result["container_ids"], ["gen_fixture"])
        self.assertEqual(result["file_ids"], ["cfile_fixture"])
        self.assertEqual(result["usage"]["input_tokens"], 12)
        self.assertEqual(result["transcript_completeness"], "unverified")
        self.assertEqual(len(result["streams"][0]["stdout_sha256"]), 64)

    def test_native_call_and_output_correlate_without_executing_locally(self):
        body = self.response("shell_call_output")
        body["output"][0]["call_id"] = "call_fixture"
        body["output"].insert(0, {"type": "shell_call", "call_id": "call_fixture", "action": {"commands": ["echo secret", "false", "sleep 2"], "timeout_ms": 1000}})
        result = self.probe.inspect_response(body)
        self.assertEqual(result["call_ids"], ["call_fixture"])
        self.assertEqual(len(result["command_sha256"]), 3)
        self.assertNotIn("echo secret", json.dumps(result))

    def test_native_command_result_counts_must_match(self):
        for commands in (["true"], ["true"] * 4):
            body = self.response("shell_call_output")
            body["output"][0]["call_id"] = "call_fixture"
            body["output"].insert(0, {"type": "shell_call", "call_id": "call_fixture", "action": {"commands": commands}})
            with self.subTest(commands=commands), self.assertRaises(ValueError):
                self.probe.inspect_response(body)

    def test_unpaired_native_calls_are_rejected(self):
        body = self.response()
        body["output"].insert(0, {"type": "shell_call", "call_id": "call_missing", "action": {"commands": ["false"]}})
        with self.assertRaises(ValueError):
            self.probe.inspect_response(body)

    def test_shape_drift_and_unexpected_tools_are_rejected(self):
        for kind in ("function_call", "openrouter:bash", "new_beta_tool", "reasoning_changed"):
            body = self.response(kind)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.probe.inspect_response(body)

    def test_bad_outcomes_are_rejected(self):
        for outcome in ({"type": "exit"}, {"type": "exit", "exit_code": True}, {"type": "exit", "exit_code": "0"}, {"type": "unknown"}):
            body = self.response()
            body["output"][0]["output"][0]["outcome"] = outcome
            with self.subTest(outcome=outcome), self.assertRaises(ValueError):
                self.probe.inspect_response(body)

    def test_incomplete_empty_or_missing_shell_transcript_rejected(self):
        for body in ({}, {"status": "failed", "output": []}, {"status": "completed", "output": []}, {"status": "completed", "output": [{"type": "message"}]}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.probe.inspect_response(body)

    def test_untrusted_ids_and_usage_do_not_enter_trace(self):
        for field, bad in (("container_id", "secret\nvalue"), ("files", [{"file_id": "../../secret"}]), ("output", "not-a-list")):
            body = self.response()
            body["output"][0][field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.probe.inspect_response(body)
        body = self.response()
        body["usage"] = {"input_tokens": True, "cost": "secret", "extra": "secret"}
        result = self.probe.inspect_response(body)
        self.assertEqual(result["usage"], {})

    def test_input_is_not_mutated(self):
        body = self.response()
        original = copy.deepcopy(body)
        self.probe.inspect_response(body)
        self.assertEqual(body, original)


if __name__ == "__main__":
    unittest.main()
