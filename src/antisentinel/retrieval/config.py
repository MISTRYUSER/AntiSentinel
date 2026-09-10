"""Default-off application retrieval configuration; clients open at startup."""
import os


def coordinator_from_environment(service):
    enabled = os.getenv('ANTISENTINEL_CODE_RETRIEVAL_ENABLED', 'false').strip().lower()
    if enabled not in {'true', 'false'}:
        raise ValueError('CODE_RETRIEVAL_ENABLED must be true or false')
    if enabled == 'false':
        return None
    repositories = frozenset(item.strip() for item in os.getenv('ANTISENTINEL_CODE_RETRIEVAL_REPOSITORIES', '').split(',') if item.strip())
    uri = os.getenv('ANTISENTINEL_CODE_RETRIEVAL_MILVUS_URI', '').strip()
    if not repositories or not uri:
        raise ValueError('retrieval requires repository allowlist and Milvus URI')
    if service.code_map_store is None or service.code_map_evidence_store is None:
        raise ValueError('retrieval requires SQLite persistence and Evidence store')
    from antisentinel.code_map.source_context import SourceEvidenceService
    from antisentinel.persistence.local_vector_memory import QwenFlashEmbedder
    from .coordinator import RetrievalCoordinator
    from .milvus_adapter import MilvusAdapter
    from .slicing import SLICE_REVISION

    revision = os.getenv('ANTISENTINEL_CODE_RETRIEVAL_PROJECTION', 'path-symbol-source-v1').strip()
    if not revision:
        raise ValueError('empty retrieval projection revision')
    def embedder_factory():
        embedder = QwenFlashEmbedder.from_env()
        embedder.max_retries = 0  # The durable worker owns retry accounting.
        return embedder
    def index_factory(embedder):
        return MilvusAdapter(uri, 'antisentinel_code', dimension=embedder.dimension,
            model_revision=embedder.model_name, template_revision='path-symbol-source-v1',
            projection_revision=revision, rpc_timeout=10.0,
            token=os.getenv('ANTISENTINEL_CODE_RETRIEVAL_MILVUS_TOKEN') or None,
            source_ranges=revision == SLICE_REVISION)
    return RetrievalCoordinator(service.code_map_store,
        SourceEvidenceService(None, service.code_map_store, service.code_map_evidence_store),
        allowed_repositories=repositories, index_factory=index_factory,
        embedder_factory=embedder_factory,
        query_timeout=float(os.getenv('ANTISENTINEL_CODE_RETRIEVAL_QUERY_TIMEOUT', '10')))
