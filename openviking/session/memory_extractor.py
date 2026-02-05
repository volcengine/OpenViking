# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Memory Extractor for OpenViking.

Extracts 6 categories of memories from session:
- UserMemory: profile, preferences, entities, events
- AgentMemory: cases, patterns
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional
from uuid import uuid4

from openviking.core.context import Context, ContextType, Vectorize
from openviking.prompts import render_prompt
from openviking.storage.viking_fs import get_viking_fs
from openviking.utils import get_logger
from openviking.utils.config import get_openviking_config

logger = get_logger(__name__)


class MemoryCategory(str, Enum):
    """Memory category enumeration."""

    # UserMemory categories
    PROFILE = "profile"  # User profile (written to profile.md)
    PREFERENCES = "preferences"  # User preferences (aggregated by topic)
    ENTITIES = "entities"  # Entity memories (projects, people, concepts)
    EVENTS = "events"  # Event records (decisions, milestones)

    # AgentMemory categories
    CASES = "cases"  # Cases (specific problems + solutions)
    PATTERNS = "patterns"  # Patterns (reusable processes/methods)


@dataclass
class CandidateMemory:
    """Candidate memory extracted from session."""

    category: MemoryCategory
    abstract: str  # L0: One-sentence summary
    overview: str  # L1: Medium detail, free Markdown
    content: str  # L2: Full narrative, free Markdown
    source_session: str
    user: str


class MemoryExtractor:
    """Extracts memories from session messages with 6-category classification."""

    # Category to directory mapping
    CATEGORY_DIRS = {
        MemoryCategory.PROFILE: "memories/profile.md",  # User profile
        MemoryCategory.PREFERENCES: "memories/preferences",
        MemoryCategory.ENTITIES: "memories/entities",
        MemoryCategory.EVENTS: "memories/events",
        MemoryCategory.CASES: "memories/cases",
        MemoryCategory.PATTERNS: "memories/patterns",
    }

    def __init__(self):
        """Initialize memory extractor."""

    async def extract(
        self,
        context: dict,
        user: str,
        session_id: str,
    ) -> List[CandidateMemory]:
        """Extract memory candidates from messages."""
        user = user or "default"
        vlm = get_openviking_config().vlm
        if not vlm or not vlm.is_available():
            logger.warning("LLM not available, skipping memory extraction")
            return []

        # Format all messages
        messages = context["messages"]

        formatted_messages = "\n".join([f"[{m.role}]: {m.content}" for m in messages if m.content])

        if not formatted_messages:
            return []

        # Call LLM to extract memories
        prompt = render_prompt(
            "compression.memory_extraction",
            {
                "summary": "",
                "recent_messages": formatted_messages,
                "user": user,
                "feedback": "",
            },
        )

        try:
            from openviking.utils.llm import parse_json_from_response

            response = await vlm.get_completion_async(prompt)
            data = parse_json_from_response(response) or {}

            candidates = []
            for mem in data.get("memories", []):
                category_str = mem.get("category", "patterns")
                try:
                    category = MemoryCategory(category_str)
                except ValueError:
                    category = MemoryCategory.PATTERNS

                candidates.append(
                    CandidateMemory(
                        category=category,
                        abstract=mem.get("abstract", ""),
                        overview=mem.get("overview", ""),
                        content=mem.get("content", ""),
                        source_session=session_id,
                        user=user,
                    )
                )

            logger.info(f"Extracted {len(candidates)} candidate memories")
            return candidates

        except Exception as e:
            logger.error(f"Memory extraction failed: {e}")
            return []

    async def create_memory(
        self,
        candidate: CandidateMemory,
        user: str,
        session_id: str,
    ) -> Optional[Context]:
        """Create Context object from candidate and persist to AGFS as .md file."""
        viking_fs = get_viking_fs()
        if not viking_fs:
            logger.warning("VikingFS not available, skipping memory creation")
            return None

        # Special handling for profile: append to profile.md
        if candidate.category == MemoryCategory.PROFILE:
            await self._append_to_profile(candidate, viking_fs)
            memory_uri = "viking://user/memories/profile.md"
            memory = Context(
                uri=memory_uri,
                parent_uri="viking://user/memories",
                is_leaf=True,
                abstract=candidate.abstract,
                context_type=ContextType.MEMORY.value,
                category=candidate.category.value,
                session_id=session_id,
                user=user,
            )
            logger.info(f"uri {memory_uri} abstract: {candidate.abstract} content: {candidate.content}")
            memory.set_vectorize(Vectorize(text=candidate.content))
            return memory

        # Determine parent URI based on category
        if candidate.category in [
            MemoryCategory.PREFERENCES,
            MemoryCategory.ENTITIES,
            MemoryCategory.EVENTS,
        ]:
            parent_uri = f"viking://user/{self.CATEGORY_DIRS[candidate.category]}"
        else:  # CASES, PATTERNS
            parent_uri = f"viking://agent/{self.CATEGORY_DIRS[candidate.category]}"

        # Generate file URI (store directly as .md file, no directory creation)
        memory_id = f"mem_{str(uuid4())}"
        memory_uri = f"{parent_uri}/{memory_id}.md"

        # Write to AGFS as single .md file
        try:
            await viking_fs.write_file(memory_uri, candidate.content)
            logger.info(f"Created memory file: {memory_uri}")
        except Exception as e:
            logger.error(f"Failed to write memory to AGFS: {e}")
            return None

        # Create Context object
        memory = Context(
            uri=memory_uri,
            parent_uri=parent_uri,
            is_leaf=True,
            abstract=candidate.abstract,
            context_type=ContextType.MEMORY.value,
            category=candidate.category.value,
            session_id=session_id,
            user=user,
        )
        logger.info(f"uri {memory_uri} abstract: {candidate.abstract} content: {candidate.content}")
        memory.set_vectorize(Vectorize(text=candidate.content))
        return memory

    async def _append_to_profile(self, candidate: CandidateMemory, viking_fs) -> None:
        """Update user profile - always merge with existing content."""
        uri = "viking://user/memories/profile.md"
        existing = ""
        try:
            existing = await viking_fs.read_file(uri) or ""
        except Exception:
            pass

        if not existing.strip():
            await viking_fs.write_file(uri=uri, content=candidate.content)
            logger.info(f"Created profile at {uri}")
        else:
            merged = await self._merge_memory(existing, candidate.content, "profile")
            content = merged if merged else candidate.content
            await viking_fs.write_file(uri=uri, content=content)
            logger.info(f"Merged profile info to {uri}")

    async def _merge_memory(self, existing: str, new: str, category: str) -> Optional[str]:
        """Use LLM to merge existing and new memory content."""
        vlm = get_openviking_config().vlm
        if not vlm or not vlm.is_available():
            return None

        prompt = render_prompt(
            "compression.memory_merge",
            {"existing_content": existing, "new_content": new, "category": category},
        )

        try:
            merged = await vlm.get_completion_async(prompt)
            return merged.strip() if merged else None
        except Exception as e:
            logger.error(f"Memory merge failed: {e}")
            return None
