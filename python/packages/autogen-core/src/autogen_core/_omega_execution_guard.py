"""Bounded, deterministic offline forensic execution guard.

In-memory idempotency and audit are process-local, not durable production guarantees.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from typing import Any, Mapping

from autogen_core._omega_dispatcher import DispatchContext, ForensicToolDispatcher

_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class ExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuditRecord:
    tenant_id: str
    case_id: str
    request_id: str
    tool_name: str
    payload_sha256: str
    previous_hash: str
    record_hash: str


class GuardedExecution:
    """Serializes dispatch, enforcing request budgets and fail-closed replay semantics."""

    def __init__(self, dispatcher: ForensicToolDispatcher, *, max_calls: int = 100, max_payload_bytes: int = 16384) -> None:
        if not isinstance(dispatcher, ForensicToolDispatcher):
            raise TypeError("invalid_dispatcher")
        if type(max_calls) is not int or not 1 <= max_calls <= 100000:
            raise ValueError("invalid_max_calls")
        if type(max_payload_bytes) is not int or not 64 <= max_payload_bytes <= 1048576:
            raise ValueError("invalid_payload_budget")
        self._dispatcher = dispatcher
        self._max_calls = max_calls
        self._max_payload_bytes = max_payload_bytes
        self._lock = threading.RLock()
        self._counts: dict[tuple[str, str], int] = {}
        self._replay: dict[tuple[str, str, str], tuple[str, object]] = {}
        self._audit: list[AuditRecord] = []

    def call(self, context: DispatchContext, tool_name: str, payload: Mapping[str, Any], *, request_id: str) -> object:
        if not isinstance(context, DispatchContext):
            raise TypeError("invalid_context")
        if not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None:
            raise ValueError("invalid_request_id")
        if not isinstance(payload, Mapping):
            raise ValueError("invalid_payload")
        try:
            serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("unserializable_payload") from exc
        if len(serialized.encode("ascii")) > self._max_payload_bytes:
            raise ExecutionError("payload_budget_exceeded")
        fingerprint = hashlib.sha256((tool_name + "\x00" + serialized).encode("utf-8")).hexdigest()
        key = (context.tenant_id, context.case_id, request_id)
        quota_key = (context.tenant_id, context.case_id)
        with self._lock:
            self._dispatcher.authorize(context, tool_name)
            if key in self._replay:
                old_fingerprint, old_result = self._replay[key]
                if old_fingerprint != fingerprint:
                    raise ExecutionError("idempotency_conflict")
                return old_result
            if self._counts.get(quota_key, 0) >= self._max_calls:
                raise ExecutionError("call_quota_exceeded")
            result = self._dispatcher.execute(context, tool_name, payload)
            previous = self._audit[-1].record_hash if self._audit else "0" * 64
            record_fields = (context.tenant_id, context.case_id, request_id, tool_name, fingerprint, previous)
            digest = hashlib.sha256(json.dumps(record_fields, separators=(",", ":")).encode("utf-8")).hexdigest()
            self._audit.append(AuditRecord(*record_fields, digest))
            self._counts[quota_key] = self._counts.get(quota_key, 0) + 1
            self._replay[key] = (fingerprint, result)
            return result

    def count(self, tenant_id: str, case_id: str) -> int:
        with self._lock:
            return self._counts.get((tenant_id, case_id), 0)

    def audit_records(self) -> tuple[AuditRecord, ...]:
        with self._lock:
            return tuple(self._audit)

    def verify_audit(self) -> bool:
        with self._lock:
            previous = "0" * 64
            for record in self._audit:
                fields = (record.tenant_id, record.case_id, record.request_id, record.tool_name, record.payload_sha256, previous)
                digest = hashlib.sha256(json.dumps(fields, separators=(",", ":")).encode("utf-8")).hexdigest()
                if record.previous_hash != previous or record.record_hash != digest:
                    return False
                previous = digest
            return True
