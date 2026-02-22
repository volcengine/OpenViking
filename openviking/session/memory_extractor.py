# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Memory Extractor for OpenViking.

Extracts 6 categories of memories from session:
- UserMemory: profile, preferences, entities, events
- AgentMemory: cases, patterns
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional
from uuid import uuid4

from openviking.core.context import Context, ContextType, Vectorize
from openviking.prompts import render_prompt
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

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
    language: str = "auto"


@dataclass
class MergedMemoryPayload:
    """Structured merged memory payload returned by one LLM call."""

    abstract: str
    overview: str
    content: str
    reason: str = ""


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

    @staticmethod
    def _detect_output_language(messages: List, fallback_language: str = "en") -> str:
        """Detect dominant language from user messages only.

        We intentionally scope detection to user role content so assistant/system
        text does not bias the target output language for stored memories.
        """
        fallback = (fallback_language or "en").strip() or "en"

        user_text = "\n".join(
            str(getattr(m, "content", "") or "")
            for m in messages
            if getattr(m, "role", "") == "user" and getattr(m, "content", None)
        )

        if not user_text:
            return fallback

        # Detect scripts that are largely language-unique first.
        counts = {
            "ko": len(re.findall(r"[\uac00-\ud7af]", user_text)),
            "ru": len(re.findall(r"[\u0400-\u04ff]", user_text)),
            "ar": len(re.findall(r"[\u0600-\u06ff]", user_text)),
        }

        detected, score = max(counts.items(), key=lambda item: item[1])
        if score > 0:
            return detected

        # CJK disambiguation:
        # - Japanese often includes Han characters too, so Han-count alone can
        #   misclassify Japanese as Chinese.
        # - If any Kana is present, prioritize Japanese.
        kana_count = len(re.findall(r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]", user_text))
        han_count = len(re.findall(r"[\u4e00-\u9fff]", user_text))

        if kana_count > 0:
            return "ja"
        if han_count > 0:
            return "zh-CN"

        return fallback

    async def extract(
        self,
        context: dict,
        user: UserIdentifier,
        session_id: str,
    ) -> List[CandidateMemory]:
        """Extract memory candidates from messages."""
        user = user
        vlm = get_openviking_config().vlm
        if not vlm or not vlm.is_available():
            logger.warning("LLM not available, skipping memory extraction")
            return []

        # Format all messages
        messages = context["messages"]

        formatted_messages = "\n".join([f"[{m.role}]: {m.content}" for m in messages if m.content])

        if not formatted_messages:
            return []

        config = get_openviking_config()
        fallback_language = (config.language_fallback or "en").strip() or "en"
        output_language = self._detect_output_language(
            messages, fallback_language=fallback_language
        )

        # Call LLM to extract memories
        prompt = render_prompt(
            "compression.memory_extraction",
            {
                "summary": "",
                "recent_messages": formatted_messages,
                "user": user._user_id,
                "feedback": "",
                "output_language": output_language,
            },
        )

        try:
            from openviking_cli.utils.llm import parse_json_from_response

            request_summary = {
                "user": user._user_id,
                "output_language": output_language,
                "recent_messages_len": len(formatted_messages),
                "recent_messages": formatted_messages,
            }
            logger.debug("Memory extraction LLM request summary: %s", request_summary)
            response = await vlm.get_completion_async(prompt)
            logger.debug("Memory extraction LLM raw response: %s", response)
            data = parse_json_from_response(response) or {}
            logger.debug("Memory extraction LLM parsed payload: %s", data)

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
                        language=output_language,
                    )
                )

            logger.info(
                f"Extracted {len(candidates)} candidate memories (language={output_language})"
            )
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
            payload = await self._append_to_profile(candidate, viking_fs)
            if not payload:
                return None
            memory_uri = "viking://user/memories/profile.md"
            memory = Context(
                uri=memory_uri,
                parent_uri="viking://user/memories",
                is_leaf=True,
                abstract=payload.abstract,
                context_type=ContextType.MEMORY.value,
                category=candidate.category.value,
                session_id=session_id,
                user=user,
            )
            logger.info(f"uri {memory_uri} abstract: {payload.abstract} content: {payload.content}")
            memory.set_vectorize(Vectorize(text=payload.content))
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

    async def _append_to_profile(
        self,
        candidate: CandidateMemory,
        viking_fs,
    ) -> Optional[MergedMemoryPayload]:
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
            return MergedMemoryPayload(
                abstract=candidate.abstract,
                overview=candidate.overview,
                content=candidate.content,
                reason="created",
            )
        else:
            payload = await self._merge_memory_bundle(
                existing_abstract="",
                existing_overview="",
                existing_content=existing,
                new_abstract=candidate.abstract,
                new_overview=candidate.overview,
                new_content=candidate.content,
                category="profile",
                output_language=candidate.language,
            )
            if not payload:
                logger.warning("Profile merge bundle failed; keeping existing profile unchanged")
                return None
            await viking_fs.write_file(uri=uri, content=payload.content)
            logger.info(f"Merged profile info to {uri}")
            return payload

    async def _merge_memory_bundle(
        self,
        existing_abstract: str,
        existing_overview: str,
        existing_content: str,
        new_abstract: str,
        new_overview: str,
        new_content: str,
        category: str,
        output_language: str = "auto",
    ) -> Optional[MergedMemoryPayload]:
        """Use one LLM call to generate merged L0/L1/L2 payload."""
        vlm = get_openviking_config().vlm
        if not vlm or not vlm.is_available():
            return None

        prompt = render_prompt(
            "compression.memory_merge_bundle",
            {
                "existing_abstract": existing_abstract,
                "existing_overview": existing_overview,
                "existing_content": existing_content,
                "new_abstract": new_abstract,
                "new_overview": new_overview,
                "new_content": new_content,
                "category": category,
                "output_language": output_language,
            },
        )

        try:
            from openviking_cli.utils.llm import parse_json_from_response

            response = await vlm.get_completion_async(prompt)
            data = parse_json_from_response(response) or {}
            if not isinstance(data, dict):
                logger.error("Memory merge bundle parse failed: non-dict payload")
                return None

            abstract = str(data.get("abstract", "") or "").strip()
            overview = str(data.get("overview", "") or "").strip()
            content = str(data.get("content", "") or "").strip()
            reason = str(data.get("reason", "") or "").strip()
            decision = str(data.get("decision", "") or "").strip().lower()

            if decision and decision != "merge":
                logger.error("Memory merge bundle invalid decision=%s", decision)
                return None
            if not abstract or not content:
                logger.error(
                    "Memory merge bundle missing required fields abstract/content: %s",
                    data,
                )
                return None

            return MergedMemoryPayload(
                abstract=abstract,
                overview=overview,
                content=content,
                reason=reason,
            )
        except Exception as e:
            logger.error(f"Memory merge bundle failed: {e}")
            return None
