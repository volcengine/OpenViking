# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Dialogue-pair records used for mid-term vector recall.

The records are deliberately storage-format agnostic. ``Session`` stages the
manifest and rendered Markdown below an archive, then publishes deterministic
copies to the peer's ``memories/midterm_qa`` vector-recall directory. Long-term
memory extraction continues to consume the original archived messages.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from openviking.message import Message, TextPart

_LEGACY_SPEAKER_PREFIX = re.compile(r"^([^:\n]{1,80}):\s+([\s\S]+)$")


def _message_text(message: Message) -> str:
    return "\n".join(
        part.text.strip()
        for part in message.parts
        if isinstance(part, TextPart) and part.text and part.text.strip()
    )


def _legacy_speaker_map(messages: list[Message]) -> dict[str, tuple[str, str]]:
    """Recognize old LoCoMo ``Speaker: text`` payloads conservatively.

    A single colon-bearing user message is ordinary prose more often than a
    speaker label.  Legacy parsing is enabled only when at least two distinct
    prefixes occur in the archive.
    """

    candidates: dict[str, tuple[str, str]] = {}
    names: set[str] = set()
    for message in messages:
        if getattr(message, "speaker", None) or message.role != "user":
            continue
        match = _LEGACY_SPEAKER_PREFIX.match(_message_text(message))
        if not match:
            continue
        speaker, body = match.group(1).strip(), match.group(2).strip()
        if speaker and body:
            candidates[message.id] = (speaker, body)
            names.add(speaker.casefold())
    return candidates if len(names) >= 2 else {}


@dataclass(frozen=True)
class MidtermBlock:
    speaker: str
    role: str
    peer_id: str | None
    created_at: str | None
    ended_at: str | None
    message_ids: tuple[str, ...]
    texts: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "speaker": self.speaker,
            "role": self.role,
            "peer_id": self.peer_id,
            "created_at": self.created_at,
            "ended_at": self.ended_at,
            "message_ids": list(self.message_ids),
            "texts": list(self.texts),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MidtermBlock":
        return cls(
            speaker=str(value.get("speaker") or "unknown"),
            role=str(value.get("role") or "user"),
            peer_id=(str(value["peer_id"]) if value.get("peer_id") is not None else None),
            created_at=(str(value["created_at"]) if value.get("created_at") is not None else None),
            ended_at=(str(value["ended_at"]) if value.get("ended_at") is not None else None),
            message_ids=tuple(str(item) for item in value.get("message_ids") or []),
            texts=tuple(str(item) for item in value.get("texts") or []),
        )


