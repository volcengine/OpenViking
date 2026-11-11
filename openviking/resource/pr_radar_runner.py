# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Watch sidecar that publishes GitHub PR radar reports.

The runner is invoked once per watch trigger *before* the content-ingestion
short-circuit (a watch whose git HEAD is unchanged returns early and never
re-enters resource ingestion). It is strictly best-effort: every failure is
caught and reported back as a skipped outcome, so radar can never fail the
hosting watch run.

State lives next to the reports under the radar directory rather than in the
watch checkpoint, because the unchanged-HEAD path does not persist checkpoints.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from openviking.integrations.github_radar.collector import collect_prs
from openviking.integrations.github_radar.gh_cli import GhCli, GhCliError, GhCliUnavailable, have_gh
from openviking.integrations.github_radar.models import (
    LATEST_REPORT_NAME,
    REPORTS_DIRNAME,
    STATE_FILENAME,
    RadarConfig,
    RadarState,
    escape_repo_slug,
)
from openviking.integrations.github_radar.report import render_report, report_stamp
from openviking.server.error_mapping import is_not_found_error
from openviking.utils.code_hosting_utils import is_github_url, parse_code_hosting_url
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RadarRunResult:
    """Outcome of one radar invocation; never carries an exception."""

    status: str  # "disabled" | "skipped" | "empty" | "published" | "failed"
    reason: str = ""
    pr_count: int = 0
    target_uri: str = ""
    warnings: list[str] = field(default_factory=list)


class PrRadarRunner:
    """Orchestrates gh-based PR collection and report persistence for one watch."""

    def __init__(self, viking_fs: Any, gh: Optional[GhCli] = None) -> None:
        self._fs = viking_fs
        self._gh = gh or GhCli()

    async def maybe_run(self, task: Any, ctx: Any) -> RadarRunResult:
        """Entry point for a watch trigger. Never raises."""
        try:
            config = RadarConfig.from_kwargs(getattr(task, "processor_kwargs", None))
            if not config.enabled:
                return RadarRunResult(status="disabled")

            repo = self._resolve_repo(getattr(task, "path", ""))
            if repo is None:
                return RadarRunResult(
                    status="skipped",
                    reason="pr_radar only supports GitHub repositories",
                )

            if not have_gh():
                warning = "`gh` CLI not found on PATH; PR radar deferred to next watch trigger"
                logger.warning("[PrRadar] %s: %s", repo, warning)
                return RadarRunResult(status="skipped", reason=warning)

            target_uri = self._target_uri(config, repo, ctx.account_id)
            state = await self._load_state(target_uri, ctx)
            outcome = await collect_prs(self._gh, repo, config, state)

            if outcome.state != state:
                await self._write_state(target_uri, outcome.state, ctx)

            if not outcome.prs:
                return RadarRunResult(
                    status="empty",
                    target_uri=target_uri,
                    reason="no new pull requests in this window",
                )

            report = render_report(repo, outcome.prs)
            await self._publish_report(target_uri, report, ctx)
            return RadarRunResult(
                status="published",
                pr_count=len(outcome.prs),
                target_uri=target_uri,
            )
        except GhCliUnavailable as exc:
            logger.warning("[PrRadar] gh unavailable: %s", exc)
            return RadarRunResult(status="skipped", reason=str(exc))
        except GhCliError as exc:
            # Transport-level failure: state was held inside the collector.
            logger.warning("[PrRadar] gh call failed, will retry next trigger: %s", exc)
            return RadarRunResult(status="failed", reason=str(exc))
        except Exception as exc:  # noqa: BLE001 -- sidecar must never break the watch
            logger.exception("[PrRadar] unexpected failure")
            return RadarRunResult(status="failed", reason=type(exc).__name__)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _resolve_repo(path: str) -> Optional[str]:
        if not path or not is_github_url(path):
            return None
        repo = parse_code_hosting_url(path)
        return repo if repo and "/" in repo else None

    @staticmethod
    def _target_uri(config: RadarConfig, repo: str, account_id: str) -> str:
        if config.target_uri:
            return config.target_uri.rstrip("/")
        account = account_id or "default"
        return (
            f"viking://user/{account}/resources/radar/{escape_repo_slug(repo)}"
        )

    async def _load_state(self, target_uri: str, ctx: Any) -> RadarState:
        try:
            raw = await self._fs.read_file(f"{target_uri}/{STATE_FILENAME}", ctx=ctx)
        except Exception as exc:  # noqa: BLE001
            if is_not_found_error(exc):
                return RadarState()
            raise
        try:
            return RadarState.from_dict(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            logger.warning("[PrRadar] unreadable state file, starting from empty watermark")
            return RadarState()

    async def _write_state(self, target_uri: str, state: RadarState, ctx: Any) -> None:
        payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        await self._fs.write_file(
            f"{target_uri}/{STATE_FILENAME}",
            payload,
            ctx=ctx,
        )

    async def _publish_report(self, target_uri: str, report: str, ctx: Any) -> None:
        stamp = report_stamp()
        await self._fs.write_file(f"{target_uri}/{LATEST_REPORT_NAME}", report, ctx=ctx)
        await self._fs.write_file(
            f"{target_uri}/{REPORTS_DIRNAME}/{stamp}.md",
            report,
            ctx=ctx,
        )


def radar_config_or_none(processor_kwargs: Optional[dict[str, Any]]) -> Optional[RadarConfig]:
    """Return a parsed radar config only when the reserved key is present."""
    if not processor_kwargs or "pr_radar" not in processor_kwargs:
        return None
    return RadarConfig.from_kwargs(processor_kwargs)


def validate_pr_radar_config(path: str, processor_kwargs: Optional[dict[str, Any]]) -> None:
    """Fail fast at watch creation when radar is enabled on an unsupported source.

    Called from the resource service so an invalid radar request is rejected at
    submit time rather than warning on every watch trigger forever.
    """
    config = radar_config_or_none(processor_kwargs)
    if config is None or not config.enabled:
        return
    if not is_github_url(path):
        from openviking_cli.exceptions import InvalidArgumentError

        raise InvalidArgumentError(
            "pr_radar is only supported for GitHub repositories in this release"
        )
