from concurrent.futures import ThreadPoolExecutor

import pytest
from autogen_core._omega_oesi import ScopedRouter


def test_scope_isolation():
    router = ScopedRouter()
    assert router.decide("a", "x", 1, 1)[1]
    assert router.decide("a", "x", .6, .6)[1]
    assert not router.decide("b", "x", .6, .6)[1]
    assert not router.decide("a", "y", .6, .6)[1]


def test_capacity_and_cleanup():
    router = ScopedRouter(1)
    router.decide("a", "x", 1, 1)
    with pytest.raises(OverflowError):
        router.decide("b", "x", 1, 1)
    assert router.clear("a", "x")
    assert not router.clear("a", "x")
    assert not router.decide("b", "x", .6, .6)[1]


def test_invalid_does_not_allocate():
    router = ScopedRouter(1)
    with pytest.raises(ValueError):
        router.decide("a", "x", float("nan"), 0)
    with pytest.raises(ValueError):
        router.decide("../a", "x", 1, 1)
    assert router.decide("b", "x", 1, 1)[1]


def test_concurrent_capacity():
    router = ScopedRouter(8)
    def allocate(i):
        try:
            return router.decide("tenant", str(i), 1, 1)[1]
        except OverflowError:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(allocate, range(64))) == 8
