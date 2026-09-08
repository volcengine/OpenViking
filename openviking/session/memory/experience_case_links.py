# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Keep public Case links in sync with Experience lifecycle state.

Draft provenance lives in EXP -> trajectory -> Case, not in a public Case link.
This lets promotion restore links to earlier Cases without exposing drafts.
"""

from __future__ import annotations

import asyncio
import logging
import random
from time import monotonic
from typing import Any

from openviking.server.error_mapping import is_not_found_error
from openviking.session.memory.dataclass import MemoryFile, StoredLink
from openviking.session.memory.experience_lifecycle import (
    experience_case_link_uris,
    experience_is_case_linkable,
    experience_source_trajectory_uris,
)
from openviking.session.memory.memory_type_registry import create_default_registry
from openviking.session.memory.merge_op.link_merge import merge_links
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage.errors import LockAcquisitionError

# Match policy/memory apply's bounded wait; never add a global commit mutex.
CASE_LINK_LOCK_TIMEOUT_SECONDS = 300.0
_LOCK_ATTEMPT_SECONDS = 0.05
_LOCK_RETRY_INITIAL_SECONDS = 0.01
_LOCK_RETRY_MAX_SECONDS = 0.25
logger = logging.getLogger(__name__)


async def acquire_case_link_lease(
    lock_client: Any, paths: list[str], *, deadline: float | None = None
) -> Any:
    """Wait for endpoint locks without monopolizing AGFS's shared thread pool.

    Native acquisition, file I/O and release all use the same worker pool. Use
    short native attempts and sleep asynchronously between conflicts so the
    holder can still finish its writes and release. Each attempt retains a brief
    native retry window; longer waits happen in the event loop. All attempts and
    coverage expansion share one absolute deadline.
    """

    if deadline is None:
        deadline = monotonic() + CASE_LINK_LOCK_TIMEOUT_SECONDS
    paths = sorted(set(paths))
    retry_delay = _LOCK_RETRY_INITIAL_SECONDS
    last_conflict = None
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("Case/Experience link lock wait budget exhausted") from last_conflict
        try:
            return await _acquire_case_link_once(
                lock_client, paths, timeout=min(remaining, _LOCK_ATTEMPT_SECONDS)
            )
        except LockAcquisitionError as exc:
            last_conflict = exc
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("Case/Experience link lock wait budget exhausted") from last_conflict
        await asyncio.sleep(min(remaining, random.uniform(retry_delay / 2, retry_delay)))
        retry_delay = min(retry_delay * 2, _LOCK_RETRY_MAX_SECONDS)


async def _acquire_case_link_once(lock_client: Any, paths: list[str], *, timeout: float) -> Any:
    """Drain one native attempt if its caller is cancelled.

    Native acquisition runs in a worker thread, so cancellation cannot stop it.
    Drain the bounded acquisition and release any late lease before propagating
    cancellation; callers have not entered their release-finally block yet.
    """

    acquisition = asyncio.create_task(
        lock_client.pathlock_acquire_exact_batch(paths, timeout_secs=timeout)
    )
    try:
        return await asyncio.shield(acquisition)
    except asyncio.CancelledError:

        async def release_abandoned_lease() -> None:
            try:
                lease = await acquisition
            except (asyncio.CancelledError, Exception):
                return  # Failed acquisition has no lease to release.
            try:
                await release_case_link_lease(lock_client, lease)
            except Exception:
                logger.exception("Failed to release cancelled Case/Experience link lease")

        cleanup = asyncio.create_task(release_abandoned_lease())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue  # A second cancellation must not interrupt lease cleanup.
        cleanup.result()
        raise


async def release_case_link_lease(lock_client: Any, lease: Any) -> None:
    """Finish releasing even if cancellation arrives before the worker starts.

    Cancelling an unshielded to_thread call can discard a queued release. The
    native lease would then outlive the commit that owned it. Propagate cancel
    only after the release completes, including repeated cancellation requests.
    """
    release = asyncio.create_task(lock_client.pathlock_release(lease))
    try:
        await asyncio.shield(release)
    except asyncio.CancelledError:
        while not release.done():
            try:
                await asyncio.shield(release)
            except asyncio.CancelledError:
                continue
        release.result()
        raise


async def sync_experience_case_links(
    experience_uri: str,
    *,
    viking_fs: Any,
    ctx: Any,
    candidate_case_uris: set[str] | None = None,
) -> None:
    """Add promoted links, or remove non-promoted links, under endpoint locks.

    Read again after acquiring locks; a concurrent promotion/degradation or newly
    attached source must not be overwritten using an old snapshot. No directory
    scan, lifecycle change, or content-read policy change is involved.
    """

    async def read_optional(uri: str) -> MemoryFile | None:
        try:
            raw = await viking_fs.read_file(uri, ctx=ctx)
        except Exception as exc:
            if is_not_found_error(exc):
                return None
            raise
        return MemoryFileUtils.read(raw, uri=uri) if raw else None

    async def discover() -> tuple[MemoryFile | None, set[str], set[str]]:
        experience = await read_optional(experience_uri)
        if experience is None:
            return None, set(), set()
        cases = set(candidate_case_uris or ())
        cases.update(experience_case_link_uris(experience.backlinks, experience_uri=experience_uri))
        trajectories = experience_source_trajectory_uris(experience.links)
        for uri in sorted(trajectories):
            trajectory = await read_optional(uri)
            if trajectory is not None:
                cases.update(experience_case_link_uris(trajectory.backlinks, experience_uri=uri))
        return experience, cases, trajectories

    experience, cases, trajectories = await discover()
    if experience is None or not cases:
        return
    lock_client = getattr(viking_fs, "_async_agfs", None)
    if lock_client is None or not callable(getattr(viking_fs, "_uri_to_path", None)):
        raise RuntimeError("Case/Experience link synchronization requires endpoint path locks")
    required = {experience_uri, *cases, *trajectories}
    deadline = monotonic() + CASE_LINK_LOCK_TIMEOUT_SECONDS
    for _ in range(3):
        lease = await acquire_case_link_lease(
            lock_client,
            [viking_fs._uri_to_path(uri, ctx=ctx) for uri in required],
            deadline=deadline,
        )
        try:
            experience, cases, trajectories = await discover()
            if experience is None:
                return
            expanded = required | cases | trajectories
            if expanded != required:
                required = expanded
                continue
            await _write_case_links(
                experience,
                cases,
                read_optional=read_optional,
                viking_fs=viking_fs,
                ctx=ctx,
                lease=lease,
            )
            return
        finally:
            await release_case_link_lease(lock_client, lease)
    raise RuntimeError("Case/Experience link lock coverage changed repeatedly; retry required")


async def _write_case_links(
    experience: MemoryFile,
    case_uris: set[str],
    *,
    read_optional: Any,
    viking_fs: Any,
    ctx: Any,
    lease: Any,
) -> None:
    promoted = experience_is_case_linkable(experience.extra_fields.get("status"))
    schema = create_default_registry().get("cases")
    case_updates: list[tuple[MemoryFile, str]] = []
    new_links: list[dict[str, Any]] = []
    for uri in sorted(case_uris):
        case = await read_optional(uri)
        if case is None:
            continue
        original_content = MemoryFileUtils.write(case)
        retained = [link for link in case.links if link.get("to_uri") != experience.uri]
        if promoted:
            existing = [link for link in case.links if link.get("to_uri") == experience.uri]
            links = existing or [
                StoredLink(
                    from_uri=uri,
                    to_uri=experience.uri,
                    link_type="related_to",
                    weight=1.0,
                ).model_dump()
            ]
            new_links.extend(links)
            retained = merge_links(retained, links)
        case.links = retained
        rendered = MemoryFileUtils.write(case, content_template=schema.content_template)
        # Rewrite the template even for stale visible links not present in metadata.
        if rendered != original_content:
            case_updates.append((case, rendered))

    original_backlinks = list(experience.backlinks)
    retained_backlinks = [
        link
        for link in original_backlinks
        if not (link.get("from_uri") in case_uris and link.get("to_uri") == experience.uri)
    ]
    experience.backlinks = merge_links(retained_backlinks, new_links)

    async def write_backlinks() -> None:
        if experience.backlinks != original_backlinks:
            # Reconciliation changes links only, not the EXP policy content.
            await viking_fs.write_file(
                experience.uri, MemoryFileUtils.write(experience), ctx=ctx, lease_ref=lease
            )

    if promoted:
        # Never publish a forward link before its backlink is durable.
        await write_backlinks()
    for case, rendered in case_updates:
        await viking_fs.write_file(case.uri, rendered, ctx=ctx, lease_ref=lease)
    if not promoted:
        # Keep backlinks until all removals succeed so failures remain retryable.
        await write_backlinks()
