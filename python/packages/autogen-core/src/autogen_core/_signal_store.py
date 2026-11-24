"""Durable, tenant-scoped SQLite event journal for authorized OMEGA-X observations."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Mapping

from autogen_core._signal_intelligence import SignalEvent
from autogen_core._signal_ingest import SourcePolicy


class SignalJournal:
    def __init__(self, path: str | Path, policies: Mapping[str, SourcePolicy]) -> None:
        if not policies or any(not isinstance(k, str) or not isinstance(v, SourcePolicy) for k, v in policies.items()):
            raise ValueError('invalid_policies')
        self._policies = dict(policies)
        self._tenants = frozenset(p.tenant_id for p in policies.values())
        self._db = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
        self._db.execute('PRAGMA busy_timeout=5000')
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('CREATE TABLE IF NOT EXISTS events (tenant TEXT NOT NULL, event_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(tenant,event_id))')

    def __enter__(self) -> 'SignalJournal':
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._db.close()

    def append(self, event: SignalEvent) -> str:
        if not isinstance(event, SignalEvent):
            raise ValueError('invalid_event')
        policy = self._policies.get(event.source_id)
        if policy is None or policy.tenant_id != event.tenant_id or event.domain not in policy.allowed_domains:
            raise PermissionError('source_not_authorized')
        payload = json.dumps((event.tenant_id, event.source_id, event.domain, event.timestamp_ms, event.kind, event.severity), separators=(',', ':'), ensure_ascii=True)
        event_id = hashlib.sha256(payload.encode('ascii')).hexdigest()
        self._db.execute('INSERT OR IGNORE INTO events(tenant,event_id,payload) VALUES(?,?,?)', (event.tenant_id,event_id,payload))
        return event_id

    def read(self, tenant_id: str, *, limit: int = 1024) -> tuple[SignalEvent, ...]:
        if not isinstance(tenant_id, str) or tenant_id not in self._tenants:
            raise PermissionError('unknown_tenant')
        if type(limit) is not int or not 1 <= limit <= 1024:
            raise ValueError('invalid_limit')
        rows = self._db.execute('SELECT payload FROM events WHERE tenant=? ORDER BY event_id LIMIT ?', (tenant_id,limit)).fetchall()
        return tuple(SignalEvent(*json.loads(payload)) for (payload,) in rows)

    def count(self, tenant_id: str) -> int:
        if not isinstance(tenant_id, str) or tenant_id not in self._tenants:
            raise PermissionError('unknown_tenant')
        return int(self._db.execute('SELECT COUNT(*) FROM events WHERE tenant=?', (tenant_id,)).fetchone()[0])
