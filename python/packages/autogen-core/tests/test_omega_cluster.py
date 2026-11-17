import asyncio
import multiprocessing as mp
import os
import time
from pathlib import Path

import pytest

from autogen_core import (
    ClusterCoordinator, ClusterConflict, ClusterDenied, ClusterWorker, LeaseLost,
    ProcessWorker, Resources, TenantSpec, WorkerLimits, WorkerSpec,
)

R = Resources


def setup(path, **kwargs):
    return ClusterCoordinator(path, R(2, 200, 200),
        {'a': TenantSpec(R(2, 200, 200), frozenset({'f'})),
         'b': TenantSpec(R(1, 100, 100), frozenset({'f'}))},
        {'w1': WorkerSpec(R(2, 200, 200), frozenset({'a', 'b'}), frozenset({'f'})),
         'w2': WorkerSpec(R(2, 200, 200), frozenset({'a'}), frozenset({'f'}))}, **kwargs)


def submit(c, job='j', tenant='a', **kw):
    return c.submit(tenant, job, 'f', {'x': 1}, R(1, 100, 100), **kw)


def test_idempotent_submit_and_conflicting_spec(tmp_path):
    c = setup(tmp_path/'db')
    assert submit(c) == submit(c)
    with pytest.raises(ClusterConflict):
        c.submit('a', 'j', 'f', {'x': 2}, R(1, 100, 100))
    assert submit(c, tenant='b').tenant == 'b'
    with pytest.raises(ClusterDenied): c.submit('a', 'x', 'unapproved', {}, R(1, 1, 1))
    with pytest.raises(ClusterDenied): submit(c, tenant='unknown')
    with pytest.raises(ValueError): submit(c, 'x', max_attempts=2)


def test_config_pinned_and_bounded(tmp_path):
    c = setup(tmp_path/'db', max_jobs=2, max_pending_per_tenant=1)
    submit(c)
    with pytest.raises(ClusterDenied): submit(c, 'j2')
    submit(c, tenant='b')
    with pytest.raises(ClusterConflict): setup(tmp_path/'db')
    with pytest.raises(ClusterDenied): c.register('unknown')
    with pytest.raises(ValueError): c.submit('a', 'bad', 'f', {'x': float('nan')}, R(1, 1, 1))


def test_quotas_worker_capability_and_result(tmp_path):
    c = setup(tmp_path/'db')
    w1, w2 = c.register('w1'), c.register('w2')
    submit(c, 'b1', 'b'); submit(c, 'b2', 'b'); submit(c, 'a1'); submit(c, 'a2')
    a = c.claim(w2); b = c.claim(w1)
    assert a.tenant == 'a' and b.tenant == 'b'
    assert c.claim(w1) is None and c.claim(w2) is None
    assert c.complete(w1, b, {'ok': True}).result == {'ok': True}
    assert c.claim(w2).job_id == 'a2'
    assert c.claim(w1) is None
    assert c.complete(w2, a, 42).state == 'succeeded'
    assert c.claim(w1).job_id == 'b2'


def test_worker_session_and_fencing(tmp_path):
    tick = [1000]
    c = setup(tmp_path/'db', lease_ms=100, clock_ms=lambda: tick[0])
    w = c.register('w1')
    with pytest.raises(ClusterConflict): c.register('w1')
    submit(c, idempotent=True, max_attempts=2)
    a = c.claim(w)
    tick[0] += 100
    w2 = c.register('w1')
    b = c.claim(w2)
    assert b.fence == a.fence + 1 and b.idempotency_key == a.idempotency_key
    with pytest.raises(LeaseLost): c.complete(w, a, 1)
    with pytest.raises(LeaseLost): c.complete(w2, a, 1)
    assert c.complete(w2, b, 2).result == 2
    with pytest.raises(LeaseLost): c.complete(w2, b, 3)


def test_expired_non_idempotent_and_exhausted(tmp_path):
    tick = [1000]
    c = setup(tmp_path/'db', lease_ms=100, clock_ms=lambda: tick[0])
    w = c.register('w1')
    submit(c, 'unsafe'); submit(c, 'once', idempotent=True)
    c.claim(w); c.claim(w)
    tick[0] += 100
    assert c.reap() == 2
    assert c.get('a', 'unsafe').state == 'uncertain'
    assert c.get('a', 'once').state == 'uncertain'
    assert c.claim(c.register('w1')) is None


