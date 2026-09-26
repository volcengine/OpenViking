# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Turn-checkpoint planning for OpenViking sessions.

When Turn-budget retention keeps a still-active User Turn but archives the
assistant/tool steps that overflow the budget, Phase 2 records a bounded
cumulative *checkpoint* summary for that Turn. Context assembly later re-injects
the checkpoint as a synthetic ``ContextPart`` right after the retained anchor,
so the model keeps continuity without replaying the archived raw steps.

``CheckpointPlanner`` owns every derivation over checkpoint metadata:

* ``collect_requests_for_phase2`` — validate and merge the partial-Turn work a
  commit owns (its own archive plus any covered failed archives), then attach
  the previous cumulative summary per anchor.
* ``build_records`` — bind the ordinal LLM summaries to server-owned IDs and
  enforce the per-Turn token budget.
* ``get_effective_completed_checkpoints`` — resolve the effective completed
  checkpoint per anchor (v2 cumulative first-hit, else legacy v1 delta merge).
* ``insert_terminal_checkpoints`` — splice completed checkpoints into an
  assembled message list.

It holds no mutable session state; it composes an :class:`ArchiveStore` for all
archive reads.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openviking.message import Message
from openviking.message.part import ContextPart
from openviking.session.archive_store import ArchiveStore
from openviking.session.retention import build_turns, is_user_query
from openviking.utils.token_estimation import estimate_text_tokens, truncate_text_to_token_budget

CUMULATIVE_CHECKPOINT_VERSION = 2


@dataclass(frozen=True)
class CheckpointRequest:
    """Server-owned mapping for one checkpoint summary requested from Phase 2."""

    turn_anchor_message_id: str
    source_message_ids: tuple[str, ...]
    retained_message_token_budget: int
    estimated_active_tokens: int
    previous_checkpoint_abstract: str = ""
    previous_checkpoint_source_message_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckpointSnapshot:
    """Effective completed checkpoint state for one retained User Turn."""

    turn_anchor_message_id: str
    source_message_ids: tuple[str, ...]
    abstract: str
    archive_id: str
    archive_uri: str


