# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenClaw migration helpers.

Phase 1 focuses on two import paths:

1. Native OpenClaw memory markdown files -> direct OpenViking memory import
2. Historical OpenClaw session transcripts -> session replay + commit
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence

from openviking_cli.exceptions import NotFoundError

_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-.+")
_SAFE_SEGMENT_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_TEXT_TYPES = {
    "text",
    "input_text",
    "output_text",
    "markdown",
    "output_markdown",
    "input_markdown",
}


@dataclass(frozen=True)
class OpenClawMemoryArtifact:
    """One OpenClaw memory file mapped onto a target OpenViking memory URI."""

    source_path: Path
    category: str
    uri: str
    kind: str


@dataclass(frozen=True)
class OpenClawTranscriptMessage:
    """One replayable transcript message."""

    role: str
    content: str
    created_at: str | None = None


@dataclass(frozen=True)
class OpenClawTranscriptSession:
    """One OpenClaw transcript session discovered on disk."""

    agent_id: str
    session_id: str
    session_key: str
    transcript_path: Path
    label: str = ""
    updated_at: str = ""
    channel: str = ""


class OpenClawMigrationClient(Protocol):
    """Synchronous client surface required by the OpenClaw migration helper."""

    def stat(self, uri: str) -> dict[str, Any]: ...

    def import_memory(
        self,
        uri: str,
        content: str,
        *,
        mode: str = "replace",
        wait: bool = True,
        timeout: float | None = None,
        telemetry: bool = False,
    ) -> dict[str, Any]: ...

    def session_exists(self, session_id: str) -> bool: ...

    def get_session(self, session_id: str, auto_create: bool = True) -> dict[str, Any]: ...

    def delete_session(self, session_id: str) -> Any: ...

    def create_session(self, session_id: str) -> Any: ...

    def add_message(
        self,
        session_id: str,
        role: str,
        *,
        content: str,
        created_at: str | None = None,
    ) -> Any: ...

    def commit_session(self, session_id: str, *, telemetry: bool = False) -> dict[str, Any]: ...

    def get_task(self, task_id: str) -> dict[str, Any]: ...


def _sanitize_segment(value: str, *, fallback: str) -> str:
    sanitized = _SAFE_SEGMENT_RE.sub("-", value.strip()).strip("._-")
    return sanitized or fallback


def _dedupe_uri(uri: str, seen: dict[str, int]) -> str:
    count = seen.get(uri, 0)
    seen[uri] = count + 1
    if count == 0:
        return uri

    path = Path(uri)
    suffix = path.suffix or ".md"
    return f"{uri[: -len(suffix)]}-{count + 1}{suffix}"


def _memory_uri_for_category(category: str, slug: str) -> str:
    if category in {"preferences", "entities", "events"}:
        return f"viking://user/memories/{category}/{slug}.md"
    return f"viking://agent/memories/{category}/{slug}.md"


