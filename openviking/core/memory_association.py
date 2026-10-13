# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Reserved derived-memory paths; no NLP or runtime dependencies."""

from openviking.core.namespace import classify_uri


def memory_root(uri: str) -> str | None:
    if not uri.startswith("viking://"):
        return None
    classification = classify_uri(uri)
    if not classification.is_memory or classification.content_index is None:
        return None
    return "viking://" + "/".join(classification.parts[: classification.content_index + 1])


def is_association_uri(uri: str) -> bool:
    root = memory_root(uri)
    return bool(root and (uri == root + "/.association" or uri.startswith(root + "/.association/")))


def is_memory_source(uri: str) -> bool:
    return bool(
        memory_root(uri)
        and not is_association_uri(uri)
        and uri.endswith(".md")
        and not uri.rsplit("/", 1)[-1].startswith(".")
    )
