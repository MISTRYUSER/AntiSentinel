"""SQLite projection store for code retrieval, separate from Memory tables."""

from __future__ import annotations

from collections.abc import Iterable

from antisentinel.persistence.sqlite_database import SQLiteDatabase

from .models import CodeSearchDocument, CodeSearchHit, CodeSearchScope
from .lexical_publication import LexicalPublication, record


class SQLiteCodeSearchStore:
    """Own the code-search projection and its publication pointer."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database
        self.database.initialize()
        existing={r[0] for r in database.query("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'code_search_documents' in existing and 'code_search_documents_fts' not in existing and database.query('SELECT COUNT(*) FROM code_search_documents')[0][0]:
            raise RuntimeError('lexical_index_missing: explicit rebuild required')
        self._ensure_schema()
        self.publications = LexicalPublication(database)

    def _ensure_schema(self) -> None:
        with self.database.transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS code_search_documents (
                    document_id TEXT PRIMARY KEY,
                    repository_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    published_generation INTEGER NOT NULL,
                    commit_sha TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    language TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    embedding_input_hash TEXT NOT NULL,
                    projection_revision TEXT NOT NULL,
                    text TEXT NOT NULL,
                    UNIQUE(repository_id, snapshot_id, published_generation, document_id, projection_revision)
                );
                CREATE INDEX IF NOT EXISTS idx_code_search_documents_scope
                    ON code_search_documents(repository_id, snapshot_id, published_generation, commit_sha);
                CREATE VIRTUAL TABLE IF NOT EXISTS code_search_documents_fts USING fts5(
                    document_id UNINDEXED,
                    path,
                    symbol,
                    identifiers,
                    comments,
                    code,
                    tokenize='unicode61'
                );
                CREATE TABLE IF NOT EXISTS code_search_manifests (
                    repository_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    published_generation INTEGER NOT NULL,
                    commit_sha TEXT NOT NULL,
                    projection_revision TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('building', 'ready', 'failed')),
                    PRIMARY KEY(repository_id, snapshot_id, published_generation, commit_sha, projection_revision)
                );
                """
            )

            fields = {r['name'] for r in connection.execute('PRAGMA table_info(code_search_documents)')}
            for name, kind in [('byte_start','INTEGER'), ('byte_end','INTEGER'), ('parent_source_hash','TEXT')]:
                if name not in fields:
                    connection.execute(f'ALTER TABLE code_search_documents ADD COLUMN {name} {kind}')

    def upsert_documents(self, documents: Iterable[CodeSearchDocument]) -> None:
        values = tuple(documents)
        with self.database.transaction() as connection:
            for document in values:
                published=connection.execute("SELECT 1 FROM code_search_manifests WHERE repository_id=? AND snapshot_id=? AND published_generation=? AND commit_sha=? AND projection_revision=? AND (status='ready' OR published_once=1)",(document.repository_id,document.snapshot_id,document.published_generation,document.commit_sha,document.projection_revision)).fetchone()
                if published:
                    old=connection.execute('SELECT * FROM code_search_documents WHERE document_id=?',(document.document_id,)).fetchone()
                    if old is None or dict(old)!=record(document):
                        raise ValueError('published projection is immutable')
                    continue
                connection.execute(
                    """
                    INSERT INTO code_search_documents (
                        document_id, repository_id, snapshot_id, published_generation, commit_sha,
                        node_id, chunk_id, path, symbol, language, source_hash,
                        embedding_input_hash, projection_revision, text, byte_start, byte_end, parent_source_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(document_id) DO UPDATE SET
                        repository_id=excluded.repository_id,
                        snapshot_id=excluded.snapshot_id,
                        published_generation=excluded.published_generation,
                        commit_sha=excluded.commit_sha,
                        node_id=excluded.node_id,
                        chunk_id=excluded.chunk_id,
                        path=excluded.path,
                        symbol=excluded.symbol,
                        language=excluded.language,
                        source_hash=excluded.source_hash,
                        embedding_input_hash=excluded.embedding_input_hash,
                        projection_revision=excluded.projection_revision,
                        text=excluded.text, byte_start=excluded.byte_start, byte_end=excluded.byte_end,
                        parent_source_hash=excluded.parent_source_hash
                    """,
                    (
                        document.document_id, document.repository_id, document.snapshot_id,
                        document.published_generation, document.commit_sha, document.node_id,
                        document.chunk_id, document.path, document.symbol, document.language,
                        document.source_hash, document.embedding_input_hash,
                        document.projection_revision, document.text, document.byte_start, document.byte_end, document.parent_source_hash,
                    ),
                )
                connection.execute(
                    "DELETE FROM code_search_documents_fts WHERE document_id=?",
                    (document.document_id,),
                )
                connection.execute(
                    """
                    INSERT INTO code_search_documents_fts
                        (document_id, path, symbol, identifiers, comments, code)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document.document_id, document.path, document.symbol,
                        f"{document.path} {document.symbol}", "", document.text,
                    ),
                )

    def begin_manifest(self, scope, projection_revision, expected_documents):
        self.publications.begin(scope, projection_revision, expected_documents)

    def publish_manifest(self, scope: CodeSearchScope, projection_revision: str):
        return self.publications.publish(scope, projection_revision)

    def resolve_projection(self, scope, projection_revision=None):
        return self.publications.active(scope, projection_revision)

    def count_documents(self, scope: CodeSearchScope) -> int:
        rows = self.database.query(
            """
            SELECT COUNT(*) AS count FROM code_search_documents
            WHERE repository_id=? AND snapshot_id=? AND published_generation=? AND commit_sha=?
            """,
            scope.key,
        )
        return int(rows[0]["count"])

    def graph_sources(self, scope, candidate, projection_revision):
        if self.resolve_projection(scope, projection_revision) is None:
            raise ValueError('graph projection not ready')
        rows = self.database.query('SELECT * FROM code_search_documents WHERE repository_id=? AND snapshot_id=? AND published_generation=? AND commit_sha=? AND projection_revision=? AND chunk_id=? ORDER BY byte_start,byte_end,document_id LIMIT 31',
            (*scope.key, projection_revision, candidate['chunk_id']))
        sources = []
        for row in rows:
            doc = CodeSearchDocument(**{key: row[key] for key in CodeSearchDocument.__dataclass_fields__})
            if doc.parent_source_hash != candidate['source_hash'] or doc.node_id != candidate['node_id'] or doc.path != candidate['path']:
                raise ValueError('graph projection source mismatch')
            sources.append({**doc.source_identity, 'document_id': doc.document_id})
        return sources

    def has_ready_manifest(self, scope: CodeSearchScope, *, projection_revision=None) -> bool:
        return self.resolve_projection(scope, projection_revision) is not None

    def query_fts(self, scope: CodeSearchScope, query: str, limit: int = 30, *, projection_revision=None) -> tuple[CodeSearchHit, ...]:
        if not query.strip() or limit < 1:
            return ()
        revision=self.resolve_projection(scope, projection_revision)
        if revision is None:return ()
        rows = self.database.query(
                """
                SELECT f.document_id, bm25(code_search_documents_fts, 1.0, 1.0, 4.0, 2.0, 1.0, 1.0) AS score,
                       d.repository_id, d.snapshot_id, d.published_generation, d.commit_sha,
                       d.node_id, d.chunk_id, d.path, d.source_hash, d.byte_start, d.byte_end, d.parent_source_hash
                FROM code_search_documents_fts AS f
                JOIN code_search_documents AS d ON d.document_id=f.document_id
                JOIN code_search_manifests AS m
                  ON m.repository_id=d.repository_id AND m.snapshot_id=d.snapshot_id
                 AND m.published_generation=d.published_generation AND m.commit_sha=d.commit_sha
                 AND m.projection_revision=d.projection_revision AND m.status='ready'
                WHERE code_search_documents_fts MATCH ? AND d.repository_id=? AND d.snapshot_id=?
                  AND d.published_generation=? AND d.commit_sha=? AND d.projection_revision=?
                ORDER BY score ASC, f.document_id ASC
                LIMIT ?
                """,
                (query, *scope.key, revision, limit),
            )
        return tuple(
            CodeSearchHit(
                document_id=row["document_id"],
                rank=float(row["score"]),
                channel="keyword",
                source_identity={
                    "repository_id": row["repository_id"],
                    "snapshot_id": row["snapshot_id"],
                    "published_generation": row["published_generation"],
                    "commit_sha": row["commit_sha"],
                    "node_id": row["node_id"],
                    "chunk_id": row["chunk_id"],
                    "path": row["path"],
                    "source_hash": row["source_hash"],
                    **_range_identity(row),
                },
            )
            for row in rows
        )

    def query_exact(self, scope: CodeSearchScope, query: str, limit: int = 30, *, projection_revision=None) -> tuple[CodeSearchHit, ...]:
        if not query.strip() or limit < 1:
            return ()
        revision=self.resolve_projection(scope, projection_revision)
        if revision is None:return ()
        rows = self.database.query(
            """
            SELECT d.document_id, d.repository_id, d.snapshot_id, d.published_generation,
                   d.commit_sha, d.node_id, d.chunk_id, d.path, d.source_hash, d.byte_start, d.byte_end, d.parent_source_hash
            FROM code_search_documents AS d
            JOIN code_search_manifests AS m
              ON m.repository_id=d.repository_id AND m.snapshot_id=d.snapshot_id
             AND m.published_generation=d.published_generation AND m.commit_sha=d.commit_sha
             AND m.projection_revision=d.projection_revision AND m.status='ready'
            WHERE d.repository_id=? AND d.snapshot_id=? AND d.published_generation=?
              AND d.commit_sha=? AND d.projection_revision=? AND (d.symbol=? OR d.path=?)
            ORDER BY d.document_id ASC
            LIMIT ?
            """,
            (*scope.key, revision, query.strip(), query.strip(), limit),
        )
        return tuple(
            CodeSearchHit(
                document_id=row["document_id"],
                rank=-1.0,
                channel="keyword",
                source_identity={
                    "repository_id": row["repository_id"],
                    "snapshot_id": row["snapshot_id"],
                    "published_generation": row["published_generation"],
                    "commit_sha": row["commit_sha"],
                    "node_id": row["node_id"],
                    "chunk_id": row["chunk_id"],
                    "path": row["path"],
                    "source_hash": row["source_hash"],
                    **_range_identity(row),
                },
            )
            for row in rows
        )


def _range_identity(row):
    return {key: row[key] for key in ('byte_start','byte_end','parent_source_hash')} if row['byte_start'] is not None else {}
