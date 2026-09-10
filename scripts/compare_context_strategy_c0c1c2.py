#!/usr/bin/env python3
"""Correct LongMemEval context-strategy contrast: C0 / C1 / C2.

C0  Naive recent-fill of raw haystack up to max_tokens (no retrieval ranking).
    Also records full_history_estimated_tokens; overflow if haystack does not fit.
C1  SoftBudget + keyword recall top-k (previous A arm).
C2  ContextBudget floors_degrade + keyword recall top-k (previous B arm).

Token savings are measured vs C0 (and vs full haystack estimate), not C1 vs C2.

  PYTHONPATH=src python scripts/compare_context_strategy_c0c1c2.py \\
    --data data/longmemeval_s_cleaned.json --limit 10 \\
    --out docs/validation/prd-002b-phase-b/longmemeval-c0c1c2-20260910
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from time import monotonic
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter  # noqa: E402
from antisentinel.domain.incident import Incident  # noqa: E402
from antisentinel.domain.session import Session  # noqa: E402
from antisentinel.domain.turn import Turn  # noqa: E402
from antisentinel.evaluation.longmemeval import KeywordMemoryRetriever, LongMemEvalCase, LongMemEvalLoader  # noqa: E402
from antisentinel.evaluation.prd002b_phase_b_stress import measure_pack, pack_phase_a_soft, pack_phase_b  # noqa: E402
from antisentinel.memory.models import MemoryContextView  # noqa: E402
from antisentinel.ports.model import ModelRequest  # noqa: E402
from antisentinel.worker.runtime.budget import ContextBudget, estimate_tokens  # noqa: E402
from antisentinel.worker.runtime.pack import PackInput, SoftBudget  # noqa: E402

EXPECTED_SHA256 = "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
SYSTEM = (
    "You answer memory questions using only the provided memory/context. "
    "Return exactly one JSON object: "
    '{"final":{"summary":"<concise answer>","diagnosis":"<same answer>","confidence":0.0,"evidence_refs":[]}}. '
    "Do not call tools. Put the factual answer in summary."
)


def _load_env() -> None:
    path = ROOT / ".env.local"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(key, None)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[\"'`]", "", text)
    return text


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", _normalize(text))


def score_answer(predicted: str, gold: str) -> dict[str, Any]:
    pred_n = _normalize(predicted or "")
    gold_n = _normalize(gold or "")
    if not gold_n:
        return {"exact_contain": False, "token_recall": 0.0, "token_f1": 0.0, "correct": False}
    exact = gold_n in pred_n or pred_n in gold_n
    gold_toks = _tokens(gold_n)
    pred_toks = _tokens(pred_n)
    if not gold_toks:
        return {"exact_contain": exact, "token_recall": 0.0, "token_f1": 0.0, "correct": exact}
    gold_set = set(gold_toks)
    pred_set = set(pred_toks)
    overlap = len(gold_set & pred_set)
    recall = overlap / len(gold_set)
    precision = overlap / len(pred_set) if pred_set else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    correct = exact or recall >= 0.7 or f1 >= 0.6
    return {
        "exact_contain": exact,
        "token_recall": round(recall, 4),
        "token_f1": round(f1, 4),
        "correct": correct,
    }


def _session_block(session_id: str, text: str, *, cap: int | None = None) -> str:
    body = text if cap is None else text[:cap]
    return f"[{session_id}] {body}"


def full_history_digest(case: LongMemEvalCase) -> str:
    return "\n".join(_session_block(s.session_id, s.text) for s in case.sessions)


def naive_recent_fill(case: LongMemEvalCase, *, max_tokens: int, reserve: int = 9000) -> tuple[str, dict[str, Any]]:
    """Keep newest sessions until digest fits under max_tokens after wire overhead reserve.

    No retrieval ranking. reserve covers system/question JSON wrap + safety_scale.
    """
    budget = max(512, max_tokens - reserve)
    chosen: list[str] = []
    chosen_ids: list[str] = []
    used = 0
    for session in reversed(case.sessions):
        block = _session_block(session.session_id, session.text)
        cost = estimate_tokens(block)
        if chosen and used + cost > budget:
            break
        if not chosen and cost > budget:
            # Single oversized session: hard truncate body.
            block = _session_block(session.session_id, session.text, cap=budget * 3)
            cost = estimate_tokens(block)
            chosen = [block]
            chosen_ids = [session.session_id]
            used = cost
            break
        chosen.insert(0, block)
        chosen_ids.insert(0, session.session_id)
        used += cost
    # Drop oldest kept sessions until under budget (handles estimate drift).
    while len(chosen) > 1 and estimate_tokens("\n".join(chosen)) > budget:
        chosen.pop(0)
        chosen_ids.pop(0)
    digest = "\n".join(chosen)
    gold = set(case.answer_session_ids)
    covered = len(gold & set(chosen_ids))
    return digest, {
        "strategy": "naive_recent_fill",
        "sessions_kept": len(chosen_ids),
        "sessions_total": len(case.sessions),
        "overflow": len(chosen_ids) < len(case.sessions),
        "digest_estimated_tokens": estimate_tokens(digest),
        "gold_session_coverage": (covered / len(gold)) if gold else None,
        "kept_session_ids": chosen_ids[:20],
    }


def recall_digest(case: LongMemEvalCase, *, top_k: int = 5) -> tuple[str, dict[str, Any]]:
    ranked = KeywordMemoryRetriever().retrieve(case, top_k=top_k)
    by_id = {session.session_id: session for session in case.sessions}
    lines: list[str] = []
    refs: list[str] = []
    for session_id in ranked.session_ids:
        session = by_id.get(session_id)
        if session is None:
            continue
        lines.append(_session_block(session_id, session.text, cap=4000))
        refs.append(session_id)
    # Light index crumbs (same as prior eval harness).
    for session in case.sessions:
        lines.append(f"idx:{session.session_id}:{session.text[:240]}")
    digest = "\n".join(lines)
    gold = set(case.answer_session_ids)
    covered = len(gold & set(refs))
    return digest, {
        "strategy": "keyword_recall",
        "top_k": top_k,
        "sessions_kept": len(refs),
        "sessions_total": len(case.sessions),
        "overflow": False,
        "digest_estimated_tokens": estimate_tokens(digest),
        "gold_session_coverage": (covered / len(gold)) if gold else None,
        "recall_at_k": ranked.recall_at_k,
        "kept_session_ids": refs,
    }


def _memory_view(digest: str, *, session_id: str, refs: list[str]) -> MemoryContextView:
    return MemoryContextView(
        session_id=session_id,
        digest=digest,
        evidence_refs=tuple(refs),
        memory_ids=tuple(f"mem-{item}" for item in refs),
        estimated_tokens=0,
    )


def _pack(
    case: LongMemEvalCase,
    *,
    mode: str,
    max_tokens: int,
    digest: str,
    refs: list[str],
):
    incident = Incident.create(title=f"lme:{case.question_id}", source="longmemeval", summary=case.question)
    session = Session.create(incident_id=incident.incident_id, participant_ids=["eval"])
    turn = Turn.create(session_id=session.session_id)
    budget = ContextBudget(max_context_tokens=max_tokens, target_fill_ratio=0.75, strict=(mode == "C2"))
    view = _memory_view(
        digest,
        session_id=case.sessions[-1].session_id if case.sessions else case.question_id,
        refs=refs,
    )
    inp = PackInput(
        incident=incident,
        session=session,
        turn=turn,
        working_set=None,
        tools=[],
        memory_context=view,
        budget=budget,
        soft_budget=SoftBudget(),
        system_override=SYSTEM,
    )
    if mode == "C0":
        # Soft pack of the naive-filled digest (no global degrade beyond what SoftBudget does).
        result = pack_phase_a_soft(inp)
    elif mode == "C1":
        result = pack_phase_a_soft(inp)
    else:
        result = pack_phase_b(inp)
    metrics = measure_pack(
        label=mode,
        result=result,
        early_marker="",
        recent_turn_limit=3,
        core_tool_names=[],
    ).to_dict()
    return result, metrics, incident, session, turn


def _make_real_model() -> tuple[OpenAICompatibleModelAdapter, str, str]:
    api_key = os.environ.get("ANTISENTINEL_MODEL_API_KEY", "").strip()
    base_url = os.environ.get("ANTISENTINEL_MODEL_BASE_URL", "").strip()
    model_name = os.environ.get("ANTISENTINEL_MODEL_NAME", "").strip()
    mode = os.environ.get("ANTISENTINEL_MODEL_MODE", "").strip().lower()
    if mode == "fake":
        raise SystemExit("refusing Fake path: set ANTISENTINEL_MODEL_MODE=real")
    if not api_key or not base_url or not model_name:
        raise SystemExit("requires ANTISENTINEL_MODEL_API_KEY/BASE_URL/NAME")
    adapter = OpenAICompatibleModelAdapter(
        base_url=base_url,
        api_key=api_key,
        model=model_name,
        timeout=float(os.getenv("ANTISENTINEL_MODEL_TIMEOUT_SECONDS", "120")),
    )
    host = base_url.split("//", 1)[-1].split("/", 1)[0]
    return adapter, host, model_name


def _ask_real(model, *, pack, incident, session, turn, question: str) -> dict[str, Any]:
    messages = list(pack.messages)
    messages.append(
        {
            "role": "user",
            "content": (
                f"Question: {question}\n"
                "Using only memory/context above, answer factually. "
                "Return JSON final.summary as the answer string."
            ),
        }
    )
    request = ModelRequest(
        incident_id=incident.incident_id,
        session_id=session.session_id,
        turn_id=turn.turn_id,
        messages=messages,
        tools=[],
    )
    started = monotonic()
    try:
        response = model.complete(request)
        latency_ms = round((monotonic() - started) * 1000, 1)
        text = ""
        if getattr(response, "final", None) is not None:
            text = f"{response.final.summary}\n{response.final.diagnosis}".strip()
        else:
            text = str(getattr(response, "raw", response))[:4000]
        usage = getattr(response, "usage", None)
        return {
            "ok": True,
            "answer": text,
            "latency_ms": latency_ms,
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "answer": "",
            "latency_ms": round((monotonic() - started) * 1000, 1),
            "input_tokens": 0,
            "output_tokens": 0,
            "error": {"code": type(exc).__name__, "message": str(exc)[:400]},
        }


def _run_arm(model, case: LongMemEvalCase, *, mode: str, max_tokens: int, top_k: int) -> dict[str, Any]:
    full = full_history_digest(case)
    full_est = estimate_tokens(full)
    if mode == "C0":
        digest, meta = naive_recent_fill(case, max_tokens=max_tokens)
        refs = list(meta.get("kept_session_ids") or [])
    else:
        digest, meta = recall_digest(case, top_k=top_k)
        refs = list(meta.get("kept_session_ids") or [])
    meta = {
        **meta,
        "full_history_estimated_tokens": full_est,
        "full_history_overflow_vs_max": full_est > max_tokens,
    }
    pack, metrics, incident, session, turn = _pack(
        case, mode=mode, max_tokens=max_tokens, digest=digest, refs=refs
    )
    real = _ask_real(model, pack=pack, incident=incident, session=session, turn=turn, question=case.question)
    scored = score_answer(real.get("answer") or "", case.answer)
    return {
        "question_id": case.question_id,
        "question_type": case.question_type,
        "mode": mode,
        "gold_answer": case.answer,
        "context_meta": meta,
        "pack": {
            "within_budget": metrics["within_budget"],
            "total_estimated": metrics["total_estimated"],
            "budget_fill": metrics["budget_fill"],
        },
        "real": {
            "ok": real["ok"],
            "answer_excerpt": (real.get("answer") or "")[:500],
            "latency_ms": real["latency_ms"],
            "input_tokens": real["input_tokens"],
            "output_tokens": real["output_tokens"],
            "error": real["error"],
        },
        "score": scored,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="LongMemEval C0/C1/C2 context strategy contrast")
    parser.add_argument("--data", type=Path, default=ROOT / "data" / "longmemeval_s_cleaned.json")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-tokens", type=int, default=32000)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--require-sha", action="store_true")
    args = parser.parse_args()

    _load_env()
    # Prefer longer timeout for C0 large prompts.
    os.environ.setdefault("ANTISENTINEL_MODEL_TIMEOUT_SECONDS", "120")
    model, host, model_name = _make_real_model()

    if not args.data.exists():
        raise SystemExit(f"missing dataset: {args.data}")
    digest = _sha256(args.data)
    if args.require_sha and digest != EXPECTED_SHA256:
        raise SystemExit(f"sha256 mismatch: got {digest}")

    cases: list[LongMemEvalCase] = []
    for case in LongMemEvalLoader(args.data):
        if case.question_id.endswith("_abs"):
            continue
        cases.append(case)
        if len(cases) >= args.limit:
            break

    rows = []
    for index, case in enumerate(cases, 1):
        row: dict[str, Any] = {
            "question_id": case.question_id,
            "question_type": case.question_type,
        }
        for mode in ("C0", "C1", "C2"):
            print(f"[{index}/{len(cases)}] {case.question_id} {mode}...", flush=True)
            row[mode] = _run_arm(
                model, case, mode=mode, max_tokens=args.max_tokens, top_k=args.top_k
            )
        rows.append(row)

    def arm_mean(arm: str, path: list[str]) -> float:
        values: list[float] = []
        for row in rows:
            cur: Any = row[arm]
            for key in path:
                cur = cur[key]
            values.append(float(cur))
        return _mean(values)

    summary: dict[str, Any] = {
        "design": {
            "C0": "naive_recent_fill raw haystack to max_tokens (no retrieval)",
            "C1": "SoftBudget + keyword recall top-k",
            "C2": "ContextBudget floors_degrade + keyword recall top-k",
        },
        "mode": "real",
        "model_host": host,
        "model_name": model_name,
        "dataset": str(args.data),
        "sha256": digest,
        "sha256_match": digest == EXPECTED_SHA256,
        "n": len(rows),
        "max_tokens": args.max_tokens,
        "top_k": args.top_k,
    }
    for arm in ("C0", "C1", "C2"):
        summary[f"{arm}_correct_rate"] = arm_mean(arm, ["score", "correct"])
        summary[f"{arm}_token_recall_mean"] = arm_mean(arm, ["score", "token_recall"])
        summary[f"{arm}_provider_ok_rate"] = arm_mean(arm, ["real", "ok"])
        summary[f"{arm}_mean_input_tokens"] = arm_mean(arm, ["real", "input_tokens"])
        summary[f"{arm}_mean_total_estimated"] = arm_mean(arm, ["pack", "total_estimated"])
        summary[f"{arm}_mean_digest_estimated"] = arm_mean(arm, ["context_meta", "digest_estimated_tokens"])
        summary[f"{arm}_overflow_rate"] = arm_mean(arm, ["context_meta", "overflow"])
        cov = []
        for row in rows:
            value = row[arm]["context_meta"].get("gold_session_coverage")
            if value is not None:
                cov.append(float(value))
        summary[f"{arm}_mean_gold_session_coverage"] = _mean(cov)

    summary["mean_full_history_estimated_tokens"] = arm_mean("C0", ["context_meta", "full_history_estimated_tokens"])
    summary["full_history_overflow_rate"] = arm_mean("C0", ["context_meta", "full_history_overflow_vs_max"])

    c0_tok = summary["C0_mean_input_tokens"]
    c2_tok = summary["C2_mean_input_tokens"]
    full_est = summary["mean_full_history_estimated_tokens"]
    summary["token_saving_C2_vs_C0"] = round(1.0 - (c2_tok / c0_tok), 4) if c0_tok > 0 else None
    summary["token_saving_C2_vs_full_estimate"] = (
        round(1.0 - (c2_tok / full_est), 4) if full_est > 0 else None
    )
    summary["delta_correct_C2_minus_C0"] = round(summary["C2_correct_rate"] - summary["C0_correct_rate"], 4)
    summary["delta_correct_C2_minus_C1"] = round(summary["C2_correct_rate"] - summary["C1_correct_rate"], 4)

    claims = {
        "used_real_model": True,
        "C2_provider_all_ok": summary["C2_provider_ok_rate"] == 1.0,
        "C2_saves_tokens_vs_C0": (summary["token_saving_C2_vs_C0"] or 0) > 0.05,
        "C2_correct_not_worse_than_C0_by_5pct": summary["C2_correct_rate"] + 1e-9
        >= summary["C0_correct_rate"] - 0.05,
        "full_history_does_not_fit": summary["full_history_overflow_rate"] >= 0.9,
    }
    summary["claims"] = claims
    summary["pass"] = all(bool(v) for v in claims.values())
    summary["note"] = (
        "Primary token-saving claim is C2 vs C0 (naive fill), not C1 vs C2. "
        "correct = exact contain OR gold-token recall>=0.7 OR F1>=0.6."
    )

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "compare.json", {"summary": summary, "rows": rows})
    table = [
        "| arm | correct | mean_input_tokens | gold_session_cov | overflow |",
        "|---|---:|---:|---:|---:|",
        f"| C0 naive fill | {summary['C0_correct_rate']} | {summary['C0_mean_input_tokens']} | "
        f"{summary['C0_mean_gold_session_coverage']} | {summary['C0_overflow_rate']} |",
        f"| C1 SoftBudget+recall | {summary['C1_correct_rate']} | {summary['C1_mean_input_tokens']} | "
        f"{summary['C1_mean_gold_session_coverage']} | {summary['C1_overflow_rate']} |",
        f"| C2 ContextBudget+recall | {summary['C2_correct_rate']} | {summary['C2_mean_input_tokens']} | "
        f"{summary['C2_mean_gold_session_coverage']} | {summary['C2_overflow_rate']} |",
        "",
        f"- full_history_estimated_mean: **{summary['mean_full_history_estimated_tokens']}**",
        f"- token_saving C2 vs C0: **{summary['token_saving_C2_vs_C0']}**",
        f"- token_saving C2 vs full estimate: **{summary['token_saving_C2_vs_full_estimate']}**",
        f"- pass: **{summary['pass']}**",
    ]
    (out / "README.md").write_text(
        "\n".join(
            [
                "# LongMemEval-S C0 / C1 / C2 (correct contrast)",
                "",
                f"- model: `{host}` / `{model_name}`",
                f"- n={summary['n']}, max_tokens={summary['max_tokens']}",
                "",
                *table,
                "",
                "```json",
                json.dumps({k: summary[k] for k in summary if k != "note"}, ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
