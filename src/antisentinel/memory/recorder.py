"""Route runtime results into durable facts and the short-term timeline."""

from __future__ import annotations

from datetime import datetime, timezone

from antisentinel.domain.event import Event
from antisentinel.persistence.event_store import FileEventStore
from antisentinel.persistence.evidence_store import FileEvidenceStore
from antisentinel.persistence.memory_store import FileMemoryStore, InMemoryMemoryCache
from antisentinel.persistence.state_store import FileStateStore
from antisentinel.worker.runtime.loop import RuntimeResult

from .session_tree import SessionTimelineTree
from .rollout import RolloutMemory
from .candidates import CandidateSource, RuleCandidateExtractor, RuleMemoryClassifier
from .jobs import InMemoryMemoryJobQueue, MemoryJob
from .operator_graph import FilePreferenceStore, PreferenceCandidate, PreferenceGraph
from .worker import MemoryWorker
from .recall import MemoryRecall, MemoryScope
from .models import MemoryRecord, MemorySourceRef
from antisentinel.tracing.telemetry import Telemetry, TraceContext


class MemoryRecorder:
    def __init__(
        self,
        storage_root: str | None = None,
        *, event_store=None, evidence_store=None, state_store=None,
        memory_store=None, preference_store=None, job_queue=None, memory_classifier=None,
        cache=None, start_worker: bool = False, telemetry: Telemetry | None = None, candidate_retriever=None,
    ) -> None:
        if storage_root is None and any(item is None for item in (event_store, evidence_store, state_store, memory_store, preference_store)):
            raise ValueError("storage_root or all persistence stores are required")
        self.events = event_store or FileEventStore(storage_root)
        self.evidence = evidence_store or FileEvidenceStore(storage_root)
        self.state = state_store or FileStateStore(storage_root)
        shared_cache = cache or InMemoryMemoryCache()
        durable_memory = memory_store or FileMemoryStore(storage_root)
        self.rollout_memory = RolloutMemory(durable_memory, shared_cache)
        self.preference_graph = PreferenceGraph(preference_store or FilePreferenceStore(storage_root), shared_cache)
        self.memory_jobs = job_queue or InMemoryMemoryJobQueue()
        self.candidate_extractor = RuleCandidateExtractor()
        self.memory_classifier = memory_classifier or RuleMemoryClassifier()
        self.worker = MemoryWorker(self)
        self.telemetry = telemetry or Telemetry(service_name="antisentinel.memory")
        self.candidate_retriever = candidate_retriever
        self.trees: dict[str, SessionTimelineTree] = {}
        if start_worker:
            self.worker.start()

    def recall_context(self, *, session_id: str, operator_id: str, incident_id: str, query: str, token_budget: int = 400):
        tree = self.trees.setdefault(session_id, SessionTimelineTree(session_id))
        return MemoryRecall(
            {session_id: tree},
            records_provider=self.rollout_memory.durable.list_by_operator,
            evidence_lookup=self.evidence.get,
            candidate_retriever=self.candidate_retriever,
        ).recall(
            MemoryScope(kind="session", scope_id=session_id),
            query=query, token_budget=token_budget,
            operator_id=operator_id, incident_id=incident_id,
        )

    def record(self, result: RuntimeResult, *, operator_id: str = "unknown", user_input: str = "") -> SessionTimelineTree:
        trace = TraceContext(
            trace_id=result.trace_id or f"trace-{result.session_id}",
            session_id=result.session_id,
            request_id=f"memory-record-{result.session_id}",
            parent_request_id=f"session-{result.session_id}",
        )
        tree = self.trees.setdefault(result.session_id, SessionTimelineTree(result.session_id))
        for event in sorted(result.events, key=lambda item: item.occurred_at):
            routed = self._route(event, result)
            self.events.append(routed)
            tree.append_event(routed)
        for evidence in result.evidences:
            self.evidence.put_once(evidence, incident_id=result.incident_id)
        self.state.save_projection(
            result.incident_id,
            {
                "session_id": result.session_id,
                "status": result.status,
                "turn_count": result.turn_count,
                "event_count": len(result.events),
                "evidence_ref_count": len(result.evidence_refs),
            },
        )
        if result.status in {"completed", "failed"}:
            with self.telemetry.span("memory.rollout.persist", context=trace):
                self.rollout_memory.record(result)
        source = CandidateSource(
            operator_id=operator_id,
            session_id=result.session_id,
            incident_id=result.incident_id,
            user_input=user_input,
            tool_summaries=tuple(item.get("summary", "") for item in result.task_summaries),
            agent_conclusion=result.final.diagnosis if result.final else None,
        )
        candidates = tuple(self.candidate_extractor.extract(source))
        enqueue_trace = trace.child(request_id=f"memory-enqueue-{result.session_id}")
        with self.telemetry.span("memory.job.enqueue", context=enqueue_trace, candidate_count=len(candidates)):
            self.memory_jobs.enqueue(MemoryJob(job_id=f"memory:{result.session_id}", source=source, candidates=candidates))
        return tree

    def process_one_memory_job(self):
        job = self.memory_jobs.claim()
        if job is None:
            return None
        trace = TraceContext(trace_id=f"trace-{job.source.session_id}", session_id=job.source.session_id, request_id=f"memory-classify-{job.job_id}")
        try:
            with self.telemetry.span("memory.classify", context=trace, candidate_count=len(job.candidates)):
                classifications = tuple(self.memory_classifier.classify(candidate) for candidate in job.candidates)
            for candidate, classification in zip(job.candidates, classifications):
                if not classification.should_remember:
                    continue
                if classification.memory_type == "preference":
                    self.preference_graph.upsert(PreferenceCandidate(
                        operator_id=candidate.operator_id, subject="diagnosis_order", predicate="prefers",
                        object=candidate.text, source_ids=(), confidence=classification.confidence,
                        source_refs=(MemorySourceRef("session", candidate.session_id),),
                    ))
                elif classification.memory_type in {"semantic", "episodic"}:
                    self.rollout_memory.durable.append_record(MemoryRecord.create(
                        memory_id=f"{classification.memory_type}:{candidate.candidate_id}",
                        memory_type=classification.memory_type, operator_id=candidate.operator_id,
                        incident_id=candidate.incident_id, session_id=candidate.session_id, content=candidate.text,
                        source_refs=(MemorySourceRef("session", candidate.session_id),),
                        extraction_confidence=classification.confidence,
                        valid_from=datetime.now(timezone.utc), extractor_revision=type(self.memory_classifier).__name__,
                        reason_code=classification.reason_code,
                    ))
        except Exception as exc:  # retryable derived-memory failure; canonical facts already persisted
            error = type(exc).__name__
            if job.attempts >= 2:
                self.memory_jobs.fail(job.job_id, error)
                with self.telemetry.span("memory.job.failed", context=trace, attempt=job.attempts + 1, error_type=error):
                    pass
            else:
                self.memory_jobs.retry(job.job_id, error)
                with self.telemetry.span("memory.job.retry", context=trace, attempt=job.attempts + 1, error_type=error):
                    pass
            return None
        self.memory_jobs.ack(job.job_id)
        return job, classifications

    @staticmethod
    def _route(event: Event, result: RuntimeResult) -> Event:
        related = {
            "incident_id": str(result.incident_id),
            "session_id": str(result.session_id),
            **dict(event.related_ids),
        }
        return Event(
            event_id=event.event_id, type=event.type, aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id, correlation_id=event.correlation_id,
            occurred_at=event.occurred_at, payload=event.payload,
            causation_id=event.causation_id, related_ids=related,
        )
