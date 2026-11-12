# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Initialize the static agent instructions without extracting them from sessions."""

from typing import Any

from openviking.core.namespace import canonical_user_root, classify_uri
from openviking.server.identity import RequestContext
from openviking.storage.pathlock_transfer import PathLockGuard
from openviking.system_assets import SYSTEM_ASSETS_DIR
from openviking_cli.exceptions import NotFoundError


def is_static_agent_memory(uri: str) -> bool:
    """Recognize agent files that automatic memory updates must leave untouched."""
    parent, _, name = uri.split("?", 1)[0].rstrip("/").rpartition("/")
    if name.casefold() not in {"soul.md", "identity.md"}:
        return False
    parent = parent.replace("viking://~/", "viking://user/_/", 1)
    return classify_uri(parent).is_memory_root


async def ensure_agent_file(viking_fs: Any, ctx: RequestContext, *, lease_ref: Any = None) -> None:
    root = f"{canonical_user_root(ctx)}/memories"
    uri = f"{root}/soul.md"
    try:
        await viking_fs.read_file(uri, ctx=ctx)
    except (FileNotFoundError, NotFoundError):
        pass
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

    content = (SYSTEM_ASSETS_DIR / "memory" / "soul.md").read_text(encoding="utf-8")
    await viking_fs.write_file(uri, content, ctx=ctx, lease_ref=lease_ref)
