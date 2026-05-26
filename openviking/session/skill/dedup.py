# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Lightweight batch dedup for session-derived skill operations."""

import re
from typing import Any, Optional

from openviking.session.memory.dataclass import ResolvedOperations
from openviking_cli.utils import get_logger

logger = get_logger(__name__)
_WHITESPACE_RE = re.compile(r"\s+")


def dedup_session_skill_operations(operations: ResolvedOperations) -> ResolvedOperations:
    seen_signatures: dict[str, int] = {}
    deduped_operations = []
    duplicate_count = 0

    for op in operations.upsert_operations:
        signature = _build_session_skill_signature(op)
        if not signature:
            deduped_operations.append(op)
            continue

        existing_idx = seen_signatures.get(signature)
        if existing_idx is None:
            seen_signatures[signature] = len(deduped_operations)
            deduped_operations.append(op)
            continue

        duplicate_count += 1
        kept_op = deduped_operations[existing_idx]
        logger.info(
            "Deduplicated duplicate session skill candidate '%s' against '%s'",
            _skill_name(op),
            _skill_name(kept_op),
        )

    if duplicate_count == 0:
        return operations

    logger.info("Deduplicated %d duplicate session skill operations in batch", duplicate_count)
    return ResolvedOperations(
        upsert_operations=deduped_operations,
        delete_file_contents=operations.delete_file_contents,
        errors=operations.errors,
    )


def _build_session_skill_signature(op) -> Optional[str]:
    if op.memory_type != "session_skills" or op.old_memory_file_content is not None:
        return None

    full_body = _extract_full_body(op.memory_fields.get("content"))
    if not full_body:
        return None

    normalized_body = _normalize_text(full_body)
    if not normalized_body:
        return None

    return f"session_skill_body:{normalized_body}"


def _extract_full_body(content: Any) -> Optional[str]:
    if isinstance(content, str):
        return content

    if not isinstance(content, dict):
        return None

    blocks = content.get("blocks")
    if not isinstance(blocks, list) or len(blocks) != 1 or not isinstance(blocks[0], dict):
        return None

    search = str(blocks[0].get("search", "") or "")
    replace = str(blocks[0].get("replace", "") or "")
    if search.strip() or not replace.strip():
        return None

    return replace


def _normalize_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value).strip().casefold()


def _skill_name(op) -> str:
    return str(op.memory_fields.get("skill_name", "")).strip() or "<unknown>"
