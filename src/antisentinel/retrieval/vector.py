"""Vector-index contracts and durable embedding task state."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Protocol

from antisentinel.persistence.sqlite_database import SQLiteDatabase


@dataclass(frozen=True)
class VectorPoint:
    point_id: str
    vector: tuple[float, ...]
    source_identity: Mapping[str, Any]
    input_hash: str
    model_revision: str
    dimension: int
    template_revision: str
    projection_revision: str
    document_id: str | None = None

    def __post_init__(self) -> None:
        if not self.point_id.strip() or not self.input_hash.strip() or not self.model_revision.strip() or not self.template_revision.strip() or not self.projection_revision.strip():
            raise ValueError("vector point identity fields must be non-empty")
        if self.dimension < 1 or len(self.vector) != self.dimension:
            raise ValueError("vector dimension does not match vector length")
        required = ("repository_id", "snapshot_id", "published_generation", "commit_sha", "node_id", "chunk_id", "path", "source_hash")
        if any(key not in self.source_identity for key in required):
            raise ValueError("vector point source identity is incomplete")
        document_id = self.document_id or hashlib.sha256(json.dumps(dict(self.source_identity), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        object.__setattr__(self, "document_id", document_id)
        object.__setattr__(self, "point_id", canonical_point_id(document_id, self.model_revision, self.dimension, self.template_revision, self.projection_revision))


def canonical_point_id(document_id: str, model_revision: str, dimension: int, template_revision: str, projection_revision: str) -> str:
    value = "\x1f".join((document_id, model_revision, str(dimension), template_revision, projection_revision))
    return "vec-" + hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VectorSearchHit:
    point_id: str
    score: float
    source_identity: dict[str, Any]
    channel: str = "vector"
    document_id: str | None = None

    def __post_init__(self) -> None:
        if self.document_id is None:
            object.__setattr__(self, "document_id", self.point_id)


@dataclass(frozen=True)
class ReconciliationReport:
    expected_ids: frozenset[str]
    actual_ids: frozenset[str]
    missing_ids: frozenset[str]
    extra_ids: frozenset[str]
    field_mismatches: dict[str, tuple[str, ...]]

    @property
    def is_consistent(self) -> bool:
        return not self.missing_ids and not self.extra_ids and not self.field_mismatches


class VectorIndexPort(Protocol):
    def ensure_collection(self) -> None: ...
    def upsert(self, points: tuple[VectorPoint, ...] | list[VectorPoint]) -> int: ...
    def get(self, point_ids: list[str]) -> list[dict[str, Any]]: ...


class EmbeddingTaskStore:
    """Keep embedding task state and channel state in SQLite, separately."""

    _TASK_STATUSES = {"pending", "running", "retry_wait", "ready", "failed"}
    _CHANNEL_STATUSES = {"building", "ready", "blocked", "degraded"}

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database
        self.database.initialize()
        with self.database.transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS code_embedding_tasks (
                    task_id TEXT PRIMARY KEY,
                    point_id TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    model_revision TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    template_revision TEXT NOT NULL,
                    projection_revision TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    retryable INTEGER NOT NULL DEFAULT 1,
                    started_at TEXT,
                    completed_at TEXT,
                    duration_ms REAL,
                    error_code TEXT,
                    error_message TEXT,
                    lease_owner TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS code_embedding_channels (
                    repository_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    published_generation INTEGER NOT NULL,
                    commit_sha TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(repository_id, snapshot_id, published_generation, commit_sha)
                );
                CREATE INDEX IF NOT EXISTS idx_code_embedding_tasks_point ON code_embedding_tasks(point_id);
                """
            )
            existing = {row["name"] for row in connection.execute("PRAGMA table_info(code_embedding_tasks)").fetchall()}
            for name, declaration in (
                ("attempt", "INTEGER NOT NULL DEFAULT 0"), ("started_at", "TEXT"), ("completed_at", "TEXT"),
                ("duration_ms", "REAL"), ("error_code", "TEXT"), ("error_message", "TEXT"), ("lease_owner", "TEXT"),
            ):
                if name not in existing:
                    connection.execute(f"ALTER TABLE code_embedding_tasks ADD COLUMN {name} {declaration}")
        from .embedding_jobs import EmbeddingQueue
        self.queue = EmbeddingQueue(database)

    def create_task(self, task_id: str, point_id: str, input_hash: str, model_revision: str, dimension: int, template_revision: str, projection_revision: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO code_embedding_tasks
                    (task_id, point_id, input_hash, model_revision, dimension, template_revision, projection_revision, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (task_id, point_id, input_hash, model_revision, dimension, template_revision, projection_revision),
            )

    def set_task_status(self, task_id: str, status: str, *, retryable: bool = True, attempt: int | None = None, error_code: str | None = None, error_message: str | None = None, lease_owner: str | None = None, duration_ms: float | None = None) -> None:
        if status not in self._TASK_STATUSES:
            raise ValueError(f"invalid embedding task status: {status}")
        with self.database.transaction() as connection:
            managed=connection.execute('SELECT run_id FROM code_embedding_tasks WHERE task_id=?',(task_id,)).fetchone()
            if managed and managed['run_id'] is not None:
                raise ValueError('managed embedding tasks require a fenced lease')
            updates, params = ["status=?", "retryable=?", "updated_at=CURRENT_TIMESTAMP"], [status, int(retryable)]
            if attempt is not None:
                updates.append("attempt=?"); params.append(attempt)
            if status == "running":
                updates.append("started_at=COALESCE(started_at, CURRENT_TIMESTAMP)")
            if status in {"ready", "failed"}:
                updates.append("completed_at=CURRENT_TIMESTAMP")
            for column, value in (("error_code", error_code), ("error_message", error_message), ("lease_owner", lease_owner), ("duration_ms", duration_ms)):
                if value is not None:
                    updates.append(f"{column}=?"); params.append(value)
            params.append(task_id)
            result = connection.execute(f"UPDATE code_embedding_tasks SET {', '.join(updates)} WHERE task_id=?", params)
            if result.rowcount != 1:
                raise KeyError(f"unknown embedding task: {task_id}")

    def get_task_status(self, task_id: str) -> str | None:
        rows = self.database.query("SELECT status FROM code_embedding_tasks WHERE task_id=?", (task_id,))
        return str(rows[0]["status"]) if rows else None

    def get_task(self, task_id: str) -> dict[str, Any]:
        rows = self.database.query("SELECT * FROM code_embedding_tasks WHERE task_id=?", (task_id,))
        if not rows:
            raise KeyError(f"unknown embedding task: {task_id}")
        return dict(rows[0])

    def set_channel_status(self, repository_id: str, snapshot_id: str, published_generation: int, commit_sha: str, status: str) -> None:
        if status not in self._CHANNEL_STATUSES:
            raise ValueError(f"invalid embedding channel status: {status}")
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO code_embedding_channels
                    (repository_id, snapshot_id, published_generation, commit_sha, status)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(repository_id, snapshot_id, published_generation, commit_sha)
                DO UPDATE SET status=excluded.status, updated_at=CURRENT_TIMESTAMP
                """,
                (repository_id, snapshot_id, published_generation, commit_sha, status),
            )

    def get_channel_status(self, repository_id: str, snapshot_id: str, published_generation: int, commit_sha: str, *, model_revision=None, dimension=None, template_revision=None, projection_revision=None) -> str | None:
        versions=(model_revision,dimension,template_revision,projection_revision)
        if any(v is not None for v in versions):
            if any(v is None for v in versions):raise ValueError('complete channel version required')
            from .models import CodeSearchScope
            from .embedding_jobs import projection_id
            rid=projection_id(CodeSearchScope(repository_id,snapshot_id,published_generation,commit_sha),*versions)
            rows=self.database.query('SELECT status FROM code_embedding_projection_runs WHERE run_id=?',(rid,))
            return str(rows[0][0]) if rows else None
        rows = self.database.query(
            "SELECT status FROM code_embedding_channels WHERE repository_id=? AND snapshot_id=? AND published_generation=? AND commit_sha=?",
            (repository_id, snapshot_id, published_generation, commit_sha),
        )
        return str(rows[0]["status"]) if rows else None

    def mark_channel_ready(self, scope, report: ReconciliationReport) -> bool:
        if not report.is_consistent or not report.expected_ids:
            return False
        # Compatibility for explicit diagnostic publishers; never bypass Worker leases.
        from .embedding_jobs import projection_id
        with self.database.transaction() as c:
            tasks=[]
            for pid in report.expected_ids:
                values=list(c.execute('SELECT * FROM code_embedding_tasks WHERE point_id=?',(pid,)))
                if len(values)!=1 or values[0]['status']!='ready':return False
                tasks.append(values[0])
            versions={(t['model_revision'],t['dimension'],t['template_revision'],t['projection_revision']) for t in tasks}
            if len(versions)!=1:return False
            model,dimension,template,projection=next(iter(versions))
            self.queue._active(c,scope.key,projection)
            docs=list(c.execute('SELECT * FROM code_search_documents WHERE repository_id=? AND snapshot_id=? AND published_generation=? AND commit_sha=? AND projection_revision=?',(*scope.key,projection)))
            expected={canonical_point_id(d['document_id'],model,dimension,template,projection):d for d in docs}
            if set(expected)!=set(report.expected_ids) or set(expected)!=set(report.actual_ids):return False
            if any(t['input_hash']!=expected[t['point_id']]['embedding_input_hash'] or t['run_id'] is not None for t in tasks):return False
            rid=projection_id(scope,model,dimension,template,projection)
            existing=c.execute('SELECT mode FROM code_embedding_projection_runs WHERE run_id=?',(rid,)).fetchone()
            if existing and existing['mode']=='worker':return False
            c.execute('''INSERT INTO code_embedding_projection_runs VALUES(?,?,?,?,?,?,?,?,?,?, 'ready','diagnostic',?,NULL)
                ON CONFLICT(run_id) DO UPDATE SET status='ready',report_json=excluded.report_json''',
                (rid,*scope.key,model,dimension,template,projection,json.dumps(sorted(expected)),json.dumps({'expected_ids':sorted(report.expected_ids),'actual_ids':sorted(report.actual_ids)})))
        self.set_channel_status(*scope.key, "ready")
        return True

    def verify_vector_rows(self, rows, scope, *, model_revision, dimension, template_revision, projection_revision) -> None:
        """Fail closed against one SQLite snapshot before exposing index payloads."""
        from .models import CodeSearchDocument, normalize_document_record

        with self.database.transaction() as connection:
            channel = connection.execute('SELECT status FROM code_embedding_projection_runs WHERE repository_id=? AND snapshot_id=? AND published_generation=? AND commit_sha=? AND model_revision=? AND dimension=? AND template_revision=? AND projection_revision=?',(*scope.key,model_revision,dimension,template_revision,projection_revision)).fetchone()
            manifest = connection.execute(
                "SELECT m.status FROM code_search_manifests m JOIN code_search_active_projections a USING(repository_id,snapshot_id,published_generation,commit_sha,projection_revision) WHERE m.repository_id=? AND m.snapshot_id=? AND m.published_generation=? AND m.commit_sha=? AND m.projection_revision=? AND m.expected_json IS NOT NULL",
                (*scope.key, projection_revision),
            ).fetchone()
            if channel is None or channel['status'] != 'ready' or manifest is None or manifest['status'] != 'ready':
                raise ValueError('vector_authority_mismatch: publication')
            for payload in rows:
                row = connection.execute('SELECT * FROM code_search_documents WHERE document_id=?', (payload.get('document_id'),)).fetchone()
                if row is None:
                    raise ValueError('vector_authority_mismatch: document')
                data = normalize_document_record(row)
                document = CodeSearchDocument(**{k: data[k] for k in CodeSearchDocument.__dataclass_fields__})
                if document.byte_start is None and any(payload.get(key) is not None for key in ('byte_start','byte_end','parent_source_hash')):
                    raise ValueError('vector_authority_mismatch: unexpected_range')
                if tuple(getattr(document, k) for k in ('repository_id', 'snapshot_id', 'published_generation', 'commit_sha')) != scope.key or document.projection_revision != projection_revision:
                    raise ValueError('vector_authority_mismatch: scope_or_projection')
                point_id = canonical_point_id(document.document_id, model_revision, dimension, template_revision, projection_revision)
                expected = {**document.source_identity, 'document_id': document.document_id, 'point_id': point_id,
                    'input_hash': document.embedding_input_hash, 'model_revision': model_revision, 'dimension': dimension,
                    'template_revision': template_revision, 'projection_revision': projection_revision}
                if any(key not in payload or payload[key] != value for key, value in expected.items()):
                    raise ValueError('vector_authority_mismatch: payload')
                tasks = connection.execute('SELECT * FROM code_embedding_tasks WHERE point_id=?', (point_id,)).fetchall()
                if len(tasks) != 1 or tasks[0]['status'] != 'ready' or any(tasks[0][key] != expected[key] for key in ('input_hash', 'model_revision', 'dimension', 'template_revision', 'projection_revision')):
                    raise ValueError('vector_authority_mismatch: task')
