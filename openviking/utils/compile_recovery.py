"""Fingerprint the exact set of sources in an incremental recovery plan."""

import hashlib
import json
from collections.abc import Iterable


def source_set_digest(uris: Iterable[str]) -> str:
    return hashlib.sha256(json.dumps(sorted(set(uris)), ensure_ascii=False).encode()).hexdigest()
