# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Read-only directory retention queries."""

from openviking.core.ttl import ttl_scope_for_uri
from openviking.storage.ttl_view import TTLView
from openviking_cli.exceptions import InvalidArgumentError


async def get_document_ttl(fs, uri, *, ctx):
    stat = await fs.stat(uri, ctx=ctx)
    if ttl_scope_for_uri(uri) is None:
        raise InvalidArgumentError("TTL only supports events and sessions")
    return {"uri": uri, **await TTLView(fs, ctx).fields(uri, is_dir=stat.get("isDir", False))}
