# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Union native retrieval and trigger hits, rank by reciprocal rank fusion."""

import asyncio

from openviking.server.error_mapping import is_not_found_error
from openviking.session.memory.utils.content_visibility import visible_content
from openviking_cli.exceptions import PermissionDeniedError


async def fuse_memory_candidates(primary, triggered, *, fs, ctx):
    # Both lanes point to the SAME canonical namespace. URI is the identity; never
    # collapse different dated events just because their summaries look similar.
    by_uri = {row["uri"]: row for row in [*triggered, *primary] if row.get("uri")}
    scores = {}
    for lane in (primary, triggered):
        seen = set()
        for position, row in enumerate(lane, 1):
            uri = row.get("uri")
            if uri and uri not in seen:
                scores[uri] = scores.get(uri, 0.0) + 1 / (60 + position)
                seen.add(uri)

    async def evidence(uri):
        row = by_uri[uri]
        if row.get("level", 2) != 2:
            return row
        try:
            # ACL is checked at the file read too, including for original-lane hits.
            body = visible_content(await fs.read_file(uri, ctx=ctx), uri=uri)
        except Exception as exc:
            if is_not_found_error(exc) or isinstance(exc, (PermissionError, PermissionDeniedError)):
                return None
            raise
        return {**row, "abstract": body} if body.strip() else None

    rows = await asyncio.gather(
        *(evidence(uri) for uri in sorted(by_uri, key=lambda u: -scores[u]))
    )
    rows = [row for row in rows if row is not None]
    if not rows:
        return []
    # RRF is a rank score, not cosine similarity or a model relevance score.
    return sorted(
        [{**row, "_score": scores[row["uri"]], "_final_score": scores[row["uri"]]} for row in rows],
        key=lambda row: (-row["_final_score"], row["uri"]),
    )
