# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Archive finalization workflow for sessions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List

from openviking.message import Message
from openviking.session.archive_finalize_tasks import archive_index_from_id
from openviking.utils.time_utils import get_current_timestamp
from openviking_cli.exceptions import NotInitializedError
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

if TYPE_CHECKING:
    from openviking.session.session import Session

logger = get_logger(__name__)


@dataclass
class ArchiveUsageRecord:
    """Usage record captured with an archived session segment."""

    uri: str
    type: str
    contribution: float = 0.0
    input: str = ""
    output: str = ""
    success: bool = True
    timestamp: str = field(default_factory=get_current_timestamp)


def archive_index_from_uri(archive_uri: str) -> int:
    """Parse archive_NNN suffix into an integer index."""
    archive_id = archive_uri.rstrip("/").rsplit("/", 1)[-1]
    try:
        return archive_index_from_id(archive_id)
    except ValueError as exc:
        raise ValueError(f"Invalid archive URI: {archive_uri}") from exc


def _usage_records_from_payload(payload: List[Dict[str, Any]]) -> List[ArchiveUsageRecord]:
    records: List[ArchiveUsageRecord] = []
    for item in payload:
        records.append(
            ArchiveUsageRecord(
                uri=str(item.get("uri", "")),
                type=str(item.get("type", "")),
                contribution=float(item.get("contribution", 0.0) or 0.0),
                input=str(item.get("input", "")),
                output=str(item.get("output", "")),
                success=bool(item.get("success", True)),
                timestamp=str(item.get("timestamp") or get_current_timestamp()),
            )
        )
    return records


