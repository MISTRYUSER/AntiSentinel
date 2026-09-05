"""Local immutable JSON EvidenceStore adapter."""

from __future__ import annotations

import json
from pathlib import Path

from antisentinel.domain.errors import DomainError, InvalidInputError
from antisentinel.domain.evidence import Evidence


class FileEvidenceStore:
    def __init__(self, storage_root: str | Path) -> None:
        self.root = Path(storage_root)

    def put_once(self, evidence: Evidence, *, incident_id: str | None = None) -> None:
        if not isinstance(evidence, Evidence):
            raise InvalidInputError("evidence must be an Evidence")
        path = self._path_for(evidence, incident_id=incident_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            stored = Evidence.from_dict(json.loads(path.read_text(encoding="utf-8")))
            if stored.content_hash == evidence.content_hash and stored.to_dict() == evidence.to_dict():
                return
            raise DomainError(f"evidence hash conflict: {evidence.evidence_id}")
        path.write_text(json.dumps(evidence.to_dict(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    def get(self, evidence_id: str) -> Evidence | None:
        for path in sorted((self.root / "incidents").glob("*/evidence/*.json")):
            if path.stem == evidence_id:
                return Evidence.from_dict(json.loads(path.read_text(encoding="utf-8")))
        return None

    def _path_for(self, evidence: Evidence, *, incident_id: str | None = None) -> Path:
        scoped_incident_id = incident_id or str(evidence.metadata.get("incident_id", "unscoped"))
        return self.root / "incidents" / scoped_incident_id / "evidence" / f"{evidence.evidence_id}.json"
