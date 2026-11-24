"""Offline mobile-artifact triage; never executes or renders untrusted content.

No spyware attribution, vulnerability detection, or device compromise claim.
"""
from __future__ import annotations

import hashlib
import os
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArtifactEvidence:
    format: str
    assessment: str
    sha256: str
    size_bytes: int
    entries: int


def inspect_mobile_artifact(path: str | Path, *, max_bytes: int = 16 * 1024 * 1024,
                            max_uncompressed: int = 64 * 1024 * 1024) -> ArtifactEvidence:
    if type(max_bytes) is not int or not 1 <= max_bytes <= 64 * 1024 * 1024:
        raise ValueError("invalid_max_bytes")
    if type(max_uncompressed) is not int or not 1 <= max_uncompressed <= 256 * 1024 * 1024:
        raise ValueError("invalid_max_uncompressed")
    target = Path(path)
    info = target.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("non_regular_or_linked_file")
    if info.st_size > max_bytes:
        raise ValueError("artifact_too_large")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(target, flags)
    try:
        current = os.fstat(fd)
        if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or current.st_size > max_bytes:
            raise ValueError("artifact_changed")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            content = stream.read(max_bytes + 1)
        if len(content) > max_bytes or os.fstat(fd).st_size != len(content):
            raise ValueError("artifact_changed")
    finally:
        os.close(fd)
    digest = hashlib.sha256(content).hexdigest()
    if content.startswith((b"GIF87a", b"GIF89a")):
        return ArtifactEvidence("gif", "structure_only", digest, len(content), 0)
    if not content.startswith(b"PK\x03\x04"):
        return ArtifactEvidence("unknown", "unknown_format", digest, len(content), 0)
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 1024:
                return ArtifactEvidence("zip", "unsafe_archive", digest, len(content), len(entries))
            total = 0
            for entry in entries:
                name = entry.filename.replace("\\", "/")
                parts = name.split("/")
                mode = (entry.external_attr >> 16) & 0o170000
                if (name.startswith("/") or ".." in parts or any(not p for p in parts[:-1])
                    or mode == stat.S_IFLNK or entry.flag_bits & 1):
                    return ArtifactEvidence("zip", "unsafe_archive", digest, len(content), len(entries))
                total += entry.file_size
                if total > max_uncompressed:
                    return ArtifactEvidence("zip", "unsafe_archive", digest, len(content), len(entries))
            return ArtifactEvidence("zip", "structure_only", digest, len(content), len(entries))
    except (zipfile.BadZipFile, ValueError, OSError):
        return ArtifactEvidence("zip", "unsafe_archive", digest, len(content), 0)
