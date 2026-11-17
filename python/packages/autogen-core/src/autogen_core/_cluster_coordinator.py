"""Transactional single-host, multi-process job coordination using SQLite.

The database is a trusted local authority, not a network protocol or consensus
system. All producers and workers require trusted OS-level access.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import secrets
import sqlite3
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ._resource_scheduler import Resources


class ClusterDenied(RuntimeError):
    """An admission or capability rule rejected an operation."""


class ClusterConflict(RuntimeError):
    """A durable identity or configuration conflicts with existing state."""


class LeaseLost(RuntimeError):
    """The session or fencing token no longer authorizes an operation."""


def _name(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 128 or any(ord(c) < 32 for c in value):
        raise ValueError('invalid identifier')


def _names(values: frozenset[str]) -> None:
    if not isinstance(values, frozenset) or not values:
        raise ValueError('nonempty immutable capability set required')
    for value in values:
        _name(value)


def _fits(used: tuple[int, ...], demand: tuple[int, ...], limit: Resources) -> bool:
    return all(a + b <= c for a, b, c in zip(used, demand, limit.vector))


def _resources(value: Resources) -> None:
    if not isinstance(value, Resources) or any(n > 2**50 for n in value.vector):
        raise ValueError('invalid resource vector')


def _canonical(value: Any, limit: int) -> str:
    try:
        text = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError('invalid JSON value') from exc
    if len(text) > limit:
        raise ValueError('JSON size limit exceeded')
    return text


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class TenantSpec:
    quota: Resources
    handlers: frozenset[str]

    def __post_init__(self) -> None:
        _resources(self.quota)
        _names(self.handlers)


@dataclass(frozen=True)
class WorkerSpec:
    capacity: Resources
    tenants: frozenset[str]
    handlers: frozenset[str]

    def __post_init__(self) -> None:
        _resources(self.capacity)
        _names(self.tenants)
        _names(self.handlers)


@dataclass(frozen=True)
class WorkerSession:
    worker_id: str
    token: str = field(repr=False)


@dataclass(frozen=True)
class JobFence:
    """Minimal reference to a current lease; authority remains in the database."""
    tenant: str
    job_id: str
    fence: int

    def __post_init__(self) -> None:
        _name(self.tenant)
        _name(self.job_id)
        if type(self.fence) is not int or not 1 <= self.fence < 2**63:
            raise ValueError('invalid fence')


@dataclass(frozen=True)
class JobLease:
    tenant: str
    job_id: str
    handler: str
    payload: dict[str, Any]
    demand: Resources
    fence: int
    idempotency_key: str
    expires_ms: int


_current_lease: ContextVar[JobLease | None] = ContextVar('omega_cluster_lease', default=None)


def current_cluster_lease() -> JobLease | None:
    """Initial claim metadata inside the handler, not proof of a live lease."""
    return _current_lease.get()


@dataclass(frozen=True)
class JobSnapshot:
    tenant: str
    job_id: str
    state: str
    attempts: int
    fence: int
    result: Any


_SCHEMA = (
    'CREATE TABLE IF NOT EXISTS omega_cluster_config (id INTEGER PRIMARY KEY CHECK(id=1), fingerprint TEXT NOT NULL, clock_ms INTEGER NOT NULL)',
    'CREATE TABLE IF NOT EXISTS omega_cluster_workers (worker_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, expires_ms INTEGER NOT NULL)',
    '''CREATE TABLE IF NOT EXISTS omega_cluster_jobs (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT NOT NULL, job_id TEXT NOT NULL,
        handler TEXT NOT NULL, payload TEXT NOT NULL, fingerprint TEXT NOT NULL,
        slots INTEGER NOT NULL, memory_bytes INTEGER NOT NULL, cpu_millis INTEGER NOT NULL,
        idempotent INTEGER NOT NULL, max_attempts INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        fence INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'queued',
        worker_id TEXT, token_hash TEXT, expires_ms INTEGER, result TEXT,
        UNIQUE(tenant,job_id), CHECK(state IN ('queued','leased','succeeded','failed','uncertain'))
    )''',
    'CREATE INDEX IF NOT EXISTS omega_cluster_state_seq ON omega_cluster_jobs(state,seq)',
    'CREATE INDEX IF NOT EXISTS omega_cluster_tenant_state ON omega_cluster_jobs(tenant,state)',
    'CREATE INDEX IF NOT EXISTS omega_cluster_worker_state ON omega_cluster_jobs(worker_id,state)',
)


class ClusterCoordinator:
    """Bounded, durable job queue with atomic resource admission and fencing.

    Use independent instances on one local filesystem. Authentication, remote
    transport and infrastructure-level isolation are the application's concern.
    Calls are synchronous; use a thread when calling from an asyncio service.
    """

    def __init__(self, path: str | Path, capacity: Resources, tenants: Mapping[str, TenantSpec],
                 workers: Mapping[str, WorkerSpec], *, lease_ms: int = 30_000,
                 max_jobs: int = 10_000, max_pending_per_tenant: int = 1024,
                 max_payload_bytes: int = 65_536, busy_timeout_ms: int = 5000,
                 clock_ms: Callable[[], int] | None = None) -> None:
        _resources(capacity)
        for value, minimum, maximum in ((lease_ms, 10, 86_400_000), (max_jobs, 1, 1_000_000),
                                        (max_pending_per_tenant, 1, 1_000_000),
                                        (max_payload_bytes, 1, 16*1024**2), (busy_timeout_ms, 1, 60_000)):
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError('invalid coordinator limit')
        if not tenants or not workers or len(tenants) > 10_000 or len(workers) > 10_000:
            raise ValueError('invalid tenant or worker configuration')
        self._capacity = capacity
        self._tenants = dict(tenants)
        self._workers = dict(workers)
        for name, spec in self._tenants.items():
            _name(name)
            if not isinstance(spec, TenantSpec) or not _fits((0,0,0), spec.quota.vector, capacity):
                raise ValueError('invalid tenant spec')
        for name, worker in self._workers.items():
            _name(name)
            if (not isinstance(worker, WorkerSpec) or not worker.tenants <= self._tenants.keys()
                    or not _fits((0,0,0), worker.capacity.vector, capacity)):
                raise ValueError('invalid worker spec')
        self._path = Path(path).resolve()
        self._lease_ms = lease_ms
        self._max_jobs = max_jobs
        self._max_pending = max_pending_per_tenant
        self._max_payload = max_payload_bytes
        self._busy_ms = busy_timeout_ms
        self._clock = clock_ms or (lambda: time.time_ns() // 1_000_000)
        config = [1, capacity.vector, {k: [v.quota.vector, sorted(v.handlers)] for k,v in self._tenants.items()},
                  {k: [v.capacity.vector, sorted(v.tenants), sorted(v.handlers)] for k,v in self._workers.items()},
                  lease_ms, max_jobs, max_pending_per_tenant, max_payload_bytes]
        self._fingerprint = _digest(_canonical(config, 64*1024**2))
        # WAL is persistent per database; configure once before transactional use.
        with self._connection() as db:
            if db.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower() != 'wal':
                raise ClusterConflict('local WAL database required')
            db.execute('BEGIN IMMEDIATE')
            for ddl in _SCHEMA:
                db.execute(ddl)
            db.execute('INSERT OR IGNORE INTO omega_cluster_config VALUES (1,?,0)', (self._fingerprint,))
            self._check_config(db)
            db.commit()

    @property
    def lease_ms(self) -> int:
        return self._lease_ms

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self._path, timeout=self._busy_ms/1000, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA synchronous=FULL')
            yield db
        finally:
            db.close()

    def _check_config(self, db: sqlite3.Connection) -> sqlite3.Row:
        row = db.execute('SELECT * FROM omega_cluster_config WHERE id=1').fetchone()
        if row is None or row['fingerprint'] != self._fingerprint:
            raise ClusterConflict('coordinator configuration changed')
        return row

    @contextmanager
    def _transaction(self) -> Iterator[tuple[sqlite3.Connection, int]]:
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._check_config(db)
            tick = self._clock()
            if type(tick) is not int or not 0 <= tick <= 2**62:
                raise ValueError('invalid clock')
            now = max(tick, row['clock_ms'])
            db.execute('UPDATE omega_cluster_config SET clock_ms=? WHERE id=1', (now,))
            # Persist the observed time even if the caller's operation is
            # rejected. Otherwise a backward clock jump could revive a lease
            # that a previous rejected operation already observed expired.
            db.execute('SAVEPOINT omega_operation')
            try:
                yield db, now
            except Exception:
                db.execute('ROLLBACK TO omega_operation')
                db.execute('RELEASE omega_operation')
                self._reap(db, now)
                db.commit()
                raise
            else:
                db.execute('RELEASE omega_operation')
                db.commit()

    @staticmethod
    def _reap(db: sqlite3.Connection, now: int) -> int:
        # A lease is valid only while BOTH the worker session and the job lease survive.
        return db.execute('''UPDATE omega_cluster_jobs SET
            state=CASE WHEN idempotent=1 AND attempts<max_attempts THEN 'queued' ELSE 'uncertain' END,
            worker_id=NULL, token_hash=NULL, expires_ms=NULL
            WHERE state='leased' AND (expires_ms<=? OR NOT EXISTS
                (SELECT 1 FROM omega_cluster_workers w WHERE w.worker_id=omega_cluster_jobs.worker_id
                 AND w.token_hash=omega_cluster_jobs.token_hash AND w.expires_ms>?))''', (now,now)).rowcount

    def reap(self) -> int:
        with self._transaction() as (db,now):
            return self._reap(db,now)

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> JobSnapshot:
        return JobSnapshot(row['tenant'],row['job_id'],row['state'],row['attempts'],row['fence'],
                           json.loads(row['result']) if row['result'] is not None else None)

    @staticmethod
    def _job(db: sqlite3.Connection, tenant: str, job_id: str) -> sqlite3.Row:
        row = db.execute('SELECT * FROM omega_cluster_jobs WHERE tenant=? AND job_id=?',(tenant,job_id)).fetchone()
        if row is None:
            raise ClusterDenied('unknown job')
        return row

    def submit(self, tenant: str, job_id: str, handler: str, payload: Mapping[str, Any], demand: Resources,
               *, idempotent: bool = False, max_attempts: int = 1) -> JobSnapshot:
        for name in (tenant,job_id,handler):
            _name(name)
        _resources(demand)
        if (type(idempotent) is not bool or type(max_attempts) is not int or not 1 <= max_attempts <= 32
                or max_attempts > 1 and not idempotent):
            raise ValueError('retries require explicit idempotence')
        spec = self._tenants.get(tenant)
        if spec is None or handler not in spec.handlers or not _fits((0,0,0), demand.vector, spec.quota):
            raise ClusterDenied('tenant quota or handler denied')
        if not any(tenant in w.tenants and handler in w.handlers and _fits((0,0,0), demand.vector,w.capacity)
                   for w in self._workers.values()):
            raise ClusterDenied('no capable worker configured')
        if not isinstance(payload, Mapping) or not all(isinstance(k,str) for k in payload):
            raise ValueError('payload must be a JSON object')
        data = _canonical(dict(payload), self._max_payload)
        fingerprint = _digest(_canonical([handler,data,demand.vector,idempotent,max_attempts], self._max_payload*2+1024))
        with self._transaction() as (db,now):
            self._reap(db,now)
            row = db.execute('SELECT * FROM omega_cluster_jobs WHERE tenant=? AND job_id=?',(tenant,job_id)).fetchone()
            if row is not None:
                if row['fingerprint'] != fingerprint:
                    raise ClusterConflict('job identity reused with different specification')
                return self._snapshot(row)
            if db.execute('SELECT COUNT(*) FROM omega_cluster_jobs').fetchone()[0] >= self._max_jobs:
                raise ClusterDenied('job retention limit reached')
            if db.execute("SELECT COUNT(*) FROM omega_cluster_jobs WHERE tenant=? AND state IN ('queued','leased')",
                          (tenant,)).fetchone()[0] >= self._max_pending:
                raise ClusterDenied('tenant pending limit reached')
            db.execute('''INSERT INTO omega_cluster_jobs(tenant,job_id,handler,payload,fingerprint,slots,memory_bytes,
                       cpu_millis,idempotent,max_attempts) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                       (tenant,job_id,handler,data,fingerprint,*demand.vector,int(idempotent),max_attempts))
            return self._snapshot(self._job(db,tenant,job_id))

    def get(self, tenant: str, job_id: str) -> JobSnapshot:
        _name(tenant); _name(job_id)
        with self._transaction() as (db,now):
            self._reap(db, now)
            return self._snapshot(self._job(db,tenant,job_id))

    def register(self, worker_id: str) -> WorkerSession:
        _name(worker_id)
        if worker_id not in self._workers:
            raise ClusterDenied('unknown worker')
        session = WorkerSession(worker_id, secrets.token_urlsafe(32))
        with self._transaction() as (db,now):
            self._reap(db, now)
            row = db.execute('SELECT expires_ms FROM omega_cluster_workers WHERE worker_id=?',(worker_id,)).fetchone()
            if row is not None and row[0] > now:
                raise ClusterConflict('worker session already live')
            db.execute('''INSERT INTO omega_cluster_workers VALUES (?,?,?) ON CONFLICT(worker_id)
                       DO UPDATE SET token_hash=excluded.token_hash,expires_ms=excluded.expires_ms''',
                       (worker_id,_digest(session.token),now+self._lease_ms))
        return session

    def _session(self, db: sqlite3.Connection, now: int, session: WorkerSession) -> sqlite3.Row:
        if (not isinstance(session,WorkerSession) or not isinstance(session.worker_id,str)
                or session.worker_id not in self._workers or not isinstance(session.token,str)
                or not session.token.isascii() or len(session.token) != 43):
            raise LeaseLost('invalid worker session')
        row = db.execute('SELECT * FROM omega_cluster_workers WHERE worker_id=?',(session.worker_id,)).fetchone()
        if row is None or row['expires_ms'] <= now or not secrets.compare_digest(row['token_hash'],_digest(session.token)):
            raise LeaseLost('expired or replaced worker session')
        return row

    def heartbeat(self, session: WorkerSession) -> int:
        with self._transaction() as (db,now):
            self._reap(db,now)
            self._session(db,now,session)
            expires = now+self._lease_ms
            db.execute('UPDATE omega_cluster_workers SET expires_ms=? WHERE worker_id=?',(expires,session.worker_id))
            return expires

    @staticmethod
    def _used(db: sqlite3.Connection, column: str | None = None, key: str | None = None) -> tuple[int,int,int]:
        sql = "SELECT COALESCE(SUM(slots),0),COALESCE(SUM(memory_bytes),0),COALESCE(SUM(cpu_millis),0) FROM omega_cluster_jobs WHERE state='leased'"
        if column is None:
            row = db.execute(sql).fetchone()
        elif column == 'tenant':
            row = db.execute(sql+' AND tenant=?',(key,)).fetchone()
        elif column == 'worker_id':
            row = db.execute(sql+' AND worker_id=?',(key,)).fetchone()
        else:
            raise ValueError('invalid accounting dimension')
        return row[0],row[1],row[2]

    def claim(self, session: WorkerSession) -> JobLease | None:
        with self._transaction() as (db,now):
            self._reap(db,now)
            worker_row = self._session(db,now,session)
            worker = self._workers[session.worker_id]
            total = self._used(db)
            worker_used = self._used(db,'worker_id',session.worker_id)
            tenant_usage: dict[str,tuple[int,int,int]] = {}
            for row in db.execute("SELECT * FROM omega_cluster_jobs WHERE state='queued' ORDER BY seq"):
                tenant,handler = row['tenant'],row['handler']
                if tenant not in worker.tenants or handler not in worker.handlers:
                    continue
                demand = Resources(row['slots'],row['memory_bytes'],row['cpu_millis'])
                if not _fits(total,demand.vector,self._capacity) or not _fits(worker_used,demand.vector,worker.capacity):
                    continue
                if tenant not in tenant_usage:
                    tenant_usage[tenant] = self._used(db,'tenant',tenant)
                if not _fits(tenant_usage[tenant],demand.vector,self._tenants[tenant].quota):
                    continue
                expires = min(now+self._lease_ms,worker_row['expires_ms'])
                fence = row['fence']+1
                db.execute('''UPDATE omega_cluster_jobs SET state='leased', attempts=attempts+1, fence=?,
                           worker_id=?,token_hash=?,expires_ms=? WHERE seq=?''',
                           (fence,session.worker_id,worker_row['token_hash'],expires,row['seq']))
                key = _digest(_canonical([tenant,row['job_id'],row['fingerprint']],4096))
                return JobLease(tenant,row['job_id'],handler,json.loads(row['payload']),demand,fence,key,expires)
            return None

    def _lease(self, db: sqlite3.Connection, now: int, session: WorkerSession, lease: JobLease | JobFence) -> sqlite3.Row:
        self._session(db,now,session)
        JobFence(lease.tenant, lease.job_id, lease.fence)
        row = self._job(db,lease.tenant,lease.job_id)
        if (row['state'] != 'leased' or row['worker_id'] != session.worker_id or row['fence'] != lease.fence
                or row['token_hash'] != _digest(session.token) or row['expires_ms'] <= now):
            raise LeaseLost('stale job fence or expired lease')
        return row

    def renew(self, session: WorkerSession, lease: JobLease | JobFence) -> int:
        with self._transaction() as (db,now):
            self._reap(db,now)
            row = self._lease(db,now,session,lease)
            expires = now+self._lease_ms
            db.execute('UPDATE omega_cluster_workers SET expires_ms=? WHERE worker_id=?',(expires,session.worker_id))
            db.execute('UPDATE omega_cluster_jobs SET expires_ms=? WHERE seq=?',(expires,row['seq']))
            return expires

    def _finish(self, session: WorkerSession, lease: JobLease | JobFence, *, success: bool, data: str | None,
                retry: bool, uncertain: bool = False) -> JobSnapshot:
        with self._transaction() as (db,now):
            self._reap(db,now)
            row = self._lease(db,now,session,lease)
            state = 'succeeded' if success else ('queued' if retry and row['idempotent'] and row['attempts'] < row['max_attempts']
                                                else 'uncertain' if uncertain else 'failed')
            db.execute('''UPDATE omega_cluster_jobs SET state=?,result=?,worker_id=NULL,token_hash=NULL,
                       expires_ms=NULL WHERE seq=?''',(state,data,row['seq']))
            return self._snapshot(self._job(db,lease.tenant,lease.job_id))

    def complete(self, session: WorkerSession, lease: JobLease | JobFence, result: Any) -> JobSnapshot:
        return self._finish(session,lease,success=True,data=_canonical(result,self._max_payload),retry=False)

    def fail(self, session: WorkerSession, lease: JobLease | JobFence, *, retry: bool = False) -> JobSnapshot:
        if type(retry) is not bool:
            raise ValueError('invalid retry flag')
        return self._finish(session,lease,success=False,data=None,retry=retry)

    def abandon(self, session: WorkerSession, lease: JobLease | JobFence) -> JobSnapshot:
        """Unknown side-effect outcome; only declared idempotent jobs may retry."""
        return self._finish(session,lease,success=False,data=None,retry=True,uncertain=True)


async def _database_call(function: Callable[..., Any], *args: Any) -> Any:
    """Drain in-flight SQLite operations even on cancellation; never detach writes."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
        except Exception:
            if not interrupted:
                raise
    if interrupted:
        if not task.cancelled():
            task.exception()  # consume any concurrent failure
        raise asyncio.CancelledError
    return task.result()


