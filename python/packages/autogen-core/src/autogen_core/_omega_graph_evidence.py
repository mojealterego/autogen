"""Neo4j evidence graph adapter: parameterized, tenant/case scoped writes.

Driver lifecycle, authentication and Neo4j availability are managed externally.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_RELATIONS = frozenset({"related_to", "derived_from", "observed_with", "corroborates", "contradicts"})

@dataclass(frozen=True)
class EvidenceEdge:
    tenant_id: str
    case_id: str
    source_id: str
    relation: str
    target_id: str
    evidence_sha256: str
    def __post_init__(self) -> None:
        for value in (self.tenant_id, self.case_id, self.source_id, self.target_id):
            if not isinstance(value,str) or _IDENTIFIER.fullmatch(value) is None:
                raise ValueError("invalid_graph_identifier")
        if not isinstance(self.relation,str) or self.relation not in _RELATIONS:
            raise ValueError("invalid_relation")
        if not isinstance(self.evidence_sha256,str) or _DIGEST.fullmatch(self.evidence_sha256) is None:
            raise ValueError("invalid_evidence_digest")

_QUERY = """
MERGE (source:Evidence {tenant_id: $tenant_id, case_id: $case_id, evidence_id: $source_id})
MERGE (target:Evidence {tenant_id: $tenant_id, case_id: $case_id, evidence_id: $target_id})
MERGE (source)-[link:LINK {tenant_id: $tenant_id, case_id: $case_id,
  relation: $relation, evidence_sha256: $evidence_sha256}]->(target)
RETURN source.evidence_id AS source_id
"""

class Neo4jEvidenceGraph:
    def __init__(self, driver: Any, *, database: str = "neo4j") -> None:
        if driver is None or not callable(getattr(driver, "session", None)):
            raise TypeError("invalid_driver")
        if not isinstance(database,str) or _IDENTIFIER.fullmatch(database) is None:
            raise ValueError("invalid_database")
        self._driver=driver
        self._database=database

    def add_edge(self, edge: EvidenceEdge) -> None:
        if not isinstance(edge,EvidenceEdge):
            raise TypeError("invalid_edge")
        parameters = {
            "tenant_id": edge.tenant_id, "case_id": edge.case_id,
            "source_id": edge.source_id, "target_id": edge.target_id,
            "relation": edge.relation, "evidence_sha256": edge.evidence_sha256,
        }
        with self._driver.session(database=self._database) as session:
            session.execute_write(lambda tx: tx.run(_QUERY, **parameters).consume())
