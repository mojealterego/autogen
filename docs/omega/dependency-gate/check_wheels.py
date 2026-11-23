"""Verify original, pinned PyPI wheels without executing or installing them.

Usage:
  python check_wheels.py --lock python/uv.lock --wheelhouse /trusted/wheels

Python >=3.11. Any mismatch exits nonzero. Does not attempt network access,
execute wheels, import third-party code, or modify the Python environment.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import stat
from io import BytesIO
import json
import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import BadZipFile, ZipFile

DEFAULT_PACKAGES = ('jsonref', 'asyncio-atexit', 'pytest-mock')
_MAX_WHEEL_BYTES = 50 * 1024 * 1024
_MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024


def _valid_wheel(data: bytes) -> bool:
    try:
        with ZipFile(BytesIO(data), 'r') as wheel:
            infos = wheel.infolist()
            if not infos or sum(x.file_size for x in infos) > _MAX_UNCOMPRESSED_BYTES:
                return False
            for info in infos:
                name = info.filename
                if (not name or name.startswith('/') or '\\' in name or
                    any(p in ('.', '..') for p in name.split('/')) or
                    info.file_size > _MAX_UNCOMPRESSED_BYTES or
                    (info.compress_size == 0 and info.file_size > 0) or
                    (info.compress_size and info.file_size > 300 * info.compress_size)):
                    return False
                # Reject archive symlinks (Unix mode bits in external_attr).
                if (info.create_system == 3 and (info.external_attr >> 16) & 0o170000 == 0o120000):
                    return False
            return wheel.testzip() is None
    except (OSError, BadZipFile, RuntimeError, ValueError):
        return False


def audit(lockfile: Path, wheelhouse: Path, packages: tuple[str, ...] = DEFAULT_PACKAGES) -> list[dict[str, str]]:
    """Return deterministic package-by-package lock verification reports."""
    with lockfile.open('rb') as fh:
        lock = tomllib.load(fh)
    selected = {p['name']: p for p in lock.get('package', [])}
    out = []
    for name in sorted(set(packages)):
        row: dict[str, str] = {'name': name}
        package = selected.get(name)
        if package is None:
            row['status'] = 'not_in_lock'
            out.append(row)
            continue
        row['version'] = str(package['version'])
        # These three dependencies have exact, universal py3-none-any wheels.
        wheels = [w for w in package.get('wheels', []) if urlsplit(w.get('url', '')).path.endswith('-py3-none-any.whl')]
        if len(wheels) != 1:
            row['status'] = 'no_unique_pinned_wheel'
            out.append(row)
            continue
        record = wheels[0]
        filename = urlsplit(record['url']).path.rsplit('/', 1)[-1]
        if not re.fullmatch(r'[a-zA-Z0-9_.+-]+\.whl', filename):
            row['status'] = 'invalid_lock_filename'
            out.append(row)
            continue
        row['filename'] = filename
        candidate = wheelhouse / filename
        try:
            fd = os.open(candidate, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0))
        except FileNotFoundError:
            row['status'] = 'missing'
        except OSError:
            row['status'] = 'invalid_wheel'
        else:
            with os.fdopen(fd, 'rb') as fh:
                information = os.fstat(fh.fileno())
                if (not stat.S_ISREG(information.st_mode) or information.st_size > _MAX_WHEEL_BYTES
                        or information.st_size != record.get('size')):
                    row['status'] = 'size_mismatch'
                else:
                    data = fh.read(_MAX_WHEEL_BYTES + 1)
                    sha = hashlib.sha256(data).hexdigest()
                    expected = record.get('hash', '')
                    if not re.fullmatch(r'sha256:[0-9a-f]{64}', expected) or sha != expected[7:]:
                        row['status'] = 'sha256_mismatch'
                    else:
                        row['status'] = 'verified' if _valid_wheel(data) else 'invalid_wheel'
        out.append(row)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', required=True, type=Path)
    parser.add_argument('--wheelhouse', required=True, type=Path)
    parser.add_argument('--package', action='append', dest='packages', help='Repeat to override the default three packages')
    ns = parser.parse_args(argv)
    try:
        report = audit(ns.lock, ns.wheelhouse, tuple(ns.packages or DEFAULT_PACKAGES))
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({'error': type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps({'packages': report}, indent=2, sort_keys=True))
    return 0 if all(item['status'] == 'verified' for item in report) else 2


if __name__ == '__main__':
    sys.exit(main())
