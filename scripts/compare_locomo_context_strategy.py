#!/usr/bin/env python3
"""LoCoMo10 cross-validation: C0 naive recent-fill vs C2 ContextBudget+keyword recall.

Uses official-shaped data/locomo10.json (snap-research LoCoMo10).

  PYTHONPATH=src python scripts/compare_locomo_context_strategy.py \\
    --data data/locomo10.json --limit 20 \\
    --out docs/validation/prd-002b-phase-b/locomo-c0c2-20260910
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
from antisentinel.evaluation.prd002b_phase_b_stress import measure_pack, pack_phase_a_soft, pack_phase_b  # noqa: E402
from antisentinel.memory.models import MemoryContextView  # noqa: E402
from antisentinel.ports.model import ModelRequest  # noqa: E402
from antisentinel.worker.runtime.budget import ContextBudget, estimate_tokens  # noqa: E402
from antisentinel.worker.runtime.pack import PackInput, SoftBudget  # noqa: E402

SYSTEM = (
    "You answer LoCoMo questions using only the provided dialogue memory/context. "
    "Return exactly one JSON object: "
    '{"final":{"summary":"<concise answer>","diagnosis":"<same answer>","confidence":0.0,"evidence_refs":[]}}. '
    "Do not call tools."
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
    gold_n = _normalize(str(gold) if gold is not None else "")
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


def _session_text(turns: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for turn in turns:
        speaker = turn.get("speaker") or turn.get("speaker_name") or "?"
        dia = turn.get("dia_id") or ""
        text = turn.get("text") or turn.get("blip_caption") or ""
        parts.append(f"{dia} {speaker}: {text}")
    return "\n".join(parts)


def load_locomo_cases(path: Path, *, limit: int) -> list[dict[str, Any]]:
    records = json.loads(path.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = []
    # Round-robin across conversations for coverage.
    per_conv: dict[str, list[dict[str, Any]]] = {}
    for sample in records:
        sample_id = str(sample.get("sample_id") or sample.get("id") or len(per_conv))
        conversation = sample.get("conversation") or {}
        sessions: list[tuple[str, str]] = []
        session_keys = sorted(
            (key for key in conversation.keys() if re.fullmatch(r"session_\d+", key)),
            key=lambda key: int(key.split("_")[1]),
        )
        for key in session_keys:
            turns = conversation.get(key) or []
            if not isinstance(turns, list):
                continue
            sessions.append((key, _session_text(turns)))
        qa_items = sample.get("qa") or []
        for index, qa in enumerate(qa_items):
            answer = qa.get("answer")
            if isinstance(answer, list):
                answer = "; ".join(str(item) for item in answer)
            evidence = qa.get("evidence") or []
            if not isinstance(evidence, list):
                evidence = [evidence]
            # Map D1:3 style evidence to session_1 roughly (D{n} -> session_n).
            gold_sessions: list[str] = []
            for item in evidence:
                match = re.match(r"D(\d+)", str(item))
                if match:
                    gold_sessions.append(f"session_{match.group(1)}")
            case = {
                "case_id": f"{sample_id}:{index}",
                "sample_id": sample_id,
                "category": str(qa.get("category") or ""),
                "question": str(qa.get("question") or ""),
                "answer": str(answer or ""),
                "evidence": [str(item) for item in evidence],
                "gold_sessions": sorted(set(gold_sessions)),
                "sessions": sessions,
            }
            per_conv.setdefault(sample_id, []).append(case)

    # Take up to ceil(limit/n_conv) from each conversation.
    conv_ids = list(per_conv.keys())
    if not conv_ids:
        return []
    taken = 0
    cursor = 0
    while taken < limit:
        progress = False
        for sample_id in conv_ids:
            bucket = per_conv[sample_id]
            if cursor < len(bucket):
                cases.append(bucket[cursor])
                taken += 1
                progress = True
                if taken >= limit:
                    break
        if not progress:
            break
        cursor += 1
    return cases


def keyword_score(question: str, text: str) -> float:
    q = set(_tokens(question))
    if not q:
        return 0.0
    t = set(_tokens(text))
    return len(q & t) / len(q)


def naive_recent_fill(sessions: list[tuple[str, str]], *, max_tokens: int, reserve: int = 9000) -> tuple[str, dict[str, Any]]:
    budget = max(512, max_tokens - reserve)
    chosen: list[str] = []
    ids: list[str] = []
    used = 0
    for session_id, text in reversed(sessions):
        block = f"[{session_id}]\n{text}"
        cost = estimate_tokens(block)
        if chosen and used + cost > budget:
            break
        if not chosen and cost > budget:
            block = f"[{session_id}]\n{text[: budget * 3]}"
            cost = estimate_tokens(block)
            chosen = [block]
            ids = [session_id]
            used = cost
            break
        chosen.insert(0, block)
        ids.insert(0, session_id)
        used += cost
    while len(chosen) > 1 and estimate_tokens("\n\n".join(chosen)) > budget:
        chosen.pop(0)
        ids.pop(0)
    digest = "\n\n".join(chosen)
    return digest, {
        "strategy": "naive_recent_fill",
        "sessions_kept": len(ids),
        "sessions_total": len(sessions),
        "overflow": len(ids) < len(sessions),
        "digest_estimated_tokens": estimate_tokens(digest),
        "kept_session_ids": ids,
    }


def recall_fill(
    question: str,
    sessions: list[tuple[str, str]],
    *,
    top_k: int,
) -> tuple[str, dict[str, Any]]:
    ranked = sorted(
        ((session_id, text, keyword_score(question, text)) for session_id, text in sessions),
        key=lambda item: (-item[2], item[0]),
    )
    picked = [(sid, text) for sid, text, score in ranked[:top_k] if score > 0] or [
        (sid, text) for sid, text, _ in ranked[:top_k]
    ]
    lines = [f"[{sid}]\n{text[:4000]}" for sid, text in picked]
    for sid, text in sessions:
        lines.append(f"idx:{sid}:{text[:240]}")
    digest = "\n\n".join(lines)
    return digest, {
        "strategy": "keyword_recall",
        "top_k": top_k,
        "sessions_kept": len(picked),
        "sessions_total": len(sessions),
        "overflow": False,
        "digest_estimated_tokens": estimate_tokens(digest),
        "kept_session_ids": [sid for sid, _ in picked],
    }


def _gold_coverage(meta: dict[str, Any], gold_sessions: list[str]) -> float | None:
    if not gold_sessions:
        return None
    kept = set(meta.get("kept_session_ids") or [])
    return len(kept & set(gold_sessions)) / len(set(gold_sessions))


def _pack(mode: str, *, question: str, digest: str, refs: list[str], max_tokens: int):
    incident = Incident.create(title="locomo", source="locomo", summary=question)
    session = Session.create(incident_id=incident.incident_id, participant_ids=["eval"])
    turn = Turn.create(session_id=session.session_id)
    budget = ContextBudget(max_context_tokens=max_tokens, target_fill_ratio=0.75, strict=(mode == "C2"))
    view = MemoryContextView(
        session_id=refs[-1] if refs else session.session_id,
        digest=digest,
        evidence_refs=tuple(refs),
        memory_ids=tuple(f"mem-{item}" for item in refs),
        estimated_tokens=0,
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
    result = pack_phase_a_soft(inp) if mode == "C0" else pack_phase_b(inp)
    metrics = measure_pack(
        label=mode, result=result, early_marker="", recent_turn_limit=3, core_tool_names=[]
    ).to_dict()
    return result, metrics, incident, session, turn


def _make_real_model():
    api_key = os.environ.get("ANTISENTINEL_MODEL_API_KEY", "").strip()
    base_url = os.environ.get("ANTISENTINEL_MODEL_BASE_URL", "").strip()
    model_name = os.environ.get("ANTISENTINEL_MODEL_NAME", "").strip()
    if os.environ.get("ANTISENTINEL_MODEL_MODE", "").strip().lower() == "fake":
        raise SystemExit("refusing Fake path")
    if not api_key or not base_url or not model_name:
        raise SystemExit("requires real model credentials")
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


def _run_arm(model, case: dict[str, Any], *, mode: str, max_tokens: int, top_k: int) -> dict[str, Any]:
    sessions = case["sessions"]
    full = "\n\n".join(f"[{sid}]\n{text}" for sid, text in sessions)
    full_est = estimate_tokens(full)
    if mode == "C0":
        digest, meta = naive_recent_fill(sessions, max_tokens=max_tokens)
    else:
        digest, meta = recall_fill(case["question"], sessions, top_k=top_k)
    meta = {
        **meta,
        "full_history_estimated_tokens": full_est,
        "full_history_overflow_vs_max": full_est > max_tokens,
        "gold_session_coverage": _gold_coverage(meta, case["gold_sessions"]),
    }
    refs = list(meta.get("kept_session_ids") or [])
    pack, metrics, incident, session, turn = _pack(
        mode, question=case["question"], digest=digest, refs=refs, max_tokens=max_tokens
    )
    real = _ask_real(
        model, pack=pack, incident=incident, session=session, turn=turn, question=case["question"]
    )
    scored = score_answer(real.get("answer") or "", case["answer"])
    return {
        "case_id": case["case_id"],
        "sample_id": case["sample_id"],
        "category": case["category"],
        "mode": mode,
        "gold_answer": case["answer"],
        "evidence": case["evidence"],
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
    parser = argparse.ArgumentParser(description="LoCoMo C0 vs C2 context strategy contrast")
    parser.add_argument("--data", type=Path, default=ROOT / "data" / "locomo10.json")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--max-tokens", type=int, default=32000)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    _load_env()
    os.environ.setdefault("ANTISENTINEL_MODEL_TIMEOUT_SECONDS", "120")
    model, host, model_name = _make_real_model()
    if not args.data.exists():
        raise SystemExit(f"missing dataset: {args.data}")

    cases = load_locomo_cases(args.data, limit=args.limit)
    rows = []
    for index, case in enumerate(cases, 1):
        row: dict[str, Any] = {
            "case_id": case["case_id"],
            "sample_id": case["sample_id"],
            "category": case["category"],
            "question": case["question"],
        }
        for mode in ("C0", "C2"):
            print(f"[{index}/{len(cases)}] {case['case_id']} {mode}...", flush=True)
            row[mode] = _run_arm(model, case, mode=mode, max_tokens=args.max_tokens, top_k=args.top_k)
        rows.append(row)

    def arm_mean(arm: str, path: list[str]) -> float:
        values = []
        for row in rows:
            cur: Any = row[arm]
            for key in path:
                cur = cur[key]
            values.append(float(cur))
        return _mean(values)

    summary: dict[str, Any] = {
        "design": {
            "C0": "naive_recent_fill raw dialogue sessions to max_tokens",
            "C2": "ContextBudget + keyword recall top-k",
            "dataset": "locomo10.json (official LoCoMo10 shape)",
            "license_note": "LoCoMo is CC BY-NC — research/internal only",
        },
        "mode": "real",
        "model_host": host,
        "model_name": model_name,
        "dataset": str(args.data),
        "n": len(rows),
        "max_tokens": args.max_tokens,
        "top_k": args.top_k,
        "conversations_covered": len({row["sample_id"] for row in rows}),
    }
    for arm in ("C0", "C2"):
        summary[f"{arm}_correct_rate"] = arm_mean(arm, ["score", "correct"])
        summary[f"{arm}_token_recall_mean"] = arm_mean(arm, ["score", "token_recall"])
        summary[f"{arm}_provider_ok_rate"] = arm_mean(arm, ["real", "ok"])
        summary[f"{arm}_mean_input_tokens"] = arm_mean(arm, ["real", "input_tokens"])
        summary[f"{arm}_mean_total_estimated"] = arm_mean(arm, ["pack", "total_estimated"])
        summary[f"{arm}_overflow_rate"] = arm_mean(arm, ["context_meta", "overflow"])
        cov = []
        for row in rows:
            value = row[arm]["context_meta"].get("gold_session_coverage")
            if value is not None:
                cov.append(float(value))
        summary[f"{arm}_mean_gold_session_coverage"] = _mean(cov)

    summary["mean_full_history_estimated_tokens"] = arm_mean(
        "C0", ["context_meta", "full_history_estimated_tokens"]
    )
    summary["full_history_overflow_rate"] = arm_mean(
        "C0", ["context_meta", "full_history_overflow_vs_max"]
    )
    c0_tok = summary["C0_mean_input_tokens"]
    c2_tok = summary["C2_mean_input_tokens"]
    full_est = summary["mean_full_history_estimated_tokens"]
    summary["token_saving_C2_vs_C0"] = round(1.0 - (c2_tok / c0_tok), 4) if c0_tok > 0 else None
    summary["token_saving_C2_vs_full_estimate"] = (
        round(1.0 - (c2_tok / full_est), 4) if full_est > 0 else None
    )
    summary["delta_correct_C2_minus_C0"] = round(
        summary["C2_correct_rate"] - summary["C0_correct_rate"], 4
    )
    claims = {
        "used_real_model": True,
        "C2_provider_all_ok": summary["C2_provider_ok_rate"] == 1.0,
        "C2_saves_tokens_vs_C0": (summary["token_saving_C2_vs_C0"] or 0) > 0.05,
        "C2_correct_not_worse_than_C0_by_5pct": summary["C2_correct_rate"] + 1e-9
        >= summary["C0_correct_rate"] - 0.05,
    }
    summary["claims"] = claims
    summary["pass"] = all(bool(v) for v in claims.values())

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "compare.json", {"summary": summary, "rows": rows})
    (out / "README.md").write_text(
        "\n".join(
            [
                "# LoCoMo10 C0 vs C2 (cross-validation)",
                "",
                f"- model: `{host}` / `{model_name}`",
                f"- n={summary['n']}, conversations={summary['conversations_covered']}",
                f"- license: CC BY-NC (internal research)",
                "",
                "| arm | correct | mean_input_tokens | gold_session_cov | overflow |",
                "|---|---:|---:|---:|---:|",
                f"| C0 naive fill | {summary['C0_correct_rate']} | {summary['C0_mean_input_tokens']} | "
                f"{summary['C0_mean_gold_session_coverage']} | {summary['C0_overflow_rate']} |",
                f"| C2 ContextBudget+recall | {summary['C2_correct_rate']} | {summary['C2_mean_input_tokens']} | "
                f"{summary['C2_mean_gold_session_coverage']} | {summary['C2_overflow_rate']} |",
                "",
                f"- full_history_estimated_mean: **{summary['mean_full_history_estimated_tokens']}**",
                f"- token_saving C2 vs C0: **{summary['token_saving_C2_vs_C0']}**",
                f"- token_saving C2 vs full estimate: **{summary['token_saving_C2_vs_full_estimate']}**",
                f"- pass: **{summary['pass']}**",
                "",
                "```json",
                json.dumps(summary, ensure_ascii=False, indent=2),
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
