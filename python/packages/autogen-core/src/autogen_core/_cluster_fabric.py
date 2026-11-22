"""Bounded, mutually authenticated RPC for a single ClusterCoordinator authority.

Each TCP connection carries one length-prefixed JSON request and response. The
certificate's DER SHA-256 pin binds a principal to a fixed producer or worker.
No automatic network retries: a disconnected write can already be committed.
"""
from __future__ import annotations

import hashlib
import json
import math
import socket
import socketserver
import ssl
import struct
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal

from ._cluster_coordinator import (
    ClusterConflict, ClusterCoordinator, ClusterDenied, JobFence, JobLease, JobSnapshot,
    LeaseLost, WorkerSession,
)
from ._resource_scheduler import Resources
from ._authority_failover import LeadershipLost


class FabricTransportError(RuntimeError):
    """RPC failed; the outcome of a submitted mutation may be unknown."""


@dataclass(frozen=True)
class FabricPrincipal:
    role: Literal['producer', 'worker']
    identity: str

    def __post_init__(self) -> None:
        if (self.role not in ('producer', 'worker') or not isinstance(self.identity, str)
                or not self.identity or len(self.identity) > 128
                or any(ord(c) < 32 for c in self.identity)):
            raise ValueError('invalid fabric principal')


def _limits(timeout: float, max_frame: int) -> None:
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300
            or type(max_frame) is not int or not 1024 <= max_frame <= 64 * 1024**2):
        raise ValueError('invalid transport limits')


def _secure(context: ssl.SSLContext, *, server: bool) -> None:
    if (not isinstance(context, ssl.SSLContext) or context.verify_mode != ssl.CERT_REQUIRED
            or context.minimum_version < ssl.TLSVersion.TLSv1_2
            or (not server and not context.check_hostname)
            or context.protocol != (ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)):
        raise ValueError('TLS 1.2+, certificate validation and client hostname verification required')


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for k, v in pairs:
        if k in obj:
            raise ValueError('duplicate JSON key')
        obj[k] = v
    return obj


def _invalid_constant(value: str) -> Any:
    raise ValueError('nonfinite JSON constant')


def _remaining(sock: socket.socket, deadline: float) -> None:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError('transport deadline exceeded')
    sock.settimeout(seconds)


def _read_exact(sock: socket.socket, size: int, deadline: float) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        _remaining(sock, deadline)
        piece = sock.recv(min(size - len(buf), 65536))
        if not piece:
            raise EOFError('incomplete frame')
        buf.extend(piece)
    return bytes(buf)


def _receive(sock: socket.socket, limit: int, deadline: float) -> dict[str, Any]:
    size = struct.unpack('!I', _read_exact(sock, 4, deadline))[0]
    if not 0 < size <= limit:
        raise ValueError('frame size exceeded')
    value = json.loads(_read_exact(sock, size, deadline).decode('utf-8'),
                       object_pairs_hook=_unique, parse_constant=_invalid_constant)
    if not isinstance(value, dict):
        raise ValueError('object frame required')
    return value


def _send(sock: socket.socket, value: dict[str, Any], limit: int, deadline: float) -> None:
    data = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode('ascii')
    if not 0 < len(data) <= limit:
        raise ValueError('frame size exceeded')
    _remaining(sock, deadline)
    sock.sendall(struct.pack('!I', len(data)) + data)


