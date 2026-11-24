"""OM-75 regression: routing identifiers must be plain strings."""
import pytest

from autogen_core._omega_oesi import ScopedRouter


class SpoofedScope(str):
    def __hash__(self) -> int:
        return hash("victim")

    def __eq__(self, other: object) -> bool:
        return True


@pytest.mark.parametrize("operation", ["decide", "clear"])
@pytest.mark.parametrize("position", [0, 1])
def test_scope_subclass_cannot_alias_or_clear_existing_state(operation: str, position: int) -> None:
    router = ScopedRouter()
    router.decide("victim", "victim", 1, 1)
    scope = ["victim", "victim"]
    scope[position] = SpoofedScope("attacker")
    with pytest.raises(ValueError, match="invalid_scope"):
        if operation == "decide":
            router.decide(scope[0], scope[1], 0, 0)
        else:
            router.clear(scope[0], scope[1])
    assert router.decide("victim", "victim", .6, .6)[1]
