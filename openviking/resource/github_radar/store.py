# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Radar-local two-file persistence using ordinary user access."""

from datetime import timedelta
from typing import Any

from openviking.resource.github_radar.models import PullRequest, RadarConfig, RadarState, utc
from openviking.resource.watch_types import WatchTask
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier


class RadarStore:
    def __init__(self, fs: Any, task: WatchTask) -> None:
        self.fs = fs
        self.ctx = RequestContext(
            user=UserIdentifier(task.account_id, task.user_id), role=Role.USER, bypass_acl=False
        )
        self.root = f"viking://user/{task.user_id}/resources/radar/{task.task_id}"
        self.task = task

    async def load(
        self, repository: str, config: RadarConfig
    ) -> tuple[RadarState, list[PullRequest]]:
        state_raw = await self._read_optional(".radar-state.json")
        queue_raw = await self._read_optional(".radar-signals.jsonl")
        queue = [
            PullRequest.model_validate_json(line)
            for line in (queue_raw or b"").splitlines()
            if line.strip()
        ]
        if state_raw is None:
            if queue_raw is not None:
                raise RuntimeError("Radar state is missing while its queue exists.")
            anchor = utc(self.task.created_at)
            start = anchor - timedelta(days=config.backfill_days)
            state = RadarState(
                repository=repository, anchor=anchor, backfill_start=start, reported_until=start
            )
            # Establish state before any queue writes, so an interrupted first
            # collection never looks like a fresh Watch.
            await self.write_state(state)
        else:
            state = RadarState.model_validate_json(state_raw)
            if state.repository != repository:
                raise RuntimeError("Radar repository changed; create a new Watch.")
            if queue_raw is None and state.seen_numbers and state.pending_publish is None:
                raise RuntimeError("Radar queue is missing; refusing to reset progress.")
        if any(pr.repository != repository for pr in queue):
            raise RuntimeError("Radar queue contains a different repository.")
        # A crash after queue persistence but before progress persistence is
        # recovered by treating those facts as already collected.
        state.seen_numbers = sorted(set(state.seen_numbers) | {pr.number for pr in queue})
        return state, queue

    async def _read_optional(self, name: str) -> bytes | None:
        try:
            return await self.fs.read(f"{self.root}/{name}", ctx=self.ctx)
        except Exception as exc:
            if is_not_found_error(exc):
                return None
            raise

    async def write_state(self, state: RadarState) -> None:
        await self.fs.write_file(
            f"{self.root}/.radar-state.json", state.model_dump_json(), ctx=self.ctx
        )

    async def write_queue(self, queue: list[PullRequest]) -> None:
        data = "".join(pr.model_dump_json() + "\n" for pr in queue)
        await self.fs.write_file(f"{self.root}/.radar-signals.jsonl", data, ctx=self.ctx)

    async def save_collection(self, state: RadarState, queue: list[PullRequest]) -> None:
        await self.write_queue(queue)
        await self.write_state(state)

    async def publish(self, state: RadarState) -> RadarState:
        pending = state.pending_publish
        if pending is None:
            return state
        final = RadarState.model_validate(pending.state_after)
        if final.pending_publish is not None or final.repository != state.repository:
            raise RuntimeError("Invalid Radar publication checkpoint.")
        # IDs originate here, but reject corrupt persisted paths before writing.
        if not pending.report_id or any(c not in "0123456789abcdef-" for c in pending.report_id):
            raise RuntimeError("Invalid Radar report ID.")
        for name in (f"reports/{pending.report_id}.md", "latest.md"):
            await self.fs.write_file(f"{self.root}/{name}", pending.content, ctx=self.ctx)
        await self.write_queue(pending.queue_after)
        # Keep the recovery record until both queue and final progress persist.
        recovering = final.model_copy(update={"pending_publish": pending})
        await self.write_state(recovering)
        await self.write_state(final)
        return final
