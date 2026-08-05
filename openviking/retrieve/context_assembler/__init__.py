# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Server-side context assembly kernel shared by /search and /recall."""

from openviking.retrieve.context_assembler.models import AssembledEntry, AssembleResult
from openviking.retrieve.context_assembler.params import (
    CATEGORY_KEYS,
    DEFAULT_LIMIT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_OTHER_PEER_PENALTIES,
    DEFAULT_QUOTAS,
    DEFAULT_TIER_BY_CATEGORY,
    MAX_EXCLUDE_URIS,
    MEMORY_CATEGORIES,
    PURPOSE_PRESETS,
    AssembleParams,
    DetailRequest,
    normalize_detail,
    normalize_penalties,
    normalize_quotas,
)
from openviking.retrieve.context_assembler.pipeline import assemble_context
from openviking.retrieve.context_assembler.render import render_context, render_entry
from openviking.retrieve.context_assembler.rewrite import normalize_digest, server_rewrite_enabled

__all__ = [
    "AssembleParams",
    "AssembleResult",
    "AssembledEntry",
    "CATEGORY_KEYS",
    "DEFAULT_LIMIT",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_OTHER_PEER_PENALTIES",
    "DEFAULT_QUOTAS",
    "DEFAULT_TIER_BY_CATEGORY",
    "DetailRequest",
    "MAX_EXCLUDE_URIS",
    "MEMORY_CATEGORIES",
    "PURPOSE_PRESETS",
    "assemble_context",
    "normalize_detail",
    "normalize_digest",
    "normalize_penalties",
    "normalize_quotas",
    "render_context",
    "render_entry",
    "server_rewrite_enabled",
]
