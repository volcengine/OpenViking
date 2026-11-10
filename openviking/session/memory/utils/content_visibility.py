# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Public-content projection for persisted memory files."""

from openviking.core.namespace import classify_uri
from openviking.session.memory.utils.memory_fields import strip_memory_fields_trailer


def visible_content(
    raw_content: str,
    *,
    uri: str,
    offset: int = 0,
    limit: int = -1,
) -> str:
    """Hide a memory file's reserved metadata trailer, then apply line slicing.

    MEMORY_FIELDS is a reserved trailer only in memory namespaces. Resource and
    skill files may legitimately contain similarly shaped HTML comments, so
    their content is returned verbatim apart from the requested line slice.
    """
    content = raw_content
    if classify_uri(uri).is_memory:
        content = strip_memory_fields_trailer(content)

    if offset == 0 and limit == -1:
        return content
    lines = content.splitlines(keepends=True)
    sliced = lines[offset:] if limit == -1 else lines[offset : offset + limit]
    return "".join(sliced)
