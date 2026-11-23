"""Audit immutable user-supplied source archive bytes without importing code.

For research supply-chain provenance, not a signature or third-party malware scan.
Use only inputs whose SHA256 is pinned in SOURCE_PROVENANCE.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import stat
import zipfile
from pathlib import Path

_ROOT=Path(__file__).resolve().parent


def inspect_source(path:Path, spec:dict) -> dict:
    if not path.is_file() or path.is_symlink():raise ValueError('source is missing or symlinked')
    with path.open('rb') as fd:
        digest=hashlib.file_digest(fd,'sha256').hexdigest()
    if digest!=spec['sha256']:raise ValueError('source SHA-256 differs from pinned manifest')
    with zipfile.ZipFile(path) as archive:
        members=archive.infolist()
        if not 1 <= len(members) <= spec['max_members']:
            raise ValueError('unexpected archive member count')
        seen=set();total=0
        for member in members:
            name=member.filename
            chunks=Path(name).parts
            if (name.startswith('/') or '\\' in name or '..' in chunks or name in seen or
                    stat.S_IFMT(member.external_attr >> 16)==stat.S_IFLNK):
                raise ValueError('invalid archive member')
            seen.add(name)
            total+=member.file_size
            if member.file_size>spec['max_member_size'] or total>spec['max_unpacked_bytes']:
                raise ValueError('archive expansion budget exceeded')
        missing=set(spec['required'])-seen
        if missing:raise ValueError('required upstream source files absent: '+','.join(sorted(missing)))
        # The model weights are not in this source archive. Do not silently infer
        # that a code tarball includes a usable checkpoint.
        weights=[name for name in seen if name.endswith(('.safetensors','.gguf','.pt','.pth'))]
        if weights:raise ValueError('unexpected binary model checkpoint in source archive')
        if archive.testzip() is not None:raise ValueError('CRC failed')
    return {'sha256':digest,'archive':path.name,'files':len(members),
            'unpacked_bytes':total,'weights_present':False,'status':'verified'}


def audit(ltn:Path,cwm:Path,manifest:Path=_ROOT/'SOURCE_PROVENANCE.json')->dict:
    settings=json.loads(manifest.read_text(encoding='utf-8'))
    return {name:inspect_source(path,settings[name]) for name,path in [('ltn',ltn),('cwm',cwm)]}


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ltn',type=Path,required=True)
    parser.add_argument('--cwm',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(audit(args.ltn,args.cwm),sort_keys=True,indent=2))


if __name__=='__main__':main()