def discover_openclaw_memory_artifacts(
    openclaw_dir: str | Path,
    *,
    category_override: str | None = None,
) -> list[OpenClawMemoryArtifact]:
    """Discover OpenClaw native memory markdown files.

    OpenClaw stores durable memory under ``workspace/MEMORY.md`` plus
    ``workspace/memory/*.md``. We map those files to deterministic OpenViking
    memory URIs so reruns can skip or overwrite cleanly.
    """

    if category_override == "profile":
        raise ValueError("category_override=profile is not supported for multi-file migration")
    if category_override and category_override not in {
        "preferences",
        "entities",
        "events",
        "cases",
        "patterns",
        "tools",
        "skills",
    }:
        raise ValueError(f"unsupported category_override: {category_override}")

    base = Path(openclaw_dir).expanduser()
    workspace = base / "workspace"
    memory_dir = workspace / "memory"
    seen: dict[str, int] = {}
    artifacts: list[OpenClawMemoryArtifact] = []

    memory_md = workspace / "MEMORY.md"
    if memory_md.is_file():
        category = category_override or "entities"
        uri = _memory_uri_for_category(category, "openclaw-memory")
        artifacts.append(
            OpenClawMemoryArtifact(
                source_path=memory_md,
                category=category,
                uri=_dedupe_uri(uri, seen),
                kind="memory-md",
            )
        )

    if not memory_dir.is_dir():
        return artifacts

    for path in sorted(memory_dir.glob("*.md")):
        stem = path.stem
        if category_override:
            category = category_override
        elif _DATE_ONLY_RE.fullmatch(stem):
            category = "events"
        elif _DATE_PREFIX_RE.fullmatch(stem):
            category = "cases"
        else:
            category = "entities"

        slug = _sanitize_segment(f"openclaw-{stem}", fallback="openclaw-memory")
        uri = _memory_uri_for_category(category, slug)
        kind = "daily-log" if _DATE_ONLY_RE.fullmatch(stem) else "session-summary"
        artifacts.append(
            OpenClawMemoryArtifact(
                source_path=path,
                category=category,
                uri=_dedupe_uri(uri, seen),
                kind=kind,
            )
        )

    return artifacts


