# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Watch sidecar that publishes GitHub PR radar reports.

The runner is invoked once per watch trigger *before* the content-ingestion
short-circuit (a watch whose git HEAD is unchanged returns early and never
re-enters resource ingestion). It is strictly best-effort: every failure is
caught and reported back as a skipped outcome, so radar can never fail the
hosting watch run.

Three pieces of state live next to the reports under the radar directory
rather than in the watch checkpoint, because the unchanged-HEAD path does not
persist checkpoints:

  * ``.radar-state.json`` collection watermark -- how far gh has been read;
  * ``.radar-state.json`` digest watermark (``last_digest_at``) -- the right
    edge of the last published report window;
  * ``.radar-signals.jsonl`` -- digest queue (collected PRs not yet published).

Window semantics are anchored on the watch's ``created_at``: the first report
covers ``[anchor - backfill_days, anchor]`` once the backfill is fully read;
every later report covers one ``watch_interval`` window. Triggers inside a
window only accumulate; the pending safety valve may force a clearly-labeled
partial digest without moving the window boundary. A digest renders only the
signals whose ``updated_at`` falls inside its labeled window; newer signals
stay queued, so a delayed trigger catches up one window per digest instead of
dumping the whole backlog into a mislabeled report.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from openviking.resource.github_radar.collector import collect_prs
from openviking.resource.github_radar.gh_cli import GhCli, GhCliError, GhCliUnavailable, have_gh
from openviking.resource.github_radar.models import (
    LATEST_REPORT_NAME,
    REPORTS_DIRNAME,
    STATE_FILENAME,
    PrSignal,
    RadarConfig,
    RadarState,
    escape_repo_slug,
    fmt_ts,
    parse_ts,
    utc_now,
)
from openviking.resource.github_radar.report import (
    render_enriched_report,
    render_report,
    report_stamp,
)
from openviking.resource.github_radar.signal_store import RadarSignalStore
from openviking.resource.github_radar.summarizer import RadarSummarizer
from openviking.server.error_mapping import is_not_found_error
from openviking.utils.code_hosting_utils import is_github_url, parse_code_hosting_url
from openviking_cli.exceptions import ConflictError
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_RESOURCE_URI_PREFIX = "viking://resources/"
_USER_RESOURCE_MARKER = "__user__"
_ACCOUNT_RESOURCE_MARKER = "__account__"


@dataclass(frozen=True)
class DigestPlan:
    """Whether this trigger produces a report and which window it covers.

    ``action`` is ``digest`` (a window boundary was reached) or ``accumulate``.
    ``advance_to`` is the new value for the digest watermark; empty means the
    boundary must not move (a safety-valve partial digest).
    """

    action: str
    window_start: str = ""
    window_end: str = ""
    advance_to: str = ""
    partial: bool = False


@dataclass
class RadarRunResult:
    """Outcome of one radar invocation; never carries an exception."""

    # "disabled" | "skipped" | "empty" | "accumulating" | "published" | "failed"
    status: str
    reason: str = ""
    pr_count: int = 0
    target_uri: str = ""
    warnings: list[str] = field(default_factory=list)
    # "template" = deterministic list only; "enriched" = LLM summary layer added
    report_mode: str = "template"
    window_start: str = ""
    window_end: str = ""


