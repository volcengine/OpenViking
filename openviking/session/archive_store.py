# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Archive state and read helpers for OpenViking sessions.

A committed session archives older messages under
``<session_uri>/history/archive_NNN/``. Each archive directory carries its own
terminal marker (``.done`` / ``.failed.json``), an optional Working Memory
``.overview.md`` / ``.abstract.md`` sidecar, ``messages.jsonl`` raw content, and
``.meta.json`` bookkeeping.

``ArchiveStore`` centralizes every read-only derivation over that layout:
directory listing, terminal-state resolution, marker-driven state scanning,
coverage computation, and the individual sidecar readers. It holds no mutable
session state — only the filesystem handle, request context, and session URI —
so it can be composed by ``Session`` and exercised in isolation.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from openviking.message import Message
from openviking.pyagfs.exceptions import AGFSClientError, AGFSHTTPError, AGFSNotFoundError
from openviking.storage.abstract_overview import body_for_preview
from openviking.utils.token_estimation import estimate_text_tokens
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

# Durable per-step memory coverage recorded in archive metadata.
_MEMORY_STEP_NAMES = ("long_term",)


class _ArchiveMessagesCorruptError(ValueError):
    """Raised when an archive messages file cannot be deserialized."""


def is_storage_not_found(exc: BaseException) -> bool:
    if isinstance(exc, AGFSClientError):
        return isinstance(exc, AGFSNotFoundError) or (
            isinstance(exc, AGFSHTTPError) and exc.status_code == 404
        )
    if isinstance(exc, (FileNotFoundError, NotFoundError)):
        nested = exc.__cause__ or exc.__context__
        return nested is None or is_storage_not_found(nested)
    return False


def extract_abstract_from_summary(summary: str) -> str:
    """Extract one-sentence overview from a structured summary."""
    if not summary:
        return ""

    match = re.search(r"^\*\*[^*]+\*\*:\s*(.+)$", summary, re.MULTILINE)
    if match:
        return match.group(1).strip()

    first_line = summary.split("\n")[0].strip()
    return first_line if first_line else ""


@dataclass
class ArchiveState:
    """Filesystem-derived state for one archive directory."""

    archive_id: str
    archive_uri: str
    index: int
    state: Literal["pending", "completed", "failed"]
    overview: str = ""
    done: Dict[str, Any] = field(default_factory=dict)
    failed: Dict[str, Any] = field(default_factory=dict)

    @property
    def coverage_start_index(self) -> int:
        raw = self.done.get("coverage_start_archive")
        if isinstance(raw, str):
            match = re.fullmatch(r"archive_(\d+)", raw)
            if match:
                return int(match.group(1))
        return self.index

    @property
    def coverage_end_index(self) -> int:
        raw = self.done.get("coverage_end_archive")
        if isinstance(raw, str):
            match = re.fullmatch(r"archive_(\d+)", raw)
            if match:
                return int(match.group(1))
        return self.index


