"""Offline forensic evidence checks. No network, geolocation or device access."""
from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass
from typing import Mapping

@dataclass(frozen=True)
class ImeiEvidence:
    valid: bool
    tac: str | None
    digest: str | None
    reason: str

def inspect_imei(value: str) -> ImeiEvidence:
    """Check a 15-digit IMEI using Luhn; TAC is not a model or location lookup."""
    if not isinstance(value, str) or re.fullmatch(r'[0-9]{15}', value) is None:
        return ImeiEvidence(False, None, None, 'invalid_format')
    digits = [int(c) for c in value]
    total = 0
    for i, digit in enumerate(digits):
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    if total % 10:
        return ImeiEvidence(False, None, None, 'invalid_check_digit')
    return ImeiEvidence(True, value[:8], hashlib.sha256(value.encode('ascii')).hexdigest(), 'valid_structure_only')

@dataclass(frozen=True)
class CameraEvidence:
    status: str
    digest: str | None
    reason: str

def inspect_camera_metadata(expected_serial: str, metadata: Mapping[str, str]) -> CameraEvidence:
    """Compare a trusted reference serial with supplied metadata, not live web evidence."""
    if not isinstance(expected_serial, str) or re.fullmatch(r'[A-Za-z0-9-]{4,64}', expected_serial) is None:
        raise ValueError('invalid_reference_serial')
    if not isinstance(metadata, Mapping) or len(metadata) > 64:
        raise ValueError('invalid_metadata')
    if any(not isinstance(k, str) or not isinstance(v, str) or len(k) > 128 or len(v) > 4096 for k, v in metadata.items()):
        raise ValueError('invalid_metadata_entry')
    keys = ('SerialNumber', 'BodySerialNumber', 'CameraSerialNumber')
    observed = [metadata[k].strip() for k in keys if k in metadata and metadata[k].strip()]
    if not observed:
        return CameraEvidence('unknown', None, 'serial_not_present')
    if len(set(observed)) != 1:
        return CameraEvidence('conflict', None, 'inconsistent_serial_fields')
    digest = hashlib.sha256(observed[0].encode('utf-8')).hexdigest()
    if observed[0] == expected_serial:
        return CameraEvidence('match', digest, 'supplied_metadata_only')
    return CameraEvidence('mismatch', digest, 'different_serial')
