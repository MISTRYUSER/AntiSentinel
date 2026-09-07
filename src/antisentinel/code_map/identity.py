"""Canonical identities for immutable code-map facts."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def snapshot_id_for(repository_id: str, commit_sha: str, parser_revision: str, rules_digest: str) -> str:
    return _digest({
        "commit_sha": commit_sha,
        "parser_revision": parser_revision,
        "repository_id": repository_id,
        "rules_digest": rules_digest,
    })


def node_id_for(snapshot_id: str, path: str, kind: str, qualified_name: str, start_line: int, end_line: int) -> str:
    return _digest({
        "end_line": end_line,
        "kind": kind,
        "path": path,
        "qualified_name": qualified_name,
        "snapshot_id": snapshot_id,
        "start_line": start_line,
    })


def content_hash(data: bytes) -> str:
    if not isinstance(data, bytes):
        raise TypeError("content_hash expects bytes")
    return hashlib.sha256(data).hexdigest()
