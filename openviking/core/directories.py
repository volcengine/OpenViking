# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Preset directory structure definitions for OpenViking.

OpenViking uses a virtual filesystem where all directories are data records.
This module defines the preset directory structure that is created on initialization.
"""

import asyncio
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence

from openviking.core.context import Context, Vectorize
from openviking.core.namespace import (
    canonical_user_root,
    context_type_for_uri,
    is_session_uri,
    user_space_fragment,
)
from openviking.server.identity import RequestContext
from openviking.storage.abstract_overview import AbstractOverviewFormatError, body_for_preview
from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter
from openviking.storage.vector_ids import vector_record_id

if TYPE_CHECKING:
    from openviking.storage import VikingDBManager
    from openviking.storage.viking_fs import VikingFS

from openviking_cli.exceptions import NotFoundError


@dataclass
class DirectoryDefinition:
    """Directory definition."""

    path: str  # Relative path, e.g., "memory/identity"
    abstract: str  # L0 summary
    overview: str  # L1 description
    children: List["DirectoryDefinition"] = field(default_factory=list)


@dataclass(frozen=True)
class _DirectoryTarget:
    uri: str
    parent_uri: Optional[str]
    definition: DirectoryDefinition
    scope: str
    ctx: RequestContext


# Preset directory trees, keyed by their namespace path.
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
    "agent": DirectoryDefinition(
        path="",
        abstract="Shared agent capabilities and configuration available across the account.",
        overview="Account-wide capability definitions, organized into skills, communication "
        "endpoints, tools, and payments. Each category has its own directory.",
        children=[
            DirectoryDefinition(
                path="skills",
                abstract="Shared skill registry. Stores callable skill definitions available across the account.",
                overview="Use this directory for skills shared by users in the same account. "
                "Each skill is stored in its own directory using the SKILL.md format. "
                "User-private skills remain under viking://user/{user_id}/skills.",
            ),
            DirectoryDefinition(
                path="endpoints",
                abstract="Shared communication endpoint configuration for agent capabilities.",
                overview="Stores definitions of external communication endpoints, organized "
                "by protocol or service, such as A2A and ANP.",
            ),
            DirectoryDefinition(
                path="tools",
                abstract="Shared tool configuration for agent capabilities.",
                overview="Stores tool integration definitions, organized by protocol or "
                "provider, such as MCP server configurations.",
            ),
            DirectoryDefinition(
                path="payments",
                abstract="Shared payment capability configuration for agents.",
                overview="Stores payment capability definitions, organized by protocol "
                "or provider, such as AP2 configurations.",
            ),
        ],
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

    async def initialize_account_workspace(self, ctx: RequestContext) -> tuple[int, int]:
        """Initialize account and first-user preset directories as one batch."""
        account_roots, account_children = self._account_directory_targets(ctx)
        user_root, user_children = self._user_directory_targets(ctx)
        created = await self._initialize_directory_trees(
            (*account_roots, user_root), (*account_children, *user_children)
        )
        user_count = sum(target.scope == "user" for target in created)
        return len(created) - user_count, user_count

    async def initialize_account_directories(self, ctx: RequestContext) -> int:
        """Initialize account-shared roots and their preset first-level directories.

        ``viking://user`` is the container of user spaces, not a space itself.
        Its concrete metadata belongs to ``viking://user/{user_id}`` and is
        created by ``initialize_user_directories``.
        """
        roots, children = self._account_directory_targets(ctx)
        return len(await self._initialize_directory_trees(roots, children))

    async def initialize_user_directories(self, ctx: RequestContext) -> int:
        """Initialize the current user's root and first-level entry directories.

        Concrete leaf namespaces under entries such as ``memories`` or ``sessions``
        are still created lazily when content is written. This keeps a new user
        root discoverable without materializing the full empty taxonomy.
        """
        if "user" not in PRESET_DIRECTORIES:
            return 0
        user_root, user_children = self._user_directory_targets(ctx)
        return len(await self._initialize_directory_trees((user_root,), user_children))

    async def _initialize_directory_trees(
        self, roots: Sequence[_DirectoryTarget], children: Sequence[_DirectoryTarget]
    ) -> list[_DirectoryTarget]:
        """Initialize roots before children and index successful writes as one batch."""
        results = await asyncio.gather(
            *(self._ensure_agfs_directory(target) for target in roots),
            return_exceptions=True,
        )
        created, error = self._partition_directory_results(results)
        if error is None:
            results = await asyncio.gather(
                *(self._ensure_agfs_directory(target) for target in children),
                return_exceptions=True,
            )
            created_children, error = self._partition_directory_results(results)
            created.extend(created_children)
        await self._ensure_directory_l0_l1_vectors(created)
        if error is not None:
            raise error
        return created

    @staticmethod
    def _account_directory_targets(
        ctx: RequestContext,
    ) -> tuple[list[_DirectoryTarget], list[_DirectoryTarget]]:
        roots = []
        children = []
        for scope in ("resources", "agent"):
            uri = f"viking://{scope}"
            tree = PRESET_DIRECTORIES[scope]
            roots.append(
                _DirectoryTarget(
                    uri=uri,
                    parent_uri=None,
                    definition=tree,
                    scope=scope,
                    ctx=ctx,
                )
            )
            children.extend(
                _DirectoryTarget(
                    uri=f"{uri}/{child.path}",
                    parent_uri=uri,
                    definition=child,
                    scope=scope,
                    ctx=ctx,
                )
                for child in tree.children
            )
        return roots, children

    @staticmethod
    def _user_directory_targets(
        ctx: RequestContext,
    ) -> tuple[_DirectoryTarget, list[_DirectoryTarget]]:
        # Preset initialization is a server-controlled write to the current
        # user's own root and first-level directories. Actor-peer view must
        # still protect peer subtrees during normal filesystem mutations, but
        # it must not prevent a fresh user from creating the container that
        # owns those subtrees in the first place.
        initialization_ctx = replace(ctx, actor_peer_id=None)
        user_tree = PRESET_DIRECTORIES["user"]
        user_root_uri = canonical_user_root(initialization_ctx)
        root = _DirectoryTarget(
            uri=user_root_uri,
            parent_uri="viking://user",
            definition=user_tree,
            scope="user",
            ctx=initialization_ctx,
        )
        children = [
            _DirectoryTarget(
                uri=f"{user_root_uri}/{child.path}",
                parent_uri=user_root_uri,
                definition=child,
                scope="user",
                ctx=initialization_ctx,
            )
            for child in user_tree.children
        ]
        return root, children

    @staticmethod
    def _partition_directory_results(
        results: Sequence[_DirectoryTarget | None | BaseException],
    ) -> tuple[list[_DirectoryTarget], Optional[BaseException]]:
        """Keep successful concurrent writes while preserving the first failure."""
        created_targets = []
        first_error = None
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                if first_error is None:
                    first_error = result
            elif result is not None:
                created_targets.append(result)
        return created_targets, first_error

    async def _ensure_agfs_directory(self, target: _DirectoryTarget) -> _DirectoryTarget | None:
        """Fill missing L0/L1 files, preserving stored text for vector initialization."""
        from openviking_cli.utils.logger import get_logger

        logger = get_logger(__name__)
        viking_fs = self._get_viking_fs()
        contents = {}
        missing = {}
        try:
            for name, default in (
                ("abstract", target.definition.abstract),
                ("overview", target.definition.overview),
            ):
                try:
                    raw = await viking_fs.read_file(f"{target.uri}/.{name}.md", ctx=target.ctx)
                except (FileNotFoundError, NotFoundError):
                    contents[name] = default
                    missing[name] = contents[name]
                else:
                    contents[name] = body_for_preview(raw)
        except AbstractOverviewFormatError as exc:
            logger.warning(
                "[VikingFS] Directory %s (account=%s) has invalid L0/L1 metadata; "
                "preserving existing contents: %s",
                target.uri,
                target.ctx.account_id,
                exc,
            )
            return None
        if not missing:
            logger.debug(f"[VikingFS] Directory {target.uri} already exists")
            return None
        logger.debug(f"[VikingFS] Creating directory: {target.uri} for scope {target.scope}")
        await viking_fs.write_context(
            uri=target.uri,
            **missing,
            is_leaf=False,
            ctx=target.ctx,
        )
        return replace(target, definition=replace(target.definition, **contents))

    async def _ensure_directory_l0_l1_vectors(self, targets: list[_DirectoryTarget]) -> None:
        """Seed missing L0/L1 records after one batch existence read."""
        vector_targets = [
            (target, level, vector_text)
            for target in targets
            if not is_session_uri(target.uri)
            for level, vector_text in (
                (0, target.definition.abstract),
                (1, target.definition.overview),
            )
        ]
        if not vector_targets:
            return

        record_ids = [
            vector_record_id(target.ctx.account_id, target.uri, level)
            for target, level, _vector_text in vector_targets
        ]
        existing = await self.vikingdb.get(record_ids, ctx=vector_targets[0][0].ctx)
        existing_ids = {record.get("id") for record in existing if record.get("id")}

        messages = []
        for record_id, (target, level, vector_text) in zip(record_ids, vector_targets, strict=True):
            if record_id in existing_ids:
                continue
            context = Context(
                uri=target.uri,
                parent_uri=target.parent_uri,
                is_leaf=False,
                context_type=context_type_for_uri(target.uri),
                abstract=target.definition.abstract,
                level=level,
                user=target.ctx.user,
                account_id=target.ctx.account_id,
                owner_space=self._owner_space_for_scope(scope=target.scope, ctx=target.ctx),
            )
            context.set_vectorize(Vectorize(text=vector_text))
            emb_msg = EmbeddingMsgConverter.from_context(context)
            if emb_msg:
                messages.append(emb_msg)
        await asyncio.gather(
            *(self.vikingdb.enqueue_embedding_msg(message) for message in messages)
        )

    @staticmethod
    def _owner_space_for_scope(scope: str, ctx: RequestContext) -> str:
        if scope in {"user", "session"}:
            return user_space_fragment(ctx)
        return ""
