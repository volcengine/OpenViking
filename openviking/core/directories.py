# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Preset directory structure definitions for OpenViking.

OpenViking uses a virtual filesystem where all directories are data records.
This module defines the preset directory structure that is created on initialization.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional

from openviking.core.context import Context, Vectorize
from openviking.core.namespace import (
    canonical_user_root,
    context_type_for_uri,
    is_session_uri,
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
                    DirectoryDefinition(
                        path="cases",
                        abstract="User's case memories. Stores concrete problem contexts and resolutions learned from sessions.",
                        overview="Access when handling similar future problems. Cases are specific examples, separate from reusable patterns.",
                    ),
                    DirectoryDefinition(
                        path="patterns",
                        abstract="User's pattern memories. Stores reusable methods, workflows, and SOP-like lessons.",
                        overview="Access when applying accumulated methods to new tasks. Patterns are generalized from cases and interactions.",
                    ),
                    DirectoryDefinition(
                        path="tools",
                        abstract="User's tool usage memories. Stores tool behavior, parameter experience, and failure modes.",
                        overview="Access when deciding how to call tools or diagnosing tool failures.",
                    ),
                    DirectoryDefinition(
                        path="skills",
                        abstract="User's skill execution memories. Stores experience about using configured skills.",
                        overview="Access when choosing or executing skills. This is memory about skill usage, not the skill definition itself.",
                    ),
                    DirectoryDefinition(
                        path="trajectories",
                        abstract="User's execution trajectory records. Stores end-to-end task execution traces when trajectory memory is enabled.",
                        overview="Access when reviewing how a previous task was executed.",
                    ),
                    DirectoryDefinition(
                        path="experiences",
                        abstract="User's generalized experience memories distilled from execution trajectories.",
                        overview="Access when applying lessons learned from repeated execution trajectories.",
                    ),
                ],
            ),
            DirectoryDefinition(
                path="resources",
                abstract="User-owned resource storage. Contains private documents and knowledge resources owned by the current User.",
                overview="Use this directory for resources scoped to the current User. Project and document directories are created lazily as content is added.",
            ),
            DirectoryDefinition(
                path="privacy",
                abstract="User privacy config root. Stores user-scoped sensitive configuration snapshots by category and target key.",
                overview="Use this directory to access privacy-managed configuration values such as skill secrets. Concrete category and target-key subdirectories are created lazily by the privacy config service.",
            ),
            DirectoryDefinition(
                path="peers",
                abstract="User peer memory root. Stores the current User's long-term memory about stable interaction peers.",
                overview="Use this directory when the current User needs to distinguish long-term interaction objects such as visitors, teammates, or external contacts. Peer directories are created lazily from session peer_id values.",
            ),
            DirectoryDefinition(
                path="skills",
                abstract="User skill registry. Uses Claude Skills protocol format, flat storage of callable skill definitions owned by the current User.",
                overview="Access when the current User or a proxy acting with the current User's API key needs to execute specific tasks. Skills categorized by tags, "
                "should retrieve relevant skills before executing tasks, select most appropriate skill to execute.",
            ),
            DirectoryDefinition(
                path="sessions",
                abstract="User session registry. Stores conversation state, live messages, tool outputs, and session history owned by the current User.",
                overview="Use this directory to inspect or migrate user-owned session records. Session entries are created lazily when sessions are started.",
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
        """Initialize account-shared scope roots.

        ``viking://user`` is a current-user shorthand at API boundaries. Its
        concrete metadata belongs to ``viking://user/{user_id}`` and is created
        by ``initialize_user_directories``.
        """
        count = 0
        scope_roots = {
            "resources": PRESET_DIRECTORIES["resources"],
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
        """Initialize the current user's root and first-level entry directories.

        Concrete leaf namespaces under entries such as ``memories`` or ``sessions``
        are still created lazily when content is written. This keeps a new user
        root discoverable without materializing the full empty taxonomy.
        """
        if "user" not in PRESET_DIRECTORIES:
            return 0
        user_space_root = canonical_user_root(ctx)
        user_tree = PRESET_DIRECTORIES["user"]
        parent_uri = "viking://user"
        count = 0
        if await self._ensure_directory(
            uri=user_space_root,
            parent_uri=parent_uri,
            defn=user_tree,
            scope="user",
            ctx=ctx,
        ):
            count += 1

        for child in user_tree.children:
            child_uri = f"{user_space_root}/{child.path}"
            if await self._ensure_directory(
                uri=child_uri,
                parent_uri=user_space_root,
                defn=child,
                scope="user",
                ctx=ctx,
            ):
                count += 1

        return count

    async def initialize_agent_directories(self, ctx: RequestContext) -> int:
        """Deprecated compatibility hook; agent directories are no longer initialized."""
        return 0

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
        if agfs_created and not is_session_uri(uri):
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
                context_type=context_type_for_uri(uri),
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