def plan_digest(
    *,
    state: RadarState,
    config: RadarConfig,
    pending_count: int,
    caught_up: bool,
    anchor: datetime,
    now: datetime,
) -> DigestPlan:
    """Pure window-gating decision, anchored on the watch's ``created_at``.

    Backfill phase (no digest watermark yet): the first report covers
    ``[anchor - backfill_days, anchor]`` and is only released once the full
    backfill is read. Steady phase: windows are ``[last_digest_at,
    last_digest_at + digest_interval_minutes]``; triggers inside a window only
    accumulate. The digest interval is independent of the watch trigger
    interval, so collection can run hourly while reports stay daily. The
    pending safety valve may force a partial digest, which never moves the
    window boundary.
    """
    safety_valve = 0 < config.max_pending_prs <= pending_count

    if not state.last_digest_at:
        window_end = anchor
        window_start = anchor - timedelta(days=config.backfill_days)
        if safety_valve:
            return DigestPlan(
                action="digest",
                window_start=fmt_ts(window_start),
                window_end=fmt_ts(min(now, window_end)),
                partial=True,
            )
        if not caught_up:
            return DigestPlan(
                action="accumulate",
                window_start=fmt_ts(window_start),
                window_end=fmt_ts(window_end),
            )
        return DigestPlan(
            action="digest",
            window_start=fmt_ts(window_start),
            window_end=fmt_ts(window_end),
            advance_to=fmt_ts(window_end),
        )

    last_digest = parse_ts(state.last_digest_at)
    next_digest = last_digest + timedelta(minutes=config.digest_interval_minutes)
    if safety_valve:
        return DigestPlan(
            action="digest",
            window_start=state.last_digest_at,
            window_end=fmt_ts(min(now, next_digest)),
            partial=True,
        )
    if now < next_digest:
        return DigestPlan(
            action="accumulate", window_start=state.last_digest_at,
            window_end=fmt_ts(next_digest),
        )
    if not caught_up:
        # The boundary is due but collection has not read the full window
        # (per-run cap or a transport failure). Closing it now would strand
        # unread in-window PRs outside every future window, so hold the
        # boundary and retry on the next trigger, mirroring backfill gating.
        return DigestPlan(
            action="accumulate", window_start=state.last_digest_at,
            window_end=fmt_ts(next_digest),
        )
    return DigestPlan(
        action="digest",
        window_start=state.last_digest_at,
        window_end=fmt_ts(next_digest),
        advance_to=fmt_ts(next_digest),
    )


def partition_pending(
    prs: list[PrSignal], window_start: str, window_end: str
) -> tuple[list[PrSignal], list[PrSignal]]:
    """Split pending signals into (inside-window, deferred-to-next-window).

    A digest only owns signals whose ``updated_at`` falls inside its labeled
    window; anything newer stays queued so a delayed trigger can neither
    mislabel a PR under an earlier window nor swallow several windows into one
    report. Bounds are inclusive, matching the inclusive collection ``since``.
    Timestamps share the fixed ``%Y-%m-%dT%H:%M:%SZ`` shape, so lexicographic
    comparison equals chronological order. A row without a parseable
    timestamp cannot be placed and is treated as inside to avoid starving it.

    Multiple versions of one PR (distinct ``updated_at``) may land in the same
    window; only the newest version is rendered, so a report lists each PR
    number at most once while older versions remain available for their own
    windows via the deferred list.
    """
    in_window: list[PrSignal] = []
    deferred: list[PrSignal] = []
    newest_by_number: dict[int, int] = {}
    for pr in prs:
        stamp = pr.updated_at
        if not stamp or (window_start <= stamp <= window_end):
            position = newest_by_number.get(pr.number)
            if position is None:
                newest_by_number[pr.number] = len(in_window)
                in_window.append(pr)
            elif stamp and stamp > in_window[position].updated_at:
                in_window[position] = pr
        else:
            deferred.append(pr)
    return in_window, deferred


