# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Single source of truth for the file-content fingerprint used by incremental diff.

The fingerprint is the md5 of the *final stored* file bytes for a resource URI
(after any encoding normalization or link rewriting). Incremental diff compares
this fingerprint on both sides to skip re-processing unchanged files. Every write
path that maintains the fingerprint MUST use :func:`content_md5` so the scheme
stays consistent; it is a content-equality hint, not a security digest.
"""

import hashlib


def content_md5(data: bytes) -> str:
    """Return the hex md5 of the final stored file bytes."""
    return hashlib.md5(data, usedforsecurity=False).hexdigest()
