# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Memory Extractor for OpenViking.

Extracts 8 categories of memories from session:
- UserMemory: profile, preferences, entities, events
- AgentMemory: cases, patterns, tools, skills
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional
from uuid import uuid4

from openviking.core.context import Context, ContextType, Vectorize
from openviking.prompts import render_prompt
from openviking.server.identity import RequestContext
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import get_current_telemetry
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)

FIELD_MAX_LENGTH = 1000
FIELD_MAX_LENGTHS = {
    "best_for": 500,
    "optimal_params": 800,
    "common_failures": 1000,
    "recommendation": 500,
    "recommended_flow": 800,
    "key_dependencies": 500,
}


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

    # Tool/Skill Memory categories
    TOOLS = "tools"  # Tool usage memories (optimization, statistics)
    SKILLS = "skills"  # Skill execution memories (workflow, strategy)


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
class ToolSkillCandidateMemory(CandidateMemory):
    """Tool/Skill Memory 专用候选，扩展名称字段。"""

    tool_name: str = ""  # Tool 名称（用于 tools 类别）
    skill_name: str = ""  # Skill 名称（用于 skills 类别）
    # tool_status: str = "completed"  # completed | error
    duration_ms: int = 0  # 执行耗时（毫秒）
    prompt_tokens: int = 0  # 输入 Token
    completion_tokens: int = 0  # 输出 Token
    call_time: int = 0  # 调用次数
    success_time: int = 0  # 成功调用次数
    best_for: str = ""
    optimal_params: str = ""
    recommended_flow: str = ""
    key_dependencies: str = ""
    common_failures: str = ""
    recommendation: str = ""


@dataclass
class MergedMemoryPayload:
    """Structured merged memory payload returned by one LLM call."""

    abstract: str
    overview: str
    content: str
    reason: str = ""


