# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Run Radar without changing source Watch results or public ACL contracts."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from openviking.resource.github_radar.collector import GitHubCollector
from openviking.resource.github_radar.models import (
    PendingPublish,
    github_repository,
    utc,
    validate_radar,
)
from openviking.resource.github_radar.render import render
from openviking.resource.github_radar.store import RadarStore
from openviking.resource.github_radar.summarizer import RadarSummarizer
from openviking.resource.watch_types import WatchTask
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


async def run_radar(task: WatchTask, fs: Any, processor: Any) -> None:
    config = validate_radar(
        task.path,
        task.processor_kwargs,
        watch_interval=task.watch_interval,
    )
    if config is None or not config.enabled:
        return
    repository = github_repository(task.path)
    store = RadarStore(fs, task)
    state, queue = await store.load(repository, config)
    try:
        if state.pending_publish is not None:
            await store.publish(state)
            logger.info("Radar %s resumed publication at %s/latest.md", task.task_id, store.root)
            return
        collector = GitHubCollector()
        now = utc(datetime.now(timezone.utc))
        complete = await collector.collect(state, queue, config, now, store.save_collection)
        if not complete:
            logger.info("Radar %s retained %s PRs; collection incomplete", task.task_id, len(queue))
            return
        start = state.reported_until
        end = (
            state.anchor
            if not state.backfill_done
            else start + timedelta(minutes=config.digest_interval_minutes)
        )
        if end > now or state.collected_until is None or end > state.collected_until:
            return
        selected = [
            pr
            for pr in queue
            if (pr.merged_at >= start if not state.backfill_done else pr.merged_at > start)
            and pr.merged_at <= end
        ]
        # A late-indexed merge in an already closed window must not silently
        # appear in a different window or disappear when the queue is pruned.
        late = [pr for pr in queue if state.backfill_done and pr.merged_at <= start]
        if late:
            raise RuntimeError(
                "Radar found newly indexed PRs in a published window; retained for inspection."
            )
        after_queue = [pr for pr in queue if pr not in selected]
        final = state.model_copy(deep=True)
        final.reported_until = end
        final.backfill_done = True
        final.last_error = None
        if not selected:
            await store.save_collection(final, after_queue)
            logger.info("Radar %s closed empty window ending %s", task.task_id, end.isoformat())
            return
        summary = await RadarSummarizer(
            getattr(processor, "vlm_resolver", None), task.account_id
        ).summarize(selected, config)
        report_id = str(uuid.uuid4())
        final.last_report_id = report_id
        content = render(
            repository, start, end, now, selected, summary, backfill=not state.backfill_done
        )
        state.pending_publish = PendingPublish(
            report_id=report_id,
            content=content,
            queue_after=after_queue,
            state_after=final.model_dump(mode="json"),
        )
        await store.write_state(state)
        await store.publish(state)
        logger.info(
            "Radar %s published %s PRs (%s) at %s/reports/%s.md",
            task.task_id,
            len(selected),
            summary.mode,
            store.root,
            report_id,
        )
    except Exception:
        # Persist a safe diagnostic, preserving both raw facts and any publication
        # checkpoint. Storage/read failures are never interpreted as fresh state.
        state.last_error = "Radar execution failed; consult the server log for the failure stage."
        try:
            await store.save_collection(state, queue)
        except Exception:
            logger.warning("Radar %s could not persist failure status", task.task_id)
        raise
