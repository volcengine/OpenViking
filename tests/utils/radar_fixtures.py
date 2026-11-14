# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Small deterministic Radar inputs and storage fault injection."""

from datetime import datetime, timedelta, timezone

from openviking.resource.github_radar.models import PullRequest, RadarConfig, RadarState
from openviking.resource.watch_types import WatchTask

ANCHOR = datetime(2026, 11, 14, tzinfo=timezone.utc)
REPO = "volcengine/openviking"


def config(**values):
    return RadarConfig(enabled=True, digest_interval_minutes=30, **values)


def task(**values):
    return WatchTask(
        path="https://github.com/volcengine/OpenViking",
        account_id="tenant",
        user_id="alice",
        created_at=ANCHOR,
        processor_kwargs={"pr_radar": config().model_dump()},
        **values,
    )


def state():
    start = ANCHOR - timedelta(days=7)
    return RadarState(repository=REPO, anchor=ANCHOR, backfill_start=start, reported_until=start)


def pr(number=1, merged_at=None):
    when = merged_at or ANCHOR - timedelta(hours=1)
    return PullRequest(
        repository=REPO,
        number=number,
        title=f"PR {number}",
        url=f"https://github.com/volcengine/OpenViking/pull/{number}",
        author="author",
        created_at=when,
        updated_at=when,
        closed_at=when,
        merged_at=when,
    )


def node(number=1, merged_at=None):
    item = pr(number, merged_at)
    return {
        "number": item.number,
        "title": item.title,
        "url": item.url,
        "body": "PR description",
        "state": "MERGED",
        "isDraft": False,
        "createdAt": item.created_at.isoformat(),
        "updatedAt": item.updated_at.isoformat(),
        "closedAt": item.closed_at.isoformat(),
        "mergedAt": item.merged_at.isoformat(),
        "repository": {"nameWithOwner": "volcengine/OpenViking"},
        "author": {"login": "author"},
        "labels": {"nodes": [], "pageInfo": {"hasNextPage": False}},
    }


def page(nodes, *, count=None, cursor=None):
    return {
        "issueCount": len(nodes) if count is None else count,
        "nodes": nodes,
        "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor},
    }


class MemoryFS:
    """Per-test storage, with one-shot failures before or after a write."""

    def __init__(self):
        self.files = {}
        self.contexts = []
        self.fail_at = None
        self.fail_after_write = False
        self.writes = 0
        self._async_agfs = self

    async def _ensure_access(self, uri, ctx, *, action):
        self.contexts.append(ctx)

    def _uri_to_path(self, uri, *, ctx):
        return uri

    async def mv(self, old_path, new_path, *, fs_ctx):
        self.files[new_path] = self.files.pop(old_path)

    async def read(self, uri, *, ctx):
        self.contexts.append(ctx)
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return self.files[uri]

    async def write_file(self, uri, content, *, ctx):
        self.contexts.append(ctx)
        self.writes += 1
        fail = self.writes == self.fail_at
        if fail and not self.fail_after_write:
            raise OSError("injected write failure")
        self.files[uri] = content.encode() if isinstance(content, str) else content
        if fail:
            raise OSError("injected write failure")
