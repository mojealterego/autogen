"""Offline E.164 syntax validation; no HLR, subscriber or live carrier lookup."""
from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass

_E164 = re.compile(r'\+[1-9][0-9]{1,14}\Z')

@dataclass(frozen=True)
class PhoneEvidence:
    valid: bool
    country_code: str | None
    digest: str | None
    reason: str

def inspect_e164(value: str) -> PhoneEvidence:
    if not isinstance(value, str) or _E164.fullmatch(value) is None:
        return PhoneEvidence(False, None, None, 'invalid_e164_syntax')
    return PhoneEvidence(True, None, hashlib.sha256(value.encode('ascii')).hexdigest(), 'syntax_only_no_carrier_or_location')
