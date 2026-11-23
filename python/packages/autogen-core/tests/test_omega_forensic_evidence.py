"""Offline forensic evidence contract tests."""
from autogen_core._forensic_evidence import inspect_imei, inspect_camera_metadata
import pytest

def test_imei_luhn_and_redaction():
    valid = inspect_imei('490154203237518')
    assert valid.valid and valid.tac == '49015420' and len(valid.digest) == 64
    assert '490154203237518' not in repr(valid)
    assert inspect_imei('490154203237519').reason == 'invalid_check_digit'
    for value in ('', '123', 'a'*15, '４'*15, '12345678901234\n'):
        assert not inspect_imei(value).valid

def test_camera_match_conflict_and_unknown():
    assert inspect_camera_metadata('ABCD1234', {'BodySerialNumber': 'ABCD1234'}).status == 'match'
    assert inspect_camera_metadata('ABCD1234', {'SerialNumber': 'OTHER123'}).status == 'mismatch'
    assert inspect_camera_metadata('ABCD1234', {}).status == 'unknown'
    assert inspect_camera_metadata('ABCD1234', {'SerialNumber': 'ABCD1234', 'BodySerialNumber': 'OTHER123'}).status == 'conflict'

def test_camera_input_bounds_and_no_serial_leak():
    with pytest.raises(ValueError):
        inspect_camera_metadata('../bad', {})
    with pytest.raises(ValueError):
        inspect_camera_metadata('ABCD1234', {'a': 'x'*4097})
    result = inspect_camera_metadata('ABCD1234', {'SerialNumber': 'ABCD1234'})
    assert 'ABCD1234' not in repr(result)