class MemoryExtractor:
    """Extracts memories from session messages with 8-category classification."""

    # Category to directory mapping
    CATEGORY_DIRS = {
        MemoryCategory.PROFILE: "memories/profile.md",  # User profile
        MemoryCategory.PREFERENCES: "memories/preferences",
        MemoryCategory.ENTITIES: "memories/entities",
        MemoryCategory.EVENTS: "memories/events",
        MemoryCategory.CASES: "memories/cases",
        MemoryCategory.PATTERNS: "memories/patterns",
        # Tool/Skill Memory categories
        MemoryCategory.TOOLS: "memories/tools",
        MemoryCategory.SKILLS: "memories/skills",
    }

    # Categories that belong to user space
    _USER_CATEGORIES = {
        MemoryCategory.PROFILE,
        MemoryCategory.PREFERENCES,
        MemoryCategory.ENTITIES,
        MemoryCategory.EVENTS,
    }

    # Categories that belong to agent space
    _AGENT_CATEGORIES = {
        MemoryCategory.CASES,
        MemoryCategory.PATTERNS,
    }

    def __init__(self):
        """Initialize memory extractor."""
        self._tool_desc_cache: dict[str, str] = {}
        self._tool_desc_cache_ready: bool = False

    @staticmethod
    def _get_owner_space(category: MemoryCategory, ctx: RequestContext) -> str:
        """Derive owner_space from memory category.

        PROFILE / PREFERENCES / ENTITIES / EVENTS → user_space
        CASES / PATTERNS → agent_space
        """
        if category in MemoryExtractor._USER_CATEGORIES:
            return ctx.user.user_space_name()
        return ctx.user.agent_space_name()

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

        # Detect scripts that are largely language-unique.
        # Require threshold to avoid misclassifying mixed-language texts
        # (e.g., Chinese with a single Cyrillic letter).
        total_chars = len(re.findall(r"\S", user_text))
        if total_chars == 0:
            return fallback

        counts = {
            "ko": len(re.findall(r"[\uac00-\ud7af]", user_text)),
            "ru": len(re.findall(r"[\u0400-\u04ff]", user_text)),
            "ar": len(re.findall(r"[\u0600-\u06ff]", user_text)),
        }

        detected, score = max(counts.items(), key=lambda item: item[1])
        # Threshold: at least 2 chars AND at least 10% of non-whitespace chars
        if score >= 2 and score / total_chars >= 0.10:
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

    def _format_message_with_parts(self, msg) -> str:
        """格式化单条消息，包含文本和工具调用"""
        import json

        from openviking.message.part import ToolPart

        parts = getattr(msg, "parts", [])
        lines = []

        for part in parts:
            if hasattr(part, "text") and part.text:
                lines.append(part.text)
            elif isinstance(part, ToolPart):
                tool_info = {
                    "type": "tool_call",
                    "tool_name": part.tool_name,
                    "tool_input": part.tool_input,
                    "tool_output": part.tool_output[:500] if part.tool_output else "",
                    "tool_status": part.tool_status,
                    "duration_ms": part.duration_ms,
                }
                if part.skill_uri:
                    skill_name = part.skill_uri.rstrip("/").split("/")[-1]
                    tool_info["skill_name"] = skill_name
                lines.append(f"[ToolCall] {json.dumps(tool_info, ensure_ascii=False)}")

        return "\n".join(lines) if lines else ""

    async def extract(
        self,
        context: dict,
        user: UserIdentifier,
        session_id: str,
        *,
        strict: bool = False,
    ) -> List[CandidateMemory]:
        """Extract memory candidates from messages.

        When ``strict`` is True, extraction failures are re-raised as
        ``RuntimeError`` so async task tracking can mark tasks as failed.
        """
        user = user
        vlm = get_openviking_config().vlm
        if not vlm or not vlm.is_available():
            logger.warning("LLM not available, skipping memory extraction")
            return []

        telemetry = get_current_telemetry()
        messages = context["messages"]
        from openviking.message.part import ToolPart

        from .tool_skill_utils import (
            calibrate_skill_name,
            calibrate_tool_name,
            collect_skill_stats,
            collect_tool_stats,
        )

        tool_parts = []
        tool_stats_map = {}
        skill_stats_map = {}
        formatted_messages = ""
        output_language = "en"
        prompt = ""

        with telemetry.measure("memory.extract.stage.prepare_inputs"):
            for msg in messages:
                for part in getattr(msg, "parts", []):
                    if isinstance(part, ToolPart):
                        tool_parts.append(part)

            formatted_lines = []
            for m in messages:
                msg_content = self._format_message_with_parts(m)
                if msg_content:
                    formatted_lines.append(f"[{m.role}]: {msg_content}")

            formatted_messages = "\n".join(formatted_lines)

            if not formatted_messages:
                logger.warning("No formatted messages, returning empty list")
                return []

            config = get_openviking_config()
            fallback_language = (config.language_fallback or "en").strip() or "en"
            output_language = self._detect_output_language(
                messages, fallback_language=fallback_language
            )
            history_summary = str(context.get("summary") or "")

            prompt = render_prompt(
                "compression.memory_extraction",
                {
                    "summary": history_summary,
                    "recent_messages": formatted_messages,
                    "user": user._user_id,
                    "feedback": "",
                    "output_language": output_language,
                },
            )

        with telemetry.measure("memory.extract.stage.tool_skill_stats"):
            tool_stats_map = collect_tool_stats(tool_parts)
            skill_stats_map = collect_skill_stats(tool_parts)

        try:
            from openviking_cli.utils.llm import parse_json_from_response

            request_summary = {
                "user": user._user_id,
                "output_language": output_language,
                "summary_len": len(history_summary),
                "recent_messages_len": len(formatted_messages),
                "summary": history_summary,
                "recent_messages": formatted_messages,
            }
            logger.debug("Memory extraction LLM request summary: %s", request_summary)
            with telemetry.measure("memory.extract.stage.llm_extract"):
                response = await vlm.get_completion_async(prompt)
            logger.debug("Memory extraction LLM raw response: %s", response)
            with telemetry.measure("memory.extract.stage.normalize_candidates"):
                data = parse_json_from_response(response) or {}
                if isinstance(data, list):
                    logger.warning(
                        "Memory extraction received list instead of dict; wrapping as memories"
                    )
                    data = {"memories": data}
                elif not isinstance(data, dict):
                    logger.warning(
                        "Memory extraction received unexpected type %s; skipping",
                        type(data).__name__,
                    )
                    data = {}
            logger.debug("Memory extraction LLM parsed payload: %s", data)

            candidates = []
            for mem in data.get("memories", []):
                category_str = mem.get("category", "patterns")
                try:
                    category = MemoryCategory(category_str)
                except ValueError:
                    category = MemoryCategory.PATTERNS

                # 只在 tools/skills 时使用 ToolSkillCandidateMemory
                if category in (MemoryCategory.TOOLS, MemoryCategory.SKILLS):
                    with telemetry.measure("memory.extract.stage.tool_skill_stats"):
                        llm_tool_name = mem.get("tool_name", "") or ""
                        llm_skill_name = mem.get("skill_name", "") or ""

                        tool_name = ""
                        skill_name = ""
                        stats = {}

                        if category == MemoryCategory.TOOLS:
                            canonical_tool_name, _ = calibrate_tool_name(llm_tool_name, tool_parts)
                            if not canonical_tool_name:
                                continue
                            tool_name = canonical_tool_name
                            stats = tool_stats_map.get(tool_name, {})

                        if category == MemoryCategory.SKILLS:
                            canonical_skill_name, _ = calibrate_skill_name(
                                llm_skill_name, tool_parts
                            )
                            if not canonical_skill_name:
                                continue
                            skill_name = canonical_skill_name
                            stats = skill_stats_map.get(skill_name, {})

                        call_time = stats.get("call_count", 0)
                        if call_time == 0:
                            continue

                        candidates.append(
                            ToolSkillCandidateMemory(
                                category=category,
                                abstract=mem.get("abstract", ""),
                                overview=mem.get("overview", ""),
                                content=mem.get("content", ""),
                                source_session=session_id,
                                user=user,
                                language=output_language,
                                tool_name=tool_name,
                                skill_name=skill_name,
                                call_time=call_time,
                                success_time=stats.get("success_time", 0),
                                duration_ms=(
                                    stats.get("duration_ms", 0)
                                    if category == MemoryCategory.TOOLS
                                    else 0
                                ),
                                prompt_tokens=(
                                    stats.get("prompt_tokens", 0)
                                    if category == MemoryCategory.TOOLS
                                    else 0
                                ),
                                completion_tokens=(
                                    stats.get("completion_tokens", 0)
                                    if category == MemoryCategory.TOOLS
                                    else 0
                                ),
                                best_for=str(mem.get("best_for", "") or "").strip(),
                                optimal_params=str(mem.get("optimal_params", "") or "").strip(),
                                recommended_flow=str(mem.get("recommended_flow", "") or "").strip(),
                                key_dependencies=str(mem.get("key_dependencies", "") or "").strip(),
                                common_failures=str(mem.get("common_failures", "") or "").strip(),
                                recommendation=str(mem.get("recommendation", "") or "").strip(),
                            )
                        )
                else:
                    # 现有逻辑不变，前向兼容
                    with telemetry.measure("memory.extract.stage.normalize_candidates"):
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
            if strict:
                raise RuntimeError(f"memory_extraction_failed: {e}") from e
            return []

    async def extract_strict(
        self,
        context: dict,
        user: UserIdentifier,
        session_id: str,
    ) -> List[CandidateMemory]:
        """Compatibility wrapper: strict mode delegates to ``extract``."""
        return await self.extract(context, user, session_id, strict=True)

    async def create_memory(
        self,
        candidate: CandidateMemory,
        user: str,
        session_id: str,
        ctx: RequestContext,
    ) -> Optional[Context]:
        """Create Context object from candidate and persist to AGFS as .md file."""
        viking_fs = get_viking_fs()
        if not viking_fs:
            logger.warning("VikingFS not available, skipping memory creation")
            return None

        owner_space = self._get_owner_space(candidate.category, ctx)

        # Special handling for profile: append to profile.md
        if candidate.category == MemoryCategory.PROFILE:
            payload = await self._append_to_profile(candidate, viking_fs, ctx=ctx)
            if not payload:
                return None
            user_space = ctx.user.user_space_name()
            memory_uri = f"viking://user/{user_space}/memories/profile.md"
            memory = Context(
                uri=memory_uri,
                parent_uri=f"viking://user/{user_space}/memories",
                is_leaf=True,
                abstract=payload.abstract,
                context_type=ContextType.MEMORY.value,
                category=candidate.category.value,
                session_id=session_id,
                user=user,
                account_id=ctx.account_id,
                owner_space=owner_space,
            )
            logger.info(f"uri {memory_uri} abstract: {payload.abstract} content: {payload.content}")
            memory.set_vectorize(Vectorize(text=payload.content))
            return memory

        # Determine parent URI based on category
        cat_dir = self.CATEGORY_DIRS[candidate.category]
        if candidate.category in [
            MemoryCategory.PREFERENCES,
            MemoryCategory.ENTITIES,
            MemoryCategory.EVENTS,
        ]:
            parent_uri = f"viking://user/{ctx.user.user_space_name()}/{cat_dir}"
        else:  # CASES, PATTERNS
            parent_uri = f"viking://agent/{ctx.user.agent_space_name()}/{cat_dir}"

        # Generate file URI (store directly as .md file, no directory creation)
        memory_id = f"mem_{str(uuid4())}"
        memory_uri = f"{parent_uri}/{memory_id}.md"

        # Write to AGFS as single .md file
        try:
            await viking_fs.write_file(memory_uri, candidate.content, ctx=ctx)
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
            account_id=ctx.account_id,
            owner_space=owner_space,
        )
        logger.info(f"uri {memory_uri} abstract: {candidate.abstract} content: {candidate.content}")
        memory.set_vectorize(Vectorize(text=candidate.content))
        return memory

    async def _append_to_profile(
        self,
        candidate: CandidateMemory,
        viking_fs,
        ctx: RequestContext,
    ) -> Optional[MergedMemoryPayload]:
        """Update user profile - always merge with existing content."""
        uri = f"viking://user/{ctx.user.user_space_name()}/memories/profile.md"
        existing = ""
        try:
            existing = await viking_fs.read_file(uri, ctx=ctx) or ""
        except Exception:
            pass

        if not existing.strip():
            await viking_fs.write_file(uri=uri, content=candidate.content, ctx=ctx)
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
            await viking_fs.write_file(uri=uri, content=payload.content, ctx=ctx)
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

    async def _merge_tool_memory(
        self, tool_name: str, candidate: CandidateMemory, ctx: "RequestContext"
    ) -> Optional[Context]:
        """合并 Tool Memory，统计数据用 Python 累加"""
        if not tool_name or not tool_name.strip():
            logger.warning("Tool name is empty, skipping tool memory merge")
            return None

        agent_space = ctx.user.agent_space_name()
        uri = f"viking://agent/{agent_space}/memories/tools/{tool_name}.md"
        viking_fs = get_viking_fs()

        if not viking_fs:
            logger.warning("VikingFS not available, skipping tool memory merge")
            return None

        existing = ""
        try:
            existing = await viking_fs.read_file(uri, ctx=ctx) or ""
        except NotFoundError:
            existing = ""
        except Exception as e:
            logger.warning(
                "Failed to read existing tool memory %s: %s; skipping write to avoid data loss",
                uri,
                e,
            )
            return None

        if not isinstance(candidate, ToolSkillCandidateMemory):
            logger.warning("Tool memory merge requires ToolSkillCandidateMemory, skipping")
            return None

        if candidate.call_time <= 0:
            logger.warning("Tool memory merge skipped due to call_time=0: %s", tool_name)
            return None

        new_stats = {
            "total_calls": candidate.call_time,
            "success_count": candidate.success_time,
            "fail_count": candidate.call_time - candidate.success_time,
            "total_time_ms": candidate.duration_ms or 0,
            "total_tokens": (candidate.prompt_tokens or 0) + (candidate.completion_tokens or 0),
        }
        new_guidelines = (candidate.content or "").strip()
        abstract_override: Optional[str] = None
        new_fields = {
            "best_for": (candidate.best_for or "").strip(),
            "optimal_params": (candidate.optimal_params or "").strip(),
            "common_failures": (candidate.common_failures or "").strip(),
            "recommendation": (candidate.recommendation or "").strip(),
        }
        fallback_fields = self._extract_tool_memory_context_fields_from_text(
            "\n".join([str(candidate.overview or "").strip(), str(candidate.content or "").strip()])
        )
        for k, v in fallback_fields.items():
            if not new_fields.get(k) and v:
                new_fields[k] = v.strip()

        if not existing.strip():
            merged_stats = self._compute_statistics_derived(new_stats)
            merged_content = self._generate_tool_memory_content(
                tool_name, merged_stats, new_guidelines, fields=new_fields
            )
            await viking_fs.write_file(uri=uri, content=merged_content, ctx=ctx)
            return self._create_tool_context(uri, candidate, ctx)

        existing_stats = self._parse_tool_statistics(existing)
        merged_stats = self._merge_tool_statistics(existing_stats, new_stats)
        if merged_stats.get("total_calls", 0) < existing_stats.get("total_calls", 0):
            logger.warning(
                "Tool memory merge violates monotonic total_calls: tool=%s existing=%s merged=%s; skipping write",
                tool_name,
                existing_stats.get("total_calls", 0),
                merged_stats.get("total_calls", 0),
            )
            return None
        existing_guidelines = self._extract_tool_guidelines(existing)
        if existing_guidelines is None:
            existing_guidelines = ""
        existing_fields = self._extract_tool_memory_context_fields_from_text(existing)
        merged_fields = {
            "best_for": await self._merge_kv_field(
                existing_fields.get("best_for", ""), new_fields.get("best_for", ""), "best_for"
            ),
            "optimal_params": await self._merge_kv_field(
                existing_fields.get("optimal_params", ""),
                new_fields.get("optimal_params", ""),
                "optimal_params",
            ),
            "common_failures": await self._merge_kv_field(
                existing_fields.get("common_failures", ""),
                new_fields.get("common_failures", ""),
                "common_failures",
            ),
            "recommendation": await self._merge_kv_field(
                existing_fields.get("recommendation", ""),
                new_fields.get("recommendation", ""),
                "recommendation",
            ),
        }
        if new_guidelines:
            payload = await self._merge_memory_bundle(
                existing_abstract="",
                existing_overview="",
                existing_content=existing_guidelines,
                new_abstract=candidate.abstract,
                new_overview=candidate.overview,
                new_content=new_guidelines,
                category="tools",
                output_language=candidate.language,
            )
            if payload and payload.content:
                merged_guidelines = payload.content.strip()
                if payload.abstract:
                    abstract_override = payload.abstract.strip() or None
            else:
                merged_guidelines = (existing_guidelines + "\n\n" + new_guidelines).strip()

        merged_content = self._generate_tool_memory_content(
            tool_name, merged_stats, merged_guidelines, fields=merged_fields
        )
        await viking_fs.write_file(uri=uri, content=merged_content, ctx=ctx)
        return self._create_tool_context(uri, candidate, ctx, abstract_override=abstract_override)

    def _compute_statistics_derived(self, stats: dict) -> dict:
        """计算派生统计数据（平均值、成功率）"""
        if stats["total_calls"] > 0:
            stats["avg_time_ms"] = stats["total_time_ms"] / stats["total_calls"]
            stats["avg_tokens"] = stats["total_tokens"] / stats["total_calls"]
            stats["success_rate"] = stats["success_count"] / stats["total_calls"]
        else:
            stats["avg_time_ms"] = 0
            stats["avg_tokens"] = 0
            stats["success_rate"] = 0
        return stats

    def _parse_tool_statistics(self, content: str) -> dict:
        """从 Tools Markdown 内容中解析 Tools 已有信息，用于后续统计分析"""
        stats = {
            "total_calls": 0,
            "success_count": 0,
            "fail_count": 0,
            "total_time_ms": 0,
            "total_tokens": 0,
        }

        match = re.search(r"总调用次数(?:\*+)?\s*[:：]\s*(\d+)", content)
        if match:
            stats["total_calls"] = int(match.group(1))
        else:
            match = re.search(r"(?im)^Based on\s+(\d+)\s+historical\s+calls\s*:", content)
            if match:
                stats["total_calls"] = int(match.group(1))

        match = re.search(
            r"成功率(?:\*+)?\s*[:：]\s*([\d.]+)%\s*[（(]\s*(\d+)\s*成功\s*[，,]\s*(\d+)\s*失败",
            content,
        )
        if match:
            stats["success_count"] = int(match.group(2))
            stats["fail_count"] = int(match.group(3))
            if stats["total_calls"] <= 0:
                stats["total_calls"] = stats["success_count"] + stats["fail_count"]
        else:
            match = re.search(r"成功率(?:\*+)?\s*[:：]\s*([\d.]+)%", content)
            if match and stats["total_calls"] > 0:
                success_rate = float(match.group(1)) / 100
                stats["success_count"] = int(stats["total_calls"] * success_rate)
                stats["fail_count"] = stats["total_calls"] - stats["success_count"]
            else:
                match = re.search(
                    r"(?im)^-\s*Success rate:\s*([\d.]+)%\s*\((\d+)\s+successful,\s*(\d+)\s+failed\)",
                    content,
                )
                if match:
                    stats["success_count"] = int(match.group(2))
                    stats["fail_count"] = int(match.group(3))
                    if stats["total_calls"] <= 0:
                        stats["total_calls"] = stats["success_count"] + stats["fail_count"]
                else:
                    match = re.search(r"(?im)^-\s*Success rate:\s*([\d.]+)%", content)
                    if match and stats["total_calls"] > 0:
                        success_rate = float(match.group(1)) / 100
                        stats["success_count"] = int(stats["total_calls"] * success_rate)
                        stats["fail_count"] = stats["total_calls"] - stats["success_count"]

        match = re.search(r"平均耗时(?:\*+)?\s*[:：]\s*([\d.]+)ms", content)
        if match and stats["total_calls"] > 0:
            stats["total_time_ms"] = float(match.group(1)) * stats["total_calls"]
        else:
            match = re.search(r"平均耗时(?:\*+)?\s*[:：]\s*([\d.]+)s", content)
            if match and stats["total_calls"] > 0:
                stats["total_time_ms"] = float(match.group(1)) * 1000 * stats["total_calls"]
        if stats["total_time_ms"] == 0:
            match = re.search(r"(?im)^-\s*Avg time:\s*([\d.]+)s", content)
            if match and stats["total_calls"] > 0:
                stats["total_time_ms"] = float(match.group(1)) * 1000 * stats["total_calls"]
            else:
                match = re.search(r"(?im)^-\s*Avg time:\s*([\d.]+)ms", content)
                if match and stats["total_calls"] > 0:
                    stats["total_time_ms"] = float(match.group(1)) * stats["total_calls"]

        match = re.search(r"平均Token(?:\*+)?\s*[:：]\s*(\d+)", content)
        if match and stats["total_calls"] > 0:
            stats["total_tokens"] = int(match.group(1)) * stats["total_calls"]
        else:
            match = re.search(r"(?im)^-\s*Avg time:.*?Avg tokens:\s*(\d+)", content)
            if match and stats["total_calls"] > 0:
                stats["total_tokens"] = int(match.group(1)) * stats["total_calls"]
            else:
                match = re.search(r"(?im)^-\s*Avg tokens:\s*(\d+)", content)
                if match and stats["total_calls"] > 0:
                    stats["total_tokens"] = int(match.group(1)) * stats["total_calls"]

        return stats

    def _merge_tool_statistics(self, existing: dict, new: dict) -> dict:
        """累加Tools统计数据"""
        merged = {
            "total_calls": existing["total_calls"] + new["total_calls"],
            "success_count": existing["success_count"] + new["success_count"],
            "fail_count": existing["fail_count"] + new["fail_count"],
            "total_time_ms": existing["total_time_ms"] + new["total_time_ms"],
            "total_tokens": existing["total_tokens"] + new["total_tokens"],
            "avg_time_ms": 0.0,
            "avg_tokens": 0.0,
            "success_rate": 0.0,
        }
        if merged["total_calls"] > 0:
            merged["avg_time_ms"] = merged["total_time_ms"] / merged["total_calls"]
            merged["avg_tokens"] = merged["total_tokens"] / merged["total_calls"]
            merged["success_rate"] = merged["success_count"] / merged["total_calls"]
        return merged

    def _format_ms(self, value_ms: float) -> str:
        """格式化毫秒值：默认保留3位小数，很小的值保留至少一个有效数字"""
        if value_ms == 0:
            return "0.000ms"
        formatted = f"{value_ms:.3f}"
        if formatted == "0.000":
            first_nonzero = -1
            s = f"{value_ms:.20f}"
            for i, c in enumerate(s):
                if c not in ("0", "."):
                    first_nonzero = i
                    break
            if first_nonzero > 0:
                decimals_needed = first_nonzero - s.index(".") + 1
                formatted = f"{value_ms:.{decimals_needed}f}"
        return f"{formatted}ms"

    def _format_duration(self, value_ms: float) -> str:
        if value_ms is None:
            return "N/A"
        try:
            value_ms = float(value_ms)
        except Exception:
            return "N/A"
        if value_ms <= 0:
            return "0s"
        if value_ms >= 1000:
            return f"{value_ms / 1000:.1f}s"
        return f"{int(round(value_ms))}ms"

    def _ensure_tool_desc_cache(self) -> None:
        if self._tool_desc_cache_ready:
            return
        self._tool_desc_cache_ready = True
        try:
            from vikingbot.agent.tools.factory import register_default_tools
            from vikingbot.agent.tools.registry import ToolRegistry
            from vikingbot.config.loader import load_config

            registry = ToolRegistry()
            config = load_config()
            register_default_tools(
                registry=registry,
                config=config,
                include_message_tool=False,
                include_spawn_tool=False,
                include_cron_tool=False,
                include_image_tool=False,
                include_viking_tools=True,
            )
            cache: dict[str, str] = {}
            for name in registry.tool_names:
                tool = registry.get(name)
                desc = getattr(tool, "description", "") if tool else ""
                if desc:
                    cache[name] = str(desc)
            self._tool_desc_cache = cache
        except Exception:
            self._tool_desc_cache = {}

    def _get_tool_static_description(self, tool_name: str) -> str:
        if not tool_name:
            return ""
        self._ensure_tool_desc_cache()
        return (self._tool_desc_cache.get(tool_name) or "").strip()

    def _extract_content_field(self, content: str, keys: list[str]) -> str:
        if not content:
            return ""
        for key in keys:
            m = re.search(rf"(?im)^[ \t>*-]*{re.escape(key)}\s*[:：]\s*(.+?)\s*$", content)
            if m:
                return (m.group(1) or "").strip()
        return ""

    def _extract_content_section(self, content: str, headings: list[str]) -> str:
        if not content:
            return ""
        for h in headings:
            m = re.search(
                rf"(?im)^[ \t]*##[ \t]*{re.escape(h)}[ \t]*\n([\s\S]*?)(?=^[ \t]*##[ \t]|\Z)",
                content,
            )
            if m:
                return (m.group(1) or "").strip()
        return ""

    def _compact_block(self, text: str) -> str:
        if not text:
            return ""
        lines = []
        for line in str(text).splitlines():
            s = line.strip()
            if not s:
                continue
            s = re.sub(r"^[>*\-\s]+", "", s).strip()
            if s:
                lines.append(s)
        return "; ".join(lines).strip()

    async def _merge_kv_field(
        self, existing_value: str, new_value: str, field_name: str = ""
    ) -> str:
        a = (existing_value or "").strip()
        b = (new_value or "").strip()
        if not a:
            return b
        if not b:
            return a
        if a == b:
            return a
        parts = []
        for s in (a, b):
            for p in [x.strip() for x in re.split(r"[;\n；]+", s)]:
                if p and p not in parts:
                    parts.append(p)
        merged = "; ".join(parts).strip()

        max_length = FIELD_MAX_LENGTHS.get(field_name, FIELD_MAX_LENGTH)
        if len(merged) <= max_length:
            return merged

        compressed = await self._compress_field_content(merged, field_name, max_length)
        if compressed:
            return compressed
        return self._smart_truncate(merged, max_length)

    async def _compress_field_content(
        self, content: str, field_name: str, max_length: int
    ) -> Optional[str]:
        vlm = get_openviking_config().vlm
        if not vlm or not vlm.is_available():
            return None

        target_length = int(max_length * 0.8)
        prompt = render_prompt(
            "compression.field_compress",
            {
                "field_name": field_name,
                "content": content,
                "max_length": target_length,
            },
        )

        try:
            response = await vlm.get_completion_async(prompt)
            compressed = response.strip()
            if len(compressed) <= max_length:
                logger.info(
                    "Field compression succeeded: field=%s original=%d compressed=%d target=%d",
                    field_name,
                    len(content),
                    len(compressed),
                    target_length,
                )
                return compressed
            logger.warning(
                "Compressed content still exceeds max_length: field=%s len=%d max=%d, using fallback",
                field_name,
                len(compressed),
                max_length,
            )
            return None
        except Exception as e:
            logger.warning(f"Field compression failed for {field_name}: {e}")
            return None

    def _smart_truncate(self, text: str, max_length: int) -> str:
        if len(text) <= max_length:
            return text
        truncated = text[:max_length]
        last_sep = truncated.rfind(";")
        if last_sep > max_length * 0.7:
            return truncated[:last_sep]
        last_space = truncated.rfind(" ")
        if last_space > max_length * 0.7:
            return truncated[:last_space]
        return truncated

    def _extract_tool_memory_context_fields_from_text(self, text: str) -> dict:
        return {
            "best_for": self._extract_content_field(
                text, ["Best for", "Best scenarios", "最佳场景", "适用场景"]
            ),
            "optimal_params": self._extract_content_field(
                text, ["Optimal params", "Optimal parameters", "最优参数", "推荐参数"]
            ),
            "common_failures": self._extract_content_field(
                text, ["Common failures", "常见失败", "失败模式"]
            ),
            "recommendation": self._extract_content_field(
                text, ["Recommendation", "Recommendations", "推荐", "建议"]
            ),
        }

    def _extract_skill_memory_context_fields_from_text(self, text: str) -> dict:
        return {
            "best_for": self._extract_content_field(text, ["Best for", "最佳场景", "适用场景"]),
            "recommended_flow": self._extract_content_field(
                text, ["Recommended flow", "Recommended Flow", "推荐流程", "推荐步骤"]
            ),
            "key_dependencies": self._extract_content_field(
                text, ["Key dependencies", "Key Dependencies", "关键依赖", "前置条件"]
            ),
            "common_failures": self._extract_content_field(
                text, ["Common failures", "常见失败", "失败模式"]
            ),
            "recommendation": self._extract_content_field(
                text, ["Recommendation", "Recommendations", "推荐", "建议"]
            ),
        }

    def _generate_tool_memory_content(
        self, tool_name: str, stats: dict, guidelines: str, fields: Optional[dict] = None
    ) -> str:
        static_desc = self._get_tool_static_description(tool_name) or "N/A"
        fields = fields or {}
        best_for = (fields.get("best_for") or "").strip()
        optimal_params = (fields.get("optimal_params") or "").strip()
        common_failures = (fields.get("common_failures") or "").strip()
        recommendation = (fields.get("recommendation") or "").strip()

        if not best_for:
            best_for = self._extract_content_field(
                guidelines, ["Best for", "Best scenarios", "最佳场景", "适用场景"]
            ) or self._compact_block(
                self._extract_content_section(
                    guidelines, ["Best Scenarios", "Best for", "最佳场景"]
                )
            )
        if not optimal_params:
            optimal_params = self._extract_content_field(
                guidelines, ["Optimal params", "Optimal parameters", "最优参数", "推荐参数"]
            ) or self._compact_block(
                self._extract_content_section(
                    guidelines, ["Optimal Parameters", "Optimal params", "最优参数"]
                )
            )
        if not common_failures:
            common_failures = self._extract_content_field(
                guidelines, ["Common failures", "常见失败", "失败模式"]
            ) or self._compact_block(
                self._extract_content_section(guidelines, ["Common Failures", "常见失败"])
            )
        if not recommendation:
            recommendation = self._extract_content_field(
                guidelines, ["Recommendation", "Recommendations", "推荐", "建议"]
            ) or self._compact_block(
                self._extract_content_section(
                    guidelines, ["Recommendations", "Recommendation", "推荐"]
                )
            )

        best_for = best_for or ""
        optimal_params = optimal_params or ""
        common_failures = common_failures or ""
        recommendation = recommendation or ""

        return (
            "Tool: "
            + str(tool_name)
            + "\n\n"
            + "Static Description:\n"
            + f'"{static_desc}"\n\n'
            + "Tool Memory Context:\n"
            + f"Based on {stats['total_calls']} historical calls:\n"
            + f"- Success rate: {stats['success_rate'] * 100:.1f}% ({stats['success_count']} successful, {stats['fail_count']} failed)\n"
            + f"- Avg time: {self._format_duration(stats.get('avg_time_ms', 0))}, Avg tokens: {int(stats.get('avg_tokens', 0))}\n"
            + f"- Best for: {best_for}\n"
            + f"- Optimal params: {optimal_params}\n"
            + f"- Common failures: {common_failures}\n"
            + f"- Recommendation: {recommendation}\n\n"
            + (guidelines or "").strip()
            + "\n"
        )

    def _create_tool_context(
        self,
        uri: str,
        candidate: CandidateMemory,
        ctx: "RequestContext",
        abstract_override: Optional[str] = None,
    ) -> Context:
        """创建 Tool Memory 的 Context 对象"""
        agent_space = ctx.user.agent_space_name()
        return Context(
            uri=uri,
            parent_uri=f"viking://agent/{agent_space}/memories/tools",
            is_leaf=True,
            abstract=abstract_override or candidate.abstract,
            context_type=ContextType.MEMORY.value,
            category=candidate.category.value,
            session_id=candidate.source_session,
            user=candidate.user,
            account_id=ctx.account_id,
            owner_space=agent_space,
        )

    def _extract_tool_guidelines(self, content: str) -> str:
        headings = r"(使用指南|Guidelines|Guildlines)"
        m = re.search(rf"^##\s*{headings}\s*\n", content, flags=re.MULTILINE)
        if m:
            return content[m.end() :].strip()

        m = re.search(r"(?im)^Guidelines:\s*\n", content)
        if m:
            return content[m.end() :].strip()

        m = re.search(
            r"^##\s*工具信息[\s\S]*?^##\s*调用统计[\s\S]*?^\-\s*\*\*平均Token\*\*:.*$\n\n",
            content,
            flags=re.MULTILINE,
        )
        if m:
            return content[m.end() :].strip()

        return content.strip()

    async def _merge_skill_memory(
        self, skill_name: str, candidate: CandidateMemory, ctx: "RequestContext"
    ) -> Optional[Context]:
        """合并 Skill Memory，统计数据用 Python 累加"""
        if not skill_name or not skill_name.strip():
            logger.warning("Skill name is empty, skipping skill memory merge")
            return None

        agent_space = ctx.user.agent_space_name()
        uri = f"viking://agent/{agent_space}/memories/skills/{skill_name}.md"
        viking_fs = get_viking_fs()

        if not viking_fs:
            logger.warning("VikingFS not available, skipping skill memory merge")
            return None

        existing = ""
        try:
            existing = await viking_fs.read_file(uri, ctx=ctx) or ""
        except NotFoundError:
            existing = ""
        except Exception as e:
            logger.warning(
                "Failed to read existing skill memory %s: %s; skipping write to avoid data loss",
                uri,
                e,
            )
            return None

        new_stats = {
            "total_executions": 0,
            "success_count": 0,
            "fail_count": 0,
        }
        if isinstance(candidate, ToolSkillCandidateMemory) and candidate.call_time > 0:
            new_stats["total_executions"] = candidate.call_time
            new_stats["success_count"] = candidate.success_time
            new_stats["fail_count"] = max(0, candidate.call_time - candidate.success_time)
        else:
            new_stats = self._parse_skill_statistics(candidate.content)
        if new_stats["total_executions"] == 0:
            new_stats["total_executions"] = 1
            if "error" in candidate.content.lower() or "fail" in candidate.content.lower():
                new_stats["fail_count"] = 1
                new_stats["success_count"] = 0
            else:
                new_stats["success_count"] = 1
                new_stats["fail_count"] = 0
        new_guidelines = (candidate.content or "").strip()
        abstract_override: Optional[str] = None
        new_fields = {
            "best_for": "",
            "recommended_flow": "",
            "key_dependencies": "",
            "common_failures": "",
            "recommendation": "",
        }
        if isinstance(candidate, ToolSkillCandidateMemory):
            new_fields = {
                "best_for": (candidate.best_for or "").strip(),
                "recommended_flow": (candidate.recommended_flow or "").strip(),
                "key_dependencies": (candidate.key_dependencies or "").strip(),
                "common_failures": (candidate.common_failures or "").strip(),
                "recommendation": (candidate.recommendation or "").strip(),
            }
        fallback_fields = self._extract_skill_memory_context_fields_from_text(
            "\n".join([str(candidate.overview or "").strip(), str(candidate.content or "").strip()])
        )
        for k, v in fallback_fields.items():
            if not new_fields.get(k) and v:
                new_fields[k] = v.strip()

        if not existing.strip():
            merged_stats = self._compute_skill_statistics_derived(new_stats)
            merged_content = self._generate_skill_memory_content(
                skill_name, merged_stats, new_guidelines, fields=new_fields
            )
            await viking_fs.write_file(uri=uri, content=merged_content, ctx=ctx)
            return self._create_skill_context(uri, candidate, ctx)

        existing_stats = self._parse_skill_statistics(existing)
        merged_stats = self._merge_skill_statistics(existing_stats, new_stats)
        if merged_stats.get("total_executions", 0) < existing_stats.get("total_executions", 0):
            logger.warning(
                "Skill memory merge violates monotonic total_executions: skill=%s existing=%s merged=%s; skipping write",
                skill_name,
                existing_stats.get("total_executions", 0),
                merged_stats.get("total_executions", 0),
            )
            return None
        existing_guidelines = self._extract_skill_guidelines(existing) or existing.strip()
        existing_fields = self._extract_skill_memory_context_fields_from_text(existing)
        merged_fields = {
            "best_for": await self._merge_kv_field(
                existing_fields.get("best_for", ""), new_fields.get("best_for", ""), "best_for"
            ),
            "recommended_flow": await self._merge_kv_field(
                existing_fields.get("recommended_flow", ""),
                new_fields.get("recommended_flow", ""),
                "recommended_flow",
            ),
            "key_dependencies": await self._merge_kv_field(
                existing_fields.get("key_dependencies", ""),
                new_fields.get("key_dependencies", ""),
                "key_dependencies",
            ),
            "common_failures": await self._merge_kv_field(
                existing_fields.get("common_failures", ""),
                new_fields.get("common_failures", ""),
                "common_failures",
            ),
            "recommendation": await self._merge_kv_field(
                existing_fields.get("recommendation", ""),
                new_fields.get("recommendation", ""),
                "recommendation",
            ),
        }
        merged_guidelines = existing_guidelines
        if new_guidelines:
            payload = await self._merge_memory_bundle(
                existing_abstract="",
                existing_overview="",
                existing_content=existing_guidelines,
                new_abstract=candidate.abstract,
                new_overview=candidate.overview,
                new_content=new_guidelines,
                category="skills",
                output_language=candidate.language,
            )
            if payload and payload.content:
                merged_guidelines = payload.content.strip()
                if payload.abstract:
                    abstract_override = payload.abstract.strip() or None
            else:
                merged_guidelines = (existing_guidelines + "\n\n" + new_guidelines).strip()

        merged_content = self._generate_skill_memory_content(
            skill_name, merged_stats, merged_guidelines, fields=merged_fields
        )
        await viking_fs.write_file(uri=uri, content=merged_content, ctx=ctx)
        return self._create_skill_context(uri, candidate, ctx, abstract_override=abstract_override)

    def _compute_skill_statistics_derived(self, stats: dict) -> dict:
        """计算 Skill 派生统计数据（成功率）"""
        if stats["total_executions"] > 0:
            stats["success_rate"] = stats["success_count"] / stats["total_executions"]
        else:
            stats["success_rate"] = 0
        return stats

    def _parse_skill_statistics(self, content: str) -> dict:
        """从 Markdown 内容中解析 Skill 统计数据"""
        stats = {
            "total_executions": 0,
            "success_count": 0,
            "fail_count": 0,
        }

        match = re.search(r"总执行次数(?:\*+)?\s*[:：]\s*(\d+)", content)
        if match:
            stats["total_executions"] = int(match.group(1))
        else:
            match = re.search(
                r"(?im)^Based on\s+(\d+)\s+historical\s+executions\s*:",
                content,
            )
            if match:
                stats["total_executions"] = int(match.group(1))

        match = re.search(
            r"成功率(?:\*+)?\s*[:：]\s*([\d.]+)%\s*[（(]\s*(\d+)\s*成功\s*[，,]\s*(\d+)\s*失败",
            content,
        )
        if match:
            stats["success_count"] = int(match.group(2))
            stats["fail_count"] = int(match.group(3))
            if stats["total_executions"] <= 0:
                stats["total_executions"] = stats["success_count"] + stats["fail_count"]
        else:
            match = re.search(r"成功率(?:\*+)?\s*[:：]\s*([\d.]+)%", content)
            if match and stats["total_executions"] > 0:
                success_rate = float(match.group(1)) / 100
                stats["success_count"] = int(stats["total_executions"] * success_rate)
                stats["fail_count"] = stats["total_executions"] - stats["success_count"]
            else:
                match = re.search(
                    r"(?im)^-\s*Success rate:\s*([\d.]+)%\s*\((\d+)\s+successful,\s*(\d+)\s+failed\)",
                    content,
                )
                if match:
                    stats["success_count"] = int(match.group(2))
                    stats["fail_count"] = int(match.group(3))
                    if stats["total_executions"] <= 0:
                        stats["total_executions"] = stats["success_count"] + stats["fail_count"]
                else:
                    match = re.search(r"(?im)^-\s*Success rate:\s*([\d.]+)%", content)
                    if match and stats["total_executions"] > 0:
                        success_rate = float(match.group(1)) / 100
                        stats["success_count"] = int(stats["total_executions"] * success_rate)
                        stats["fail_count"] = stats["total_executions"] - stats["success_count"]

        return stats

    def _merge_skill_statistics(self, existing: dict, new: dict) -> dict:
        """累加 Skill 统计数据"""
        merged = {
            "total_executions": existing["total_executions"] + new["total_executions"],
            "success_count": existing["success_count"] + new["success_count"],
            "fail_count": existing["fail_count"] + new["fail_count"],
            "success_rate": 0.0,
        }
        if merged["total_executions"] > 0:
            merged["success_rate"] = merged["success_count"] / merged["total_executions"]
        return merged

    def _generate_skill_memory_content(
        self, skill_name: str, stats: dict, guidelines: str, fields: Optional[dict] = None
    ) -> str:
        fields = fields or {}
        best_for = (fields.get("best_for") or "").strip()
        recommended_flow = (fields.get("recommended_flow") or "").strip()
        key_dependencies = (fields.get("key_dependencies") or "").strip()
        common_failures = (fields.get("common_failures") or "").strip()
        recommendation = (fields.get("recommendation") or "").strip()

        if not best_for:
            best_for = self._extract_content_field(
                guidelines, ["Best for", "最佳场景", "适用场景"]
            ) or self._compact_block(
                self._extract_content_section(
                    guidelines, ["Best for", "Best Scenarios", "最佳场景"]
                )
            )
        if not recommended_flow:
            recommended_flow = self._extract_content_field(
                guidelines, ["Recommended flow", "推荐流程", "推荐步骤"]
            ) or self._compact_block(
                self._extract_content_section(
                    guidelines, ["Recommended Flow", "推荐流程", "推荐步骤"]
                )
            )
        if not key_dependencies:
            key_dependencies = self._extract_content_field(
                guidelines, ["Key dependencies", "关键依赖", "前置条件"]
            ) or self._compact_block(
                self._extract_content_section(
                    guidelines, ["Key Dependencies", "关键依赖", "前置条件"]
                )
            )
        if not common_failures:
            common_failures = self._extract_content_field(
                guidelines, ["Common failures", "常见失败", "失败模式"]
            ) or self._compact_block(
                self._extract_content_section(guidelines, ["Common Failures", "常见失败"])
            )
        if not recommendation:
            recommendation = self._extract_content_field(
                guidelines, ["Recommendation", "Recommendations", "推荐", "建议"]
            ) or self._compact_block(
                self._extract_content_section(
                    guidelines, ["Recommendations", "Recommendation", "推荐"]
                )
            )

        best_for = best_for or ""
        recommended_flow = recommended_flow or ""
        key_dependencies = key_dependencies or ""
        common_failures = common_failures or ""
        recommendation = recommendation or ""

        return (
            "Skill: "
            + str(skill_name)
            + "\n\n"
            + "Skill Memory Context:\n"
            + f"Based on {stats['total_executions']} historical executions:\n"
            + f"- Success rate: {stats['success_rate'] * 100:.1f}% ({stats['success_count']} successful, {stats['fail_count']} failed)\n"
            + f"- Best for: {best_for}\n"
            + f"- Recommended flow: {recommended_flow}\n"
            + f"- Key dependencies: {key_dependencies}\n"
            + f"- Common failures: {common_failures}\n"
            + f"- Recommendation: {recommendation}\n\n"
            + (guidelines or "").strip()
            + "\n"
        )

    def _create_skill_context(
        self,
        uri: str,
        candidate: CandidateMemory,
        ctx: "RequestContext",
        abstract_override: Optional[str] = None,
    ) -> Context:
        """创建 Skill Memory 的 Context 对象"""
        agent_space = ctx.user.agent_space_name()
        return Context(
            uri=uri,
            parent_uri=f"viking://agent/{agent_space}/memories/skills",
            is_leaf=True,
            abstract=abstract_override or candidate.abstract,
            context_type=ContextType.MEMORY.value,
            category=candidate.category.value,
            session_id=candidate.source_session,
            user=candidate.user,
            account_id=ctx.account_id,
            owner_space=agent_space,
        )

    def _extract_skill_guidelines(self, content: str) -> str:
        headings = r"(使用指南|Guidelines|Guildlines)"
        m = re.search(rf"^##\s*{headings}\s*\n", content, flags=re.MULTILINE)
        if m:
            return content[m.end() :].strip()

        m = re.search(r"(?im)^Guidelines:\s*\n", content)
        if m:
            return content[m.end() :].strip()

        m = re.search(
            r"^##\s*技能信息[\s\S]*?^##\s*执行统计[\s\S]*?^\-\s*\*\*成功率\*\*:.*$\n\n",
            content,
            flags=re.MULTILINE,
        )
        if m:
            return content[m.end() :].strip()

        return content.strip()