class CheckpointPlanner:
    """Plan, record, and restore Turn checkpoints over archive metadata."""

    def __init__(self, archives: ArchiveStore):
        self._archives = archives

    @staticmethod
    def records_for_anchors(
        meta: Dict[str, Any],
        anchor_ids: set[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Return structurally valid checkpoint records grouped by requested anchor."""
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        checkpoints = meta.get("checkpoints")
        if not isinstance(checkpoints, list):
            return grouped

        for checkpoint in checkpoints:
            if not isinstance(checkpoint, dict):
                continue
            anchor_id = checkpoint.get("turn_anchor_message_id")
            source_ids = checkpoint.get("source_message_ids")
            abstract = checkpoint.get("abstract")
            if not isinstance(anchor_id, str) or anchor_id not in anchor_ids:
                continue
            if not isinstance(source_ids, list) or not source_ids:
                continue
            if not isinstance(abstract, str) or not abstract.strip():
                continue
            valid_source_ids = tuple(item for item in source_ids if isinstance(item, str) and item)
            if not valid_source_ids:
                continue
            raw_version = checkpoint.get("checkpoint_version", 1)
            try:
                checkpoint_version = max(1, int(raw_version))
            except (TypeError, ValueError):
                checkpoint_version = 1
            grouped.setdefault(anchor_id, []).append(
                {
                    "source_message_ids": valid_source_ids,
                    "abstract": abstract.strip(),
                    "checkpoint_version": checkpoint_version,
                }
            )
        return grouped

    async def get_effective_completed_checkpoints(
        self,
        anchor_ids: set[str],
        *,
        before_archive_index: Optional[int] = None,
    ) -> Dict[str, CheckpointSnapshot]:
        """Resolve completed checkpoint history for the requested retained Turns.

        Version 2 records are cumulative, so the first one found while scanning
        newest to oldest is authoritative. Version 1 records are deltas; they are
        collected until a v2 base (or the beginning of history) and merged in
        chronological order. This keeps new sessions bounded while preserving
        legacy histories during migration.
        """
        if not anchor_ids:
            return {}

        histories: Dict[str, Dict[str, Any]] = {
            anchor_id: {
                "base": None,
                "legacy_chunks": [],
                "resolved": False,
            }
            for anchor_id in anchor_ids
        }
        refs = await self._archives.completed_refs(before_archive_index=before_archive_index)
        for archive in refs:  # newest → oldest
            unresolved = {
                anchor_id for anchor_id, history in histories.items() if not history["resolved"]
            }
            if not unresolved:
                break
            grouped = self.records_for_anchors(
                await self._archives.read_meta(archive["archive_uri"]),
                unresolved,
            )
            for anchor_id, records in grouped.items():
                history = histories[anchor_id]
                cumulative = [
                    record
                    for record in records
                    if record["checkpoint_version"] >= CUMULATIVE_CHECKPOINT_VERSION
                ]
                if cumulative:
                    # A v2 record already includes every older compressed prefix.
                    record = cumulative[-1]
                    history["base"] = {
                        **record,
                        "archive_id": archive["archive_id"],
                        "archive_uri": archive["archive_uri"],
                    }
                    history["resolved"] = True
                    continue

                history["legacy_chunks"].append(
                    {
                        "source_message_ids": tuple(
                            dict.fromkeys(
                                source_id
                                for record in records
                                for source_id in record["source_message_ids"]
                            )
                        ),
                        "abstract": "\n\n".join(record["abstract"] for record in records),
                        "archive_id": archive["archive_id"],
                        "archive_uri": archive["archive_uri"],
                    }
                )

        snapshots: Dict[str, CheckpointSnapshot] = {}
        for anchor_id, history in histories.items():
            base = history["base"]
            legacy_chunks = list(reversed(history["legacy_chunks"]))
            chronological_chunks = ([base] if base else []) + legacy_chunks
            if not chronological_chunks:
                continue

            source_message_ids: List[str] = []
            abstracts: List[str] = []
            for chunk in chronological_chunks:
                seen = set(source_message_ids)
                new_source_ids = [
                    source_id for source_id in chunk["source_message_ids"] if source_id not in seen
                ]
                if not new_source_ids:
                    continue
                source_message_ids.extend(new_source_ids)
                abstracts.append(chunk["abstract"])
            if not source_message_ids or not abstracts:
                continue

            newest = history["legacy_chunks"][0] if history["legacy_chunks"] else base
            snapshots[anchor_id] = CheckpointSnapshot(
                turn_anchor_message_id=anchor_id,
                source_message_ids=tuple(source_message_ids),
                abstract="\n\n".join(abstracts),
                archive_id=newest["archive_id"],
                archive_uri=newest["archive_uri"],
            )
        return snapshots

    async def collect_requests_for_phase2(
        self,
        archive_uri: str,
        covered_failed_archives: List[str],
        messages: List[Message],
    ) -> List[CheckpointRequest]:
        """Collect and validate partial-Turn checkpoint work owned by this Phase 2.

        Failed archives rolled into the current commit contribute their pending
        checkpoint sources. Requests sharing one retained user anchor are merged
        before the LLM call, so context assembly inserts one checkpoint per Turn.
        """
        archive_root = archive_uri.rstrip("/").rsplit("/", 1)[0]
        current_archive_id = archive_uri.rstrip("/").split("/")[-1]
        archive_ids = list(
            dict.fromkeys(
                [
                    archive_id
                    for archive_id in [*covered_failed_archives, current_archive_id]
                    if isinstance(archive_id, str) and re.fullmatch(r"archive_\d+", archive_id)
                ]
            )
        )
        archive_ids.sort(key=lambda item: int(item.split("_")[1]))

        message_by_id = {message.id: message for message in messages}
        message_ids = set(message_by_id)
        message_order = {message.id: index for index, message in enumerate(messages)}
        turn_anchor_by_message_id: Dict[str, Optional[str]] = {}
        for turn in build_turns(messages):
            owner_anchor_id = turn.anchor.id if turn.anchor is not None else None
            for message in turn.messages:
                turn_anchor_by_message_id[message.id] = owner_anchor_id
        merged: Dict[str, Dict[str, Any]] = {}
        for archive_id in archive_ids:
            meta = await self._archives.read_meta(f"{archive_root}/{archive_id}")
            plan = meta.get("retention_plan")
            if not isinstance(plan, dict) or not plan.get("partial_turn"):
                continue

            anchor_id = plan.get("turn_anchor_message_id")
            raw_source_ids = plan.get("checkpoint_source_message_ids")
            if not isinstance(anchor_id, str) or not anchor_id:
                raise ValueError(f"{archive_id} has a partial Turn without a valid anchor")
            if not isinstance(raw_source_ids, list) or not raw_source_ids:
                raise ValueError(
                    f"{archive_id} has a partial Turn without checkpoint source messages"
                )
            source_ids = [
                source_id
                for source_id in raw_source_ids
                if isinstance(source_id, str) and source_id
            ]
            if len(source_ids) != len(raw_source_ids):
                raise ValueError(f"{archive_id} has invalid checkpoint source message IDs")

            missing_ids = [
                message_id
                for message_id in [anchor_id, *source_ids]
                if message_id not in message_ids
            ]
            if missing_ids:
                raise ValueError(
                    f"{archive_id} checkpoint source is missing messages: {missing_ids}"
                )
            invalid_source_ids = [
                source_id
                for source_id in source_ids
                if is_user_query(message_by_id[source_id])
                or turn_anchor_by_message_id.get(source_id) != anchor_id
            ]
            if invalid_source_ids:
                raise ValueError(
                    f"{archive_id} checkpoint source is outside its Assistant/Tool prefix: "
                    f"{invalid_source_ids}"
                )

            request = merged.setdefault(
                anchor_id,
                {
                    "source_message_ids": [],
                    "retained_message_token_budget": 0,
                    "estimated_active_tokens": 0,
                },
            )
            request["source_message_ids"] = list(
                dict.fromkeys([*request["source_message_ids"], *source_ids])
            )
            # The newest plan for the same still-active Turn is authoritative.
            request["retained_message_token_budget"] = max(
                0, int(plan.get("retained_message_token_budget", 0) or 0)
            )
            request["estimated_active_tokens"] = max(
                0, int(plan.get("estimated_active_tokens", 0) or 0)
            )

        requests: List[CheckpointRequest] = []
        for anchor_id, request in merged.items():
            source_ids = sorted(
                request["source_message_ids"],
                key=lambda message_id: message_order[message_id],
            )
            requests.append(
                CheckpointRequest(
                    turn_anchor_message_id=anchor_id,
                    source_message_ids=tuple(source_ids),
                    retained_message_token_budget=request["retained_message_token_budget"],
                    estimated_active_tokens=request["estimated_active_tokens"],
                )
            )
        requests.sort(
            key=lambda request: min(
                message_order[source_id] for source_id in request.source_message_ids
            )
        )
        previous_by_anchor = await self.get_effective_completed_checkpoints(
            {request.turn_anchor_message_id for request in requests},
            before_archive_index=self._archives.archive_index_from_uri(archive_uri),
        )
        return [
            CheckpointRequest(
                turn_anchor_message_id=request.turn_anchor_message_id,
                source_message_ids=request.source_message_ids,
                retained_message_token_budget=request.retained_message_token_budget,
                estimated_active_tokens=request.estimated_active_tokens,
                previous_checkpoint_abstract=(
                    previous_by_anchor[request.turn_anchor_message_id].abstract
                    if request.turn_anchor_message_id in previous_by_anchor
                    else ""
                ),
                previous_checkpoint_source_message_ids=(
                    previous_by_anchor[request.turn_anchor_message_id].source_message_ids
                    if request.turn_anchor_message_id in previous_by_anchor
                    else ()
                ),
            )
            for request in requests
        ]

    @staticmethod
    def build_records(
        requests: List[CheckpointRequest],
        summaries: tuple[str, ...],
    ) -> List[Dict[str, Any]]:
        """Bind ordinal LLM outputs to server-owned IDs and enforce local budgets."""
        if len(summaries) != len(requests):
            raise ValueError(
                "Working Memory output returned "
                f"{len(summaries)} checkpoint summaries for {len(requests)} requests"
            )

        records: List[Dict[str, Any]] = []
        for request, raw_summary in zip(requests, summaries, strict=True):
            summary = raw_summary.strip() if isinstance(raw_summary, str) else ""
            if not summary:
                raise ValueError("Working Memory output contains an empty checkpoint summary")

            configured_budget = request.retained_message_token_budget
            if configured_budget > 0:
                available = configured_budget - request.estimated_active_tokens
                checkpoint_budget = (
                    min(1024, available) if available > 0 else min(256, configured_budget)
                )
            else:
                checkpoint_budget = 1024
            abstract = truncate_text_to_token_budget(summary, max(1, checkpoint_budget))
            if not abstract:
                raise ValueError("Checkpoint summary is empty after local token truncation")
            records.append(
                {
                    "checkpoint_version": CUMULATIVE_CHECKPOINT_VERSION,
                    "turn_anchor_message_id": request.turn_anchor_message_id,
                    "source_message_ids": list(
                        dict.fromkeys(
                            [
                                *request.previous_checkpoint_source_message_ids,
                                *request.source_message_ids,
                            ]
                        )
                    ),
                    "abstract": abstract,
                    "estimated_tokens": estimate_text_tokens(abstract),
                }
            )
        return records

    async def insert_terminal_checkpoints(
        self,
        messages: List[Message],
        terminal: Optional[Dict[str, Any]],
    ) -> List[Message]:
        """Insert completed checkpoints after their retained User anchors.

        New v2 checkpoints are cumulative, so the newest terminal archive is a
        constant-cost first hit. A terminal v1 checkpoint triggers the legacy
        compatibility scan and merges its older delta records chronologically.
        Pending or failed terminal archives are never passed to this method.
        """
        if not messages or terminal is None:
            return messages

        message_ids = {message.id for message in messages}
        candidates: Dict[str, Dict[str, Any]] = {}
        meta = await self._archives.read_meta(terminal["archive_uri"])
        grouped = self.records_for_anchors(meta, message_ids)
        legacy_anchor_ids: set[str] = set()
        for anchor_id, records in grouped.items():
            cumulative = [
                record
                for record in records
                if record["checkpoint_version"] >= CUMULATIVE_CHECKPOINT_VERSION
            ]
            if not cumulative:
                legacy_anchor_ids.add(anchor_id)
                continue
            record = cumulative[-1]
            candidates[anchor_id] = {
                "archive_id": terminal["archive_id"],
                "archive_uri": terminal["archive_uri"],
                "source_message_ids": list(record["source_message_ids"]),
                "abstract": record["abstract"],
            }

        if legacy_anchor_ids:
            legacy = await self.get_effective_completed_checkpoints(
                legacy_anchor_ids,
                before_archive_index=terminal["index"] + 1,
            )
            for anchor_id, snapshot in legacy.items():
                candidates[anchor_id] = {
                    "archive_id": snapshot.archive_id,
                    "archive_uri": snapshot.archive_uri,
                    "source_message_ids": list(snapshot.source_message_ids),
                    "abstract": snapshot.abstract,
                }

        if not candidates:
            return messages

        result: List[Message] = []
        for message in messages:
            result.append(message)
            candidate = candidates.get(message.id)
            if not candidate:
                continue
            abstract = candidate["abstract"]
            if not abstract:
                continue
            result.append(
                Message(
                    id=f"checkpoint_{candidate['archive_id']}_{message.id}",
                    role="assistant",
                    parts=[
                        ContextPart(
                            uri=candidate["archive_uri"],
                            context_type="memory",
                            abstract=abstract,
                        )
                    ],
                    # The checkpoint is synthesized by OpenViking, not authored
                    # by the user who owns the retained anchor.
                    peer_id=None,
                    created_at=message.created_at,
                    turn_id=message.turn_id,
                    message_kind="checkpoint",
                    source_message_ids=candidate["source_message_ids"],
                )
            )
        return result
