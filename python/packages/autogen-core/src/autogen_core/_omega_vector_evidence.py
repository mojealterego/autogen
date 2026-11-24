"""Scoped Qdrant evidence vector adapter; not a Titans or R3Mem implementation."""
from __future__ import annotations
import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any

_ID=re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_DIGEST=re.compile(r"[0-9a-f]{64}\Z")

@dataclass(frozen=True)
class VectorEvidence:
    tenant_id: str
    case_id: str
    evidence_id: str
    evidence_sha256: str
    vector: tuple[float,...]
    def __post_init__(self) -> None:
        for field in (self.tenant_id,self.case_id,self.evidence_id):
            if not isinstance(field,str) or _ID.fullmatch(field) is None:
                raise ValueError("invalid_identifier")
        if not isinstance(self.evidence_sha256,str) or _DIGEST.fullmatch(self.evidence_sha256) is None:
            raise ValueError("invalid_digest")
        if not isinstance(self.vector,tuple) or not 1 <= len(self.vector) <= 8192:
            raise ValueError("invalid_vector")
        if any(type(value) not in (float,int) or not math.isfinite(value) for value in self.vector):
            raise ValueError("invalid_vector")

class QdrantEvidenceStore:
    def __init__(self,client:Any,*,collection:str) -> None:
        if client is None or not callable(getattr(client,"upsert",None)):
            raise TypeError("invalid_qdrant_client")
        if not isinstance(collection,str) or _ID.fullmatch(collection) is None:
            raise ValueError("invalid_collection")
        self._client=client
        self._collection=collection
    def upsert(self,evidence:VectorEvidence) -> None:
        if not isinstance(evidence,VectorEvidence):
            raise TypeError("invalid_evidence")
        from qdrant_client.models import PointStruct
        # Stable ID binds tenant, case and evidence, preventing cross-tenant point collisions.
        key="\x00".join((evidence.tenant_id,evidence.case_id,evidence.evidence_id))
        digest=hashlib.sha256(key.encode("utf-8")).hexdigest()
        point_id=str(__import__("uuid").UUID(hex=digest[:32]))
        point=PointStruct(id=point_id,vector=list(evidence.vector),payload={
            "tenant_id":evidence.tenant_id,"case_id":evidence.case_id,
            "evidence_id":evidence.evidence_id,"evidence_sha256":evidence.evidence_sha256,
        })
        self._client.upsert(collection_name=self._collection,points=[point],wait=True)
