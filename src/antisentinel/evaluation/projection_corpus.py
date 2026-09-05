"""LongMemEval corpus variants aligned with official index-extension comparisons."""

from __future__ import annotations

from datetime import datetime, timezone
import re

from antisentinel.memory.projection import ProjectionSource, RuleMemoryProjector


def build_projection_corpora(case, *, projector=None):
    raw, summaries, keys = [], [], []
    projector = projector or RuleMemoryProjector()
    for session in case.sessions:
        turns = tuple((turn.turn_id, turn.content) for turn in session.turns)
        source = ProjectionSource("benchmark", case.question_id, session.session_id, evaluation_time(session.date), turns)
        projections = projector.project(source)
        refs = tuple(turn.turn_id for turn in session.turns)
        raw.append({"session_id": session.session_id, "turn_ids": refs, "content": session.text, "corpus": "raw"})
        for projection in projections:
            target = summaries if projection.kind == "session_digest" else keys
            target.append({"session_id": session.session_id, "turn_ids": tuple(ref.ref_id for ref in projection.source_refs), "content": projection.content, "corpus": "session_summ" if projection.kind == "session_digest" else "keyphrase_userfact", "projection_kind": projection.kind, "status": projection.status})
    return {"raw": tuple(raw), "session_summ": tuple(summaries), "keyphrase_userfact": tuple(keys), "raw_plus_projection": tuple(raw + summaries + keys)}


def evaluation_time(value: str) -> datetime:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value): return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    matched = re.match(r"(\d{4})[-/](\d{2})[-/](\d{2})(?:\s+\([A-Za-z]+\))?\s+(\d{2}):(\d{2})", value)
    if matched is None: raise ValueError(f"unsupported session date: {value}")
    return datetime(int(matched.group(1)), int(matched.group(2)), int(matched.group(3)), int(matched.group(4)), int(matched.group(5)), tzinfo=timezone.utc)
