# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Archive-source records in the shared context collection."""

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from openviking.core.namespace import canonical_session_root
from openviking.server.identity import RequestContext
from openviking.storage.expr import FilterExpr, RawDSL
from openviking.utils.path_safety import validate_safe_viking_uri_path
from openviking_cli.exceptions import InvalidArgumentError

ARCHIVE_SOURCE_RECORD_TYPE = "archive_source"
ARCHIVE_SOURCE_CONTEXT_TYPE = "memory"
MAX_ARCHIVE_SOURCE_CHARS = 4096
EXCLUDE_ARCHIVE_SOURCES = RawDSL(
    {"op": "must_not", "field": "type", "conds": [ARCHIVE_SOURCE_RECORD_TYPE]}
)


def archive_source_exclusion(context_type: str | None) -> FilterExpr | None:
    """Exclude sources when the ordinary search can match memory records."""
    if not context_type or context_type == ARCHIVE_SOURCE_CONTEXT_TYPE:
        return EXCLUDE_ARCHIVE_SOURCES
    return None


def is_archive_source_uri(uri: str, ctx: RequestContext) -> bool:
    """Check for a canonical messages.jsonl path under the caller's Session root."""
    prefix = canonical_session_root(ctx) + "/"
    if not uri.startswith(prefix):
        return False
    try:
        if validate_safe_viking_uri_path(uri) != uri:
            return False
    except ValueError:
        return False
    session_id, _, archive_path = uri[len(prefix) :].partition("/")
    return session_id not in {"", ".", ".."} and bool(
        re.fullmatch(r"history/archive_[0-9]+/messages\.jsonl", archive_path)
    )


def make_archive_source_record(
    *,
    archive_uri: str,
    excerpt: str,
    ctx: RequestContext,
) -> dict[str, Any]:
    if not is_archive_source_uri(archive_uri, ctx):
        raise InvalidArgumentError("Archive source must reference the caller's archived messages")
    if len(excerpt) > MAX_ARCHIVE_SOURCE_CHARS or not excerpt.strip():
        raise InvalidArgumentError("Invalid archive source excerpt")

    identity = json.dumps(
        [ctx.account_id, archive_uri, excerpt],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": "archive_source_" + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "uri": archive_uri,
        "type": ARCHIVE_SOURCE_RECORD_TYPE,
        "context_type": ARCHIVE_SOURCE_CONTEXT_TYPE,
        "level": 2,
        "is_leaf": True,
        "abstract": excerpt,
        "account_id": ctx.account_id,
        "owner_user_id": ctx.user.user_id,
        "created_at": now,
        "updated_at": now,
        "active_count": 0,
    }


def archive_source_hit(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_uri": record["uri"],
        "excerpt": record["abstract"],
        "score": record.get("_score", 0.0),
    }
