from antisentinel.code_map.store import SQLiteCodeMapStore
from antisentinel.code_map.source_context import SourceBudgetExceeded

import pytest

from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.retrieval.graph import GraphExpander, GraphSeed
from antisentinel.retrieval.evidence import CodeEvidenceAssembler
from antisentinel.retrieval.tools import build_code_retrieval_tools
from antisentinel.retrieval.engine import RetrievalResult
from antisentinel.retrieval.fusion import FusedCodeSearchHit


def make_graph_store(tmp_path):
    database = SQLiteDatabase(tmp_path / "facts.sqlite")
    database.initialize()
    with database.transaction() as connection:
        connection.executescript(
            """
            INSERT INTO code_map_repositories(repository_id,remote_url,credential_ref,tracked_ref,min_interval,max_interval,rules_json,budget_json,created_at,updated_at)
            VALUES ('repo','https://example/repo','cred','main',1,10,'{}','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP),
                   ('other-repo','https://example/other','cred','main',1,10,'{}','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP);
            INSERT INTO code_map_snapshots(snapshot_id,repository_id,commit_sha,parser_revision,rules_digest,status,created_at)
            VALUES ('snapshot-a','repo','commit-a','parser-v1','rules-v1','ready',CURRENT_TIMESTAMP);
            UPDATE code_map_snapshots SET published_generation=1;
            INSERT INTO code_map_nodes(node_id,snapshot_id,repository_id,commit_sha,kind,qualified_name,path,start_line,end_line)
            VALUES ('n1','snapshot-a','repo','commit-a','function','root','src/a.py',1,10),
                   ('n2','snapshot-a','repo','commit-a','function','child','src/a.py',6,10),
                   ('n3','snapshot-a','repo','commit-a','function','unrelated','src/b.py',1,5),
                   ('n4','snapshot-a','other-repo','commit-a','function','foreign','src/c.py',1,5);
            INSERT INTO code_map_edges(edge_id,snapshot_id,source_node_id,relation,target_node_id,resolution,basis)
            VALUES ('e1','snapshot-a','n1','contains','n2','resolved','ast'),
                   ('e2','snapshot-a','n1','contains','n3','unresolved','text'),
                   ('e3','snapshot-a','n1','contains','n4','resolved','ast');
            """
        )
    return database


def test_graph_expander_uses_validated_edges_and_reports_unresolved(tmp_path):
    store = SQLiteCodeMapStore(make_graph_store(tmp_path))
    expander = GraphExpander(store)
    scope = {"repository_id": "repo", "snapshot_id": "snapshot-a", "commit_sha": "commit-a", "published_generation": 1}

    result = expander.expand([GraphSeed("n1", "doc-root", 1)], scope=scope, depth=1, node_budget=20, edge_budget=40)

    assert [item.node_id for item in result.items] == ["n2"]
    assert result.items[0].seed_document_id == "doc-root"
    assert result.items[0].relation == "contains"
    assert result.unresolved_count == 1
    assert result.degraded is True


def test_graph_expander_marks_budget_truncation(tmp_path):
    store = SQLiteCodeMapStore(make_graph_store(tmp_path))
    with pytest.raises(ValueError, match="maximum"):
        GraphExpander(store).expand([GraphSeed("n1", "doc-root", 1)], scope={"repository_id": "repo", "snapshot_id": "snapshot-a", "commit_sha": "commit-a", "published_generation": 1}, depth=1, node_budget=21, edge_budget=40)


class FakeSourceEvidence:
    def __init__(self):
        self.calls = []

    def read_source(self, incident_id, repository_id, snapshot_id, chunk_id, *, max_bytes=32768, **kwargs):
        self.calls.append((incident_id, repository_id, snapshot_id, chunk_id))
        if max_bytes < 10:
            raise SourceBudgetExceeded('source_budget_exceeded')
        return {"evidence_id": f"e-{chunk_id}", "path": "src/a.py", "content": "x" * 10, "content_hash": f"hash-{chunk_id}"}


def test_evidence_assembler_deduplicates_and_enforces_fragment_budget():
    source = FakeSourceEvidence()
    assembler = CodeEvidenceAssembler(source)
    candidates = [
        {"repository_id": "repo", "snapshot_id": "snapshot-a", "chunk_id": "chunk-1"},
        {"repository_id": "repo", "snapshot_id": "snapshot-a", "chunk_id": "chunk-1"},
        {"repository_id": "repo", "snapshot_id": "snapshot-a", "chunk_id": "chunk-2"},
    ]

    result = assembler.select("incident-a", candidates, max_fragments=2, max_bytes=15)

    assert len(result.slices) == 1
    assert result.total_bytes == 10
    assert result.truncated is True
    assert len(source.calls) == 2


def test_retrieval_tools_are_read_only_and_return_bounded_payloads():
    hit = FusedCodeSearchHit("doc-1", 1.0, ("keyword",), {"keyword": 1}, {"path": "src/a.py"})
    service = type("Service", (), {"search": lambda self, *args, **kwargs: RetrievalResult((hit,), "keyword", {"keyword": "ready"})})()
    graph = type("Graph", (), {"expand": lambda self, seeds, **kwargs: type("Result", (), {"items": (), "unresolved_count": 0, "degraded": False, "truncated": False})()})()
    evidence = CodeEvidenceAssembler(FakeSourceEvidence())
    scope = lambda arguments: {"incident_id": "incident-a", "repository_id": "repo", "snapshot_id": "snapshot-a", "published_generation": 1, "commit_sha": "commit-a"}

    definitions = build_code_retrieval_tools(service, scope, graph_expander=graph, evidence_assembler=evidence)

    assert [definition.name for definition in definitions] == ["code_retrieval.search", "code_retrieval.expand_graph", "code_retrieval.read_evidence"]
    assert all(definition.read_only is True for definition in definitions)
    result = definitions[0].handler({"query": "timeout", "mode": "keyword"})
    assert result.status == "succeeded"
    assert result.result["hits"][0]["document_id"] == "doc-1"
