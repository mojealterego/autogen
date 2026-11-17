import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).parents[1] / 'src/autogen_core/_resource_scheduler.py'
spec = importlib.util.spec_from_file_location('omega_resource_scheduler', MODULE)
assert spec and spec.loader
s = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = s
spec.loader.exec_module(s)

R = s.Resources


def scheduler(**kwargs):
    return s.ResourceScheduler(R(2, 200, 200), {'a': R(2, 200, 200), 'b': R(1, 100, 100)}, **kwargs)


def test_invalid_resources_and_unknown_tenant():
    with pytest.raises(ValueError): R(True, 10, 10)
    with pytest.raises(ValueError): R(1, 0, 10)
    async def run():
        sc = scheduler()
        with pytest.raises(s.AdmissionDenied):
            async with sc.reserve('unknown', R(1, 10, 10)): pass
        with pytest.raises(s.AdmissionDenied):
            async with sc.reserve('b', R(2, 10, 10)): pass
        assert sc.snapshot().active == 0
    asyncio.run(run())


def test_fifo_no_bypass_and_global_limit():
    async def run():
        sc = scheduler()
        entered = []
        async def work(tenant, demand, name):
            async with sc.reserve(tenant, demand): entered.append(name)
        async with sc.reserve('a', R(1, 100, 100)):
            first = asyncio.create_task(work('a', R(2, 200, 200), 'first'))
            await asyncio.sleep(0)
            second = asyncio.create_task(work('b', R(1, 100, 100), 'second'))
            await asyncio.sleep(0)
            assert entered == [] and sc.snapshot().queued == 2
        await asyncio.gather(first, second)
        assert entered == ['first', 'second']
        assert sc.snapshot().active == 0
    asyncio.run(run())


def test_tenant_and_memory_caps():
    async def run():
        sc = scheduler()
        async with sc.reserve('b', R(1, 80, 30)):
            async with sc.reserve('a', R(1, 120, 170)):
                assert sc.snapshot().used == (2, 200, 200)
            with pytest.raises(TimeoutError):
                async with sc.reserve('b', R(1, 20, 10), timeout=.01): pass
        assert sc.snapshot().used == (0, 0, 0)
        assert sc.snapshot().queued == 0
    asyncio.run(run())


def test_queue_bound_and_waiter_cancellation():
    async def run():
        sc = scheduler(max_queue=1)
        async def wait():
            async with sc.reserve('a', R(2, 200, 200)): pass
        async with sc.reserve('a', R(2, 200, 200)):
            pending = asyncio.create_task(wait()); await asyncio.sleep(0)
            with pytest.raises(s.QueueFull): await wait()
            pending.cancel()
            with pytest.raises(asyncio.CancelledError): await pending
            assert sc.snapshot().queued == 0
        assert sc.snapshot().active == 0
    asyncio.run(run())


def test_cancel_after_grant_before_resume():
    async def run():
        sc = scheduler()
        entered = False
        async def wait():
            nonlocal entered
            async with sc.reserve('a', R(2, 200, 200)): entered = True
        async with sc.reserve('a', R(2, 200, 200)):
            pending = asyncio.create_task(wait()); await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError): await pending
        assert not entered and sc.snapshot().active == 0 and sc.snapshot().queued == 0
    asyncio.run(run())


def test_close_rejects_pending_and_new_but_drains_active():
    async def run():
        sc = scheduler()
        async def wait():
            async with sc.reserve('a', R(2, 200, 200)): pass
        async with sc.reserve('a', R(2, 200, 200)):
            pending = asyncio.create_task(wait()); await asyncio.sleep(0)
            sc.close(); sc.close()
            with pytest.raises(s.AdmissionDenied): await pending
            with pytest.raises(s.AdmissionDenied): await wait()
            assert sc.snapshot().active == 1
        assert sc.snapshot().active == 0 and sc.snapshot().closed
    asyncio.run(run())


def test_handler_releases_on_failure_and_cancellation():
    async def run():
        sc = scheduler()
        started = asyncio.Event()
        async def fail(inputs): raise RuntimeError('failure')
        async def block(inputs): started.set(); await asyncio.Event().wait()
        with pytest.raises(RuntimeError): await s.scheduled_handler(sc, 'a', R(1, 100, 100), fail)({})
        task = asyncio.create_task(s.scheduled_handler(sc, 'a', R(1, 100, 100), block)({}))
        await started.wait(); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert sc.snapshot().active == 0
    asyncio.run(run())


def test_cross_loop_rejected():
    sc = scheduler()
    async def run():
        async with sc.reserve('a', R(1, 10, 10)): pass
    asyncio.run(run())
    with pytest.raises(RuntimeError, match='event loop'): asyncio.run(run())


def test_global_capacity_under_concurrency():
    async def run():
        sc = scheduler(max_queue=100)
        async def work(i):
            tenant = 'a' if i % 2 else 'b'
            async with sc.reserve(tenant, R(1, 100, 100)):
                snap = sc.snapshot()
                assert snap.used[0] <= 2 and snap.used[1] <= 200
                assert snap.by_tenant['b'][0] <= 1
                await asyncio.sleep(0)
        await asyncio.gather(*(work(i) for i in range(80)))
        snap = sc.snapshot()
        assert snap.active == snap.queued == 0 and snap.granted == 80
    asyncio.run(run())


def test_individual_tenant_queue_limit_preserves_other_tenant_capacity():
    async def run():
        sc = scheduler(max_queue=4, max_queued_per_tenant=1)
        async def wait(tenant):
            async with sc.reserve(tenant, R(1, 100, 100)): pass
        async with sc.reserve('a', R(2, 200, 200)):
            first = asyncio.create_task(wait('a')); await asyncio.sleep(0)
            with pytest.raises(s.QueueFull): await wait('a')
            second = asyncio.create_task(wait('b')); await asyncio.sleep(0)
            assert sc.snapshot().queued == 2
        await asyncio.gather(first, second)
        assert sc.snapshot().active == 0
    asyncio.run(run())