def test_renewal_and_clock_rollback(tmp_path):
    tick = [1000]
    c = setup(tmp_path/'db', lease_ms=100, clock_ms=lambda: tick[0])
    w = c.register('w1'); submit(c); a = c.claim(w)
    tick[0] = 1070
    c.renew(w, a)
    tick[0] = 1150
    c.heartbeat(w)
    assert c.get('a', 'j').state == 'leased'
    tick[0] = 900
    c.renew(w, a)  # monotonic persisted high-water mark
    tick[0] = 1249
    assert c.get('a', 'j').state == 'leased'
    tick[0] = 1250
    assert c.get('a', 'j').state == 'uncertain'
    with pytest.raises(LeaseLost): c.renew(w, a)


def test_explicit_failure_retry_and_limits(tmp_path):
    c = setup(tmp_path/'db', max_payload_bytes=50)
    w = c.register('w1'); submit(c, idempotent=True, max_attempts=2)
    a = c.claim(w)
    with pytest.raises(ValueError): c.complete(w, a, 'x'*100)
    assert c.fail(w, a, retry=True).state == 'queued'
    b = c.claim(w)
    assert b.fence == a.fence + 1
    assert c.fail(w, b, retry=True).state == 'failed'
    with pytest.raises(LeaseLost): c.fail(w, a)


def _claim_in_child(path, name, event, queue):
    c = setup(path, lease_ms=2000)
    w = c.register(name)
    event.wait(10)
    a = c.claim(w)
    queue.put(None if a is None else (a.job_id, a.fence))


def test_real_multiprocess_atomic_claim(tmp_path):
    path = tmp_path/'db'; c = setup(path, lease_ms=2000); submit(c)
    ctx = mp.get_context('spawn'); event = ctx.Event(); queue = ctx.Queue()
    ps = [ctx.Process(target=_claim_in_child, args=(path, name, event, queue)) for name in ('w1', 'w2')]
    for p in ps: p.start()
    try:
        event.set(); outcomes = [queue.get(timeout=15) for _ in ps]
        assert outcomes.count(('j', 1)) == 1 and outcomes.count(None) == 1
    finally:
        for p in ps:
            p.join(5)
            if p.is_alive(): p.kill(); p.join()
    assert all(p.exitcode == 0 for p in ps)


def _crash(path, marker):
    c = setup(path, lease_ms=200)
    w = c.register('w1'); a = c.claim(w)
    Path(marker).write_text(str(a.fence)); os._exit(71)


def test_real_crash_recovery(tmp_path):
    path = tmp_path/'db'; marker = tmp_path/'marker'; c = setup(path, lease_ms=200)
    submit(c, idempotent=True, max_attempts=2)
    p = mp.get_context('spawn').Process(target=_crash, args=(path, marker)); p.start(); p.join(15)
    if p.is_alive(): p.kill(); p.join(); pytest.fail('child stuck')
    assert p.exitcode == 71 and marker.read_text() == '1'
    deadline = time.monotonic() + 5
    while c.get('a','j').state == 'leased' and time.monotonic() < deadline: time.sleep(.02)
    a = c.claim(c.register('w2')); assert a.fence == 2


@pytest.mark.asyncio
async def test_real_process_worker_round_trip(tmp_path):
    c = setup(tmp_path/'db', lease_ms=2000)
    w = c.register('w1'); submit(c)
    script = tmp_path/'worker.py'; script.write_text('import sys,json\nprint(json.dumps(json.load(sys.stdin)["x"]+1))')
    runner = ClusterWorker(c, w, {'f': ProcessWorker(script, WorkerLimits(timeout=2))}, heartbeat_interval=.1)
    result = await runner.run_once()
    assert result.state == 'succeeded' and result.result == 2
    assert await runner.run_once() is None


