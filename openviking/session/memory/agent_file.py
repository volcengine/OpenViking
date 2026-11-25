# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Initialize the static agent instructions without extracting them from sessions."""

from typing import Any

from openviking.core.namespace import canonical_user_root, classify_uri
from openviking.server.identity import RequestContext
from openviking.storage.pathlock_transfer import PathLockGuard
from openviking.system_assets import SYSTEM_ASSETS_DIR
from openviking_cli.exceptions import NotFoundError

SOUL_FILENAME = "SOUL.md"
LEGACY_SOUL_PATH = "memories/soul.md"


def is_static_agent_memory(uri: str) -> bool:
    """Recognize agent files that automatic memory updates must leave untouched."""
    parent, _, name = uri.split("?", 1)[0].rstrip("/").rpartition("/")
    if name.casefold() not in {"soul.md", "identity.md"}:
        return False
    parent = (
        "viking://user/_"
        if parent == "viking://~"
        else parent.replace("viking://~/", "viking://user/_/", 1)
    )
    classification = classify_uri(parent)
    return classification.is_memory_root or (
        name == SOUL_FILENAME and classification.is_user_namespace_root
    )


async def ensure_agent_file(viking_fs: Any, ctx: RequestContext, *, lease_ref: Any = None) -> None:
    root = canonical_user_root(ctx)
    for path in (SOUL_FILENAME, LEGACY_SOUL_PATH):
        try:
            await viking_fs.read_file(f"{root}/{path}", ctx=ctx)
        except (FileNotFoundError, NotFoundError):
            continue
        else:
            return

    if lease_ref is None:
        guard = PathLockGuard(viking_fs._async_agfs)
        try:
            path = viking_fs._uri_to_path(root, ctx=ctx)
            lease = await guard.acquire(
                lambda: viking_fs._async_agfs.pathlock_acquire_tree(path, timeout_secs=10.0)
            )
            await ensure_agent_file(viking_fs, ctx, lease_ref=lease)
        finally:
            await guard.close()
        return

    uri = f"{root}/{SOUL_FILENAME}"
    content = (SYSTEM_ASSETS_DIR / "agent" / SOUL_FILENAME).read_text(encoding="utf-8")
    await viking_fs.write_file(uri, content, ctx=ctx, lease_ref=lease_ref)
