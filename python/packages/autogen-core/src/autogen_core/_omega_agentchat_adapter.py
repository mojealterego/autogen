"""Optional AutoGen AgentChat tool adapter for bounded offline checks.

Import autogen_core.tools lazily to keep the forensic domain dependency-free.
Each agent gets a fixed, trusted tenant/case context supplied by its host.
"""
from __future__ import annotations
from typing import Any
from autogen_core._omega_dispatcher import DispatchContext
from autogen_core._omega_execution_guard import GuardedExecution

def build_forensic_tools(guard: GuardedExecution, context: DispatchContext) -> list[Any]:
    if not isinstance(guard, GuardedExecution) or not isinstance(context, DispatchContext):
        raise TypeError("invalid_tool_adapter")
    from autogen_core.tools import FunctionTool

    async def check_imei(imei: str, request_id: str) -> str:
        result = guard.call(context, "imei_structure_check", {"imei": imei}, request_id=request_id)
        return f"valid={result.valid};tac={result.tac};reason={result.reason};digest={result.digest}"

    async def check_camera_serial(expected_serial: str, observed_serial: str, request_id: str) -> str:
        result = guard.call(context, "exif_metadata_check",
                            {"expected_serial": expected_serial, "metadata": {"SerialNumber": observed_serial}},
                            request_id=request_id)
        return f"status={result.status};reason={result.reason};digest={result.digest}"

    return [
        FunctionTool(check_imei, name="imei_structure_check",
                     description="Offline IMEI syntax and checksum validation; no registry or device lookup."),
        FunctionTool(check_camera_serial, name="exif_metadata_check",
                     description="Compare supplied camera serial evidence; no live stolen-camera registry lookup."),
    ]