def test_rejected_expiry_cannot_revive_lease_after_clock_rollback(tmp_path):
    tick = [1000]
    c = setup(tmp_path/'db', lease_ms=100, clock_ms=lambda: tick[0])
    w = c.register('w1'); submit(c); a = c.claim(w)
    tick[0] = 1100
    with pytest.raises(LeaseLost): c.complete(w, a, 'expired')
    tick[0] = 1000
    with pytest.raises(LeaseLost): c.complete(w, a, 'resurrected')
    assert c.get('a','j').state == 'uncertain'


@pytest.mark.asyncio
async def test_worker_context_renewal_and_cancellation(tmp_path):
    from autogen_core import current_cluster_lease
    c = setup(tmp_path/'db', lease_ms=400)
    w = c.register('w1'); submit(c)
    entered = asyncio.Event(); end = asyncio.Event(); cleanup = asyncio.Event()
    async def handler(data):
        assert current_cluster_lease().job_id == 'j'
        assert current_cluster_lease().fence == 1
        entered.set()
        try: await end.wait()
        finally: cleanup.set()
    runner = ClusterWorker(c, w, {'f': handler}, heartbeat_interval=.04)
    task = asyncio.create_task(runner.run_once())
    await asyncio.wait_for(entered.wait(), 2)
    try:
        await asyncio.sleep(.6)  # longer than original lease
        assert c.get('a','j').state == 'leased'
        with pytest.raises(ClusterConflict): await runner.run_once()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    assert cleanup.is_set() and current_cluster_lease() is None
    deadline = time.monotonic() + 3
    while c.get('a','j').state == 'leased' and time.monotonic() < deadline: await asyncio.sleep(.03)
    assert c.get('a','j').state == 'uncertain'


def test_real_multiprocess_aggregate_quota(tmp_path):
    path = tmp_path/'db'; c = setup(path, lease_ms=2000)
    for name in ('j1','j2'): c.submit('a',name,'f',{},R(2,200,200))
    ctx = mp.get_context('spawn'); event = ctx.Event(); queue = ctx.Queue()
    ps = [ctx.Process(target=_claim_in_child,args=(path,name,event,queue)) for name in ('w1','w2')]
    for p in ps: p.start()
    try:
        event.set(); outcomes = [queue.get(timeout=15) for _ in ps]
        assert outcomes.count(('j1',1)) == 1 and outcomes.count(None) == 1
        assert c.get('a','j2').state == 'queued'
    finally:
        for p in ps:
            p.join(5)
            if p.is_alive(): p.kill(); p.join()
    assert all(p.exitcode == 0 for p in ps)


@pytest.mark.asyncio
async def test_cancel_during_claim_drains_database_write(tmp_path, monkeypatch):
    import threading
    c = setup(tmp_path/'db', lease_ms=1000)
    w = c.register('w1'); submit(c)
    original = c.claim
    entered = threading.Event(); proceed = threading.Event()
    def blocked(session):
        entered.set()
        if not proceed.wait(3): raise RuntimeError('test gate timed out')
        return original(session)
    monkeypatch.setattr(c, 'claim', blocked)
    async def handler(data): pytest.fail('cancelled claim must not start work')
    runner = ClusterWorker(c, w, {'f': handler})
    task = asyncio.create_task(runner.run_once())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel(); await asyncio.sleep(0)
        assert not task.done()
        with pytest.raises(ClusterConflict): await runner.run_once()
    finally:
        proceed.set()
        with pytest.raises(asyncio.CancelledError): await task
    assert c.get('a','j').state == 'leased'


@pytest.mark.asyncio
async def test_lost_heartbeat_cancels_handler(tmp_path, monkeypatch):
    c = setup(tmp_path/'db', lease_ms=1000)
    w = c.register('w1'); submit(c)
    cleaned = asyncio.Event()
    async def handler(data):
        try: await asyncio.Event().wait()
        finally: cleaned.set()
    def lost(*args): raise LeaseLost('test loss')
    monkeypatch.setattr(c,'renew',lost)
    runner = ClusterWorker(c,w,{'f':handler},heartbeat_interval=.02)
    with pytest.raises(LeaseLost): await asyncio.wait_for(runner.run_once(),2)
    assert cleaned.is_set()
    assert c.get('a','j').state != 'succeeded'