class SessionArchiveFinalizer:
    """Finalize persisted session archive tasks."""

    def __init__(self, session: "Session"):
        self._session = session

    async def finalize_from_task(
        self,
        task_id: str,
        archive_uri: str,
        usage_records_payload: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Finalize one persisted archive task."""
        from openviking.service.task_tracker import get_task_tracker

        session = self._session
        tracker = get_task_tracker()
        archive_index = archive_index_from_uri(archive_uri)
        messages = await self._read_archive_messages_strict(archive_uri)
        first_message_id = messages[0].id if messages else ""
        last_message_id = messages[-1].id if messages else ""
        usage_records = _usage_records_from_payload(usage_records_payload)
        extraction_messages = await session._hydrate_tool_outputs_for_extraction(messages)

        await tracker.start(
            task_id,
            account_id=session.ctx.account_id,
            user_id=session.ctx.user.user_id,
        )
        latest_archive_overview = await session._get_latest_completed_archive_overview(
            exclude_archive_uri=archive_uri
        )
        summary = await session._generate_archive_summary_async(
            extraction_messages,
            latest_archive_overview=latest_archive_overview,
        )
        if not summary:
            raise RuntimeError("archive_summary_failed: empty summary")

        await self._write_archive_summary_files(archive_uri, summary)
        await self._merge_and_save_commit_meta(
            archive_index=archive_index,
            memories_extracted={},
            telemetry_snapshot=None,
        )
        await self.write_done_file(archive_uri, first_message_id, last_message_id)
        side_effect_result = await self._run_memory_side_effects_best_effort(
            archive_uri=archive_uri,
            messages=extraction_messages,
            usage_records=usage_records,
            latest_archive_overview=latest_archive_overview,
        )

        result = {
            "session_id": session.session_id,
            "archive_uri": archive_uri,
            "memories_extracted": side_effect_result["memories_extracted"],
            "session_skills_extracted": len(side_effect_result["session_skill_uris"]),
            "session_skill_uris": side_effect_result["session_skill_uris"],
            "active_count_updated": side_effect_result["active_count_updated"],
            "token_usage": {
                "llm": dict(session._meta.llm_token_usage),
                "embedding": dict(session._meta.embedding_token_usage),
                "total": {
                    "total_tokens": session._meta.llm_token_usage["total_tokens"]
                    + session._meta.embedding_token_usage["total_tokens"]
                },
            },
        }
        await tracker.complete(
            task_id,
            result,
            account_id=session.ctx.account_id,
            user_id=session.ctx.user.user_id,
        )
        return result

    async def write_done_file(
        self,
        archive_uri: str,
        first_message_id: str,
        last_message_id: str,
    ) -> None:
        """Write .done marker file to the archive directory."""
        session = self._session
        if not session._viking_fs:
            return
        content = json.dumps(
            {
                "starting_message_id": first_message_id,
                "ending_message_id": last_message_id,
            },
            ensure_ascii=False,
        )
        await session._viking_fs.write_file(
            uri=f"{archive_uri}/.done",
            content=content,
            ctx=session.ctx,
        )

    async def write_failed_marker(
        self,
        archive_uri: str,
        stage: str,
        error: str,
    ) -> None:
        """Persist a terminal failure marker for the archive."""
        session = self._session
        if not session._viking_fs:
            return
        payload = {
            "stage": stage,
            "error": error,
            "failed_at": get_current_timestamp(),
        }
        await session._viking_fs.write_file(
            uri=f"{archive_uri}/.failed.json",
            content=json.dumps(payload, ensure_ascii=False),
            ctx=session.ctx,
        )

    async def _write_archive_summary_files(self, archive_uri: str, summary: str) -> None:
        session = self._session
        abstract = session._extract_abstract_from_summary(summary)
        if not session._viking_fs:
            return
        await session._viking_fs.write_file(
            uri=f"{archive_uri}/.abstract.md",
            content=abstract,
            ctx=session.ctx,
        )
        await session._viking_fs.write_file(
            uri=f"{archive_uri}/.overview.md",
            content=summary,
            ctx=session.ctx,
        )
        await session._viking_fs.write_file(
            uri=f"{archive_uri}/.meta.json",
            content=json.dumps(
                {
                    "overview_tokens": -(-len(summary) // 4),
                    "abstract_tokens": -(-len(abstract) // 4),
                }
            ),
            ctx=session.ctx,
        )

    async def _read_archive_messages_strict(self, archive_uri: str) -> List[Message]:
        """Read archived messages and fail on malformed JSONL."""
        session = self._session
        if not session._viking_fs:
            raise NotInitializedError("VikingFS")
        content = await session._viking_fs.read_file(
            f"{archive_uri}/messages.jsonl",
            ctx=session.ctx,
        )
        messages: List[Message] = []
        for lineno, line in enumerate(content.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                messages.append(Message.from_dict(json.loads(line)))
            except Exception as exc:
                raise ValueError(
                    f"Invalid messages.jsonl at {archive_uri} line {lineno}: {exc}"
                ) from exc
        return messages

    async def _run_memory_side_effects_best_effort(
        self,
        archive_uri: str,
        messages: List[Message],
        usage_records: List[ArchiveUsageRecord],
        latest_archive_overview: str,
    ) -> Dict[str, Any]:
        """Run post-.done side effects without making archive completion fail."""
        session = self._session
        memories_extracted: Dict[str, int] = {}
        session_skill_uris: List[str] = []
        active_count_updated = 0
        try:
            ov_config = get_openviking_config()
            memory_extraction_enabled = bool(ov_config.memory.extraction_enabled)
            session_skill_extraction_enabled = bool(
                getattr(ov_config.memory, "session_skill_extraction_enabled", False)
            )
            if session._session_compressor and (
                memory_extraction_enabled or session_skill_extraction_enabled
            ):
                extracted = []
                if memory_extraction_enabled:
                    extracted = await session._session_compressor.extract_long_term_memories(
                        messages=messages,
                        user=session.user,
                        session_id=session.session_id,
                        ctx=session.ctx,
                        latest_archive_overview=latest_archive_overview,
                        archive_uri=archive_uri,
                    )
                has_agent_memory = hasattr(session._session_compressor, "extract_agent_memories")
                agent_result: Dict[str, List[Any]] = {"contexts": [], "session_skills": []}
                if has_agent_memory:
                    agent_result = await session._session_compressor.extract_agent_memories(
                        messages=messages,
                        ctx=session.ctx,
                        latest_archive_overview=latest_archive_overview,
                        archive_uri=archive_uri,
                    )
                agent_extracted = list(agent_result.get("contexts", []))
                session_skills = list(agent_result.get("session_skills", []))
                for ctx_item in list(extracted or []) + agent_extracted:
                    cat = getattr(ctx_item, "category", "") or "unknown"
                    memories_extracted[cat] = memories_extracted.get(cat, 0) + 1
                session_skill_uris = [
                    item.get("uri") or item.get("root_uri")
                    for item in session_skills
                    if isinstance(item, dict) and (item.get("uri") or item.get("root_uri"))
                ]

            if session._viking_fs:
                for usage in usage_records:
                    try:
                        await session._viking_fs.link(
                            session._session_uri,
                            usage.uri,
                            ctx=session.ctx,
                        )
                    except Exception as e:
                        logger.warning(f"Failed to create relation to {usage.uri}: {e}")

            if session._vikingdb_manager:
                uris = [u.uri for u in usage_records if u.uri]
                if uris:
                    try:
                        active_count_updated = (
                            await session._vikingdb_manager.increment_active_count(
                                session.ctx, uris
                            )
                        )
                    except Exception as e:
                        logger.debug(f"Could not update active_count for usage URIs: {e}")

            if memories_extracted:
                await self._merge_and_save_commit_meta(
                    archive_index=archive_index_from_uri(archive_uri),
                    memories_extracted=memories_extracted,
                    telemetry_snapshot=None,
                )
        except Exception as e:
            logger.warning(
                "Memory side effects failed for session %s archive %s: %s",
                session.session_id,
                archive_uri,
                e,
            )
        return {
            "memories_extracted": memories_extracted,
            "session_skill_uris": session_skill_uris,
            "active_count_updated": active_count_updated,
        }

    async def _merge_and_save_commit_meta(
        self,
        archive_index: int,
        memories_extracted: Dict[str, int],
        telemetry_snapshot: Any,
    ) -> None:
        """Reload and merge latest meta state before persisting commit results."""
        session = self._session
        latest_meta = session._meta
        try:
            meta_content = await session._viking_fs.read_file(
                f"{session._session_uri}/.meta.json",
                ctx=session.ctx,
            )
            latest_meta = session._meta.__class__.from_dict(json.loads(meta_content))
        except Exception:
            latest_meta = session._meta

        if telemetry_snapshot:
            llm = telemetry_snapshot.summary.get("tokens", {}).get("llm", {})
            latest_meta.llm_token_usage["prompt_tokens"] += llm.get("input", 0)
            latest_meta.llm_token_usage["completion_tokens"] += llm.get("output", 0)
            latest_meta.llm_token_usage["total_tokens"] += llm.get("total", 0)
            embedding = telemetry_snapshot.summary.get("tokens", {}).get("embedding", {})
            latest_meta.embedding_token_usage["total_tokens"] += embedding.get("total", 0)

        latest_meta.commit_count = max(latest_meta.commit_count, archive_index)
        for cat, count in memories_extracted.items():
            latest_meta.memories_extracted[cat] = latest_meta.memories_extracted.get(cat, 0) + count
            latest_meta.memories_extracted["total"] = (
                latest_meta.memories_extracted.get("total", 0) + count
            )
        latest_meta.last_commit_at = get_current_timestamp()
        latest_meta.message_count = await session._read_live_message_count()
        session._meta = latest_meta
        await session._save_meta()
