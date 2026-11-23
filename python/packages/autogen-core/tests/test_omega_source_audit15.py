"""OMEGA 15 supply chain: pinned upstream archives are data, not executable code."""
import hashlib
import importlib.util
import stat
import zipfile
from pathlib import Path

import pytest


path=Path(__file__).resolve().parents[4]/'docs/omega/upstream-integration/audit_sources.py'
spec=importlib.util.spec_from_file_location('omega15_source_audit',path)
assert spec is not None and spec.loader is not None
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _archive(tmp_path, names, *, extra=None):
    path=tmp_path/'source.zip'
    with zipfile.ZipFile(path,'w') as z:
        for name in names:z.writestr(name,'trusted bytes')
        if extra:z.writestr(extra[0],extra[1])
    pinned={'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'max_members':10,'max_member_size':1024,'max_unpacked_bytes':4096,'required':['ltn/LICENSE']}
    return path,pinned


def test_accept_pinned_manifest_without_execution(tmp_path):
    p,ref=_archive(tmp_path,['ltn/LICENSE','ltn/core.py'])
    result=mod.inspect_source(p,ref)
    assert result['status']=='verified' and result['files']==2 and not result['weights_present']


def test_reject_checksum_mismatch(tmp_path):
    p,ref=_archive(tmp_path,['ltn/LICENSE'])
    ref['sha256']='0'*64
    with pytest.raises(ValueError):mod.inspect_source(p,ref)


def test_reject_path_traversal_even_with_pinned_sha(tmp_path):
    p,ref=_archive(tmp_path,['ltn/LICENSE','../escape.py'])
    with pytest.raises(ValueError):mod.inspect_source(p,ref)


def test_reject_binary_checkpoint_even_with_pinned_sha(tmp_path):
    p,ref=_archive(tmp_path,['ltn/LICENSE','ltn/model.safetensors'])
    with pytest.raises(ValueError):mod.inspect_source(p,ref)


def test_reject_archive_expansion(tmp_path):
    p,ref=_archive(tmp_path,['ltn/LICENSE'],extra=('big','B'*2048))
    with pytest.raises(ValueError):mod.inspect_source(p,ref)


def test_reject_symlink_zip_entries(tmp_path):
    p=tmp_path/'source.zip'
    with zipfile.ZipFile(p,'w') as z:
        z.writestr('ltn/LICENSE','ok')
        f=zipfile.ZipInfo('ltn/escape')
        f.create_system=3
        f.external_attr=(stat.S_IFLNK | 0o777)<<16
        z.writestr(f,'/etc/passwd')
    ref={'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
         'max_members':10,'max_member_size':1024,'max_unpacked_bytes':4096,'required':['ltn/LICENSE']}
    with pytest.raises(ValueError):mod.inspect_source(p,ref)
