"""Offline, evaluation-only OpenRouter shell probe. No network or local execution.

The beta's complete namespaced item envelope is not documented. Recognized
fixtures are hypotheses for capture validation, never evidence of live support.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import secrets


ENDPOINT = "https://openrouter.ai/api/v1/responses"
MAX_CAPTURE_BYTES = 2 * 1024 * 1024


def build_request(model: str) -> dict:
    if not isinstance(model, str) or not model.strip():
        raise ValueError("invalid_model")
    return {
        "model": model.strip(),
        "session_id": secrets.token_hex(10),
        "store": False,
        "stream": False,
        "max_output_tokens": 1024,
        "input": (
            "This is a synthetic sandbox smoke test. Use the shell once to run "
            "`printf 'antisentinel-shell-probe\\n'`, then report its exit status. "
            "Do not access files, install packages, use network, or run more commands."
        ),
        "tools": [{
            "type": "openrouter:shell",
            "parameters": {
                "engine": "openrouter",
                "environment": {"type": "container_auto", "network_policy": {"type": "disabled"}},
            },
        }],
    }


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise ValueError("invalid_identifier")
    return value


def _digest(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid_text")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def inspect_response(body: object) -> dict:
    """Summarize recognized completed Responses captures, without raw text.

    Does not return ModelResponse/PlannedToolCall/Evidence or claim the remote
    transcript is complete. Shape drift is a failed probe, not a local fallback.
    """
    if not isinstance(body, dict) or body.get("status") != "completed" or body.get("error"):
        raise ValueError("response_not_completed")
    items = body.get("output")
    if not isinstance(items, list) or not items:
        raise ValueError("missing_output")
    result = {
        "execution_owner": "openrouter", "transcript_completeness": "unverified",
        "response_id": _identifier(body.get("id")), "item_types": [],
        "container_ids": [], "file_ids": [], "call_ids": [],
        "command_sha256": [], "command_outcomes": [], "streams": [], "usage": {},
    }
    native_calls, native_outputs = {}, {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("invalid_item")
        kind = item.get("type")
        if kind not in {"message", "reasoning", "shell_call", "shell_call_output", "openrouter:shell"}:
            raise ValueError("unsupported_output_item")
        result["item_types"].append(kind)
        if kind in {"message", "reasoning"}:
            continue  # No model answer/reasoning text in the summary.
        if kind == "shell_call":
            call_id = _identifier(item.get("call_id"))
            if call_id in native_calls:
                raise ValueError("duplicate_call_id")
            action = item.get("action")
            commands = action.get("commands") if isinstance(action, dict) else None
            if not isinstance(commands, list) or not commands:
                raise ValueError("missing_commands")
            native_calls[call_id] = len(commands)
            result["command_sha256"].extend(_digest(command) for command in commands)
            continue
        if kind == "shell_call_output":
            call_id = _identifier(item.get("call_id"))
            if call_id in native_outputs:
                raise ValueError("duplicate_call_output")
            result["call_ids"].append(call_id)
        elif "call_id" in item:
            result["call_ids"].append(_identifier(item["call_id"]))
        # Namespaced output[] is deliberately a narrow fixture hypothesis.
        # If real captures wrap the payload differently, fail; do not guess.
        outputs = item.get("output")
        if not isinstance(outputs, list) or not outputs:
            raise ValueError("unsupported_shell_result_envelope")
        if kind == "shell_call_output":
            native_outputs[call_id] = len(outputs)
        if "container_id" in item:
            result["container_ids"].append(_identifier(item["container_id"]))
        files = item.get("files", [])
        if not isinstance(files, list):
            raise ValueError("invalid_files")
        for file in files:
            if not isinstance(file, dict) or file.get("type") != "container_file_citation":
                raise ValueError("invalid_file_citation")
            result["file_ids"].append(_identifier(file.get("file_id")))
            file_container = _identifier(file.get("container_id"))
            if file_container != item.get("container_id"):
                raise ValueError("file_container_mismatch")
        for output in outputs:
            if not isinstance(output, dict):
                raise ValueError("invalid_command_output")
            stdout, stderr = output.get("stdout"), output.get("stderr")
            stdout_hash, stderr_hash = _digest(stdout), _digest(stderr)
            outcome = output.get("outcome")
            if not isinstance(outcome, dict):
                raise ValueError("invalid_outcome")
            if outcome.get("type") == "exit" and type(outcome.get("exit_code")) is int:
                status = f"exit:{outcome['exit_code']}"
            elif outcome.get("type") == "timeout":
                status = "timeout"
            else:
                raise ValueError("invalid_outcome")
            result["command_outcomes"].append(status)
            result["streams"].append({
                "stdout_sha256": stdout_hash, "stderr_sha256": stderr_hash,
                "stdout_bytes": len(stdout.encode("utf-8")), "stderr_bytes": len(stderr.encode("utf-8")),
            })
    if native_calls != native_outputs:
        raise ValueError("unpaired_native_shell_call")
    if not result["command_outcomes"]:
        raise ValueError("missing_shell_results")
    for key in ("container_ids", "file_ids", "call_ids"):
        result[key] = sorted(set(result[key]))
    usage = body.get("usage")
    if isinstance(usage, dict):
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = usage.get(key)
            if type(value) is int and value >= 0:
                result["usage"][key] = value
        cost = usage.get("cost")
        if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0:
            result["usage"]["cost"] = cost  # Provider-reported total; never infer shell-only cost.
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    request = commands.add_parser("request", help="Print synthetic request JSON; does not send it")
    request.add_argument("--model", required=True)
    inspect = commands.add_parser("inspect", help="Print a text-free summary of a local Responses capture")
    inspect.add_argument("capture", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "request":
            result = build_request(args.model)
        else:
            with args.capture.open("rb") as handle:
                raw = handle.read(MAX_CAPTURE_BYTES + 1)
            if len(raw) > MAX_CAPTURE_BYTES:
                raise ValueError("capture_too_large")
            result = inspect_response(json.loads(raw))
    except (OSError, ValueError, TypeError, UnicodeError):
        parser.exit(2, "probe rejected input; inspect the capture privately for beta shape drift\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
