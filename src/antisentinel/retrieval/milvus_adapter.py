"""Milvus vector-index adapter with server-side scope filtering."""

from __future__ import annotations

from pathlib import Path
import hashlib
import math
from typing import Any

from pymilvus import DataType, MilvusClient

from .models import CodeSearchScope
from antisentinel.runtime.deadline import current_deadline
from .vector import ReconciliationReport, VectorPoint, VectorSearchHit


_OUTPUT_FIELDS = [
    "point_id", "document_id", "repository_id", "snapshot_id", "published_generation", "commit_sha",
    "node_id", "chunk_id", "path", "source_hash", "input_hash", "model_revision",
    "dimension", "template_revision", "projection_revision",
]


class MilvusAdapter:
    def __init__(self, uri: str | Path, collection_name: str, *, dimension: int, model_revision: str, template_revision: str, projection_revision: str, client: Any | None = None, rpc_timeout: float | None = None, token: str | None = None, source_ranges: bool = False) -> None:
        if type(source_ranges) is not bool:
            raise ValueError('source_ranges must be boolean')
        if not collection_name.strip() or dimension < 1 or not model_revision.strip() or not template_revision.strip() or not projection_revision.strip():
            raise ValueError("collection_name, dimension, and version identity must be valid")
        self.uri = str(uri)
        if rpc_timeout is not None and (not math.isfinite(rpc_timeout) or rpc_timeout<=0):raise ValueError('invalid RPC timeout')
        self.rpc_timeout=rpc_timeout
        self.base_collection_name = collection_name
        self.dimension = dimension
        self.model_revision = model_revision
        self.template_revision = template_revision
        self.projection_revision = projection_revision
        self.source_ranges = source_ranges
        self.output_fields = _OUTPUT_FIELDS + (['byte_start','byte_end','parent_source_hash'] if source_ranges else [])
        self.collection_name = versioned_collection_name(collection_name, model_revision, dimension, template_revision, projection_revision)
        if client is None:
            if not self.uri.startswith(("http://", "https://", "unix:")):
                Path(self.uri).parent.mkdir(parents=True, exist_ok=True)
            connection = dict(self.rpc_options)
            if token is not None:
                connection['token'] = token
            client = MilvusClient(uri=self.uri, **connection)
        self.client = client

    @property
    def rpc_options(self):
        deadline = current_deadline()
        if deadline is not None:
            remaining = deadline.remaining()
            return {'timeout': min(self.rpc_timeout, remaining) if self.rpc_timeout is not None else remaining}
        return {} if self.rpc_timeout is None else {'timeout': self.rpc_timeout}

    def ensure_collection(self) -> None:
        if not self.client.has_collection(collection_name=self.collection_name, **self.rpc_options):
            schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("point_id", DataType.VARCHAR, is_primary=True, max_length=128)
            schema.add_field("document_id", DataType.VARCHAR, max_length=256)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self.dimension)
            schema.add_field("repository_id", DataType.VARCHAR, max_length=256)
            schema.add_field("snapshot_id", DataType.VARCHAR, max_length=256)
            schema.add_field("published_generation", DataType.INT64)
            schema.add_field("commit_sha", DataType.VARCHAR, max_length=256)
            schema.add_field("node_id", DataType.VARCHAR, max_length=256)
            schema.add_field("chunk_id", DataType.VARCHAR, max_length=256)
            schema.add_field("path", DataType.VARCHAR, max_length=2048)
            schema.add_field("source_hash", DataType.VARCHAR, max_length=256)
            schema.add_field("input_hash", DataType.VARCHAR, max_length=256)
            schema.add_field("model_revision", DataType.VARCHAR, max_length=256)
            schema.add_field("dimension", DataType.INT64)
            schema.add_field("template_revision", DataType.VARCHAR, max_length=256)
            schema.add_field("projection_revision", DataType.VARCHAR, max_length=256)
            if self.source_ranges:
                schema.add_field('byte_start', DataType.INT64)
                schema.add_field('byte_end', DataType.INT64)
                schema.add_field('parent_source_hash', DataType.VARCHAR, max_length=256)
            index_params = MilvusClient.prepare_index_params()
            index_params.add_index(field_name="vector", index_type="FLAT", metric_type="COSINE")
            self.client.create_collection(collection_name=self.collection_name, schema=schema, index_params=index_params, **self.rpc_options)
        else:
            self._validate_collection()
        load_collection = getattr(self.client, "load_collection", None)
        if load_collection is not None:
            load_collection(collection_name=self.collection_name, **self.rpc_options)

    def _validate_collection(self) -> None:
        describe = getattr(self.client, "describe_collection", None)
        if describe is None:
            raise ValueError("Milvus collection schema mismatch: describe_collection unavailable")
        description = describe(collection_name=self.collection_name, **self.rpc_options)
        fields = {field["name"]: field for field in description.get("fields", ())}
        required = {"point_id", "document_id", "vector", "repository_id", "snapshot_id", "published_generation", "commit_sha", "node_id", "chunk_id", "path", "source_hash", "input_hash", "model_revision", "dimension", "template_revision", "projection_revision"}
        if self.source_ranges:
            required.update(('byte_start','byte_end','parent_source_hash'))
        if set(fields) != required or description.get("auto_id") is True:
            raise ValueError("Milvus collection schema mismatch: required fields or auto_id differ")
        if not fields["point_id"].get("is_primary") or fields["point_id"].get("type") != DataType.VARCHAR:
            raise ValueError("Milvus collection schema mismatch: point_id")
        vector = fields["vector"]
        if vector.get("type") != DataType.FLOAT_VECTOR or vector.get("params", {}).get("dim") != self.dimension:
            raise ValueError("Milvus collection schema mismatch: vector dimension")
        for name in ("document_id", "repository_id", "snapshot_id", "commit_sha", "node_id", "chunk_id", "path", "source_hash", "input_hash", "model_revision", "template_revision", "projection_revision"):
            if fields[name].get("type") != DataType.VARCHAR:
                raise ValueError(f"Milvus collection schema mismatch: {name}")
        for name in ("published_generation", "dimension"):
            if fields[name].get("type") != DataType.INT64:
                raise ValueError(f"Milvus collection schema mismatch: {name}")
        if self.source_ranges and (any(fields[key].get('type') != DataType.INT64 for key in ('byte_start','byte_end')) or fields['parent_source_hash'].get('type') != DataType.VARCHAR):
            raise ValueError('Milvus source range schema mismatch')
        list_indexes = getattr(self.client, "list_indexes", None)
        describe_index = getattr(self.client, "describe_index", None)
        if list_indexes is None or describe_index is None:
            raise ValueError("Milvus collection schema mismatch: index inspection unavailable")
        indexes = list_indexes(collection_name=self.collection_name, **self.rpc_options)
        if "vector" not in indexes:
            raise ValueError("Milvus collection schema mismatch: vector index missing")
        index = describe_index(collection_name=self.collection_name, index_name="vector", **self.rpc_options)
        if index.get("index_type") != "FLAT" or index.get("metric_type") != "COSINE":
            raise ValueError("Milvus collection schema mismatch: vector index")

    def upsert(self, points: tuple[VectorPoint, ...] | list[VectorPoint]) -> int:
        self.ensure_collection()
        rows = []
        for point in points:
            if self.source_ranges != ('parent_source_hash' in point.source_identity):
                raise ValueError('source range schema does not match projection')
            if point.dimension != self.dimension or len(point.vector) != self.dimension:
                raise ValueError("vector dimension does not match collection dimension")
            if (point.model_revision, point.template_revision, point.projection_revision) != (self.model_revision, self.template_revision, self.projection_revision):
                raise ValueError("vector version identity does not match collection")
            rows.append(_point_row(point))
        if not rows:
            return 0
        result = self.client.upsert(collection_name=self.collection_name, data=rows, **self.rpc_options)
        return int(result.get("upsert_count", len(rows))) if isinstance(result, dict) else len(rows)

    def get(self, point_ids: list[str]) -> list[dict[str, Any]]:
        self.ensure_collection()
        return list(self.client.get(collection_name=self.collection_name, ids=point_ids, output_fields=self.output_fields, consistency_level='Strong', **self.rpc_options))

    def search(
        self,
        vector: tuple[float, ...] | list[float],
        scope: CodeSearchScope,
        *,
        model_revision: str,
        template_revision: str,
        projection_revision: str,
        channel_store: Any,
        limit: int = 30,
    ) -> tuple[VectorSearchHit, ...]:
        if len(vector) != self.dimension or limit < 1:
            raise ValueError("vector dimension or limit is invalid")
        if (model_revision, template_revision, projection_revision) != (self.model_revision, self.template_revision, self.projection_revision):
            raise ValueError("query version identity does not match collection")
        if scope.commit_sha.strip() == "":
            raise ValueError("scope is invalid")
        if channel_store.get_channel_status(*scope.key, model_revision=model_revision, dimension=self.dimension, template_revision=template_revision, projection_revision=projection_revision) != "ready":
            raise RuntimeError("channel_not_ready")
        self.ensure_collection()
        expression = " AND ".join(
            (
                _match("repository_id", scope.repository_id),
                _match("snapshot_id", scope.snapshot_id),
                f"published_generation == {scope.published_generation}",
                _match("commit_sha", scope.commit_sha),
                _match("model_revision", model_revision),
                f"dimension == {self.dimension}",
                _match("template_revision", template_revision),
                _match("projection_revision", projection_revision),
            )
        )
        result = self.client.search(
            collection_name=self.collection_name,
            data=[list(vector)],
            filter=expression,
            limit=limit,
            output_fields=self.output_fields,
            anns_field="vector",
            **self.rpc_options,
        )
        rows = result[0] if result else []
        payloads = []
        for row in rows:
            entity = dict(row.get('entity') if isinstance(row.get('entity'), dict) else row)
            outer_id = row.get('id', row.get('point_id'))
            if outer_id is not None and entity.get('point_id') != outer_id:
                raise ValueError('vector_authority_mismatch: primary_key')
            payloads.append(entity)
        channel_store.verify_vector_rows(payloads, scope, model_revision=model_revision,
            dimension=self.dimension, template_revision=template_revision, projection_revision=projection_revision)
        return tuple(
            _search_hit(row)
            for row in rows
        )

    def reconcile(self, points: tuple[VectorPoint, ...] | list[VectorPoint], *, expected_fields: dict[str, dict[str, Any]] | None = None) -> ReconciliationReport:
        scopes = {(point.source_identity["repository_id"], point.source_identity["snapshot_id"], point.source_identity["published_generation"], point.source_identity["commit_sha"]) for point in points}
        if len(scopes) > 1:
            raise ValueError("reconciliation requires a single Scope")
        if any((point.model_revision, point.template_revision, point.projection_revision) != (self.model_revision, self.template_revision, self.projection_revision) for point in points):
            raise ValueError("reconciliation version identity does not match collection")
        expected = {point.point_id: _point_row(point) for point in points}
        for point_id, overrides in (expected_fields or {}).items():
            if point_id in expected:
                expected[point_id].update(overrides)
        if not expected:
            return ReconciliationReport(frozenset(), frozenset(), frozenset(), frozenset(), {})
        self.ensure_collection()
        first = next(iter(expected.values()))
        scope_filter = " AND ".join(
            (_match("repository_id", first["repository_id"]), _match("snapshot_id", first["snapshot_id"]),
             f"published_generation == {first['published_generation']}", _match("commit_sha", first["commit_sha"]))
        )
        if hasattr(self.client, "query"):
            rows = self.client.query(collection_name=self.collection_name, filter=scope_filter, output_fields=self.output_fields, consistency_level='Strong', **self.rpc_options)
        else:
            rows = self.get(list(expected))
        actual_rows = {str(row.get("point_id", row.get("id"))): row for row in rows}
        expected_ids, actual_ids = frozenset(expected), frozenset(actual_rows)
        mismatches: dict[str, tuple[str, ...]] = {}
        for point_id in expected_ids & actual_ids:
            differing = tuple(sorted(key for key, value in expected[point_id].items() if key != "vector" and (key not in actual_rows[point_id] or actual_rows[point_id][key] != value)))
            if differing:
                mismatches[point_id] = differing
        return ReconciliationReport(expected_ids, actual_ids, expected_ids - actual_ids, actual_ids - expected_ids, mismatches)

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close is not None:
            close()


