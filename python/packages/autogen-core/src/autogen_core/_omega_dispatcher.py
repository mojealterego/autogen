"""Authorized offline tool dispatch for Magnetic Zero.

This dispatcher is deliberately separate from the AutoGen transport.
Only typed, read-only tools are registered; policy is checked on every call.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from autogen_core._forensic_evidence import inspect_camera_metadata, inspect_imei
from autogen_core._omega_tool_policy import Decision, ToolPolicy, ToolRequest
from autogen_core._signal_intelligence import SignalEvent, correlate_events


@dataclass(frozen=True)
class DispatchContext:
    tenant_id: str
    case_id: str


class ForensicToolDispatcher:
    def __init__(self, grants: Mapping[str, frozenset[str]]) -> None:
        if not isinstance(grants, Mapping):
            raise TypeError("invalid_grants")
        self._grants = dict(grants)
        self._policy = ToolPolicy()

    def authorize(self, context: DispatchContext, tool_name: str) -> None:
        """Re-evaluate grants on every request, including idempotent retries."""
        if not isinstance(context, DispatchContext):
            raise TypeError("invalid_context")
        request = ToolRequest(context.tenant_id, tool_name, context.case_id)
        if self._policy.evaluate(request, self._grants).decision != Decision.ALLOW:
            raise PermissionError("tool_not_authorized")

    def execute(self, context: DispatchContext, tool_name: str, payload: Mapping[str, Any]) -> object:
        self.authorize(context, tool_name)
        if not isinstance(payload, Mapping):
            raise ValueError("invalid_payload")
        if tool_name == "imei_structure_check":
            if set(payload) != {"imei"} or not isinstance(payload["imei"], str):
                raise ValueError("invalid_payload")
            return inspect_imei(payload["imei"])
        if tool_name == "exif_metadata_check":
            if set(payload) != {"expected_serial", "metadata"}:
                raise ValueError("invalid_payload")
            return inspect_camera_metadata(payload["expected_serial"], payload["metadata"])
        if tool_name == "signal_event_correlation":
            if set(payload) != {"events", "window_ms"}:
                raise ValueError("invalid_payload")
            raw = payload["events"]
            if not isinstance(raw, tuple) or any(not isinstance(item, SignalEvent) for item in raw):
                raise ValueError("invalid_payload")
            return correlate_events(raw, tenant_id=context.tenant_id, window_ms=payload["window_ms"])
        raise PermissionError("tool_not_authorized")
