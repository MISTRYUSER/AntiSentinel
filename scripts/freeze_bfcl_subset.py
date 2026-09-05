"""Freeze a reviewable BFCL subset with source hashes and ground truth."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path


CATEGORIES = ("simple_python", "irrelevance", "multi_turn_base")


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    frozen = []
    for category in CATEGORIES:
        source = args.data_root / f"BFCL_v4_{category}.json"
        answers = args.data_root / "possible_answer" / f"BFCL_v4_{category}.json"
        answer_by_id = {row["id"]: row.get("ground_truth") for row in _rows(answers)} if answers.exists() else {}
        for row in _rows(source)[:5]:
            canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            frozen.append({"case_id": row["id"], "category": category, "source_file": source.name,
                           "source_hash": hashlib.sha256(canonical).hexdigest(), "question": row["question"],
                           "function": row.get("function", []), "ground_truth": answer_by_id.get(row["id"]),
                           "derived_protocol": "AntiSentinel Task/ToolCall v1"})
    result = {"package": "bfcl-eval", "package_version": importlib.metadata.version("bfcl-eval"),
              "selection_rule": "first 5 source-order cases per category; no score-based selection",
              "case_count": len(frozen), "categories": list(CATEGORIES), "cases": frozen}
    if len(frozen) != 15:
        raise ValueError("expected exactly 15 BFCL cases")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"case_count": 15, "package_version": result["package_version"], "categories": list(CATEGORIES)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
