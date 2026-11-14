# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Explicit resource links declared in a file's YAML frontmatter.

A file names the resources it relates to with a ``related`` list::

    ---
    related:
      - viking://resources/auth/token-validation.md
    ---

The links live in the file's own bytes, so write, move, copy, delete, restore,
export and import carry them exactly like the content they describe; there is
no sidecar to keep in sync. Links are single-hop hints for retrieval only: a
linked resource still has to pass the caller's normal retrieval constraints.
"""

from __future__ import annotations

from typing import List

import yaml

RELATED_KEY = "related"
# Frontmatter is at the head of the file; never read whole documents for links.
FRONTMATTER_READ_BYTES = 8192


def parse_related_links(text: str) -> List[str]:
    """Return the ``viking://`` URIs listed under ``related``, in declared order."""
    if not text.startswith("---\n"):
        return []
    end = text.find("\n---", 4)
    if end < 0:
        return []
    try:
        meta = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return []
    links = meta.get(RELATED_KEY) if isinstance(meta, dict) else None
    if not isinstance(links, list):
        return []
    uris = []
    for link in links:
        if not isinstance(link, str):
            continue
        uri = link.strip().rstrip("/")
        if uri.startswith("viking://") and len(uri) > len("viking://"):
            uris.append(uri)
    return uris
