# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Preset directory structure definitions for OpenViking.

OpenViking uses a virtual filesystem where all directories are data records.
This module defines the preset directory structure that is created on initialization.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional

from openviking.core.context import Context, ContextType, Vectorize
from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter

if TYPE_CHECKING:
    from openviking.storage import VikingDBManager


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
                abstract="Agent's long-term memory storage. Contains cases and patterns, managed hierarchically by type.",
                overview="Use this directory to access Agent's learning memories. Contains two main categories: "
                "1) cases-specific cases, 2) patterns-reusable patterns.",
                children=[
                    DirectoryDefinition(
                        path="cases",
                        abstract="Agent's case records. Stores specific problems and solutions, new problems and resolution processes encountered in each interaction.",
                        overview="Access cases when encountering similar problems, reference historical solutions. "
                        "Cases are records of specific conversations, each independent and not updated.",
                    ),
                    DirectoryDefinition(
                        path="patterns",
                        abstract="Agent's effective patterns. Stores reusable processes and best practices distilled from multiple interactions, "
                        "validated general solutions.",
                        overview="Access patterns when executing tasks requiring strategy selection or process determination. "
                        "Patterns are highly distilled experiences, each independent and not updated; create new pattern if modification needed.",
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
    "transactions": DirectoryDefinition(
        path="",
        abstract="Transaction scope. Stores transaction records",
        overview="Per-account transaction storage",
    ),
}


def get_context_type_for_uri(uri: str) -> str:
    """Determine context_type based on URI."""
    uri = uri[:20]
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
    ):
        self.vikingdb = vikingdb

    async def initialize_all(self) -> int:
        """Initialize all global preset directories (skip user scope)."""
        from openviking_cli.utils.logger import get_logger

        logger = get_logger(__name__)
        count = 0
        for scope, root_defn in PRESET_DIRECTORIES.items():
            if scope == "user":
                logger.info("Skipping user scope (lazy initialization)")
                continue

            root_uri = f"viking://{scope}"
            created = await self._ensure_directory(
                uri=root_uri,
                parent_uri=None,
                defn=root_defn,
                scope=scope,
            )
            if created:
                count += 1

            count += await self._initialize_children(scope, root_defn.children, root_uri)
        return count

    async def initialize_user_directories(self) -> int:
        """Initialize user preset directory tree.

        Returns:
            Number of directories created
        """
        if "user" not in PRESET_DIRECTORIES:
            return 0

        user_root_uri = "viking://user"
        user_tree = PRESET_DIRECTORIES["user"]

        created = await self._ensure_directory(
            uri=user_root_uri,
            parent_uri=None,
            defn=user_tree,
            scope="user",
        )

        count = 1 if created else 0
        count += await self._initialize_children("user", user_tree.children, user_root_uri)

        return count

    async def _ensure_directory(
        self,
        uri: str,
        parent_uri: Optional[str],
        defn: DirectoryDefinition,
        scope: str,
    ) -> bool:
        """Ensure directory exists, return whether newly created."""
        from openviking_cli.utils.logger import get_logger

        logger = get_logger(__name__)
        created = False
        # 1. Ensure files exist in AGFS
        if not await self._check_agfs_files_exist(uri):
            logger.debug(f"[VikingFS] Creating directory: {uri} for scope {scope}")
            await self._create_agfs_structure(uri, defn.abstract, defn.overview)
            created = True
        else:
            logger.debug(f"[VikingFS] Directory {uri} already exists")

        # 2. Ensure record exists in vector storage
        from openviking_cli.utils.config.vectordb_config import COLLECTION_NAME

        existing = await self.vikingdb.filter(
            collection=COLLECTION_NAME,
            filter={"op": "must", "field": "uri", "conds": [uri]},
            limit=1,
        )
        if not existing:
            context = Context(
                uri=uri,
                parent_uri=parent_uri,
                is_leaf=False,
                context_type=get_context_type_for_uri(uri),
                abstract=defn.abstract,
            )
            context.set_vectorize(Vectorize(text=defn.overview))
            dir_emb_msg = EmbeddingMsgConverter.from_context(context)
            await self.vikingdb.enqueue_embedding_msg(dir_emb_msg)
            created = True
        return created

    async def _check_agfs_files_exist(self, uri: str) -> bool:
        """Check if L0/L1 files exist in AGFS."""
        from openviking.storage.viking_fs import get_viking_fs

        try:
            viking_fs = get_viking_fs()
            await viking_fs.abstract(uri)
            return True
        except Exception:
            return False

    async def _initialize_children(
        self,
        scope: str,
        children: List[DirectoryDefinition],
        parent_uri: str,
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
            )
            if created:
                count += 1

            if defn.children:
                count += await self._initialize_children(scope, defn.children, uri)

        return count

    async def _create_agfs_structure(self, uri: str, abstract: str, overview: str) -> None:
        """Create L0/L1 file structure for directory in AGFS."""
        from openviking.storage.viking_fs import get_viking_fs

        await get_viking_fs().write_context(
            uri=uri,
            abstract=abstract,
            overview=overview,
            is_leaf=False,  # Preset directories can continue traversing downward
        )
