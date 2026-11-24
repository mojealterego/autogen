import pytest
from autogen_core._omega_oesi import route


def test_thresholds():
    assert route(0,0,False)==(0,False)
    assert route(1,1,False)==(1,True)
    assert route(.6,.6,True)==(.6,True)
    assert route(.6,.6,False)==(.6,False)
    assert route(.4,.4,True)==(.4,False)


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),-1,2,True,'0'])
def test_invalid(bad):
    with pytest.raises(ValueError): route(bad,0,False)
    with pytest.raises(ValueError): route(0,bad,False)


def test_invalid_state():
    with pytest.raises(ValueError): route(0,0,1)
