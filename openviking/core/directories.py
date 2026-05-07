# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Preset directory structure definitions for OpenViking.

OpenViking uses a virtual filesystem where all directories are data records.
This module defines the preset directory structure that is created on initialization.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional

from openviking.core.context import Context, ContextType, Vectorize
from openviking.core.namespace import (
    agent_space_fragment,
    canonical_agent_root,
    canonical_user_root,
    user_space_fragment,
)
from openviking.server.identity import RequestContext
from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter

if TYPE_CHECKING:
    from openviking.storage import VikingDBManager
    from openviking.storage.viking_fs import VikingFS


@dataclass
class DirectoryDefinition:
    """Directory definition."""

    path: str  # Relative path, e.g., "memory/identity"
    abstract: str  # L0 summary
    overview: str  # L1 description
    children: List["DirectoryDefinition"] = field(default_factory=list)


# Preset directory tree - each scope has a root DirectoryDefinition
PRESET_DIRECTORIES: Dict[str, DirectoryDefinition] = {
    "session": DirectoryDefinition(
        path="",
        abstract="Session scope. Stores complete context for a single conversation, including original messages and compressed summaries.",
        overview="Session-level temporary data storage, can be archived or cleaned after session ends.",
    ),
    "user": DirectoryDefinition(
        path="",
        abstract="User scope. Stores user's long-term memory, persisted across sessions.",
        overview="User-level persistent data storage for building user profiles and managing private memories.",
        children=[
            DirectoryDefinition(
                path="memories",
                abstract="User's long-term memory storage. Contains memory types like preferences, entities, events, managed hierarchically by type.",
                overview="Use this directory to access user's personalized memories. Contains three main categories: "
                "1) preferences-user preferences, 2) entities-entity memories, 3) events-event records.",
                children=[
                    DirectoryDefinition(
                        path="preferences",
                        abstract="User's personalized preference memories. Stores preferences by topic (communication style, code standards, domain interests, etc.), "
                        "one subdirectory per preference type, same-type preferences can be appended.",
                        overview="Access when adjusting output style, following user habits, or providing personalized services. "
                        "Examples: user prefers concise communication, code needs type annotations, focus on certain tech domains. "
                        "Preferences organized by topic, same-type preferences aggregated in same subdirectory.",
                    ),
                    DirectoryDefinition(
                        path="entities",
                        abstract="Entity memories from user's world. Each entity has its own subdirectory, including projects, people, concepts, etc. "
                        "Entities are important objects in user's world, can append additional information.",
                        overview="Access when referencing user-related projects, people, concepts. "
                        "Examples: OpenViking project, colleague Zhang San, certain technical concept. "
                        "Each entity stored independently, can append updates.",
                    ),
                    DirectoryDefinition(
                        path="events",
                        abstract="User's event records. Each event has its own subdirectory, recording important events, decisions, milestones, etc. "
                        "Events are time-independent, historical records not updated.",
                        overview="Access when reviewing user history, understanding event context, or tracking user progress. "
                        "Examples: decided to refactor memory system, completed a project, attended an event. "
                        "Events are historical records, not updated once created.",
                    ),
                ],
            ),
            DirectoryDefinition(
                path="privacy",
                abstract="User privacy config root. Stores user-scoped sensitive configuration snapshots by category and target key.",
                overview="Use this directory to access privacy-managed configuration values such as skill secrets. Concrete category and target-key subdirectories are created lazily by the privacy config service.",
            ),
        ],
    ),
    "agent": DirectoryDefinition(
        path="",
        abstract="Agent scope. Stores Agent's learning memories, instructions, and skills.",
        overview="Agent-level global data storage. "
        "Contains three main categories: memories-learning memories, instructions-directives, skills-capability registry.",
        children=[
            DirectoryDefinition(
                path="memories",
                abstract="Agent's long-term memory storage. Contains trajectories and experiences, managed hierarchically by type.",
                overview="Use this directory to access Agent's learning memories. Contains two main categories: "
                "1) trajectories-task execution records, 2) experiences-generalized lessons from trajectories.",
                children=[
                    DirectoryDefinition(
                        path="trajectories",
                        abstract="Agent's execution trajectory records. Stores end-to-end task execution traces from each interaction, each trajectory is independent and not updated.",
                        overview="Access when reviewing how the agent handled past tasks or diagnosing execution history. "
                        "Trajectories are records of specific task executions, each independent and not updated once created.",
                    ),
                    DirectoryDefinition(
                        path="experiences",
                        abstract="Agent's generalized experience memories. Reusable insights and lessons distilled from execution trajectories, updated as new evidence accumulates.",
                        overview="Access when the agent encounters recurring situations or needs guidance from past lessons. "
                        "Experiences are distilled from trajectories and updated incrementally as more supporting evidence accumulates.",
                    ),
                ],
            ),
            DirectoryDefinition(
                path="instructions",
                abstract="Agent instruction set. Contains Agent's behavioral directives, rules, and constraints.",
                overview="Access when Agent needs to follow specific rules. "
                "Examples: planner agent has specific planning process requirements, executor agent has execution standards, etc.",
            ),
            DirectoryDefinition(
                path="skills",
                abstract="Agent's skill registry. Uses Claude Skills protocol format, flat storage of callable skill definitions.",
                overview="Access when Agent needs to execute specific tasks. Skills categorized by tags, "
                "should retrieve relevant skills before executing tasks, select most appropriate skill to execute.",
            ),
        ],
    ),
    "resources": DirectoryDefinition(
        path="",
        abstract="Resources scope. Independent knowledge and resource storage, not bound to specific account or Agent.",
        overview="Globally shared resource storage, organized by project/topic. "
        "No preset subdirectory structure, users create project directories as needed.",
    ),
}