def discover_openclaw_transcript_sessions(
    openclaw_dir: str | Path,
    *,
    agent_ids: Sequence[str] | None = None,
    include_orphans: bool = True,
) -> list[OpenClawTranscriptSession]:
    """Discover transcript jsonl files from ``~/.openclaw/agents/*/sessions``."""

    base = Path(openclaw_dir).expanduser()
    agents_dir = base / "agents"
    if not agents_dir.is_dir():
        return []

    selected = set(agent_ids or [])
    discovered: list[OpenClawTranscriptSession] = []

    for agent_dir in sorted(p for p in agents_dir.iterdir() if p.is_dir()):
        agent_id = agent_dir.name
        if selected and agent_id not in selected:
            continue

        sessions_dir = agent_dir / "sessions"
        index_path = sessions_dir / "sessions.json"
        seen_paths: set[Path] = set()
        index_data: dict[str, Any] = {}
        if index_path.is_file():
            try:
                index_data = json.loads(index_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                index_data = {}

        for session_key, raw_meta in sorted(index_data.items()):
            if not isinstance(raw_meta, dict):
                continue
            session_id = str(raw_meta.get("sessionId") or "").strip()
            session_file = str(raw_meta.get("sessionFile") or "").strip()
            if not session_id and session_file:
                session_id = Path(session_file).stem
            if not session_id:
                continue

            transcript_path = (
                Path(session_file) if session_file else sessions_dir / f"{session_id}.jsonl"
            )
            if not transcript_path.is_absolute():
                transcript_path = sessions_dir / transcript_path
            if not transcript_path.is_file():
                continue

            resolved = transcript_path.resolve()
            seen_paths.add(resolved)
            discovered.append(
                OpenClawTranscriptSession(
                    agent_id=agent_id,
                    session_id=session_id,
                    session_key=session_key,
                    transcript_path=resolved,
                    label=str(raw_meta.get("label") or ""),
                    updated_at=str(raw_meta.get("updatedAt") or ""),
                    channel=str(raw_meta.get("channel") or ""),
                )
            )

        if not include_orphans or not sessions_dir.is_dir():
            continue

        for transcript_path in sorted(sessions_dir.glob("*.jsonl")):
            resolved = transcript_path.resolve()
            if resolved in seen_paths:
                continue
            discovered.append(
                OpenClawTranscriptSession(
                    agent_id=agent_id,
                    session_id=transcript_path.stem,
                    session_key=transcript_path.stem,
                    transcript_path=resolved,
                )
            )

    return discovered


def _normalize_timestamp(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1_000_000_000_000:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    return None


def _extract_text_fragments(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        fragments: list[str] = []
        for item in value:
            fragments.extend(_extract_text_fragments(item))
        return fragments
    if not isinstance(value, dict):
        return []

    fragments: list[str] = []
    node_type = str(value.get("type") or "")
    if node_type in _TEXT_TYPES and isinstance(value.get("text"), str):
        text = value["text"].strip()
        if text:
            fragments.append(text)

    for key in ("text", "content", "parts"):
        child = value.get(key)
        if key == "text" and node_type in _TEXT_TYPES:
            continue
        fragments.extend(_extract_text_fragments(child))
    return fragments


def parse_openclaw_transcript(path: str | Path) -> list[OpenClawTranscriptMessage]:
    """Parse an OpenClaw jsonl transcript into replayable user/assistant messages."""

    transcript_path = Path(path)
    messages: list[OpenClawTranscriptMessage] = []
    for raw_line in transcript_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        payload = record.get("message") if record.get("type") == "message" else record
        if not isinstance(payload, dict):
            continue

        role = str(payload.get("role") or record.get("role") or "").strip()
        if role not in {"user", "assistant"}:
            continue

        fragments = _extract_text_fragments(
            payload.get("content", payload.get("parts", payload.get("text")))
        )
        text = "\n\n".join(fragment for fragment in fragments if fragment.strip()).strip()
        if not text:
            continue

        created_at = None
        for key in ("created_at", "createdAt", "timestamp", "time"):
            created_at = _normalize_timestamp(payload.get(key))
            if created_at:
                break
            created_at = _normalize_timestamp(record.get(key))
            if created_at:
                break

        messages.append(OpenClawTranscriptMessage(role=role, content=text, created_at=created_at))

    return messages


def _call_sync_client_method(
    client: OpenClawMigrationClient,
    method_name: str,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Call a sync client method and fail fast on async clients."""
    method = getattr(client, method_name)
    result = method(*args, **kwargs)
    if inspect.isawaitable(result):
        close = getattr(result, "close", None)
        if callable(close):
            close()
        raise TypeError(
            "migrate_openclaw() requires a synchronous client such as "
            "SyncOpenViking or SyncHTTPClient; async clients are not supported"
        )
    return result


def _uri_exists(client: OpenClawMigrationClient, uri: str) -> bool:
    try:
        _call_sync_client_method(client, "stat", uri)
    except NotFoundError:
        return False
    return True


def _session_exists(client: OpenClawMigrationClient, session_id: str) -> bool:
    if hasattr(client, "session_exists"):
        return bool(_call_sync_client_method(client, "session_exists", session_id))
    try:
        _call_sync_client_method(client, "get_session", session_id, auto_create=False)
    except NotFoundError:
        return False
    return True


def _stable_target_session_id(agent_id: str, session_id: str) -> str:
    raw = f"openclaw-{agent_id}-{session_id}"
    sanitized = _sanitize_segment(raw, fallback="openclaw-session")
    if len(sanitized) <= 96:
        return sanitized
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{sanitized[:83]}-{digest}"


def _wait_for_task(
    client: OpenClawMigrationClient,
    task_id: str,
    *,
    timeout: float,
    poll_interval: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() <= deadline:
        task = _call_sync_client_method(client, "get_task", task_id)
        if not task:
            time.sleep(poll_interval)
            continue
        status = str(task.get("status") or "").lower()
        if status == "completed":
            return task
        if status in {"failed", "cancelled"}:
            raise RuntimeError(f"task {task_id} {status}: {task}")
        time.sleep(poll_interval)
    raise TimeoutError(f"task {task_id} did not complete within {timeout} seconds")


def _summarize_records(records: Iterable[dict[str, Any]]) -> dict[str, int]:
    summary = {"planned": 0, "imported": 0, "skipped": 0, "failed": 0}
    for record in records:
        status = record.get("status")
        if status == "planned":
            summary["planned"] += 1
        elif status == "imported":
            summary["imported"] += 1
        elif str(status).startswith("skipped"):
            summary["skipped"] += 1
        else:
            summary["failed"] += 1
    return summary


def migrate_openclaw(
    client: OpenClawMigrationClient,
    openclaw_dir: str | Path,
    *,
    mode: str = "memory",
    dry_run: bool = False,
    overwrite: bool = False,
    wait: bool = True,
    timeout: float = 300.0,
    poll_interval: float = 1.0,
    agent_ids: Sequence[str] | None = None,
    category_override: str | None = None,
) -> dict[str, Any]:
    """Run an OpenClaw -> OpenViking migration.

    Returns a structured summary containing per-item records for both import
    paths. The caller can print or persist the records as needed.

    The helper expects a synchronous OpenViking client such as
    ``SyncOpenViking`` or ``SyncHTTPClient``. If an async client is passed,
    the first awaited method is rejected with ``TypeError`` instead of being
    silently ignored.
    """

    if mode not in {"memory", "transcript", "all"}:
        raise ValueError(f"unsupported migration mode: {mode}")

    memory_records: list[dict[str, Any]] = []
    transcript_records: list[dict[str, Any]] = []

    if mode in {"memory", "all"}:
        for artifact in discover_openclaw_memory_artifacts(
            openclaw_dir, category_override=category_override
        ):
            record = {
                "kind": artifact.kind,
                "source_path": str(artifact.source_path),
                "category": artifact.category,
                "uri": artifact.uri,
            }
            if dry_run:
                record["status"] = "planned"
                memory_records.append(record)
                continue

            content = artifact.source_path.read_text(encoding="utf-8").strip()
            if not content:
                record["status"] = "skipped_empty"
                memory_records.append(record)
                continue
            if not overwrite and _uri_exists(client, artifact.uri):
                record["status"] = "skipped_exists"
                memory_records.append(record)
                continue

            result = _call_sync_client_method(
                client,
                "import_memory",
                artifact.uri,
                content,
                mode="replace",
                wait=wait,
                timeout=timeout,
                telemetry=False,
            )
            record["status"] = "imported"
            record["result"] = result
            memory_records.append(record)

    if mode in {"transcript", "all"}:
        sessions = discover_openclaw_transcript_sessions(openclaw_dir, agent_ids=agent_ids)
        for session in sessions:
            target_session_id = _stable_target_session_id(session.agent_id, session.session_id)
            record = {
                "agent_id": session.agent_id,
                "session_key": session.session_key,
                "source_path": str(session.transcript_path),
                "target_session_id": target_session_id,
            }
            messages = parse_openclaw_transcript(session.transcript_path)
            record["message_count"] = len(messages)
            if dry_run:
                record["status"] = "planned"
                transcript_records.append(record)
                continue
            if not messages:
                record["status"] = "skipped_empty"
                transcript_records.append(record)
                continue
            if _session_exists(client, target_session_id):
                if not overwrite:
                    record["status"] = "skipped_exists"
                    transcript_records.append(record)
                    continue
                _call_sync_client_method(client, "delete_session", target_session_id)

            _call_sync_client_method(client, "create_session", target_session_id)
            for message in messages:
                _call_sync_client_method(
                    client,
                    "add_message",
                    target_session_id,
                    message.role,
                    content=message.content,
                    created_at=message.created_at,
                )
            commit_result = _call_sync_client_method(
                client, "commit_session", target_session_id, telemetry=False
            )
            record["commit_result"] = commit_result
            task_id = commit_result.get("task_id")
            if wait and task_id:
                record["task"] = _wait_for_task(
                    client,
                    task_id,
                    timeout=timeout,
                    poll_interval=poll_interval,
                )
            record["status"] = "imported"
            transcript_records.append(record)

    return {
        "mode": mode,
        "memory": {
            "records": memory_records,
            "summary": _summarize_records(memory_records),
        },
        "transcript": {
            "records": transcript_records,
            "summary": _summarize_records(transcript_records),
        },
    }
