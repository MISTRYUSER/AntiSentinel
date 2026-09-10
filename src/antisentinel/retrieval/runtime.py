"""Incident-bound retrieval tools; query embeddings belong to the server."""

from antisentinel.domain.errors import DomainError
from .tools import build_code_retrieval_tools


class IncidentRetrievalTools:
    def __init__(self, service, code_map_store, *, allowed_repositories,
                 query_encoder, graph_expander=None, evidence_assembler=None):
        self.service = service
        self.store = code_map_store
        if isinstance(allowed_repositories, (str, bytes)):
            raise ValueError('repository allowlist must be a collection of IDs')
        self.allowed_repositories = frozenset(allowed_repositories)
        if any(not isinstance(repo, str) or not repo.strip() for repo in self.allowed_repositories):
            raise ValueError('invalid repository allowlist')
        if not self.allowed_repositories or not callable(query_encoder):
            raise ValueError('retrieval requires an explicit repository allowlist and query encoder')
        self.query_encoder = query_encoder
        self.graph_expander = graph_expander
        self.evidence_assembler = evidence_assembler

    def scope(self, incident_id, arguments):
        if any(key in arguments for key in ('incident_id', 'published_generation', 'commit_sha')):
            raise DomainError('caller_scope_forbidden')
        rows = self.store.database.query(
            'SELECT DISTINCT repository_id,snapshot_id,published_generation,requested_commit '
            'FROM code_map_diagnosis_bindings WHERE incident_id=?', (incident_id,))
        selected = [dict(row) for row in rows
                    if row['repository_id'] in self.allowed_repositories
                    and all(key not in arguments or arguments[key] == row[key]
                            for key in ('repository_id', 'snapshot_id'))]
        if not selected:
            raise DomainError('retrieval_scope_unbound')
        if len(selected) != 1:
            raise DomainError('retrieval_scope_ambiguous')
        row = selected[0]
        archive = self.store.read_generation(row['snapshot_id'], row['published_generation'])
        snapshot = archive['snapshot']
        if snapshot['repository_id'] != row['repository_id'] or snapshot['commit_sha'] != row['requested_commit']:
            raise DomainError('retrieval_binding_mismatch')
        return dict(incident_id=incident_id, repository_id=row['repository_id'],
                    snapshot_id=row['snapshot_id'], published_generation=row['published_generation'],
                    commit_sha=row['requested_commit'])

    def for_incident(self, incident_id):
        return build_code_retrieval_tools(
            self.service, lambda arguments: self.scope(incident_id, arguments),
            graph_expander=self.graph_expander, evidence_assembler=self.evidence_assembler,
            query_encoder=self.query_encoder, scope_selectors=True)