def get_context_type_for_uri(uri: str) -> str:
    """Determine context_type based on URI."""
    if "/memories" in uri:
        return ContextType.MEMORY.value
    elif "/resources" in uri:
        return ContextType.RESOURCE.value
    elif "/skills" in uri:
        return ContextType.SKILL.value
    elif uri.startswith("viking://session"):
        return ContextType.MEMORY.value
    return ContextType.RESOURCE.value


class DirectoryInitializer:
    """Initialize preset directory structure."""

    def __init__(
        self,
        vikingdb: "VikingDBManager",
        viking_fs: Optional["VikingFS"] = None,
    ):
        self.vikingdb = vikingdb
        self._viking_fs = viking_fs

    def _get_viking_fs(self) -> "VikingFS":
        if self._viking_fs is not None:
            return self._viking_fs
        from openviking.storage.viking_fs import get_viking_fs

        return get_viking_fs()

    async def initialize_account_directories(self, ctx: RequestContext) -> int:
        """Initialize account-shared scope roots."""
        count = 0
        scope_roots = {
            "user": PRESET_DIRECTORIES["user"],
            "agent": PRESET_DIRECTORIES["agent"],
            "resources": PRESET_DIRECTORIES["resources"],
            "session": PRESET_DIRECTORIES["session"],
        }
        for scope, defn in scope_roots.items():
            root_uri = f"viking://{scope}"
            created = await self._ensure_directory(
                uri=root_uri,
                parent_uri=None,
                defn=defn,
                scope=scope,
                ctx=ctx,
            )
            if created:
                count += 1
        return count

    async def initialize_user_directories(self, ctx: RequestContext) -> int:
        """Initialize user-space tree lazily for the current user."""
        if "user" not in PRESET_DIRECTORIES:
            return 0
        user_space_root = canonical_user_root(ctx)
        user_tree = PRESET_DIRECTORIES["user"]
        parent_uri = "viking://user"
        if ctx.namespace_policy.isolate_user_scope_by_agent:
            container_uri = f"viking://user/{ctx.user.user_id}"
            await self._ensure_container_directory(container_uri, parent_uri=parent_uri, ctx=ctx)
            parent_uri = container_uri
        created = await self._ensure_directory(
            uri=user_space_root,
            parent_uri=parent_uri,
            defn=user_tree,
            scope="user",
            ctx=ctx,
        )
        count = 1 if created else 0
        count += await self._initialize_children(
            "user", user_tree.children, user_space_root, ctx=ctx
        )
        return count

    async def initialize_agent_directories(self, ctx: RequestContext) -> int:
        """Initialize agent-space tree lazily for the current user+agent."""
        if "agent" not in PRESET_DIRECTORIES:
            return 0
        agent_space_root = canonical_agent_root(ctx)
        agent_tree = PRESET_DIRECTORIES["agent"]
        parent_uri = "viking://agent"
        if ctx.namespace_policy.isolate_agent_scope_by_user:
            container_uri = f"viking://agent/{ctx.user.agent_id}"
            await self._ensure_container_directory(container_uri, parent_uri=parent_uri, ctx=ctx)
            parent_uri = container_uri
        created = await self._ensure_directory(
            uri=agent_space_root,
            parent_uri=parent_uri,
            defn=agent_tree,
            scope="agent",
            ctx=ctx,
        )
        count = 1 if created else 0
        count += await self._initialize_children(
            "agent", agent_tree.children, agent_space_root, ctx=ctx
        )

        return count

    async def _ensure_container_directory(
        self,
        uri: str,
        parent_uri: Optional[str],
        ctx: RequestContext,
    ) -> None:
        """Ensure an intermediate namespace container exists without seeding vectors."""
        try:
            await self._get_viking_fs().mkdir(uri, exist_ok=True, ctx=ctx)
        except Exception:
            pass

    async def _ensure_directory(
        self,
        uri: str,
        parent_uri: Optional[str],
        defn: DirectoryDefinition,
        scope: str,
        ctx: RequestContext,
    ) -> bool:
        """Ensure directory exists, return whether newly created."""
        from openviking_cli.utils.logger import get_logger

        logger = get_logger(__name__)
        created = False
        agfs_created = False
        # 1. Ensure files exist in AGFS
        if not await self._check_agfs_files_exist(uri, ctx=ctx):
            logger.debug(f"[VikingFS] Creating directory: {uri} for scope {scope}")
            await self._create_agfs_structure(uri, defn.abstract, defn.overview, ctx=ctx)
            created = True
            agfs_created = True
        else:
            logger.debug(f"[VikingFS] Directory {uri} already exists")

        # 2. Seed directory L0/L1 vectors only during fresh initialization.
        owner_space = self._owner_space_for_scope(scope=scope, ctx=ctx)
        if agfs_created:
            await self._ensure_directory_l0_l1_vectors(
                uri=uri,
                parent_uri=parent_uri,
                defn=defn,
                owner_space=owner_space,
                ctx=ctx,
            )
        return created

    async def _ensure_directory_l0_l1_vectors(
        self,
        uri: str,
        parent_uri: Optional[str],
        defn: DirectoryDefinition,
        owner_space: str,
        ctx: RequestContext,
    ) -> None:
        """Ensure L0/L1 vector records exist for a preset directory."""
        for level, vector_text in (
            (0, defn.abstract),
            (1, defn.overview),
        ):
            existing = await self.vikingdb.get_context_by_uri(
                uri=uri,
                level=level,
                limit=1,
                ctx=ctx,
            )
            if existing:
                continue
            context = Context(
                uri=uri,
                parent_uri=parent_uri,
                is_leaf=False,
                context_type=get_context_type_for_uri(uri),
                abstract=defn.abstract,
                level=level,
                user=ctx.user,
                account_id=ctx.account_id,
                owner_space=owner_space,
            )
            context.set_vectorize(Vectorize(text=vector_text))
            emb_msg = EmbeddingMsgConverter.from_context(context)
            if emb_msg:
                await self.vikingdb.enqueue_embedding_msg(emb_msg)

    @staticmethod
    def _owner_space_for_scope(scope: str, ctx: RequestContext) -> str:
        if scope in {"user", "session"}:
            return user_space_fragment(ctx)
        if scope == "agent":
            return agent_space_fragment(ctx)
        return ""

    async def _check_agfs_files_exist(self, uri: str, ctx: RequestContext) -> bool:
        """Check if L0/L1 files exist in AGFS."""
        try:
            viking_fs = self._get_viking_fs()
            await viking_fs.abstract(uri, ctx=ctx)
            return True
        except Exception:
            return False

    async def _initialize_children(
        self,
        scope: str,
        children: List[DirectoryDefinition],
        parent_uri: str,
        ctx: RequestContext,
    ) -> int:
        """Recursively initialize subdirectories."""
        count = 0

        for defn in children:
            uri = f"{parent_uri}/{defn.path}"

            created = await self._ensure_directory(
                uri=uri,
                parent_uri=parent_uri,
                defn=defn,
                scope=scope,
                ctx=ctx,
            )
            if created:
                count += 1

            if defn.children:
                count += await self._initialize_children(scope, defn.children, uri, ctx=ctx)

        return count

    async def _create_agfs_structure(
        self, uri: str, abstract: str, overview: str, ctx: RequestContext
    ) -> None:
        """Create L0/L1 file structure for directory in AGFS."""
        await self._get_viking_fs().write_context(
            uri=uri,
            abstract=abstract,
            overview=overview,
            is_leaf=False,  # Preset directories can continue traversing downward
            ctx=ctx,
        )