class ClusterWorkerBackend(Protocol):
    """Synchronous local or remote authority used by ClusterWorker."""
    @property
    def lease_ms(self) -> int: ...
    def heartbeat(self, session: WorkerSession) -> int: ...
    def claim(self, session: WorkerSession) -> JobLease | None: ...
    def renew(self, session: WorkerSession, lease: JobLease | JobFence) -> int: ...
    def complete(self, session: WorkerSession, lease: JobLease | JobFence, result: Any) -> JobSnapshot: ...
    def fail(self, session: WorkerSession, lease: JobLease | JobFence, *, retry: bool = False) -> JobSnapshot: ...
    def abandon(self, session: WorkerSession, lease: JobLease | JobFence) -> JobSnapshot: ...


class ClusterWorker:
    """Sequential, cooperative async worker with periodic lease renewal.

    Handler functions are trusted and fixed by the application. Cancellation
    stops the handler before returning; an uncooperative handler needs an OS
    process supervisor. No side-effect exactly-once guarantee is implied.
    """

    def __init__(self, coordinator: ClusterWorkerBackend, session: WorkerSession,
                 handlers: Mapping[str, Callable[[Mapping[str,Any]], Awaitable[Any]]], *,
                 heartbeat_interval: float | None = None) -> None:
        interval = coordinator.lease_ms/3000 if heartbeat_interval is None else heartbeat_interval
        if (type(interval) not in (int,float) or not math.isfinite(interval)
                or not 0 < interval < coordinator.lease_ms/1000):
            raise ValueError('heartbeat interval must be shorter than lease')
        self._coordinator = coordinator
        self._session = session
        self._handlers = dict(handlers)
        self._interval = interval
        self._running = False

    async def run_once(self) -> JobSnapshot | None:
        if self._running:
            raise ClusterConflict('worker already running')
        self._running = True
        tasks: list[asyncio.Task[Any]] = []
        try:
            await _database_call(self._coordinator.heartbeat,self._session)
            lease = await _database_call(self._coordinator.claim,self._session)
            if lease is None:
                return None
            handler = self._handlers.get(lease.handler)
            if handler is None:
                return await _database_call(self._coordinator.fail,self._session,lease)
            async def pulse() -> None:
                while True:
                    await asyncio.sleep(self._interval)
                    await _database_call(self._coordinator.renew,self._session,lease)
            async def execute() -> Any:
                token = _current_lease.set(lease)
                try:
                    return await handler(lease.payload)
                finally:
                    _current_lease.reset(token)
            work = asyncio.create_task(execute())
            heartbeat = asyncio.create_task(pulse())
            tasks = [work,heartbeat]
            await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            if heartbeat.done():
                heartbeat.result()  # fail closed on renewal error
            try:
                value = work.result()
            except Exception:
                return await _database_call(self._coordinator.abandon,self._session,lease)
            try:
                return await _database_call(self._coordinator.complete,self._session,lease,value)
            except ValueError:
                # An unrepresentable result is not proof the handler had no effects.
                return await _database_call(self._coordinator.abandon,self._session,lease)
        finally:
            # Do not explicitly release on cancellation: unknown effects remain
            # leased until reconciliation. Await cooperative cleanup before reuse.
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                cleanup = asyncio.gather(*tasks,return_exceptions=True)
                interrupted = False
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        interrupted = True
                self._running = False
                if interrupted:
                    raise asyncio.CancelledError
            self._running = False