@dataclass(frozen=True)
class MidtermRecord:
    pair_index: int
    kind: str
    session_id: str
    archive_uri: str
    blocks: tuple[MidtermBlock, ...]
    source_message_ids: tuple[str, ...]
    content_hash: str
    converted_to_long_term: bool = False
    converted_at: str | None = None

    @property
    def filename(self) -> str:
        prefix = "pair" if self.kind == "dialogue_pair" else "single_turn"
        return f"{prefix}_{self.pair_index:04d}.md"

    @property
    def recall_filename(self) -> str:
        """Stable filename used by the persistent vector-recall copy."""

        prefix = "pair" if self.kind == "dialogue_pair" else "single_turn"
        return f"{prefix}_{self.content_hash}.md"

    @property
    def peer_ids(self) -> tuple[str, ...]:
        """All peers represented by the record, preserving encounter order."""

        return tuple(dict.fromkeys(block.peer_id for block in self.blocks if block.peer_id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_index": self.pair_index,
            "kind": self.kind,
            "session_id": self.session_id,
            "archive_uri": self.archive_uri,
            "blocks": [block.to_dict() for block in self.blocks],
            "source_message_ids": list(self.source_message_ids),
            "content_hash": self.content_hash,
            "converted_to_long_term": self.converted_to_long_term,
            "converted_at": self.converted_at,
            "filename": self.filename,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MidtermRecord":
        return cls(
            pair_index=int(value["pair_index"]),
            kind=str(value["kind"]),
            session_id=str(value.get("session_id") or ""),
            archive_uri=str(value.get("archive_uri") or ""),
            blocks=tuple(MidtermBlock.from_dict(item) for item in value.get("blocks") or []),
            source_message_ids=tuple(str(item) for item in value.get("source_message_ids") or []),
            content_hash=str(value.get("content_hash") or ""),
            converted_to_long_term=bool(value.get("converted_to_long_term", False)),
            converted_at=(
                str(value["converted_at"]) if value.get("converted_at") is not None else None
            ),
        )


def build_midterm_records(
    messages: Iterable[Message],
    *,
    session_id: str,
    archive_uri: str,
) -> list[MidtermRecord]:
    """Merge consecutive same-speaker messages and pair adjacent blocks."""

    message_list = list(messages)
    legacy = _legacy_speaker_map(message_list)
    blocks: list[MidtermBlock] = []

    for message in message_list:
        text = _message_text(message)
        if not text:
            continue
        legacy_value = legacy.get(message.id)
        speaker = str(
            getattr(message, "speaker", None)
            or (legacy_value[0] if legacy_value else "")
            or ("assistant" if message.role == "assistant" else message.peer_id)
            or message.role
        ).strip()
        if legacy_value:
            text = legacy_value[1]
        else:
            prefix = f"{speaker}:"
            if text.casefold().startswith(prefix.casefold()):
                text = text[len(prefix) :].lstrip()

        if blocks and blocks[-1].speaker.casefold() == speaker.casefold():
            previous = blocks[-1]
            blocks[-1] = MidtermBlock(
                speaker=previous.speaker,
                role=previous.role,
                peer_id=previous.peer_id,
                created_at=previous.created_at,
                ended_at=message.created_at or previous.ended_at,
                message_ids=(*previous.message_ids, message.id),
                texts=(*previous.texts, text),
            )
        else:
            blocks.append(
                MidtermBlock(
                    speaker=speaker,
                    role=message.role,
                    peer_id=message.peer_id,
                    created_at=message.created_at,
                    ended_at=message.created_at,
                    message_ids=(message.id,),
                    texts=(text,),
                )
            )

    records: list[MidtermRecord] = []
    for offset in range(0, len(blocks), 2):
        pair_blocks = tuple(blocks[offset : offset + 2])
        source_ids = tuple(message_id for block in pair_blocks for message_id in block.message_ids)
        canonical = json.dumps(
            [
                {
                    "speaker": block.speaker,
                    "role": block.role,
                    "peer_id": block.peer_id,
                    "created_at": block.created_at,
                    "ended_at": block.ended_at,
                    "texts": list(block.texts),
                }
                for block in pair_blocks
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        records.append(
            MidtermRecord(
                pair_index=len(records) + 1,
                kind="dialogue_pair" if len(pair_blocks) == 2 else "single_turn",
                session_id=session_id,
                archive_uri=archive_uri,
                blocks=pair_blocks,
                source_message_ids=source_ids,
                content_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            )
        )
    return records


def render_midterm_record(record: MidtermRecord) -> str:
    first = record.blocks[0]
    participants = ", ".join(dict.fromkeys(block.speaker for block in record.blocks))
    lines = [
        "# Dialogue Pair" if record.kind == "dialogue_pair" else "# Single Turn",
        "",
        f"Session: {record.session_id}",
        f"Archive: {record.archive_uri}",
        f"Participants: {participants}",
        f"Timestamp: {first.created_at or ''}",
        f"Source-Messages: {', '.join(record.source_message_ids)}",
        f"Content-Hash: {record.content_hash}",
        f"Converted-To-Long-Term: {str(record.converted_to_long_term).lower()}",
        f"Converted-At: {record.converted_at or ''}",
    ]
    for block in record.blocks:
        lines.extend(["", f"## {block.speaker}", "", "\n".join(block.texts)])
    return "\n".join(lines).rstrip() + "\n"


def render_midterm_recall_record(record: MidtermRecord) -> str:
    """Render the stable evidence body stored in the vector index.

    Random session/archive identifiers and message UUIDs are intentionally omitted
    so importing the same conversation again produces the same URI and bytes.
    """

    first = record.blocks[0]
    participants = ", ".join(dict.fromkeys(block.speaker for block in record.blocks))
    lines = [
        "# Dialogue Pair" if record.kind == "dialogue_pair" else "# Single Turn",
        "",
        "Source-Kind: midterm_qa",
        f"Participants: {participants}",
        f"Timestamp: {first.created_at or ''}",
        f"Content-Hash: {record.content_hash}",
    ]
    for block in record.blocks:
        lines.extend(["", f"## {block.speaker}", "", "\n".join(block.texts)])
    return "\n".join(lines).rstrip() + "\n"


def serialize_midterm_manifest(records: Iterable[MidtermRecord]) -> str:
    return (
        json.dumps(
            {"version": 2, "records": [record.to_dict() for record in records]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


def deserialize_midterm_manifest(content: str) -> list[MidtermRecord]:
    payload = json.loads(content)
    return [MidtermRecord.from_dict(item) for item in payload.get("records") or []]
