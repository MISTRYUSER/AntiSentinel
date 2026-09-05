"""Inventory every SkillsBench task without executing its environment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

import yaml


def _frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise ValueError(f"missing frontmatter: {path}")
    return yaml.safe_load(parts[1]) or {}, parts[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = []
    for task_dir in sorted((args.root / "tasks").iterdir()):
        task_file = task_dir / "task.md"
        if not task_file.exists():
            continue
        metadata, body = _frontmatter(task_file)
        skills_dir = task_dir / "environment" / "skills"
        skill_names = sorted(path.name for path in skills_dir.iterdir() if path.is_dir())
        interfaces = tuple(metadata.get("metadata", {}).get("interface", []))
        gpus = int(metadata.get("sandbox", {}).get("gpus", 0) or 0)
        credential_markers = sorted(set(re.findall(r"[A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|CREDENTIALS)", body)))
        records.append({
            "task_id": task_dir.name,
            "cohort": "single" if len(skill_names) == 1 else "multi",
            "skill_count": len(skill_names),
            "skill_names": skill_names,
            "interfaces": list(interfaces),
            "gpus": gpus,
            "network_mode": metadata.get("sandbox", {}).get("network_mode"),
            "agent_timeout_sec": metadata.get("agent", {}).get("timeout_sec"),
            "verifier_timeout_sec": metadata.get("verifier", {}).get("timeout_sec"),
            "credential_markers": credential_markers,
            "official_local_candidate": gpus == 0 and not credential_markers,
            "antisentinel_status": "unsupported_missing_task_environment_adapter",
            "task_sha256": hashlib.sha256(task_file.read_bytes()).hexdigest(),
        })
    summary = {
        "source": "benchflow-ai/skillsbench main ZIP supplied by user",
        "source_sha256": args.source_sha256,
        "task_count": len(records),
        "single_skill_tasks": sum(item["cohort"] == "single" for item in records),
        "multi_skill_tasks": sum(item["cohort"] == "multi" for item in records),
        "official_local_candidates": sum(item["official_local_candidate"] for item in records),
        "antisentinel_directly_executable": sum(item["antisentinel_status"] == "ready" for item in records),
        "records": records,
    }
    if summary["task_count"] != 87 or summary["single_skill_tasks"] + summary["multi_skill_tasks"] != 87:
        raise ValueError("SkillsBench inventory is incomplete")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "records"}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
