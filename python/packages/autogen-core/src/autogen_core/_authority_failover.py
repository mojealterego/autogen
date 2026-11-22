"""Single-writer, single-host SQLite authority epochs and fail-closed handoff.

This is NOT quorum consensus or replicated multi-host HA. The authority
witness and coordinator must share one trusted local SQLite database.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path


class AuthorityBusy(RuntimeError):
    """Another authority owns a live leadership lease."""


class LeadershipLost(RuntimeError):
    """This authority does not hold a current fencing epoch."""


@dataclass(frozen=True)
class LeadershipLease:
    owner: str
    epoch: int
    token: str = field(repr=False)


@dataclass(frozen=True)
class AuthorityGuard:
    """Binds a coordinator to a lease, verified inside each write transaction."""
    store: 'AuthorityLeaseStore' = field(repr=False)
    lease: LeadershipLease = field(repr=False)

    @property
    def path(self) -> Path:
        return self.store.path

    def check_db(self, db: sqlite3.Connection) -> None:
        now = self.store._advance(db)
        self.store._verify(db, self.lease, now)


class AuthorityLeaseStore:
    """Transactional local witness; all gateway/producer mutations need guard.

    An epoch changes on each new acquisition. On takeover, existing worker
    sessions are invalidated and leased jobs become uncertain unless explicitly
    safe to retry. The witness must never be replicated by file copying while
    a live old authority can still write. Protect database files with OS ACLs.
    """

    def __init__(self, path: str | Path, *, lease_ms: int = 30_000,
                 clock_ms: Callable[[], int] | None = None) -> None:
        if type(lease_ms) is not int or not 10 <= lease_ms <= 86_400_000:
            raise ValueError('invalid leadership lease duration')
        self.path = Path(path).resolve()
        self.lease_ms = lease_ms
        self._clock = clock_ms or (lambda: time.time_ns() // 1_000_000)
        with self._connection() as db:
            mode = db.execute('PRAGMA journal_mode=WAL').fetchone()[0]
            if mode.lower() != 'wal':
                raise ValueError('WAL local database required')
            db.execute('''CREATE TABLE IF NOT EXISTS omega_authority (
                id INTEGER PRIMARY KEY CHECK(id=1), epoch INTEGER NOT NULL,
                owner TEXT, token_hash TEXT, expires_ms INTEGER NOT NULL,
                clock_high_ms INTEGER NOT NULL, duration_ms INTEGER NOT NULL)''')
            db.execute('INSERT OR IGNORE INTO omega_authority VALUES (1,0,NULL,NULL,0,0,?)', (lease_ms,))
            row = db.execute('SELECT duration_ms FROM omega_authority WHERE id=1').fetchone()
            if row is None or row[0] != lease_ms:
                raise ValueError('witness configuration changed')

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA synchronous=FULL')
            yield db
        finally:
            db.close()

    def _advance(self, db: sqlite3.Connection) -> int:
        tick = self._clock()
        if type(tick) is not int or not 0 <= tick < 2**62:
            raise ValueError('invalid authority clock')
        row = db.execute('SELECT clock_high_ms FROM omega_authority WHERE id=1').fetchone()
        if row is None:
            raise LeadershipLost('authority witness missing')
        now = max(tick, row[0])
        db.execute('UPDATE omega_authority SET clock_high_ms=? WHERE id=1', (now,))
        return now

    @contextmanager
    def _transaction(self) -> Iterator[tuple[sqlite3.Connection, int]]:
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                now = self._advance(db)
                yield db, now
            except (LeadershipLost, AuthorityBusy):
                db.commit()  # preserve the observed high-water timestamp
                raise
            except BaseException:
                db.rollback()
                raise
            else:
                db.commit()

    @staticmethod
    def _verify(db: sqlite3.Connection, lease: LeadershipLease, now: int) -> None:
        if not isinstance(lease, LeadershipLease) or not isinstance(lease.owner, str):
            raise LeadershipLost('invalid lease')
        row = db.execute('SELECT epoch,owner,token_hash,expires_ms FROM omega_authority WHERE id=1').fetchone()
        if (row is None or row['epoch'] != lease.epoch or row['owner'] != lease.owner or
                row['expires_ms'] <= now or not isinstance(lease.token, str) or
                not secrets.compare_digest(row['token_hash'] or '', hashlib.sha256(lease.token.encode()).hexdigest())):
            raise LeadershipLost('stale or expired authority lease')

    def acquire(self, owner: str) -> LeadershipLease:
        if not isinstance(owner, str) or not 0 < len(owner) <= 128 or any(ord(c) < 32 for c in owner):
            raise ValueError('invalid authority identity')
        with self._transaction() as (db, now):
            row = db.execute('SELECT epoch,owner,expires_ms FROM omega_authority WHERE id=1').fetchone()
            if row['owner'] is not None and row['expires_ms'] > now:
                raise AuthorityBusy('another authority is alive')
            epoch = row['epoch'] + 1
            if epoch >= 2**63:
                raise LeadershipLost('authority epoch exhausted')
            token = secrets.token_urlsafe(32)
            db.execute('UPDATE omega_authority SET epoch=?,owner=?,token_hash=?,expires_ms=? WHERE id=1',
                       (epoch, owner, hashlib.sha256(token.encode()).hexdigest(), now + self.lease_ms))
            # A promoted authority must never accept old worker sessions or
            # leaked in-flight commits. These transitions occur in the same WAL
            # transaction as the new leadership epoch.
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='omega_cluster_workers'").fetchone()
            if exists:
                db.execute('DELETE FROM omega_cluster_workers')
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='omega_cluster_jobs'").fetchone()
            if exists:
                db.execute('''UPDATE omega_cluster_jobs SET
                    state=CASE WHEN idempotent=1 AND attempts<max_attempts THEN 'queued' ELSE 'uncertain' END,
                    worker_id=NULL,token_hash=NULL,expires_ms=NULL WHERE state='leased' ''')
            return LeadershipLease(owner, epoch, token)

    def guard(self, lease: LeadershipLease) -> AuthorityGuard:
        return AuthorityGuard(self, lease)

    def assert_active(self, lease: LeadershipLease) -> None:
        with self._transaction() as (db, now):
            self._verify(db, lease, now)

    def renew(self, lease: LeadershipLease) -> int:
        with self._transaction() as (db, now):
            self._verify(db, lease, now)
            expires = now + self.lease_ms
            db.execute('UPDATE omega_authority SET expires_ms=? WHERE id=1', (expires,))
            return expires

    def release(self, lease: LeadershipLease) -> None:
        with self._transaction() as (db, now):
            self._verify(db, lease, now)
            db.execute('UPDATE omega_authority SET owner=NULL,token_hash=NULL,expires_ms=? WHERE id=1', (now,))