def _exact(value: Any, *keys: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError('invalid RPC fields')
    return value


def _lease(value: Any) -> JobLease:
    d = _exact(value, 'tenant', 'job_id', 'handler', 'payload', 'demand', 'fence', 'idempotency_key', 'expires_ms')
    if (not all(isinstance(d[k], str) for k in ('tenant','job_id','handler','idempotency_key'))
            or not isinstance(d['payload'], dict) or type(d['fence']) is not int or d['fence'] < 1
            or type(d['expires_ms']) is not int):
        raise ValueError('invalid lease')
    return JobLease(**{**d, 'demand': Resources(**_exact(d['demand'], 'slots', 'memory_bytes', 'cpu_millis'))})


_ERRORS = {c.__name__: c for c in (ClusterDenied, ClusterConflict, LeaseLost, LeadershipLost, ValueError)}


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server: FabricServer = self.server  # type: ignore[assignment]
        server._handle(self.request)


class FabricServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Bounded threaded TCP server; run serve_forever in a dedicated thread.

    Configure immutable SSLContext and certificate pins before startup. Only
    the coordinator host needs SQLite access. Call shutdown from another
    thread, then server_close (or use the context manager).
    """
    allow_reuse_address = True
    daemon_threads = False
    block_on_close = True
    request_queue_size = 64

    def __init__(self, address: tuple[str, int], coordinator: ClusterCoordinator,
                 tls: ssl.SSLContext, principals: Mapping[str, FabricPrincipal], *,
                 timeout: float = 10, max_frame: int = 1024**2, max_connections: int = 32) -> None:
        _secure(tls, server=True)
        _limits(timeout, max_frame)
        if (type(max_connections) is not int or not 1 <= max_connections <= 1024
                or not principals or any(not isinstance(k, str) or len(k) != 64
                or any(ch not in '0123456789abcdef' for ch in k) or not isinstance(v, FabricPrincipal)
                for k,v in principals.items())):
            raise ValueError('invalid connection or principal configuration')
        self._tls = tls
        self._coordinator = coordinator
        self._principals = dict(principals)
        self._timeout = timeout
        self._max_frame = max_frame
        self._slots = threading.BoundedSemaphore(max_connections)
        super().__init__(address, _Handler)

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def _dispatch(self, principal: FabricPrincipal, request: dict[str, Any]) -> Any:
        req = _exact(request, 'v', 'op', 'args')
        if type(req['v']) is not int or req['v'] != 1 or not isinstance(req['op'], str):
            raise ValueError('unsupported protocol')
        op, args = req['op'], req['args']
        c = self._coordinator
        if principal.role == 'producer':
            if op == 'get':
                a = _exact(args, 'tenant', 'job_id')
                if a['tenant'] != principal.identity: raise ClusterDenied('identity denied')
                return asdict(c.get(principal.identity, a['job_id']))
            if op == 'submit':
                a = _exact(args, 'tenant', 'job_id', 'handler', 'payload', 'demand', 'idempotent', 'max_attempts')
                if a['tenant'] != principal.identity: raise ClusterDenied('identity denied')
                demand = Resources(**_exact(a['demand'], 'slots', 'memory_bytes', 'cpu_millis'))
                return asdict(c.submit(principal.identity, a['job_id'], a['handler'], a['payload'], demand,
                                       idempotent=a['idempotent'], max_attempts=a['max_attempts']))
        else:
            if op == 'register':
                a = _exact(args, 'worker_id')
                if a['worker_id'] != principal.identity: raise ClusterDenied('identity denied')
                return asdict(c.register(principal.identity))
            extra = {'heartbeat': (), 'claim': (), 'renew': ('lease',), 'complete': ('lease','result'),
                     'fail': ('lease','retry'), 'abandon': ('lease',)}
            if op in extra:
                a = _exact(args, 'session', *extra[op])
                session = WorkerSession(**_exact(a['session'], 'worker_id', 'token'))
                if session.worker_id != principal.identity: raise ClusterDenied('identity denied')
                if op == 'heartbeat': return c.heartbeat(session)
                if op == 'claim':
                    lease = c.claim(session)
                    return None if lease is None else asdict(lease)
                lease = JobFence(**_exact(a['lease'], 'tenant', 'job_id', 'fence'))
                if op == 'renew': return c.renew(session, lease)
                if op == 'complete': return asdict(c.complete(session, lease, a['result']))
                if op == 'fail': return asdict(c.fail(session, lease, retry=a['retry']))
                if op == 'abandon': return asdict(c.abandon(session, lease))
        raise ClusterDenied('operation denied')

    def _handle(self, raw: socket.socket) -> None:
        deadline = time.monotonic() + self._timeout
        try:
            _secure(self._tls, server=True)
            _remaining(raw, deadline)
            with self._tls.wrap_socket(raw, server_side=True) as sock:
                der = sock.getpeercert(binary_form=True)
                principal = self._principals.get(hashlib.sha256(der).hexdigest()) if der else None
                if principal is None:
                    return
                request = _receive(sock, self._max_frame, deadline)
                try:
                    value = self._dispatch(principal, request)
                    response = {'ok': True, 'value': value}
                except (ClusterDenied, ClusterConflict, LeaseLost, LeadershipLost, ValueError) as exc:
                    # No exception text, payloads, paths or tokens on the wire.
                    response = {'ok': False, 'error': type(exc).__name__}
                except Exception:
                    response = {'ok': False, 'error': 'InternalError'}
                _send(sock, response, self._max_frame, deadline)
        except (OSError, EOFError, ValueError, RecursionError):
            # Malformed, oversized, timed-out and untrusted connections close.
            return


class FabricClient:
    """Synchronous coordinator client; compatible with ClusterWorker's API.

    Each call uses a fresh TLS connection. A transport error never implies
    rollback. Reconcile producer operations using stable job IDs and get().
    """
    def __init__(self, address: tuple[str, int], tls: ssl.SSLContext, server_hostname: str, *,
                 lease_ms: int = 30000, timeout: float = 10, max_frame: int = 1024**2) -> None:
        _secure(tls, server=False)
        _limits(timeout, max_frame)
        if (not isinstance(server_hostname,str) or not server_hostname or type(lease_ms) is not int
                or not 10 <= lease_ms <= 86_400_000):
            raise ValueError('invalid client configuration')
        self._address, self._tls, self._hostname = address, tls, server_hostname
        self._timeout, self._max_frame, self._lease_ms = timeout, max_frame, lease_ms

    @property
    def lease_ms(self) -> int:
        return self._lease_ms

    def _call(self, op: str, **args: Any) -> Any:
        deadline = time.monotonic() + self._timeout
        try:
            _secure(self._tls, server=False)
            with socket.create_connection(self._address, timeout=self._timeout) as raw:
                _remaining(raw, deadline)
                with self._tls.wrap_socket(raw, server_hostname=self._hostname) as sock:
                    _send(sock, {'v':1, 'op':op, 'args':args}, self._max_frame, deadline)
                    response = _receive(sock, self._max_frame, deadline)
            if response.get('ok') is True:
                return _exact(response, 'ok', 'value')['value']
            error = _exact(response, 'ok', 'error')['error']
            if response['ok'] is not False or not isinstance(error,str):
                raise ValueError('invalid response')
        except (OSError, EOFError, ValueError, RecursionError) as exc:
            raise FabricTransportError('RPC unavailable; reconcile any mutation') from exc
        if error in _ERRORS:
            raise _ERRORS[error]('remote operation rejected')
        raise FabricTransportError('RPC failed; reconcile any mutation')

    def submit(self, tenant: str, job_id: str, handler: str, payload: Mapping[str, Any], demand: Resources,
               *, idempotent: bool = False, max_attempts: int = 1) -> JobSnapshot:
        return JobSnapshot(**self._call('submit', tenant=tenant, job_id=job_id, handler=handler,
            payload=dict(payload), demand=asdict(demand), idempotent=idempotent, max_attempts=max_attempts))

    def get(self, tenant: str, job_id: str) -> JobSnapshot:
        return JobSnapshot(**self._call('get', tenant=tenant, job_id=job_id))

    def register(self, worker_id: str) -> WorkerSession:
        return WorkerSession(**self._call('register', worker_id=worker_id))

    def heartbeat(self, session: WorkerSession) -> int:
        return self._call('heartbeat', session=asdict(session))

    def claim(self, session: WorkerSession) -> JobLease | None:
        value = self._call('claim', session=asdict(session))
        return None if value is None else _lease(value)

    def renew(self, session: WorkerSession, lease: JobLease | JobFence) -> int:
        return self._call('renew', session=asdict(session), lease=asdict(JobFence(lease.tenant, lease.job_id, lease.fence)))

    def complete(self, session: WorkerSession, lease: JobLease | JobFence, result: Any) -> JobSnapshot:
        return JobSnapshot(**self._call('complete', session=asdict(session), lease=asdict(JobFence(lease.tenant, lease.job_id, lease.fence)), result=result))

    def fail(self, session: WorkerSession, lease: JobLease | JobFence, *, retry: bool = False) -> JobSnapshot:
        return JobSnapshot(**self._call('fail', session=asdict(session), lease=asdict(JobFence(lease.tenant, lease.job_id, lease.fence)), retry=retry))

    def abandon(self, session: WorkerSession, lease: JobLease | JobFence) -> JobSnapshot:
        return JobSnapshot(**self._call('abandon', session=asdict(session), lease=asdict(JobFence(lease.tenant, lease.job_id, lease.fence))))
