import multiprocessing as mp
import sqlite3

import pytest

from autogen_core import (AuthorityLeaseStore, AuthorityBusy, LeadershipLost,
                          ClusterCoordinator, Resources, TenantSpec, WorkerSpec)

Q = Resources(1,100,100)


def coordinator(path, guard=None):
    return ClusterCoordinator(path, Q,
        {'t': TenantSpec(Q, frozenset({'f'}))},
        {'w': WorkerSpec(Q, frozenset({'t'}), frozenset({'f'}))},
        lease_ms=1000, authority_guard=guard)


def test_initial_lease_renew_and_failover(tmp_path):
    path=tmp_path/'db'; coordinator(path)
    tick=[1000]
    store=AuthorityLeaseStore(path, lease_ms=100, clock_ms=lambda:tick[0])
    a=store.acquire('a')
    assert a.epoch==1
    with pytest.raises(AuthorityBusy): store.acquire('b')
    assert store.renew(a)>=1100
    tick[0]=1101
    b=store.acquire('b')
    assert b.epoch==2
    with pytest.raises(LeadershipLost): store.assert_active(a)
    store.assert_active(b)
    with pytest.raises(LeadershipLost): store.renew(a)


def test_fenced_coordinator_handoff_and_worker_revocation(tmp_path):
    path=tmp_path/'db';coordinator(path)
    tick=[1000];store=AuthorityLeaseStore(path,lease_ms=100,clock_ms=lambda:tick[0])
    a=store.acquire('a'); c1=coordinator(path,store.guard(a))
    c1.submit('t','job','f',{},Q,idempotent=True,max_attempts=2)
    w=c1.register('w'); lease=c1.claim(w)
    assert lease.fence==1
    tick[0]=1101
    b=store.acquire('b')
    c2=coordinator(path,store.guard(b))
    assert c2.get('t','job').state=='queued'
    with pytest.raises(LeadershipLost): c1.complete(w,lease,7)
    with pytest.raises(LeadershipLost): c1.get('t','job')
    w2=c2.register('w');lease2=c2.claim(w2)
    assert lease2.fence > lease.fence
    assert c2.complete(w2,lease2,9).result==9


def test_unguarded_api_is_denied_when_leadership_enabled(tmp_path):
    path=tmp_path/'db';original=coordinator(path)
    store=AuthorityLeaseStore(path,lease_ms=100)
    a=store.acquire('a')
    with pytest.raises(LeadershipLost): original.submit('t','job','f',{},Q)
    assert coordinator(path,store.guard(a)).submit('t','job','f',{},Q).state=='queued'


def test_clock_rollback_cannot_revive_expired_authority(tmp_path):
    path=tmp_path/'db';coordinator(path)
    tick=[1000];store=AuthorityLeaseStore(path,lease_ms=100,clock_ms=lambda:tick[0]);a=store.acquire('a')
    tick[0]=1101
    with pytest.raises(LeadershipLost): store.assert_active(a)
    tick[0]=1000
    with pytest.raises(LeadershipLost): store.assert_active(a)
    assert store.acquire('b').epoch==2


def test_release_reacquire_epoch_never_reused(tmp_path):
    path=tmp_path/'db';coordinator(path)
    store=AuthorityLeaseStore(path,lease_ms=100)
    a=store.acquire('a')
    store.release(a)
    with pytest.raises(LeadershipLost): store.assert_active(a)
    b=store.acquire('b')
    assert b.epoch==a.epoch+1


def test_stale_or_mismatched_witness_rejected(tmp_path):
    path=tmp_path/'db';coordinator(path)
    one=AuthorityLeaseStore(path,lease_ms=100)
    with pytest.raises(ValueError): AuthorityLeaseStore(path,lease_ms=200)
    a=one.acquire('leader')
    other=tmp_path/'other';coordinator(other)
    with pytest.raises(ValueError): coordinator(other,one.guard(a))


def _child_claim(path,gate,queue):
    st=AuthorityLeaseStore(path,lease_ms=3000)
    gate.wait(10)
    try:
        lease=st.acquire(str(mp.current_process().pid))
        queue.put(('ok',lease.epoch))
    except AuthorityBusy:
        queue.put(('busy',0))


def test_concurrent_processes_only_one_leader(tmp_path):
    path=tmp_path/'db';coordinator(path);AuthorityLeaseStore(path,lease_ms=3000)
    ctx=mp.get_context('spawn');gate=ctx.Event();queue=ctx.Queue()
    ps=[ctx.Process(target=_child_claim,args=(path,gate,queue)) for _ in range(2)]
    for p in ps:p.start()
    try:
        gate.set();answers=[queue.get(timeout=15) for _ in ps]
        assert sorted(v[0] for v in answers)==['busy','ok']
    finally:
        for p in ps:
            p.join(5)
            if p.is_alive(): p.terminate();p.join(5)
    assert all(p.exitcode==0 for p in ps)

from test_omega_fabric import pki, server_context, client_context, fingerprint  # noqa:E402,F401


def test_real_mtls_old_authority_fails_closed_after_takeover(tmp_path,pki):
    import threading
    from autogen_core import FabricServer, FabricClient, FabricPrincipal
    path=tmp_path/'db';coordinator(path)
    tick=[1000];store=AuthorityLeaseStore(path,lease_ms=100,clock_ms=lambda:tick[0])
    old=store.acquire('node1')
    a=coordinator(path,store.guard(old))
    pins={fingerprint(pki,'a'):FabricPrincipal('producer','t')}
    with FabricServer(('127.0.0.1',0),a,server_context(pki),pins,timeout=3) as server:
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01})
        thread.start()
        try:
            client=FabricClient(server.server_address, client_context(pki,'a'),'localhost',timeout=3)
            assert client.submit('t','j','f',{},Q).state=='queued'
            tick[0]=1101
            leader2=store.acquire('node2')
            assert coordinator(path,store.guard(leader2)).get('t','j').state=='queued'
            with pytest.raises(LeadershipLost): client.get('t','j')
            with pytest.raises(LeadershipLost): client.submit('t','another','f',{},Q)
        finally:
            server.shutdown();thread.join(3)
            assert not thread.is_alive()
