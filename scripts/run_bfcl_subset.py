"""Run the frozen BFCL simple/irrelevance subset against DeepSeek."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from time import monotonic
import re

import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from antisentinel.evaluation.bfcl_adapter import normalize_schema


def _matches(call: dict, truth: list[dict]) -> bool:
    for candidate in truth or []:
        for name, expected in candidate.items():
            if call.get("name") != name:
                continue
            actual = call.get("arguments", {})
            if all((key in actual and actual[key] in allowed) or (key not in actual and "" in allowed) for key, allowed in expected.items()):
                return True
    return False


def _complete(client: httpx.Client, url: str, headers: dict, payload: dict) -> tuple[httpx.Response, dict]:
    last_response = None
    for _ in range(3):
        last_response = client.post(url, headers=headers, json=payload)
        body = last_response.json()
        if last_response.status_code == 200 and body.get("choices"):
            return last_response, body
    raise RuntimeError(f"provider response unavailable after 2 retries: HTTP {last_response.status_code}")


def main() -> int:
    source = Path(sys.argv[1]); output = Path(sys.argv[2])
    if output.exists(): raise SystemExit("output must not exist")
    data = json.loads(source.read_text(encoding="utf-8")); output.mkdir(parents=True)
    client = httpx.Client(timeout=30); records = []
    for case in data["cases"]:
        if case["category"] == "multi_turn_base":
            records.append({"case_id": case["case_id"], "category": case["category"], "status": "unsupported_stateful_multi_turn", "passed": False, "input_tokens": 0, "output_tokens": 0, "elapsed_seconds": 0})
            continue
        aliases = {"bfcl_" + re.sub(r"[^a-zA-Z0-9_-]", "_", fn["name"]): fn["name"] for fn in case["function"]}
        payload = {"model": os.environ["ANTISENTINEL_MODEL_NAME"], "messages": [message for turn in case["question"] for message in turn], "tools": [{"type": "function", "function": {"name": alias, "description": fn.get("description", ""), "parameters": normalize_schema(fn.get("parameters", {}))}} for alias, fn in zip(aliases, case["function"])], "thinking": {"type": "disabled"}}
        started = monotonic(); response, body = _complete(client, os.environ["ANTISENTINEL_MODEL_BASE_URL"].rstrip("/") + "/chat/completions", {"Authorization": "Bearer " + os.environ["ANTISENTINEL_MODEL_API_KEY"]}, payload); message=body["choices"][0]["message"]; calls=[]
        for raw in message.get("tool_calls") or []:
            fn=raw["function"]; calls.append({"name":aliases.get(fn["name"], fn["name"]),"arguments":json.loads(fn["arguments"]) if isinstance(fn["arguments"],str) else fn["arguments"]})
        passed = (not calls) if case["category"] == "irrelevance" else (len(calls)==1 and _matches(calls[0],case["ground_truth"]))
        usage=body.get("usage",{}); records.append({"case_id":case["case_id"],"category":case["category"],"status":"passed" if passed else "failed","passed":passed,"calls":calls,"http_status":response.status_code,"input_tokens":usage.get("prompt_tokens",0),"output_tokens":usage.get("completion_tokens",0),"elapsed_seconds":round(monotonic()-started,3)})
        with (output/"results.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps(records[-1],ensure_ascii=False)+"\n")
    scored=[r for r in records if not r["status"].startswith("unsupported")]
    report={"case_count":len(records),"scored_count":len(scored),"passed":sum(r["passed"] for r in scored),"unsupported":len(records)-len(scored),"accuracy":sum(r["passed"] for r in scored)/len(scored),"input_tokens":sum(r["input_tokens"] for r in records),"output_tokens":sum(r["output_tokens"] for r in records),"elapsed_seconds":round(sum(r["elapsed_seconds"] for r in records),3)}
    (output/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps(report)); return 0


if __name__ == "__main__": raise SystemExit(main())
