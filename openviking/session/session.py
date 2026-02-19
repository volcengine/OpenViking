# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Session management for OpenViking.

Session as Context: Sessions integrated into L0/L1/L2 system.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import uuid4

from openviking.message import Message, Part
from openviking.utils.time_utils import get_current_timestamp
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger, run_async
from openviking_cli.utils.config import get_openviking_config

if TYPE_CHECKING:
    from openviking.session.compressor import SessionCompressor
    from openviking.storage import VikingDBManager
    from openviking.storage.viking_fs import VikingFS

logger = get_logger(__name__)


@dataclass
class SessionCompression:
    """Session compression information."""

    summary: str = ""
    original_count: int = 0
    compressed_count: int = 0
    compression_index: int = 0


@dataclass
class SessionStats:
    """Session statistics information."""

    total_turns: int = 0
    total_tokens: int = 0
    compression_count: int = 0
    contexts_used: int = 0
    skills_used: int = 0
    memories_extracted: int = 0


@dataclass
class Usage:
    """Usage record."""

    uri: str
    type: str  # "context" | "skill"
    contribution: float = 0.0
    input: str = ""
    output: str = ""
    success: bool = True
    timestamp: str = field(default_factory=get_current_timestamp)


class Session:
    """Session management class - Message = role + parts."""

    def __init__(
        self,
        viking_fs: "VikingFS",
        vikingdb_manager: Optional["VikingDBManager"] = None,
        session_compressor: Optional["SessionCompressor"] = None,
        user: Optional["UserIdentifier"] = None,
        session_id: Optional[str] = None,
        auto_commit_threshold: int = 8000,
    ):
        self._viking_fs = viking_fs
        self._vikingdb_manager = vikingdb_manager
        self._session_compressor = session_compressor
        self.user = user or UserIdentifier.the_default_user()
        self.session_id = session_id or str(uuid4())
        self.created_at = datetime.now()
        self._auto_commit_threshold = auto_commit_threshold
        self._session_uri = f"viking://session/{self.session_id}"

        self._messages: List[Message] = []
        self._usage_records: List[Usage] = []
        self._compression: SessionCompression = SessionCompression()
        self._stats: SessionStats = SessionStats()
        self._loaded = False

        logger.info(f"Session created: {self.session_id} for user {self.user}")

    def load(self):
        """Load session data from storage."""
        if self._loaded:
            return

        try:
            content = run_async(self._viking_fs.read_file(f"{self._session_uri}/messages.jsonl"))
            self._messages = [
                Message.from_dict(json.loads(line))
                for line in content.strip().split("\n")
                if line.strip()
            ]
            logger.info(f"Session loaded: {self.session_id} ({len(self._messages)} messages)")
        except (FileNotFoundError, Exception):
            logger.debug(f"Session {self.session_id} not found, starting fresh")

        # Restore compression_index (scan history directory)
        try:
            history_items = run_async(self._viking_fs.ls(f"{self._session_uri}/history"))
            archives = [
                item["name"] for item in history_items if item["name"].startswith("archive_")
            ]
            if archives:
                max_index = max(int(a.split("_")[1]) for a in archives)
                self._compression.compression_index = max_index
                self._stats.compression_count = len(archives)
                logger.debug(f"Restored compression_index: {max_index}")
        except Exception:
            pass

        self._loaded = True

    @property
    def messages(self) -> List[Message]:
        """Get message list."""
        return self._messages

    # ============= Core methods =============

    def used(
        self,
        contexts: Optional[List[str]] = None,
        skill: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record actually used contexts and skills."""
        if contexts:
            for uri in contexts:
                usage = Usage(uri=uri, type="context")
                self._usage_records.append(usage)
                self._stats.contexts_used += 1
                logger.debug(f"Tracked context usage: {uri}")

        if skill:
            usage = Usage(
                uri=skill.get("uri", ""),
                type="skill",
                input=skill.get("input", ""),
                output=skill.get("output", ""),
                success=skill.get("success", True),
            )
            self._usage_records.append(usage)
            self._stats.skills_used += 1
            logger.debug(f"Tracked skill usage: {skill.get('uri')}")

    def add_message(
        self,
        role: str,
        parts: List[Part],
    ) -> Message:
        """Add a message."""
        msg = Message(
            id=f"msg_{uuid4().hex}",
            role=role,
            parts=parts,
            created_at=datetime.now(),
        )
        self._messages.append(msg)

        # Update statistics
        if role == "user":
            self._stats.total_turns += 1
        self._stats.total_tokens += len(msg.content) // 4

        self._append_to_jsonl(msg)
        return msg

    def update_tool_part(
        self,
        message_id: str,
        tool_id: str,
        output: str,
        status: str = "completed",
    ) -> None:
        """Update tool status."""
        msg = next((m for m in self._messages if m.id == message_id), None)
        if not msg:
            return

        tool_part = msg.find_tool_part(tool_id)
        if not tool_part:
            return

        tool_part.tool_output = output
        tool_part.tool_status = status

        self._save_tool_result(tool_id, msg, output, status)
        self._update_message_in_jsonl()

    def commit(self) -> Dict[str, Any]:
        """Commit session: create archive, extract memories, persist."""
        result = {
            "session_id": self.session_id,
            "status": "committed",
            "memories_extracted": 0,
            "active_count_updated": 0,
            "archived": False,
            "stats": None,
        }
        if not self._messages:
            return result

        # 1. Archive current messages
        self._compression.compression_index += 1
        messages_to_archive = self._messages.copy()

        summary = self._generate_archive_summary(messages_to_archive)
        archive_abstract = self._extract_abstract_from_summary(summary)
        archive_overview = summary

        self._write_archive(
            index=self._compression.compression_index,
            messages=messages_to_archive,
            abstract=archive_abstract,
            overview=archive_overview,
        )

        self._compression.original_count += len(messages_to_archive)
        result["archived"] = True

        self._messages.clear()
        logger.info(
            f"Archived: {len(messages_to_archive)} messages → history/archive_{self._compression.compression_index:03d}/"
        )

        # 2. Extract long-term memories
        if self._session_compressor:
            logger.info(
                f"Starting memory extraction from {len(messages_to_archive)} archived messages"
            )
            memories = run_async(
                self._session_compressor.extract_long_term_memories(
                    messages=messages_to_archive,
                    user=self.user,
                    session_id=self.session_id,
                )
            )
            logger.info(f"Extracted {len(memories)} memories")
            result["memories_extracted"] = len(memories)
            self._stats.memories_extracted += len(memories)

        # 3. Write current messages to AGFS
        self._write_to_agfs(self._messages)

        # 4. Create relations
        self._write_relations()

        # 5. Update active_count
        active_count_updated = self._update_active_counts()
        result["active_count_updated"] = active_count_updated

        # 6. Update statistics
        self._stats.compression_count = self._compression.compression_index
        result["stats"] = {
            "total_turns": self._stats.total_turns,
            "contexts_used": self._stats.contexts_used,
            "skills_used": self._stats.skills_used,
            "memories_extracted": self._stats.memories_extracted,
        }

        self._stats.total_tokens = 0
        logger.info(f"Session {self.session_id} committed")
        return result

    def _update_active_counts(self) -> int:
        """Update active_count for used contexts/skills."""
        if not self._vikingdb_manager:
            return 0

        updated = 0
        storage = self._vikingdb_manager

        for usage in self._usage_records:
            try:
                run_async(
                    storage.update(
                        collection="context",
                        filter={"uri": usage.uri},
                        update={"$inc": {"active_count": 1}},
                    )
                )
                updated += 1
            except Exception as e:
                logger.debug(f"Could not update active_count for {usage.uri}: {e}")

        if updated > 0:
            logger.info(f"Updated active_count for {updated} contexts/skills")
        return updated

    def get_context_for_search(
        self, query: str, max_archives: int = 3, max_messages: int = 20
    ) -> Dict[str, Any]:
        """Get session context for intent analysis.

        Args:
            query: Query string for matching relevant archives
            max_archives: Maximum number of archives to retrieve (default 3)
            max_messages: Maximum number of messages to retrieve (default 20)

        Returns:
            - summaries: Most relevant and recent archive overview list (List[str])
            - recent_messages: Recent message list (List[Message])
        """
        # 1. Recent messages
        recent_messages = list(self._messages[-max_messages:]) if self._messages else []

        # 2. Find most relevant and recent archives using query
        summaries = []
        if self.compression.compression_index > 0:
            try:
                history_items = run_async(self._viking_fs.ls(f"{self._session_uri}/history"))
                query_lower = query.lower()

                # Collect all archives with relevance scores
                scored_archives = []
                for item in history_items:
                    name = item.get("name") if isinstance(item, dict) else item
                    if name and name.startswith("archive_"):
                        overview_uri = f"{self._session_uri}/history/{name}/.overview.md"
                        try:
                            overview = run_async(self._viking_fs.read_file(overview_uri))
                            # Calculate relevance by keyword matching
                            score = 0
                            if query_lower in overview.lower():
                                score = overview.lower().count(query_lower)
                            # Infer time from name (higher archive_NNN = newer)
                            archive_num = int(name.split("_")[1]) if "_" in name else 0
                            scored_archives.append((score, archive_num, overview))
                        except Exception:
                            pass

                # Sort: relevance first, then time, take top N
                scored_archives.sort(key=lambda x: (x[0], x[1]), reverse=True)
                summaries = [overview for _, _, overview in scored_archives[:max_archives]]

            except Exception:
                pass

        return {
            "summaries": summaries,
            "recent_messages": recent_messages,
        }

    # ============= Internal methods =============

    def _extract_abstract_from_summary(self, summary: str) -> str:
        """Extract one-sentence overview from structured summary."""
        if not summary:
            return ""

        match = re.search(r"^\*\*[^*]+\*\*:\s*(.+)$", summary, re.MULTILINE)
        if match:
            return match.group(1).strip()

        first_line = summary.split("\n")[0].strip()
        return first_line if first_line else ""

    def _generate_archive_summary(self, messages: List[Message]) -> str:
        """Generate structured summary for archive."""
        if not messages:
            return ""

        formatted = "\n".join([f"[{m.role}]: {m.content}" for m in messages])

        vlm = get_openviking_config().vlm
        if vlm and vlm.is_available():
            try:
                from openviking.prompts import render_prompt

                prompt = render_prompt(
                    "compression.structured_summary",
                    {"messages": formatted},
                )
                return run_async(vlm.get_completion_async(prompt))
            except Exception as e:
                logger.warning(f"LLM summary failed: {e}")

        turn_count = len([m for m in messages if m.role == "user"])
        return f"# Session Summary\n\n**Overview**: {turn_count} turns, {len(messages)} messages"

    def _write_archive(
        self,
        index: int,
        messages: List[Message],
        abstract: str,
        overview: str,
    ) -> None:
        """Write archive to history/archive_N/."""
        if not self._viking_fs:
            return

        viking_fs = self._viking_fs
        archive_uri = f"{self._session_uri}/history/archive_{index:03d}"

        # Write messages.jsonl
        lines = [m.to_jsonl() for m in messages]
        run_async(
            viking_fs.write_file(
                uri=f"{archive_uri}/messages.jsonl",
                content="\n".join(lines) + "\n",
            )
        )

        run_async(viking_fs.write_file(uri=f"{archive_uri}/.abstract.md", content=abstract))
        run_async(viking_fs.write_file(uri=f"{archive_uri}/.overview.md", content=overview))

        logger.debug(f"Written archive: {archive_uri}")

    def _write_to_agfs(self, messages: List[Message]) -> None:
        """Write messages.jsonl to AGFS."""
        if not self._viking_fs:
            return

        viking_fs = self._viking_fs
        turn_count = len([m for m in messages if m.role == "user"])

        abstract = self._generate_abstract()
        overview = self._generate_overview(turn_count)

        lines = [m.to_jsonl() for m in messages]
        content = "\n".join(lines) + "\n" if lines else ""

        run_async(
            viking_fs.write_file(
                uri=f"{self._session_uri}/messages.jsonl",
                content=content,
            )
        )

        # Update L0/L1
        run_async(
            viking_fs.write_file(
                uri=f"{self._session_uri}/.abstract.md",
                content=abstract,
            )
        )
        run_async(
            viking_fs.write_file(
                uri=f"{self._session_uri}/.overview.md",
                content=overview,
            )
        )

    def _append_to_jsonl(self, msg: Message) -> None:
        """Append to messages.jsonl."""
        if not self._viking_fs:
            return
        run_async(
            self._viking_fs.append_file(
                f"{self._session_uri}/messages.jsonl",
                msg.to_jsonl() + "\n",
            )
        )

    def _update_message_in_jsonl(self) -> None:
        """Update message in messages.jsonl."""
        if not self._viking_fs:
            return

        lines = [m.to_jsonl() for m in self._messages]
        content = "\n".join(lines) + "\n"
        run_async(
            self._viking_fs.write_file(
                f"{self._session_uri}/messages.jsonl",
                content,
            )
        )

    def _save_tool_result(
        self,
        tool_id: str,
        msg: Message,
        output: str,
        status: str,
    ) -> None:
        """Save tool result to tools/{tool_id}/tool.json."""
        if not self._viking_fs:
            return

        tool_part = msg.find_tool_part(tool_id)
        if not tool_part:
            return

        tool_data = {
            "tool_id": tool_id,
            "tool_name": tool_part.tool_name,
            "session_id": self.session_id,
            "input": tool_part.tool_input,
            "output": output,
            "status": status,
            "time": {"created": get_current_timestamp()},
        }
        run_async(
            self._viking_fs.write_file(
                f"{self._session_uri}/tools/{tool_id}/tool.json",
                json.dumps(tool_data, ensure_ascii=False),
            )
        )

    def _generate_abstract(self) -> str:
        """Generate one-sentence summary for session."""
        if not self._messages:
            return ""

        first = self._messages[0].content
        turn_count = self._stats.total_turns
        return f"{turn_count} turns, starting from '{first[:50]}...'"

    def _generate_overview(self, turn_count: int) -> str:
        """Generate session directory structure description."""
        parts = [
            "# Session Directory Structure",
            "",
            "## File Description",
            f"- `messages.jsonl` - Current messages ({turn_count} turns)",
        ]
        if self._compression.compression_index > 0:
            parts.append(
                f"- `history/` - Historical archives ({self._compression.compression_index} total)"
            )
        parts.extend(
            [
                "",
                "## Access Methods",
                f"- Full conversation: `{self._session_uri}`",
            ]
        )
        if self._compression.compression_index > 0:
            parts.append(f"- Historical archives: `{self._session_uri}/history/`")
        return "\n".join(parts)

    def _write_relations(self) -> None:
        """Create relations to used contexts/tools."""
        if not self._viking_fs:
            return

        viking_fs = self._viking_fs
        for usage in self._usage_records:
            try:
                run_async(viking_fs.link(self._session_uri, usage.uri))
                logger.debug(f"Created relation: {self._session_uri} -> {usage.uri}")
            except Exception as e:
                logger.warning(f"Failed to create relation to {usage.uri}: {e}")

    # ============= Properties =============

    @property
    def uri(self) -> str:
        """Session's Viking URI."""
        return self._session_uri

    @property
    def summary(self) -> str:
        """Compression summary."""
        return self._compression.summary

    @property
    def compression(self) -> SessionCompression:
        """Get compression information."""
        return self._compression

    @property
    def usage_records(self) -> List[Usage]:
        """Get usage records."""
        return self._usage_records

    @property
    def stats(self) -> SessionStats:
        """Get session statistics."""
        return self._stats

    def __repr__(self) -> str:
        return f"Session(user={self.user}, id={self.session_id})"