class ArchiveStore:
    """Read-only view over a session's ``history/archive_NNN`` directories."""

    def __init__(self, viking_fs: Any, ctx: Any, session_uri: str):
        self._viking_fs = viking_fs
        self._ctx = ctx
        self._session_uri = session_uri

    # ------------------------------------------------------------------
    # Pure helpers (no I/O)
    # ------------------------------------------------------------------

    @staticmethod
    def archive_index_from_uri(archive_uri: str) -> int:
        """Parse archive_NNN suffix into an integer index."""
        match = re.search(r"archive_(\d+)$", archive_uri.rstrip("/"))
        if not match:
            raise ValueError(f"Invalid archive URI: {archive_uri}")
        return int(match.group(1))

    @staticmethod
    def stable_deduplicate_messages(messages: List[Message]) -> List[Message]:
        """Stable-deduplicate crash/recovery overlaps by durable message id."""
        seen: set[str] = set()
        result: List[Message] = []
        for message in messages:
            if message.id in seen:
                continue
            seen.add(message.id)
            result.append(message)
        return result

    @staticmethod
    def covered_archive_ids(states: List[ArchiveState]) -> set[str]:
        """Return archives covered by an authoritative completion marker."""
        existing = {state.archive_id: state for state in states}
        covered: set[str] = set()
        for state in states:
            if state.state != "completed":
                continue
            start = max(
                1,
                min(state.coverage_start_index, state.coverage_end_index, state.index),
            )
            end = min(
                state.index,
                max(state.coverage_start_index, state.coverage_end_index),
            )
            for candidate in states:
                # A pending archive still has a live Phase 2 owner and is never
                # valid coverage input. Even malformed/manual range metadata
                # must not make its raw messages disappear.
                if start <= candidate.index <= end and candidate.state != "pending":
                    covered.add(candidate.archive_id)
            explicit = state.done.get("covered_failed_archives", [])
            if isinstance(explicit, list):
                covered.update(
                    archive_id
                    for archive_id in explicit
                    if isinstance(archive_id, str)
                    and archive_id in existing
                    and existing[archive_id].index <= state.index
                    and existing[archive_id].state == "failed"
                )
        return covered

    @staticmethod
    def merge_completed_memory_steps(
        target: Dict[str, set[str]],
        raw: Any,
    ) -> None:
        """Merge durable per-step message coverage from archive metadata."""
        if not isinstance(raw, dict):
            return
        for step in _MEMORY_STEP_NAMES:
            message_ids = raw.get(step)
            if not isinstance(message_ids, list):
                continue
            target.setdefault(step, set()).update(
                item for item in message_ids if isinstance(item, str) and item
            )

    @staticmethod
    def serialize_completed_memory_steps(
        completed: Dict[str, set[str]],
    ) -> Dict[str, List[str]]:
        return {
            step: sorted(completed.get(step, set()))
            for step in _MEMORY_STEP_NAMES
            if completed.get(step)
        }

    # ------------------------------------------------------------------
    # Directory / marker reads
    # ------------------------------------------------------------------

    async def file_exists(self, archive_uri: str, file_name: str) -> bool:
        try:
            return await self._viking_fs.exists(f"{archive_uri}/{file_name}", ctx=self._ctx)
        except Exception:
            return False

    async def terminal_state(self, archive_uri: str) -> str:
        """Return ``completed``, ``failed``, or ``pending`` for one archive."""
        if not self._viking_fs:
            return "pending"
        for marker, state in ((".done", "completed"), (".failed.json", "failed")):
            try:
                if await self._viking_fs.exists(f"{archive_uri}/{marker}", ctx=self._ctx):
                    return state
            except Exception:
                continue
        return "pending"

    async def list_refs(self) -> List[Dict[str, Any]]:
        """List archive refs sorted by archive index descending."""
        if not self._viking_fs:
            return []

        try:
            history_items = await self._viking_fs.ls(f"{self._session_uri}/history", ctx=self._ctx)
        except Exception:
            return []

        refs: List[Dict[str, Any]] = []
        for item in history_items:
            name = item.get("name") if isinstance(item, dict) else item
            if not name or not name.startswith("archive_"):
                continue
            try:
                index = int(name.split("_")[1])
            except Exception:
                continue

            refs.append(
                {
                    "archive_id": name,
                    "archive_uri": f"{self._session_uri}/history/{name}",
                    "index": index,
                }
            )

        return sorted(refs, key=lambda item: item["index"], reverse=True)

    async def scan_states(self) -> List[ArchiveState]:
        """Derive every archive state exclusively from its directory markers."""
        states: List[ArchiveState] = []
        refs = sorted(await self.list_refs(), key=lambda item: item["index"])
        for archive in refs:
            done_uri = f"{archive['archive_uri']}/.done"
            try:
                done_exists = await self._viking_fs.exists(done_uri, ctx=self._ctx)
            except Exception:
                done_exists = False
            done: Dict[str, Any] = {}
            if done_exists:
                try:
                    raw_done = await self._viking_fs.read_file(done_uri, ctx=self._ctx)
                    parsed_done = json.loads(raw_done or "{}")
                    if isinstance(parsed_done, dict):
                        done = parsed_done
                except Exception as exc:
                    # Marker existence still means completion, but unreadable
                    # contents cannot extend coverage to earlier archives.
                    logger.warning(
                        "Unreadable archive done marker %s: %s", archive["archive_uri"], exc
                    )

            if done_exists:
                # Only validate overview when Working Memory required one.
                # Otherwise leave overview empty here and let context assembly
                # lazy-load the newest terminal completed archive's overview.
                overview = ""
                if done.get("working_memory_enabled") is True:
                    overview = await self.read_overview(archive["archive_uri"])
                    if not overview.strip():
                        # New markers distinguish an intentionally overview-less
                        # working_memory=false commit from a missing/corrupt
                        # required overview. The latter remains logically live and
                        # can be rolled forward by a later successful archive.
                        logger.warning(
                            "Completed archive has no readable required overview: %s",
                            archive["archive_uri"],
                        )
                        states.append(
                            ArchiveState(
                                archive_id=archive["archive_id"],
                                archive_uri=archive["archive_uri"],
                                index=archive["index"],
                                state="failed",
                                done=done,
                                failed={
                                    "stage": "archive_overview",
                                    "error": "required overview is missing or unreadable",
                                },
                            )
                        )
                        continue

                # working_memory=false legitimately writes .done without an
                # overview. Legacy markers lack the explicit flag, so retain
                # their established completed semantics for compatibility.
                states.append(
                    ArchiveState(
                        archive_id=archive["archive_id"],
                        archive_uri=archive["archive_uri"],
                        index=archive["index"],
                        state="completed",
                        overview=overview,
                        done=done,
                    )
                )
                continue

            failed: Dict[str, Any] = {}
            failed_uri = f"{archive['archive_uri']}/.failed.json"
            try:
                failed_exists = await self._viking_fs.exists(failed_uri, ctx=self._ctx)
            except Exception:
                failed_exists = False
            if failed_exists:
                try:
                    parsed_failed = json.loads(
                        await self._viking_fs.read_file(failed_uri, ctx=self._ctx) or "{}"
                    )
                    if isinstance(parsed_failed, dict):
                        failed = parsed_failed
                except Exception as exc:
                    logger.warning("Unreadable archive failed marker %s: %s", failed_uri, exc)
            states.append(
                ArchiveState(
                    archive_id=archive["archive_id"],
                    archive_uri=archive["archive_uri"],
                    index=archive["index"],
                    state="failed" if failed_exists else "pending",
                    failed=failed,
                )
            )
        return states

    async def completed_refs(
        self,
        exclude_archive_uri: Optional[str] = None,
        before_archive_index: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return completed archive refs sorted by archive index descending."""
        completed: List[Dict[str, Any]] = []
        exclude = exclude_archive_uri.rstrip("/") if exclude_archive_uri else None

        for state in reversed(await self.scan_states()):
            if exclude and state.archive_uri == exclude:
                continue
            if before_archive_index is not None and state.index >= before_archive_index:
                continue
            if state.state != "completed":
                continue
            completed.append(
                {
                    "archive_id": state.archive_id,
                    "archive_uri": state.archive_uri,
                    "index": state.index,
                    "context_reset": state.done.get("context_reset") is True,
                }
            )

        return completed

    async def is_context_reset_archive(self, archive_uri: str) -> bool:
        """Return True when the archive's ``.done`` marks a context reset boundary."""
        try:
            done = json.loads(await self._viking_fs.read_file(f"{archive_uri}/.done", ctx=self._ctx))
        except Exception:
            return False
        return isinstance(done, dict) and done.get("context_reset") is True

    # ------------------------------------------------------------------
    # Sidecar readers
    # ------------------------------------------------------------------

    async def read_overview(self, archive_uri: str) -> str:
        """Read archive overview text."""
        try:
            overview = await self._viking_fs.read_file(
                f"{archive_uri}/.overview.md", ctx=self._ctx
            )
        except Exception:
            return ""
        return body_for_preview(overview or "")

    async def read_abstract(self, archive_uri: str, overview: str = "") -> str:
        """Read archive abstract text, falling back to summary extraction."""
        try:
            abstract = await self._viking_fs.read_file(
                f"{archive_uri}/.abstract.md", ctx=self._ctx
            )
        except Exception:
            abstract = ""

        if abstract:
            return body_for_preview(abstract)

        if not overview:
            overview = await self.read_overview(archive_uri)
        return extract_abstract_from_summary(overview)

    async def read_overview_tokens(self, archive_uri: str, overview: str) -> int:
        """Read overview token estimate from archive metadata."""
        overview_tokens = estimate_text_tokens(overview)
        try:
            meta_content = await self._viking_fs.read_file(
                f"{archive_uri}/.meta.json", ctx=self._ctx
            )
            meta_tokens = int(json.loads(meta_content).get("overview_tokens", overview_tokens))
            overview_tokens = max(overview_tokens, meta_tokens)
        except Exception:
            pass
        return overview_tokens

    async def read_messages(self, archive_uri: str) -> List[Message]:
        """Read archived messages from one archive."""
        content = await self._viking_fs.read_file(f"{archive_uri}/messages.jsonl", ctx=self._ctx)

        messages: List[Message] = []
        for line in content.strip().split("\n"):
            if not line.strip():
                continue
            try:
                messages.append(Message.from_dict(json.loads(line)))
            except (json.JSONDecodeError, AttributeError, KeyError, TypeError, ValueError) as exc:
                raise _ArchiveMessagesCorruptError("invalid message record") from exc

        return messages

    async def read_meta(self, archive_uri: str) -> Dict[str, Any]:
        try:
            content = await self._viking_fs.read_file(f"{archive_uri}/.meta.json", ctx=self._ctx)
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Derived summaries
    # ------------------------------------------------------------------

    async def latest_completed_summary(
        self,
        exclude_archive_uri: Optional[str] = None,
        before_archive_index: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return the newest readable completed archive summary."""
        for archive in await self.completed_refs(
            exclude_archive_uri,
            before_archive_index,
        ):
            if archive.get("context_reset"):
                break
            overview = await self.read_overview(archive["archive_uri"])
            if not overview:
                continue

            return {
                "archive_id": archive["archive_id"],
                "archive_uri": archive["archive_uri"],
                "overview": overview,
                "abstract": await self.read_abstract(archive["archive_uri"], overview),
                "overview_tokens": await self.read_overview_tokens(
                    archive["archive_uri"], overview
                ),
            }

        return None

    async def latest_completed_overview(
        self,
        exclude_archive_uri: Optional[str] = None,
        before_archive_index: Optional[int] = None,
    ) -> str:
        """Return the newest completed archive overview, skipping incomplete archives."""
        summary = await self.latest_completed_summary(
            exclude_archive_uri,
            before_archive_index,
        )
        return summary["overview"] if summary else ""

    async def uncovered_messages(
        self,
        states: Optional[List[ArchiveState]] = None,
    ) -> List[Message]:
        """Return pending/failed raw messages not covered by a completed archive.

        Kept as the RFC #3330 compatibility helper. Current context assembly
        and Phase 2 both skip failed archive raw messages.
        """
        states = states if states is not None else await self.scan_states()
        covered = self.covered_archive_ids(states)
        messages: List[Message] = []
        for state in states:
            if state.archive_id in covered or state.state == "completed":
                continue
            try:
                messages.extend(await self.read_messages(state.archive_uri))
            except Exception as exc:
                if not is_storage_not_found(exc):
                    raise
                logger.warning(
                    "Skipping pending archive %s because messages.jsonl is missing",
                    state.archive_uri,
                )
        return self.stable_deduplicate_messages(messages)

    async def pending_messages(self) -> List[Message]:
        """Compatibility wrapper; uncovered includes pending and failed archives."""
        return await self.uncovered_messages()
