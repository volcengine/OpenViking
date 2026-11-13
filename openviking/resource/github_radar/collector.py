# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Bounded gh GraphQL merged-PR searches with durable range progress."""

import asyncio
import json
import os
from datetime import timedelta
from typing import Any, Awaitable, Callable

from openviking.resource.github_radar.models import (
    PullRequest,
    QueryRange,
    RadarConfig,
    RadarState,
    utc,
)

QUERY = """
query($query: String!, $cursor: String) {
  search(query: $query, type: ISSUE, first: 100, after: $cursor) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number title url body state isDraft createdAt updatedAt closedAt mergedAt
        repository { nameWithOwner }
        author { login }
        labels(first: 100) { nodes { name } pageInfo { hasNextPage } }
      }
    }
  }
}
"""


class GitHubCollector:
    REQUEST_LIMIT = 32
    REQUEST_TIMEOUT = 30

    def __init__(self, token: str | None) -> None:
        # Explicit env auth prevents gh from consulting macOS Keychain.
        self.token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

    async def search(self, repository: str, span: QueryRange, cursor: str | None) -> dict[str, Any]:
        if not self.token:
            raise RuntimeError("Radar requires an account GitHub token or GH_TOKEN/GITHUB_TOKEN.")
        query = (
            f"repo:{repository} is:pr is:merged "
            f"merged:{span.start.isoformat()}..{span.end.isoformat()} sort:updated-asc"
        )
        payload = json.dumps({"query": QUERY, "variables": {"query": query, "cursor": cursor}})
        env = dict(os.environ, GH_TOKEN=self.token, GH_PROMPT_DISABLED="1")
        process = await asyncio.create_subprocess_exec(
            "gh",
            "api",
            "--hostname",
            "github.com",
            "graphql",
            "--input",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(payload.encode()), timeout=self.REQUEST_TIMEOUT
            )
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise
        if process.returncode:
            # gh stderr can contain sensitive request details; expose only status.
            raise RuntimeError(f"Radar gh api failed (exit {process.returncode}).")
        result = json.loads(stdout)
        if result.get("errors"):
            raise RuntimeError("Radar GitHub GraphQL returned errors.")
        return result["data"]["search"]

    @staticmethod
    def parse(node: dict[str, Any], repository: str) -> PullRequest:
        if (
            not node
            or node.get("state") != "MERGED"
            or not node.get("mergedAt")
            or node["repository"]["nameWithOwner"].lower() != repository
        ):
            raise RuntimeError("Radar search returned an unexpected PR.")
        if node["labels"]["pageInfo"]["hasNextPage"]:
            raise RuntimeError("Radar PR labels exceeded the supported page size.")
        number = node["number"]
        url = node["url"]
        if url.lower() != f"https://github.com/{repository}/pull/{number}":
            raise RuntimeError("Radar search returned an unexpected PR URL.")
        return PullRequest(
            repository=repository,
            number=number,
            title=node["title"],
            url=url,
            author=(node.get("author") or {}).get("login", "[deleted]"),
            draft=node["isDraft"],
            labels=[label["name"] for label in node["labels"]["nodes"]],
            body=node["body"],
            created_at=node["createdAt"],
            updated_at=node["updatedAt"],
            closed_at=node["closedAt"],
            merged_at=node["mergedAt"],
        )

    async def collect(
        self,
        state: RadarState,
        queue: list[PullRequest],
        config: RadarConfig,
        now: Any,
        save: Callable[[RadarState, list[PullRequest]], Awaitable[None]],
    ) -> bool:
        if state.scan_end is None:
            end = state.anchor if not state.backfill_done else utc(now)
            start = state.backfill_start
            if state.collected_until is not None:
                start = max(
                    start, state.collected_until - timedelta(minutes=config.query_overlap_minutes)
                )
            state.scan_end = end
            state.ranges = [QueryRange(start=start, end=end)]
            await save(state, queue)
        seen = set(state.seen_numbers)
        added = 0
        requests = 0
        while state.ranges and requests < self.REQUEST_LIMIT:
            span = state.ranges[0]
            cursor = None
            observed: set[int] = set()
            while requests < self.REQUEST_LIMIT:
                page = await self.search(state.repository, span, cursor)
                requests += 1
                count = page["issueCount"]
                if count > 1000:
                    if span.start >= span.end:
                        raise RuntimeError("Radar search exceeds 1000 PRs in one second.")
                    seconds = int((span.end - span.start).total_seconds())
                    mid = span.start + timedelta(seconds=seconds // 2)
                    state.ranges[:1] = [
                        QueryRange(start=span.start, end=mid),
                        QueryRange(start=mid + timedelta(seconds=1), end=span.end),
                    ]
                    await save(state, queue)
                    break
                for node in page["nodes"]:
                    pr = self.parse(node, state.repository)
                    if not span.start <= pr.merged_at <= span.end:
                        raise RuntimeError("Radar search returned an out-of-range merge time.")
                    observed.add(pr.number)
                    if pr.number in seen:
                        continue
                    if added >= config.max_prs_per_run:
                        await save(state, queue)
                        return False
                    queue.append(pr)
                    seen.add(pr.number)
                    state.seen_numbers = sorted(seen)
                    added += 1
                await save(state, queue)
                info = page["pageInfo"]
                if not info["hasNextPage"]:
                    if len(observed) < count:
                        raise RuntimeError("Radar search ended before all results were read.")
                    state.ranges.pop(0)
                    await save(state, queue)
                    break
                if not info["endCursor"] or info["endCursor"] == cursor:
                    raise RuntimeError("Radar search pagination did not advance.")
                cursor = info["endCursor"]
        if state.ranges:
            return False
        state.collected_until = state.scan_end
        state.scan_end = None
        state.last_error = None
        await save(state, queue)
        return True
