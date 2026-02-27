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
from openviking.server.identity import RequestContext
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
    call_time: int = 0 # 调用次数
    success_time: int = 0 # 成功调用次数


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

    def _collect_tool_stats_from_messages(self, messages: list) -> dict:
        """从消息中收集工具统计数据"""
        from openviking.message.part import ToolPart

        stats_map = {}
        for msg in messages:
            parts = getattr(msg, "parts", [])
            for part in parts:
                if isinstance(part, ToolPart):
                    name = part.tool_name
                    if not name:
                        continue
                    if name not in stats_map:
                        stats_map[name] = {
                            "duration_ms": 0,
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "success_time": 0,
                            "call_count": 0,
                        }
                    stats_map[name]["call_count"] += 1
                    if part.duration_ms is not None:
                        stats_map[name]["duration_ms"] += part.duration_ms
                    if part.prompt_tokens is not None:
                        stats_map[name]["prompt_tokens"] += part.prompt_tokens
                    if part.completion_tokens is not None:
                        stats_map[name]["completion_tokens"] += part.completion_tokens
                    if part.tool_status == "completed":
                        stats_map[name]["success_time"] += 1
        return stats_map

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

        messages = context["messages"]


        tool_stats_map = self._collect_tool_stats_from_messages(messages)

        # logger.warning(f"tool_stats_map={tool_stats_map}")

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
            # print(f"memories = {data.get('memories', [])}")
            for mem in data.get("memories", []):
                category_str = mem.get("category", "patterns")
                try:
                    category = MemoryCategory(category_str)
                except ValueError:
                    category = MemoryCategory.PATTERNS

                # 只在 tools/skills 时使用 ToolSkillCandidateMemory
                if category in (MemoryCategory.TOOLS, MemoryCategory.SKILLS):
                    tool_name = mem.get("tool_name", "")
                    skill_name = mem.get("skill_name", "")
                    stats = tool_stats_map.get(tool_name or skill_name, {})
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
                            call_time=stats.get("call_count",0),
                            success_time=stats.get("success_time",0),
                            duration_ms=stats.get("duration_ms", 0),
                            prompt_tokens=stats.get("prompt_tokens", 0),
                            completion_tokens=stats.get("completion_tokens", 0),
                        )
                    )
                else:
                    # 现有逻辑不变，前向兼容
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
        except Exception:
            pass

        if isinstance(candidate, ToolSkillCandidateMemory):
            new_stats = {
                "total_calls": candidate.call_time,
                "success_count": candidate.success_time,
                "fail_count": candidate.call_time - candidate.success_time,
                "total_time_ms": candidate.duration_ms or 0,
                "total_tokens": (candidate.prompt_tokens or 0) + (candidate.completion_tokens or 0),
            }
        else:
            new_stats = self._parse_tool_statistics(candidate.content)
            if new_stats["total_calls"] == 0:
                new_stats["total_calls"] = 1
                tool_status = getattr(candidate, 'tool_status', 'completed')
                if tool_status == "error":
                    new_stats["fail_count"] = 1
                    new_stats["success_count"] = 0
                else:
                    new_stats["success_count"] = 1
                    new_stats["fail_count"] = 0

        if not existing.strip():
            merged_stats = self._compute_statistics_derived(new_stats)
            merged_content = self._generate_tool_memory_content(tool_name, merged_stats, candidate)
            await viking_fs.write_file(uri=uri, content=merged_content, ctx=ctx)
            await self._enqueue_semantic_for_parent(uri, ctx)
            return self._create_tool_context(uri, candidate, ctx)

        existing_stats = self._parse_tool_statistics(existing)
        merged_stats = self._merge_tool_statistics(existing_stats, new_stats)
        merged_content = self._generate_tool_memory_content(tool_name, merged_stats, candidate)
        await viking_fs.write_file(uri=uri, content=merged_content, ctx=ctx)
        await self._enqueue_semantic_for_parent(uri, ctx)
        return self._create_tool_context(uri, candidate, ctx)

    async def _enqueue_semantic_for_parent(self, file_uri: str, ctx: "RequestContext") -> None:
        """Enqueue semantic generation for parent directory."""
        try:
            from openviking.storage.queuefs import get_queue_manager
            from openviking.storage.queuefs.semantic_msg import SemanticMsg

            parent_uri = "/".join(file_uri.rsplit("/", 1)[:-1])
            queue_manager = get_queue_manager()
            semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC, allow_create=True)
            msg = SemanticMsg(
                uri=parent_uri,
                context_type="memory",
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                agent_id=ctx.user.agent_id,
                role=ctx.role.value,
            )
            await semantic_queue.enqueue(msg)
            logger.debug(f"Enqueued semantic generation for: {parent_uri}")
        except Exception as e:
            logger.warning(f"Failed to enqueue semantic generation for {file_uri}: {e}")

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
        """从 Markdown 内容中解析统计数据"""
        stats = {
            "total_calls": 0,
            "success_count": 0,
            "fail_count": 0,
            "total_time_ms": 0,
            "total_tokens": 0,
        }

        match = re.search(r"总调用次数:\s*(\d+)", content)
        if match:
            stats["total_calls"] = int(match.group(1))

        match = re.search(r"成功率:\s*([\d.]+)%", content)
        if match:
            success_rate = float(match.group(1)) / 100
            stats["success_count"] = int(stats["total_calls"] * success_rate)
            stats["fail_count"] = stats["total_calls"] - stats["success_count"]

        match = re.search(r"平均耗时:\s*([\d.]+)ms", content)
        if match and stats["total_calls"] > 0:
            stats["total_time_ms"] = float(match.group(1)) * stats["total_calls"]
        else:
            match = re.search(r"平均耗时:\s*([\d.]+)s", content)
            if match and stats["total_calls"] > 0:
                stats["total_time_ms"] = float(match.group(1)) * 1000 * stats["total_calls"]

        match = re.search(r"平均Token:\s*(\d+)", content)
        if match and stats["total_calls"] > 0:
            stats["total_tokens"] = int(match.group(1)) * stats["total_calls"]

        return stats

    def _merge_tool_statistics(self, existing: dict, new: dict) -> dict:
        """累加统计数据（Python 计算）"""
        merged = {
            "total_calls": existing["total_calls"] + new["total_calls"],
            "success_count": existing["success_count"] + new["success_count"],
            "fail_count": existing["fail_count"] + new["fail_count"],
            "total_time_ms": existing["total_time_ms"] + new["total_time_ms"],
            "total_tokens": existing["total_tokens"] + new["total_tokens"],
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
                if c not in ('0', '.'):
                    first_nonzero = i
                    break
            if first_nonzero > 0:
                decimals_needed = first_nonzero - s.index('.') + 1
                formatted = f"{value_ms:.{decimals_needed}f}"
        return f"{formatted}ms"

    def _generate_tool_memory_content(
        self, tool_name: str, stats: dict, candidate: CandidateMemory
    ) -> str:
        """生成合并后的 Tool Memory 内容"""
        return f"""## 工具信息
- **名称**: {tool_name}

## 调用统计
- **总调用次数**: {stats["total_calls"]}
- **成功率**: {stats["success_rate"] * 100:.1f}%（{stats["success_count"]} 成功，{stats["fail_count"]} 失败）
- **平均耗时**: {self._format_ms(stats["avg_time_ms"])}
- **平均Token**: {int(stats["avg_tokens"])}

{candidate.content}
"""

    def _create_tool_context(
        self, uri: str, candidate: CandidateMemory, ctx: "RequestContext"
    ) -> Context:
        """创建 Tool Memory 的 Context 对象"""
        agent_space = ctx.user.agent_space_name()
        return Context(
            uri=uri,
            parent_uri=f"viking://agent/{agent_space}/memories/tools",
            is_leaf=True,
            abstract=candidate.abstract,
            context_type=ContextType.MEMORY.value,
            category=candidate.category.value,
            session_id=candidate.source_session,
            user=candidate.user,
            account_id=ctx.account_id,
            owner_space=agent_space,
        )

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
        except Exception:
            pass

        new_stats = self._parse_skill_statistics(candidate.content)
        if new_stats["total_executions"] == 0:
            new_stats["total_executions"] = 1
            if "error" in candidate.content.lower() or "fail" in candidate.content.lower():
                new_stats["fail_count"] = 1
                new_stats["success_count"] = 0
            else:
                new_stats["success_count"] = 1
                new_stats["fail_count"] = 0

        if not existing.strip():
            merged_stats = self._compute_skill_statistics_derived(new_stats)
            merged_content = self._generate_skill_memory_content(
                skill_name, merged_stats, candidate
            )
            await viking_fs.write_file(uri=uri, content=merged_content, ctx=ctx)
            await self._enqueue_semantic_for_parent(uri, ctx)
            return self._create_skill_context(uri, candidate, ctx)

        existing_stats = self._parse_skill_statistics(existing)
        merged_stats = self._merge_skill_statistics(existing_stats, new_stats)
        merged_content = self._generate_skill_memory_content(skill_name, merged_stats, candidate)
        await viking_fs.write_file(uri=uri, content=merged_content, ctx=ctx)
        await self._enqueue_semantic_for_parent(uri, ctx)
        return self._create_skill_context(uri, candidate, ctx)

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

        match = re.search(r"总执行次数:\s*(\d+)", content)
        if match:
            stats["total_executions"] = int(match.group(1))

        match = re.search(r"成功率:\s*([\d.]+)%", content)
        if match:
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
        }
        if merged["total_executions"] > 0:
            merged["success_rate"] = merged["success_count"] / merged["total_executions"]
        return merged

    def _generate_skill_memory_content(
        self, skill_name: str, stats: dict, candidate: CandidateMemory
    ) -> str:
        """生成合并后的 Skill Memory 内容"""
        return f"""## 技能信息
- **名称**: {skill_name}

## 执行统计
- **总执行次数**: {stats["total_executions"]}
- **成功率**: {stats["success_rate"] * 100:.1f}%（{stats["success_count"]} 成功，{stats["fail_count"]} 失败）

## Guildlines
{candidate.content}
"""

    def _create_skill_context(
        self, uri: str, candidate: CandidateMemory, ctx: "RequestContext"
    ) -> Context:
        """创建 Skill Memory 的 Context 对象"""
        agent_space = ctx.user.agent_space_name()
        return Context(
            uri=uri,
            parent_uri=f"viking://agent/{agent_space}/memories/skills",
            is_leaf=True,
            abstract=candidate.abstract,
            context_type=ContextType.MEMORY.value,
            category=candidate.category.value,
            session_id=candidate.source_session,
            user=candidate.user,
            account_id=ctx.account_id,
            owner_space=agent_space,
        )