class PrRadarRunner:
    """Orchestrates gh-based PR collection and report persistence for one watch."""

    def __init__(
        self,
        viking_fs: Any,
        gh: Optional[GhCli] = None,
        vlm_resolver: Any = None,
        group_resolver: Any = None,
        context_resolver: Any = None,
        watch_store: Any = None,
        path_locks: Any = None,
    ) -> None:
        self._fs = viking_fs
        self._gh = gh or GhCli()
        self._vlm_resolver = vlm_resolver
        self._group_resolver = group_resolver
        self._context_resolver = context_resolver
        self._watch_store = watch_store
        self._path_locks = path_locks

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

            target_uri = self._target_uri(
                config,
                repo,
                self._owner_user_id(task, ctx),
                getattr(task, "to_uri", None),
            )
            # Radar writes must obey the caller's own namespace/ACL policy.
            # The watch runtime context carries bypass_acl=True for trusted
            # background ingestion; reusing it here would let an explicit
            # target_uri overwrite child files (latest.md, reports, state)
            # whose own ACL denies the user, even though directory-level
            # creation checks passed. Derived targets live in the caller's
            # own user namespace and pass the ordinary owner checks.
            io_ctx = self._io_context(ctx)
            if self._context_resolver is not None:
                io_ctx = self._io_context(await self._context_resolver(io_ctx))
            if self._group_resolver is not None:
                groups = await self._group_resolver(
                    ctx.account_id, self._owner_user_id(task, ctx)
                )
                io_ctx = replace(io_ctx, group_ids=tuple(groups))
            from openviking.resource.github_radar.ownership import radar_directory_guard
            from openviking.resource.watch_manager import WatchPathLocks

            async with radar_directory_guard(
                self._fs, self._path_locks or WatchPathLocks(self._fs),
                self._watch_store, task, io_ctx, claim=have_gh(),
            ):
                pass
            state = await self._load_state(target_uri, io_ctx)
            if state.pending_publish is not None:
                return await self._finish_publication(target_uri, state.pending_publish, io_ctx)
            if not have_gh():
                warning = "`gh` CLI not found on PATH; PR radar deferred to next watch trigger"
                logger.warning("[PrRadar] %s: %s", repo, warning)
                return RadarRunResult(status="skipped", reason=warning)
            # Anchor the first-run floor on the watch creation time so the
            # collected range matches the labeled backfill window: a delayed
            # first trigger (gh missing, scheduler downtime) must not shrink
            # the queried range and silently drop the oldest window's PRs.
            anchor = self._anchor_utc(task)
            first_floor = fmt_ts(anchor - timedelta(days=config.backfill_days))
            # Only close a window that was already due before collection
            # began. Later I/O can cross a boundary while updates in that
            # window are still absent from the already returned endpoint.
            now = utc_now()
            outcome = await collect_prs(
                self._gh, repo, config, state, first_floor=first_floor
            )

            # Raw-material layer: undigested PRs accumulate across capped
            # collection passes so a report always covers a complete window.
            signal_store = RadarSignalStore(self._fs, target_uri)
            pending = await signal_store.load(io_ctx)
            merged = signal_store.merge(pending, outcome.prs)
            # Compare content, not length: a newer snapshot may append a new
            # version of an equal-length pending queue and still needs saving.
            if merged != pending:
                await signal_store.save(merged, io_ctx)

            window = plan_digest(
                state=state, config=config, pending_count=0,
                caught_up=outcome.caught_up, anchor=anchor, now=now,
            )
            # Count versions owned by this window, not future-window rows.
            # An empty due window must be able to close even with a full
            # future queue; the safety valve must not erase older snapshots
            # by expanding across multiple report windows.
            owned_count = sum(
                not pr.updated_at or window.window_start <= pr.updated_at <= window.window_end
                for pr in merged
            )
            plan = plan_digest(
                state=state,
                config=config,
                pending_count=owned_count,
                caught_up=outcome.caught_up,
                anchor=anchor,
                now=now,
            )

            if plan.action == "accumulate":
                # Persist the collection watermark; the digest boundary stays put.
                collection_state = replace(outcome.state, last_digest_at=state.last_digest_at)
                if collection_state != state:
                    await self._write_state(target_uri, collection_state, io_ctx)
                if not merged:
                    return RadarRunResult(
                        status="empty",
                        target_uri=target_uri,
                        reason="no new pull requests in this window",
                    )
                logger.info(
                    "[PrRadar] %s: %s PR(s) queued inside window %s ~ %s; digest deferred",
                    repo,
                    len(merged),
                    state.last_digest_at or "(backfill)",
                    plan.window_end or "(backfill)",
                )
                return RadarRunResult(
                    status="accumulating",
                    pr_count=len(merged),
                    target_uri=target_uri,
                    reason="window boundary not reached; PRs queued for the next digest",
                )

            # A boundary was reached (or the safety valve forced a partial digest).
            warnings: list[str] = []
            if plan.partial:
                warning = (
                    f"pending PRs reached max_pending_prs={config.max_pending_prs}; "
                    "published a partial digest, window boundary not advanced"
                )
                logger.warning("[PrRadar] %s: %s", repo, warning)
                warnings.append(warning)

            # Collection watermark advances; digest watermark only on a full digest.
            next_digest_at = plan.advance_to or state.last_digest_at
            fragments = [
                fragment for fragment in state.partial_fragments
                if fragment.get("window_start") == plan.window_start
            ]
            new_state = replace(
                outcome.state,
                last_digest_at=next_digest_at,
                partial_fragments=fragments,
            )

            # Only signals inside the labeled window belong to this report;
            # newer ones stay queued for the next window instead of being
            # mislabeled under an earlier boundary or swallowed when a lagging
            # watch has to catch up several windows one digest at a time.
            in_window, deferred = partition_pending(
                merged, plan.window_start, plan.window_end
            )

            if not in_window:
                # Boundary reached with nothing in-window: advance silently,
                # no report file and no LLM call. Deferred signals stay queued.
                # No publish can fail here, so persisting the boundary is safe.
                if not plan.partial:
                    new_state = replace(new_state, partial_fragments=[])
                if new_state != state:
                    await self._write_state(target_uri, new_state, io_ctx)
                return RadarRunResult(
                    status="empty",
                    target_uri=target_uri,
                    reason="no new pull requests in this window",
                    window_start=plan.window_start,
                    window_end=plan.window_end,
                )

            archive_stamp = report_stamp()
            if plan.partial:
                # Remember the fragment so later full-window reports label
                # themselves as continuations instead of looking complete.
                fragments.append(
                    {
                        "stamp": archive_stamp,
                        "window_start": plan.window_start,
                        "window_end": plan.window_end,
                        "pr_count": len(in_window),
                    }
                )
                new_state = replace(new_state, partial_fragments=fragments)

            report, report_mode = await self._render(
                repo,
                in_window,
                config,
                ctx,
                plan.window_start,
                plan.window_end,
                partial=plan.partial,
                continuation_fragments=None if plan.partial else fragments,
            )
            if not plan.partial:
                new_state = replace(new_state, partial_fragments=[])
            journal = {
                "stamp": archive_stamp, "report": report,
                "next_state": replace(new_state, pending_publish=None).to_dict(),
                "deferred": [pr.to_row() for pr in deferred],
                "pr_count": len(in_window), "report_mode": report_mode,
                "warnings": warnings, "window_start": plan.window_start,
                "window_end": plan.window_end,
            }
            # Prepare before publishing or draining. If any subsequent write
            # fails, the exact report, archive identity and final state remain
            # recoverable even when the signal queue has already been cleared.
            await self._write_state(
                target_uri, replace(state, pending_publish=journal), io_ctx
            )
            return await self._finish_publication(target_uri, journal, io_ctx)
        except ConflictError as exc:
            logger.warning("[PrRadar] directory conflict: %s", exc)
            return RadarRunResult(status="skipped", reason=str(exc))
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

    async def _finish_publication(
        self, target_uri: str, journal: dict, ctx: Any
    ) -> RadarRunResult:
        await self._publish_report(target_uri, journal["report"], ctx, journal["stamp"])
        deferred = [PrSignal.from_row(row) for row in journal["deferred"]]
        await RadarSignalStore(self._fs, target_uri).save(deferred, ctx)
        await self._write_state(
            target_uri, RadarState.from_dict(journal["next_state"]), ctx
        )
        return RadarRunResult(
            status="published", target_uri=target_uri,
            pr_count=journal["pr_count"], report_mode=journal["report_mode"],
            warnings=journal["warnings"], window_start=journal["window_start"],
            window_end=journal["window_end"],
        )

    @staticmethod
    def _io_context(ctx: Any) -> Any:
        """Return a context that enforces the caller's normal ACL policy.

        Watch runtime contexts minted by ``WatchManager.context`` carry
        ``bypass_acl=True`` for trusted background ingestion. Radar files may
        live at an explicit user-supplied target whose child ACLs deny the
        caller, so radar I/O must not inherit that bypass: namespace owner
        checks and per-file ACLs are evaluated normally. Test doubles (plain
        namespaces) are returned unchanged.
        """
        if dataclasses.is_dataclass(ctx) and getattr(ctx, "bypass_acl", False):
            return replace(ctx, bypass_acl=False)
        return ctx

    @staticmethod
    def _resolve_repo(path: str) -> Optional[str]:
        if not path or not is_github_url(path):
            return None
        repo = parse_code_hosting_url(path)
        return repo if repo and "/" in repo else None

    @staticmethod
    def _owner_user_id(task: Any, ctx: Any) -> str:
        """Resolve the user-namespace owner (user_id, not account_id).

        ``viking://user/<owner>/...`` is authorized against ``ctx.user.user_id``
        in namespace.is_accessible; using the tenant account_id here makes the
        radar directory self-deny for every non-root identity whose account_id
        differs from user_id. Watch runtimes carry a UserIdentifier on
        ``ctx.user``; fall back to the task field / "default" for test doubles.
        """
        user = getattr(ctx, "user", None)
        user_id = getattr(user, "user_id", None)
        if user_id:
            return str(user_id)
        return str(getattr(task, "user_id", None) or "default")

    @staticmethod
    def _target_uri(
        config: RadarConfig,
        repo: str,
        owner_user_id: str,
        watch_to_uri: Optional[str] = None,
    ) -> str:
        if config.target_uri:
            return config.target_uri.rstrip("/")
        owner = owner_user_id or "default"
        relative = PrRadarRunner._radar_relative_path(watch_to_uri)
        if relative is not None:
            # Keyed by the watch's own target URI, so two watches on the same
            # GitHub repo (different resource paths) never share radar state.
            return f"viking://user/{owner}/resources/radar/{relative}"
        # Fallback for malformed/missing watch targets: repo slug stays unique
        # per (owner, repo) and preserves the pre-watch-URI behavior.
        return (
            f"viking://user/{owner}/resources/radar/{escape_repo_slug(repo)}"
        )

    @staticmethod
    def _radar_relative_path(watch_to_uri: Optional[str]) -> Optional[str]:
        """Project a watch resource target onto the radar namespace.

        Both supported resource namespaces are covered:

          * ``viking://resources/<rel>`` -> ``<rel>`` (account resources)
          * ``viking://user/<uid>/resources/<rel>`` ->
            ``__user__/<uid>/<rel>`` (user-owned resources)

        Account paths starting with ``__user__`` or ``__account__`` gain an
        ``__account__`` prefix; the escape marker is escaped too. These paths
        previously shared ambiguous state, which is intentionally not copied.
        The reserved marker keeps the two projections collision-free, so two
        distinct watches of the same GitHub repo (e.g. projectA vs projectB
        under a user namespace) no longer fall back to one shared slug
        directory. Returns None when the target is missing or malformed.
        """
        if not watch_to_uri:
            return None
        if watch_to_uri.startswith(_RESOURCE_URI_PREFIX):
            relative = watch_to_uri[len(_RESOURCE_URI_PREFIX) :].strip("/")
            segments = relative.split("/") if relative else []
            # Escape both markers, including the escape marker itself. This
            # remains injective for every legal account resource path.
            if segments and segments[0] in {_USER_RESOURCE_MARKER, _ACCOUNT_RESOURCE_MARKER}:
                segments.insert(0, _ACCOUNT_RESOURCE_MARKER)
        else:
            body = watch_to_uri[len("viking://") :]
            parts = [p for p in body.split("/") if p]
            # Canonical shape: user / <uid> / resources / <rel...>
            if len(parts) >= 4 and parts[0] == "user" and parts[2] == "resources":
                user_id, relative_parts = parts[1], parts[3:]
                segments = [_USER_RESOURCE_MARKER, user_id, *relative_parts]
            else:
                return None
        if not segments or any(
            not segment or segment in {".", ".."} for segment in segments
        ):
            return None
        return "/".join(segments)

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

    @staticmethod
    def _anchor_utc(task: Any) -> datetime:
        """Watch creation time as an aware UTC datetime; the window anchor."""
        created = getattr(task, "created_at", None)
        if not isinstance(created, datetime):
            return utc_now()
        if created.tzinfo is None:
            # WatchTask.created_at is a naive local timestamp.
            return created.astimezone(timezone.utc)
        return created.astimezone(timezone.utc)

    async def _render(
        self,
        repo: str,
        prs: list,
        config: RadarConfig,
        ctx: Any,
        window_start: str = "",
        window_end: str = "",
        *,
        partial: bool = False,
        continuation_fragments: Optional[list] = None,
    ) -> tuple[str, str]:
        """Build the report markdown. Returns (markdown, mode) where mode is
        ``enriched`` when the LLM summary layer was produced, ``template`` on
        the deterministic fallback. Summary failures never raise."""
        if not config.summary_enabled or self._vlm_resolver is None:
            return (
                render_report(
                    repo,
                    prs,
                    window_start,
                    window_end,
                    partial=partial,
                    continuation_fragments=continuation_fragments,
                ),
                "template",
            )
        summarizer = RadarSummarizer(
            self._vlm_resolver, batch_bytes=config.summary_batch_bytes
        )
        summary = await summarizer.summarize(repo, prs, ctx.account_id)
        if summary is None:
            logger.warning(
                "[PrRadar] summary unavailable for %s, publishing template report", repo
            )
            return (
                render_report(
                    repo,
                    prs,
                    window_start,
                    window_end,
                    partial=partial,
                    continuation_fragments=continuation_fragments,
                ),
                "template",
            )
        return (
            render_enriched_report(
                repo,
                prs,
                summary,
                window_start,
                window_end,
                partial=partial,
                continuation_fragments=continuation_fragments,
            ),
            "enriched",
        )

    async def _publish_report(
        self,
        target_uri: str,
        report: str,
        ctx: Any,
        stamp: Optional[str] = None,
    ) -> None:
        stamp = stamp or report_stamp()
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


async def authorize_pr_radar_target(
    viking_fs: Any,
    processor_kwargs: Optional[dict[str, Any]],
    ctx: Any,
) -> None:
    """Authorize an explicit radar target with the caller's own authority.

    ResourceService checks the original request context. WatchManager also
    checks the refreshed background context before persisting the watch, so
    trusted request-only assertions cannot create an unusable report target.
    Runtime radar still checks each individual file with bypass disabled.
    Derived targets live in the caller's own user namespace (target_uri is
    None) and need no extra directory check.
    """
    config = radar_config_or_none(processor_kwargs)
    if config is None or not config.enabled or not config.target_uri:
        return
    from openviking.storage.acl import AclAction

    # rstrip mirrors _target_uri so the check covers the exact runtime path.
    await viking_fs._ensure_access(
        config.target_uri.rstrip("/"), ctx, action=AclAction.WRITE
    )
