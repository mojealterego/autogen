"""Deterministic offline checks of original wheels against the committed uv.lock."""
import hashlib
from pathlib import Path
from zipfile import ZipFile

import check_wheels


def fixture(tmp_path: Path) -> tuple[Path, Path]:
    wheelhouse = tmp_path / 'wheelhouse'
    wheelhouse.mkdir()
    wheel = wheelhouse / 'example-1.0.0-py3-none-any.whl'
    with ZipFile(wheel, 'w') as z:
        z.writestr('example/__init__.py', 'version = 1\n')
    checksum = hashlib.sha256(wheel.read_bytes()).hexdigest()
    lockfile = tmp_path / 'uv.lock'
    lockfile.write_text('''version = 1\n[[package]]\nname = "example"\nversion = "1.0.0"\nwheels = [\n  { url = "https://files.pythonhosted.org/packages/a/example-1.0.0-py3-none-any.whl", hash = "sha256:%s", size = %d },\n]\n''' % (checksum, wheel.stat().st_size))
    return lockfile, wheelhouse


def test_original_artifact_satisfies_verified_lock(tmp_path):
    lockfile, wheelhouse = fixture(tmp_path)
    report = check_wheels.audit(lockfile, wheelhouse, ('example',))
    assert report == [{'name': 'example', 'version': '1.0.0', 'status': 'verified',
                       'filename': 'example-1.0.0-py3-none-any.whl'}]


def test_missing_artifact_cannot_be_reported_as_pass(tmp_path):
    lockfile, wheelhouse = fixture(tmp_path)
    next(wheelhouse.iterdir()).unlink()
    report = check_wheels.audit(lockfile, wheelhouse, ('example',))
    assert report[0]['status'] == 'missing'


def test_tampering_fails_digest(tmp_path):
    lockfile, wheelhouse = fixture(tmp_path)
    wheel = next(wheelhouse.iterdir())
    data = bytearray(wheel.read_bytes())
    data[12] ^= 0x01
    wheel.write_bytes(data)
    assert check_wheels.audit(lockfile, wheelhouse, ('example',))[0]['status'] == 'sha256_mismatch'


def test_corrupt_wheel_with_matching_hash_fails_zip_guard(tmp_path):
    lockfile, wheelhouse = fixture(tmp_path)
    wheel = next(wheelhouse.iterdir())
    wheel.write_bytes(b'not a wheel')
    raw = lockfile.read_text()
    import re
    raw = re.sub(r'sha256:[0-9a-f]{64}', 'sha256:' + hashlib.sha256(wheel.read_bytes()).hexdigest(), raw)
    raw = re.sub(r'size = \d+', 'size = %d' % wheel.stat().st_size, raw)
    lockfile.write_text(raw)
    assert check_wheels.audit(lockfile, wheelhouse, ('example',))[0]['status'] == 'invalid_wheel'


def test_unknown_lock_package_denied(tmp_path):
    lockfile, wheelhouse = fixture(tmp_path)
    assert check_wheels.audit(lockfile, wheelhouse, ('unknown',))[0]['status'] == 'not_in_lock'