def _match(field: str, value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{field} == "{escaped}"'


def versioned_collection_name(base_name: str, model_revision: str, dimension: int, template_revision: str, projection_revision: str) -> str:
    identity = "\x1f".join((base_name, model_revision, str(dimension), template_revision, projection_revision))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"{base_name}_{digest}"


def _point_row(point: VectorPoint) -> dict[str, Any]:
    return {
        "point_id": point.point_id,
        "document_id": point.document_id,
        "vector": list(point.vector),
        **dict(point.source_identity),
        "input_hash": point.input_hash,
        "model_revision": point.model_revision,
        "dimension": point.dimension,
        "template_revision": point.template_revision,
        "projection_revision": point.projection_revision,
    }


def _search_hit(row: dict[str, Any]) -> VectorSearchHit:
    entity = row.get("entity") if isinstance(row.get("entity"), dict) else row
    point_id = row.get("id", row.get("point_id", entity.get("point_id")))
    return VectorSearchHit(
        point_id=str(point_id),
        score=float(row.get("distance", row.get("score", 0.0))),
        source_identity={key: entity[key] for key in ("repository_id", "snapshot_id", "published_generation", "commit_sha", "node_id", "chunk_id", "path", "source_hash", "byte_start", "byte_end", "parent_source_hash") if key in entity},
        document_id=str(entity.get("document_id", point_id)),
    )
