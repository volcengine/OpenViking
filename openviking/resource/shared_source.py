# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared-upload SOURCE references for durable resource ingestion.

Unlike :mod:`openviking.resource.staged_source`, a shared upload already lives in
durable VikingFS storage (``viking://upload/...``) with its own TTL cleanup, so
the SOURCE phase does not copy it into a task-owned temp bundle. The API only
validates and records a reference; the worker downloads the content once into a
worker-local file for parsing. Ownership of the shared object stays with the
upload store's TTL policy — the task never deletes it as its own temp.
"""

import asyncio
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict

from openviking.parse.accessors.base import LocalResource, SourceType
from openviking.server.identity import RequestContext

_SHARED_UPLOAD_ROOT = "viking://upload"


@dataclass(frozen=True)
class SharedSource:
    """A validated reference to a shared upload consumed as a SOURCE input."""

    temp_file_id: str
    content_uri: str
    account_id: str
    original_filename: str
    file_ext: str
    meta: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SharedSource":
        if not isinstance(data, dict):
            raise ValueError("shared_source must be an object")
        temp_file_id = data.get("temp_file_id")
        content_uri = data.get("content_uri")
        account_id = data.get("account_id")
        original_filename = data.get("original_filename") or ""
        file_ext = data.get("file_ext") or ""
        meta = data.get("meta")
        if not isinstance(temp_file_id, str) or not temp_file_id.startswith("shared_"):
            raise ValueError("shared_source.temp_file_id must be a shared upload id")
        if not isinstance(content_uri, str) or not content_uri.startswith(
            f"{_SHARED_UPLOAD_ROOT}/"
        ):
            raise ValueError("shared_source.content_uri must live under the upload root")
        if not isinstance(account_id, str) or not account_id:
            raise ValueError("shared_source.account_id must be a non-empty string")
        if not isinstance(original_filename, str):
            raise ValueError("shared_source.original_filename must be a string")
        if not isinstance(file_ext, str):
            raise ValueError("shared_source.file_ext must be a string")
        if meta is not None and not isinstance(meta, dict):
            raise ValueError("shared_source.meta must be an object")
        return cls(
            temp_file_id=temp_file_id,
            content_uri=content_uri.rstrip("/"),
            account_id=account_id,
            original_filename=original_filename,
            file_ext=file_ext,
            meta=dict(meta or {}),
        )


async def materialize_shared_source(
    shared: "SharedSource",
    *,
    viking_fs: Any,
    ctx: RequestContext,
) -> LocalResource:
    """Download a shared upload into a worker-local file for parsing.

    The shared object is owned by the upload store's TTL policy, so the returned
    resource only cleans up the worker-local copy. A missing or expired object
    fails loudly instead of yielding an empty input.
    """
    if not await viking_fs.exists(shared.content_uri, ctx=ctx):
        raise ValueError("Shared upload content is missing or expired")

    suffix = shared.file_ext or Path(shared.original_filename).suffix or ".tmp"
    local_root = Path(tempfile.mkdtemp(prefix="ov_shared_source_"))
    filename = shared.original_filename or f"resource{suffix}"
    local_path = local_root / Path(filename).name
    try:
        content = await viking_fs.read_file_bytes(shared.content_uri, ctx=ctx)
        await asyncio.to_thread(local_path.write_bytes, content)
    except BaseException:
        shutil.rmtree(local_root, ignore_errors=True)
        raise

    meta = dict(shared.meta)
    meta["_cleanup_path"] = str(local_root)
    return LocalResource(
        path=local_path,
        source_type=SourceType.LOCAL,
        original_source=shared.original_filename or shared.temp_file_id,
        meta=meta,
        is_temporary=True,
    )
