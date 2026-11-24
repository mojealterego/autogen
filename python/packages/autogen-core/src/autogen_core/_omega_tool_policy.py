"""Fail-closed authorization boundary for Magnetic Zero forensic agent tools.

Only bounded offline analysis is executable. Approval flags cannot enable
active intrusion, credential manipulation, or radio/network interference.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_READ_ONLY = frozenset({"imei_structure_check", "exif_metadata_check", "signal_event_correlation"})
_BLOCKED = frozenset({"bluetooth_exploit", "fbe_bypass", "evil_twin", "arp_spoof", "credential_reset"})


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class ToolRequest:
    tenant_id: str
    tool_name: str
    case_id: str
    approved: bool = False

    def __post_init__(self) -> None:
        for field in (self.tenant_id, self.tool_name, self.case_id):
            if not isinstance(field, str) or _ID.fullmatch(field) is None:
                raise ValueError("invalid_request_identifier")
        if type(self.approved) is not bool:
            raise ValueError("invalid_approval")


@dataclass(frozen=True)
class PolicyResult:
    decision: Decision
    reason: str


class ToolPolicy:
    """Pure authorization decision; no tool dispatch or side effects."""

    def evaluate(self, request: ToolRequest, scopes: Mapping[str, frozenset[str]]) -> PolicyResult:
        if not isinstance(request, ToolRequest):
            raise TypeError("invalid_request")
        if not isinstance(scopes, Mapping):
            raise TypeError("invalid_scopes")
        if request.tool_name in _BLOCKED or request.tool_name not in _READ_ONLY:
            return PolicyResult(Decision.DENY, "tool_not_permitted")
        # Scopes are keyed by case, and the caller must supply only scopes
        # authenticated for request.tenant_id. No cross-tenant scope fallback.
        permitted = scopes.get(f"{request.tenant_id}:{request.case_id}")
        if not isinstance(permitted, frozenset) or request.tool_name not in permitted:
            return PolicyResult(Decision.DENY, "scope_missing")
        return PolicyResult(Decision.ALLOW, "read_only_scope_authorized")
