"""Optional durable DAG to cluster bridge, with stable job identity and read reconciliation.

A lost RPC response is an unknown outcome. Never infer rollback or retry a
non-idempotent remote operation. Producer credentials/tenant are trusted config.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from ._cluster_coordinator import ClusterDenied, JobSnapshot
from ._cluster_fabric import FabricTransportError
from ._durable_workflow import (
    AttemptContext, CheckpointIntegrityError, OutcomeUnknownError,
    RemoteReconciliation, current_attempt,
)
from ._governed_workflow import _canonical
from ._resource_scheduler import Resources


class ProducerBackend(Protocol):
    """Tenant-scoped, synchronous producer authority (local or mTLS client)."""
    def submit(self, tenant: str, job_id: str, handler: str, payload: Mapping[str, Any],
               demand: Resources, *, idempotent: bool, max_attempts: int) -> JobSnapshot: ...
    def get(self, tenant: str, job_id: str) -> JobSnapshot: ...


class RemoteStepFailed(RuntimeError):
    """A remote job reached a definite failed state."""


def _remote_id(ctx: AttemptContext, remote_handler: str) -> str:
    return 'dag-' + hashlib.sha256(
        _canonical(['dag-cluster-v1', ctx.idempotency_key, remote_handler])
    ).hexdigest()[:48]


def distributed_step_reconciler(
    backend: ProducerBackend, tenant: str, remote_handler: str,
) -> Callable[[AttemptContext], Awaitable[RemoteReconciliation | None]]:
    """Bind a trusted, **read-only** remote terminal outcome reader.

    No `submit`, retries, or worker actions are performed during reconciliation.
    Only the authenticated, matching tenant/job record is accepted. Unavailable
    or nonterminal remote states preserve uncertainty rather than invent a result.
    """
    if (type(tenant) is not str or not 0 < len(tenant) <= 128 or
            type(remote_handler) is not str or not 0 < len(remote_handler) <= 128 or
            not callable(getattr(backend, 'get', None))):
        raise ValueError('invalid remote reconciliation binding')

    async def read(ctx: AttemptContext) -> RemoteReconciliation | None:
        if not isinstance(ctx, AttemptContext) or ctx.tenant_id != tenant:
            raise CheckpointIntegrityError('remote reader tenant mismatch')
        job_id = _remote_id(ctx, remote_handler)
        try:
            state = await asyncio.to_thread(backend.get, tenant, job_id)
        except (FabricTransportError, ClusterDenied):
            return None
        if (not isinstance(state, JobSnapshot) or state.tenant != tenant
                or state.job_id != job_id):
            raise CheckpointIntegrityError('remote status not bound to requested job')
        if state.state in ('queued', 'leased', 'uncertain'):
            return None
        if state.state not in ('succeeded', 'failed'):
            raise CheckpointIntegrityError('invalid remote terminal state')
        if state.state == 'failed' and state.result is not None:
            raise CheckpointIntegrityError('failed remote job returned unexpected data')
        return RemoteReconciliation(tenant, ctx.idempotency_key,
                                    state.state, state.result if state.state == 'succeeded' else None)

    return read


def distributed_step_handler(backend: ProducerBackend, tenant: str, remote_handler: str, demand: Resources,
                             *, idempotent: bool = False, max_attempts: int = 1,
                             poll_interval: float = .1, poll_timeout: float = 30.0) -> Callable[[Mapping[str, Any]], Awaitable[Any]]:
    """Bind a fixed remote capability to a durable workflow step.

    The durable step's tenant must match the trusted producer tenant. Its
    operation identity remains stable across worker retries and restarts.
    `poll_timeout` bounds the uncertainty window, not remote execution.
    Enforce `WorkflowStep.timeout > poll_timeout` in the calling application.
    """
    if (not isinstance(tenant, str) or not 0 < len(tenant) <= 128 or
            not isinstance(remote_handler, str) or not 0 < len(remote_handler) <= 128 or
            not isinstance(demand, Resources) or type(idempotent) is not bool or
            type(max_attempts) is not int or not 1 <= max_attempts <= 32 or
            (max_attempts > 1 and not idempotent)):
        raise ValueError('invalid distributed handler policy')
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
           for v in (poll_interval, poll_timeout)) or poll_interval > poll_timeout:
        raise ValueError('invalid polling bounds')

    async def invoke(inputs: Mapping[str, Any]) -> Any:
        ctx = current_attempt()
        if ctx is None:
            raise RuntimeError('distributed handler requires a durable attempt')
        if ctx.tenant_id != tenant:
            raise ClusterDenied('workflow tenant differs from fixed producer identity')
        # Replays of a durable intent use the same remote ID; no attempt number.
        job_id = _remote_id(ctx, remote_handler)
        try:
            await asyncio.to_thread(backend.submit, tenant, job_id, remote_handler, inputs, demand,
                                    idempotent=idempotent, max_attempts=max_attempts)
        except FabricTransportError:
            # A committed submission may have lost its acknowledgement. Read
            # by stable identity; never submit a second distinct operation.
            pass
        except (ClusterDenied, ValueError):
            raise
        except Exception as exc:
            raise OutcomeUnknownError('remote submission may have committed') from exc
        loop = asyncio.get_running_loop()
        deadline = loop.time() + poll_timeout
        while True:
            try:
                state = await asyncio.to_thread(backend.get, tenant, job_id)
            except ClusterDenied as exc:
                # A lost submit response plus missing job remains uncertain.
                raise OutcomeUnknownError('remote job not confirmed') from exc
            except Exception as exc:
                # Reads are side-effect free. Network loss is not proof of
                # remote failure; continue until bounded uncertainty timeout.
                if loop.time() >= deadline:
                    raise OutcomeUnknownError('remote status unavailable') from exc
                await asyncio.sleep(min(poll_interval, max(0, deadline - loop.time())))
                continue
            if state.state == 'succeeded':
                return state.result
            if state.state == 'failed':
                raise RemoteStepFailed('remote step failed')
            if state.state == 'uncertain':
                raise OutcomeUnknownError('remote execution outcome uncertain')
            if state.state not in ('queued', 'leased'):
                raise OutcomeUnknownError('invalid remote job state')
            if loop.time() >= deadline:
                raise OutcomeUnknownError('remote execution not finished within polling deadline')
            await asyncio.sleep(min(poll_interval, max(0, deadline - loop.time())))

    return invoke
