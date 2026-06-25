# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Local Client for OpenViking.

Implements BaseClient interface using direct service calls (embedded mode).
"""

from typing import Any, Dict, List, Optional, Union

from openviking.core.peer_id import normalize_peer_id
from openviking.server.identity import RequestContext, Role
from openviking.server.routers.skills import (
    _list_skill_files,
    _list_skills_from_root,
    _merge_skills,
    _require_skill,
    _restore_skill_privacy,
    _skill_summary_from_hit,
    _validate_skill_format,
)
from openviking.service import OpenVikingService
from openviking.service.task_tracker import get_task_tracker
from openviking.telemetry import TelemetryRequest
from openviking.telemetry.execution import (
    attach_telemetry_payload,
    run_with_telemetry,
)
from openviking.utils.search_filters import SearchContextTypeInput, merge_search_filter
from openviking.utils.tags import normalize_search_tags
from openviking_cli.client.base import BaseClient
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import run_async


def _to_jsonable(value: Any) -> Any:
    """Convert internal objects into JSON-serializable values."""
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value


def _resolve_search_filter(
    filter: Optional[Dict[str, Any]],
    context_type: Optional[SearchContextTypeInput],
    since: Optional[str],
    until: Optional[str],
    time_field: Optional[str],
    tags: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Merge public retrieval filter shortcuts into the metadata filter."""
    merged = merge_search_filter(
        filter,
        context_type=context_type,
        since=since,
        until=until,
        time_field=time_field,
    )
    normalized_tags = normalize_search_tags(tags)
    if not normalized_tags:
        return merged
    tag_filter = {"op": "must", "field": "search_tags", "conds": normalized_tags}
    if merged:
        return {"op": "and", "conds": [merged, tag_filter]}
    return tag_filter


class LocalClient(BaseClient):
    """Local Client for OpenViking (embedded mode).

    Implements BaseClient interface using direct service calls.
    """

    def __init__(
        self,
        path: Optional[str] = None,
        user: Optional[UserIdentifier] = None,
        actor_peer_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ):
        """Initialize LocalClient.

        Args:
            path: Local storage path (overrides ov.conf storage path)
            user: Explicit account/user identity for embedded mode
            actor_peer_id: Optional view filter for the current user's peer collection.
            agent_id: Legacy alias that marks the actor peer scope as legacy agent mode.
        """
        if actor_peer_id is not None and agent_id is not None:
            raise ValueError("actor_peer_id cannot be used with legacy agent_id")
        effective_actor_peer_id = actor_peer_id or agent_id
        self._service = OpenVikingService(
            path=path,
            user=user or UserIdentifier.the_default_user(),
        )
        self._user = self._service.user
        self._ctx = RequestContext(
            user=self._user,
            role=Role.USER,
            actor_peer_id=normalize_peer_id(effective_actor_peer_id),
            legacy_agent_id=normalize_peer_id(agent_id),
        )

    @property
    def service(self) -> OpenVikingService:
        """Get the underlying service instance."""
        return self._service

    # ============= Lifecycle =============

    async def initialize(self) -> None:
        """Initialize the local client."""
        await self._service.initialize()

    async def close(self) -> None:
        """Close the local client."""
        await self._service.close()

    # ============= Resource Management =============

    async def add_resource(
        self,
        path: str,
        to: Optional[str] = None,
        parent: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        wait: bool = False,
        timeout: Optional[float] = None,
        build_index: bool = True,
        summarize: bool = False,
        telemetry: TelemetryRequest = False,
        watch_interval: float = 0,
        args: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Add resource to OpenViking."""
        if to and parent:
            raise ValueError("Cannot specify both 'to' and 'parent' at the same time.")

        execution = await run_with_telemetry(
            operation="resources.add_resource",
            telemetry=telemetry,
            fn=lambda: self._service.resources.add_resource(
                path=path,
                ctx=self._ctx,
                to=to,
                parent=parent,
                reason=reason,
                instruction=instruction,
                wait=wait,
                timeout=timeout,
                build_index=build_index,
                summarize=summarize,
                watch_interval=watch_interval,
                args=args,
                **kwargs,
            ),
        )
        return attach_telemetry_payload(
            execution.result,
            execution.telemetry,
        )

    async def add_skill(
        self,
        data: Any,
        wait: bool = False,
        timeout: Optional[float] = None,
        telemetry: TelemetryRequest = False,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add skill to OpenViking."""
        execution = await run_with_telemetry(
            operation="resources.add_skill",
            telemetry=telemetry,
            fn=lambda: self._service.resources.add_skill(
                data=data,
                ctx=self._ctx,
                wait=wait,
                timeout=timeout,
                target_uri=target_uri,
            ),
        )
        return attach_telemetry_payload(
            execution.result,
            execution.telemetry,
        )

    async def list_skills(
        self,
        node_limit: int = 1000,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List installed skills."""
        from openviking.core.namespace import canonical_user_root

        service = self._service
        ctx = self._ctx
        if target_uri:
            skills = await _list_skills_from_root(service, ctx, target_uri)
            return {"root_uri": target_uri, "skills": skills, "total": len(skills)}
        else:
            user_skills = await _list_skills_from_root(
                service, ctx, f"{canonical_user_root(ctx)}/skills"
            )
            agent_skills = await _list_skills_from_root(service, ctx, "viking://agent/skills")
            merged = [*user_skills, *agent_skills]
            return {
                "root_uris": [f"{canonical_user_root(ctx)}/skills", "viking://agent/skills"],
                "skills": merged,
                "total": len(merged),
            }

    async def find_skills(
        self,
        query: str,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        level: Optional[List[int]] = None,
        telemetry: TelemetryRequest = False,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Find skills by semantic search."""
        from openviking.core.namespace import canonical_user_root

        service = self._service
        ctx = self._ctx

        async def _search_at(uri: str) -> list:
            result = await service.search.find(
                query=query,
                ctx=ctx,
                target_uri=uri,
                limit=limit,
                score_threshold=score_threshold,
                level=level,
            )
            result_dict = result.to_dict() if hasattr(result, "to_dict") else dict(result or {})
            return [_skill_summary_from_hit(hit) for hit in result_dict.get("skills", [])]

        if target_uri:
            execution = await run_with_telemetry(
                operation="skills.find",
                telemetry=telemetry,
                fn=lambda: _search_at(target_uri),
            )
            hits = execution.result
            return {
                "root_uri": target_uri,
                "skills": hits,
                "total": len(hits),
            }
        else:
            user_root = f"{canonical_user_root(ctx)}/skills"
            agent_root = "viking://agent/skills"

            user_execution = await run_with_telemetry(
                operation="skills.find",
                telemetry=telemetry,
                fn=lambda: _search_at(user_root),
            )
            user_hits = user_execution.result

            agent_hits = await _search_at(agent_root)

            merged = [*user_hits, *agent_hits]
            if merged and "score" in merged[0]:
                merged.sort(key=lambda x: x.get("score", 0), reverse=True)

            return {
                "root_uris": [user_root, agent_root],
                "skills": merged,
                "total": len(merged),
            }

    async def get_skill(
        self,
        skill_name: str,
        include_content: Optional[bool] = None,
        include_files: bool = True,
        include_source: bool = False,
        level: Optional[int] = None,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get a skill by name."""
        from openviking.server.routers.skills import (
            _parse_abstract_meta,
            _relative_skill_path,
            _skill_file_kind,
            _skill_summary_from_meta,
            SOURCE_METADATA_FILENAME,
        )
        from openviking.server.skill_source_metadata import read_skill_source_metadata

        if level is not None and level not in {0, 1, 2}:
            raise InvalidArgumentError(
                "Skill show level must be 0, 1, or 2",
                details={"field": "level", "allowed": [0, 1, 2]},
            )

        service = self._service
        ctx = self._ctx
        root_uri = await _require_skill(service, ctx, skill_name, target_uri)

        abstract = await service.fs.abstract(root_uri, ctx=ctx)
        result = _skill_summary_from_meta(skill_name, root_uri, _parse_abstract_meta(abstract))

        if level is None or level == 0:
            result["abstract"] = abstract
        if level is None or level == 1:
            result["overview"] = await service.fs.overview(root_uri, ctx=ctx)
        if level == 2 or include_content is True or (level is None and include_content is not False):
            from openviking.server.routers.skills import _skill_md_uri

            result["content"] = await service.fs.read(_skill_md_uri(root_uri), ctx=ctx)

        if include_files:
            entries = await _list_skill_files(service, ctx, root_uri)
            result["files"] = [
                {
                    "name": entry.get("name") or skill_name,
                    "uri": entry.get("uri", ""),
                    "path": _relative_skill_path(root_uri, entry.get("uri", "")),
                    "is_dir": entry.get("isDir", False),
                    "kind": _skill_file_kind(
                        _relative_skill_path(root_uri, entry.get("uri", "")),
                        entry.get("isDir", False),
                    ),
                }
                for entry in entries
                if isinstance(entry, dict)
                and _relative_skill_path(root_uri, entry.get("uri", "")) != SOURCE_METADATA_FILENAME
            ]

        if include_source:
            result["source"] = await read_skill_source_metadata(service, ctx, root_uri)

        return result

    async def update_skill(
        self,
        skill_name: str,
        data: Any,
        wait: bool = False,
        timeout: Optional[float] = None,
        source_metadata: Optional[Dict[str, Any]] = None,
        telemetry: TelemetryRequest = False,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update an existing skill."""
        service = self._service
        ctx = self._ctx

        # Verify the skill exists and determine its root URI
        root_uri = await _require_skill(service, ctx, skill_name, target_uri)
        skill_root_parent = root_uri.rsplit("/", 1)[0]

        execution = await run_with_telemetry(
            operation="skills.update",
            telemetry=telemetry,
            fn=lambda: self._update_skill_impl(
                skill_name, data, root_uri, skill_root_parent, wait, timeout, source_metadata
            ),
        )
        return attach_telemetry_payload(
            execution.result,
            execution.telemetry,
        )

    async def _update_skill_impl(
        self,
        skill_name: str,
        data: Any,
        root_uri: str,
        skill_root_parent: str,
        wait: bool,
        timeout: Optional[float],
        source_metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        import shutil
        import uuid

        from openviking.server.skill_source_metadata import persist_skill_source_metadata

        service = self._service
        ctx = self._ctx
        backup_uri = f"{skill_root_parent}/.{skill_name}.update-backup-{uuid.uuid4().hex}"
        backup_created = False
        privacy_update_attempted = False
        previous_privacy = None
        preparation = None
        privacy = service.privacy_configs
        effective_source_metadata = source_metadata or {
            "type": "embedded",
            "source": "inline_content",
            "operation": "update",
        }
        try:
            if privacy is not None:
                previous_privacy = await privacy.get_current(ctx, "skill", skill_name)
            preparation = await service.resources._skill_processor.prepare_skill_processing(  # noqa: SLF001
                data,
                ctx=ctx,
                allow_local_path_resolution=False,
            )
            expected_name = skill_name
            if preparation.skill_dict.get("name") != expected_name:
                raise InvalidArgumentError(
                    f"Skill name mismatch: path name is '{expected_name}', content name is '{preparation.skill_dict.get('name')}'",
                    details={
                        "expected": expected_name,
                        "actual": preparation.skill_dict.get("name"),
                    },
                )
            await service.fs.mv(root_uri, backup_uri, ctx=ctx)
            backup_created = True
            result = await service.resources.add_skill(
                data=preparation,
                ctx=ctx,
                wait=wait,
                timeout=timeout,
                allow_local_path_resolution=False,
                apply_privacy=False,
                privacy_change_reason="auto-extracted from update_skill",
                target_uri=skill_root_parent,
            )
            await persist_skill_source_metadata(service, ctx, result, effective_source_metadata)
            privacy_update_attempted = True
            await service.resources._skill_processor.apply_skill_privacy(  # noqa: SLF001
                preparation.skill_dict,
                preparation.privacy_values,
                ctx,
                change_reason="auto-extracted from update_skill",
                delete_if_empty=True,
            )
        except Exception:
            if backup_created:
                try:
                    await service.fs.rm(root_uri, ctx=ctx, recursive=True)
                except Exception:
                    pass
                try:
                    await service.fs.mv(backup_uri, root_uri, ctx=ctx)
                except Exception:
                    pass
            if privacy_update_attempted:
                try:
                    await _restore_skill_privacy(service, ctx, skill_name, previous_privacy)
                except Exception:
                    pass
            raise
        else:
            if backup_created:
                try:
                    await service.fs.rm(backup_uri, ctx=ctx, recursive=True)
                except Exception:
                    pass
            result["action"] = "update"
            return result
        finally:
            if preparation and preparation.cleanup_path:
                shutil.rmtree(preparation.cleanup_path, ignore_errors=True)

    async def delete_skill(
        self,
        skill_name: str,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Delete a skill."""
        service = self._service
        ctx = self._ctx
        root_uri = await _require_skill(service, ctx, skill_name, target_uri)
        result = await service.fs.rm(root_uri, ctx=ctx, recursive=True)
        return {"name": skill_name, "uri": root_uri, "root_uri": root_uri, "deleted": True}

    async def validate_skill(
        self,
        data: Any,
        strict: bool = False,
        source_path: Optional[str] = None,
        skill_dir_name: Optional[str] = None,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Validate skill data."""
        result = _validate_skill_format(
            self._service,
            data,
            strict=strict,
            skill_dir_name=skill_dir_name,
            source_path=source_path,
        )
        return result

    async def wait_processed(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Wait for all processing to complete."""
        return await self._service.resources.wait_processed(timeout=timeout)

    async def reindex(
        self,
        uri: str,
        mode: str = "vectors_only",
        wait: bool = True,
    ) -> Dict[str, Any]:
        """Reindex semantic/vector artifacts for a URI."""
        return await self._service.reindex(
            uri=uri,
            mode=mode,
            wait=wait,
        )

    async def build_index(self, resource_uris: Union[str, List[str]], **kwargs) -> Dict[str, Any]:
        """Manually trigger index building."""
        if isinstance(resource_uris, str):
            resource_uris = [resource_uris]
        return await self._service.resources.build_index(resource_uris, ctx=self._ctx, **kwargs)

    async def summarize(self, resource_uris: Union[str, List[str]], **kwargs) -> Dict[str, Any]:
        """Manually trigger summarization."""
        if isinstance(resource_uris, str):
            resource_uris = [resource_uris]
        return await self._service.resources.summarize(resource_uris, ctx=self._ctx, **kwargs)

    # ============= File System =============

    async def ls(
        self,
        uri: str,
        simple: bool = False,
        recursive: bool = False,
        output: str = "original",
        abs_limit: int = 256,
        show_all_hidden: bool = False,
    ) -> List[Any]:
        """List directory contents."""
        return await self._service.fs.ls(
            uri,
            ctx=self._ctx,
            simple=simple,
            recursive=recursive,
            output=output,
            abs_limit=abs_limit,
            show_all_hidden=show_all_hidden,
        )

    async def tree(
        self,
        uri: str,
        output: str = "original",
        abs_limit: int = 128,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Get directory tree."""
        return await self._service.fs.tree(
            uri,
            ctx=self._ctx,
            output=output,
            abs_limit=abs_limit,
            show_all_hidden=show_all_hidden,
            node_limit=node_limit,
        )

    async def stat(self, uri: str) -> Dict[str, Any]:
        """Get resource status."""
        return await self._service.fs.stat(uri, ctx=self._ctx)

    async def mkdir(self, uri: str, description: Optional[str] = None) -> None:
        """Create directory."""
        await self._service.fs.mkdir(uri, ctx=self._ctx, description=description)

    async def rm(
        self,
        uri: str,
        recursive: bool = False,
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> None:
        """Remove resource."""
        await self._service.fs.rm(
            uri,
            ctx=self._ctx,
            recursive=recursive,
            wait=wait,
            timeout=timeout,
        )

    async def mv(self, from_uri: str, to_uri: str) -> None:
        """Move resource."""
        await self._service.fs.mv(from_uri, to_uri, ctx=self._ctx)

    # ============= Content Reading =============

    async def read(self, uri: str, offset: int = 0, limit: int = -1) -> str:
        """Read file content.

        Args:
            uri: Viking URI
            offset: Starting line number (0-indexed). Default 0.
            limit: Number of lines to read. -1 means read to end. Default -1.
        """
        return await self._service.fs.read(uri, ctx=self._ctx, offset=offset, limit=limit)

    async def abstract(self, uri: str) -> str:
        """Read L0 abstract."""
        return await self._service.fs.abstract(uri, ctx=self._ctx)

    async def overview(self, uri: str) -> str:
        """Read L1 overview."""
        return await self._service.fs.overview(uri, ctx=self._ctx)

    async def write(
        self,
        uri: str,
        content: str,
        mode: str = "replace",
        wait: bool = False,
        timeout: Optional[float] = None,
        telemetry: TelemetryRequest = False,
    ) -> Dict[str, Any]:
        """Write text content to an existing file and refresh semantics/vectors."""
        execution = await run_with_telemetry(
            operation="content.write",
            telemetry=telemetry,
            fn=lambda: self._service.fs.write(
                uri=uri,
                content=content,
                ctx=self._ctx,
                mode=mode,
                wait=wait,
                timeout=timeout,
            ),
        )
        return attach_telemetry_payload(
            execution.result,
            execution.telemetry,
        )

    async def set_tags(
        self,
        uri: str,
        tags: List[str],
        mode: str = "replace",
        recursive: bool = False,
        telemetry: TelemetryRequest = False,
    ) -> Dict[str, Any]:
        """Replace explicit retrieval tags for a file or directory."""
        execution = await run_with_telemetry(
            operation="content.set_tags",
            telemetry=telemetry,
            fn=lambda: self._service.fs.set_tags(
                uri=uri,
                tags=tags,
                mode=mode,
                recursive=recursive,
                ctx=self._ctx,
            ),
        )
        return attach_telemetry_payload(
            execution.result,
            execution.telemetry,
        )

    # ============= Search =============

    async def find(
        self,
        query: str,
        target_uri: Union[str, List[str]] = "",
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict[str, Any]] = None,
        context_type: Optional[SearchContextTypeInput] = None,
        tags: Optional[List[str]] = None,
        telemetry: TelemetryRequest = False,
        since: Optional[str] = None,
        until: Optional[str] = None,
        time_field: Optional[str] = None,
        level: Optional[List[int]] = None,
    ) -> Any:
        """Semantic search without session context."""
        resolved_filter = _resolve_search_filter(
            filter, context_type, since, until, time_field, tags
        )
        execution = await run_with_telemetry(
            operation="search.find",
            telemetry=telemetry,
            fn=lambda: self._service.search.find(
                query=query,
                ctx=self._ctx,
                target_uri=target_uri,
                limit=limit,
                score_threshold=score_threshold,
                filter=resolved_filter,
                level=level,
            ),
        )
        return attach_telemetry_payload(
            execution.result,
            execution.telemetry,
        )

    async def search(
        self,
        query: str,
        target_uri: Union[str, List[str]] = "",
        session_id: Optional[str] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict[str, Any]] = None,
        context_type: Optional[SearchContextTypeInput] = None,
        tags: Optional[List[str]] = None,
        telemetry: TelemetryRequest = False,
        since: Optional[str] = None,
        until: Optional[str] = None,
        time_field: Optional[str] = None,
        level: Optional[List[int]] = None,
    ) -> Any:
        """Semantic search with optional session context."""
        resolved_filter = _resolve_search_filter(
            filter, context_type, since, until, time_field, tags
        )

        async def _search():
            session = None
            if session_id:
                session = self._service.sessions.session(self._ctx, session_id)
                await session.load()
            return await self._service.search.search(
                query=query,
                ctx=self._ctx,
                target_uri=target_uri,
                session=session,
                limit=limit,
                score_threshold=score_threshold,
                filter=resolved_filter,
                level=level,
            )

        execution = await run_with_telemetry(
            operation="search.search",
            telemetry=telemetry,
            fn=_search,
        )
        return attach_telemetry_payload(
            execution.result,
            execution.telemetry,
        )

    async def grep(
        self,
        uri: str,
        pattern: str,
        case_insensitive: bool = False,
        node_limit: Optional[int] = None,
        exclude_uri: Optional[str] = None,
        level_limit: int = 5,
    ) -> Dict[str, Any]:
        """Content search with pattern."""
        return await self._service.fs.grep(
            uri,
            pattern,
            ctx=self._ctx,
            case_insensitive=case_insensitive,
            node_limit=node_limit,
            exclude_uri=exclude_uri,
            level_limit=level_limit,
        )

    async def glob(self, pattern: str, uri: str = "viking://") -> Dict[str, Any]:
        """File pattern matching."""
        return await self._service.fs.glob(pattern, ctx=self._ctx, uri=uri)

    # ============= Relations =============

    async def relations(self, uri: str) -> List[Any]:
        """Get relations for a resource."""
        return await self._service.relations.relations(uri, ctx=self._ctx)

    async def link(self, from_uri: str, to_uris: Union[str, List[str]], reason: str = "") -> None:
        """Create link between resources."""
        await self._service.relations.link(from_uri, to_uris, ctx=self._ctx, reason=reason)

    async def unlink(self, from_uri: str, to_uri: str) -> None:
        """Remove link between resources."""
        await self._service.relations.unlink(from_uri, to_uri, ctx=self._ctx)

    # ============= Sessions =============

    async def create_session(
        self,
        session_id: Optional[str] = None,
        telemetry: TelemetryRequest = False,
        memory_policy: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new session.

        Args:
            session_id: Optional session ID. If provided, creates a session with the given ID.
                       If None, creates a new session with auto-generated ID.
        """
        execution = await run_with_telemetry(
            operation="session.create",
            telemetry=telemetry,
            fn=lambda: self._create_session_impl(session_id, memory_policy),
        )
        return attach_telemetry_payload(
            execution.result,
            execution.telemetry,
        )

    async def _create_session_impl(
        self,
        session_id: Optional[str],
        memory_policy: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        await self._service.initialize_user_directories(self._ctx)
        session = await self._service.sessions.create(
            self._ctx,
            session_id,
            memory_policy=memory_policy,
        )
        return {
            "session_id": session.session_id,
            "uri": session.uri,
            "user": session.user.to_dict(),
        }

    async def list_sessions(self) -> List[Any]:
        """List all sessions."""
        return await self._service.sessions.sessions(self._ctx)

    async def get_session(self, session_id: str, *, auto_create: bool = False) -> Dict[str, Any]:
        """Get session details."""
        session = await self._service.sessions.get(session_id, self._ctx, auto_create=auto_create)
        result = session.meta.to_dict()
        result["uri"] = session.uri
        result["user"] = session.user.to_dict()
        return result

    async def get_session_context(
        self, session_id: str, token_budget: int = 128_000
    ) -> Dict[str, Any]:
        """Get assembled session context."""
        session = await self._service.sessions.get(session_id, self._ctx, auto_create=False)
        result = await session.get_session_context(token_budget=token_budget)
        return _to_jsonable(result)

    async def get_session_archive(self, session_id: str, archive_id: str) -> Dict[str, Any]:
        """Get one completed archive for a session."""
        session = await self._service.sessions.get(session_id, self._ctx, auto_create=False)
        result = await session.get_session_archive(archive_id)
        return _to_jsonable(result)

    async def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        await self._service.sessions.delete(session_id, self._ctx)

    async def commit_session(
        self,
        session_id: str,
        telemetry: TelemetryRequest = False,
        *,
        keep_recent_count: int = 0,
    ) -> Dict[str, Any]:
        """Commit a session (archive and extract memories)."""
        execution = await run_with_telemetry(
            operation="session.commit",
            telemetry=telemetry,
            fn=lambda: self._service.sessions.commit(
                session_id,
                self._ctx,
                keep_recent_count=keep_recent_count,
            ),
        )
        return attach_telemetry_payload(
            execution.result,
            execution.telemetry,
        )

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Query background task status."""
        return await self._service.sessions.get_commit_task(task_id, self._ctx)

    async def list_tasks(
        self,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List background tasks visible to the current caller."""
        tasks = await get_task_tracker().list_tasks(
            task_type=task_type,
            status=status,
            resource_id=resource_id,
            limit=limit,
            account_id=self._ctx.account_id,
            user_id=self._ctx.user.user_id,
        )
        return [task.to_dict() for task in tasks]

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: Optional[str] = None,
        parts: Optional[List[Dict[str, Any]]] = None,
        created_at: Optional[str] = None,
        peer_id: Optional[str] = None,
        telemetry: TelemetryRequest = False,
    ) -> Dict[str, Any]:
        """Add a message to a session.

        Args:
            session_id: Session ID
            role: Message role ("user" or "assistant")
            content: Text content (simple mode, backward compatible)
            parts: Parts array (full Part support mode)
            created_at: Message creation time (ISO format string)
            peer_id: Optional stable interaction peer identity.

        If both content and parts are provided, parts takes precedence.
        """
        execution = await run_with_telemetry(
            operation="session.add_message",
            telemetry=telemetry,
            fn=lambda: self._add_message_impl(
                session_id,
                role,
                content,
                parts,
                created_at,
                peer_id,
            ),
        )
        return attach_telemetry_payload(
            execution.result,
            execution.telemetry,
        )

    async def _add_message_impl(
        self,
        session_id: str,
        role: str,
        content: Optional[str],
        parts: Optional[List[Dict[str, Any]]],
        created_at: Optional[str],
        peer_id: Optional[str],
    ) -> Dict[str, Any]:
        from openviking.message.part import Part, TextPart, part_from_dict

        session = await self._service.sessions.get(session_id, self._ctx, auto_create=True)

        message_parts: list[Part]
        if parts is not None:
            message_parts = [part_from_dict(p) for p in parts]
        elif content is not None:
            message_parts = [TextPart(text=content)]
        else:
            raise ValueError("Either content or parts must be provided")

        session.add_message(
            role,
            message_parts,
            peer_id=self._resolve_message_peer_id(role, peer_id),
            created_at=created_at,
        )
        return {
            "session_id": session_id,
            "message_count": len(session.messages),
        }

    async def batch_add_messages(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        telemetry: TelemetryRequest = False,
    ) -> Dict[str, Any]:
        """Add multiple messages to a session in one batch."""
        execution = await run_with_telemetry(
            operation="session.batch_add_messages",
            telemetry=telemetry,
            fn=lambda: self._batch_add_messages_impl(session_id, messages),
        )
        return attach_telemetry_payload(
            execution.result,
            execution.telemetry,
        )

    async def _batch_add_messages_impl(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        from openviking.message.part import Part, TextPart, part_from_dict

        session = await self._service.sessions.get(session_id, self._ctx, auto_create=True)
        specs: list[dict[str, Any]] = []

        for index, message in enumerate(messages):
            role = message.get("role")
            if not role:
                raise ValueError(f"messages[{index}]: missing required key 'role'")

            message_parts: list[Part]
            if message.get("parts") is not None:
                message_parts = [part_from_dict(part) for part in message["parts"]]
            elif message.get("content") is not None:
                message_parts = [TextPart(text=str(message["content"]))]
            else:
                raise ValueError(f"messages[{index}]: Either content or parts must be provided")

            specs.append(
                {
                    "role": role,
                    "parts": message_parts,
                    "peer_id": self._resolve_message_peer_id(role, message.get("peer_id")),
                    "created_at": message.get("created_at"),
                }
            )

        added = session.add_messages(specs)
        return {
            "session_id": session_id,
            "message_count": len(session.messages),
            "added": len(added),
        }

    def _resolve_message_peer_id(
        self,
        role: str,
        peer_id: Optional[str],
    ) -> Optional[str]:
        normalized_peer_id = normalize_peer_id(peer_id)
        if normalized_peer_id is not None:
            return normalized_peer_id
        legacy_agent_id = getattr(self._ctx, "legacy_agent_id", None)
        if legacy_agent_id is not None and role == "assistant":
            return legacy_agent_id
        return None

    # ============= Pack =============

    async def export_ovpack(
        self,
        uri: str,
        to: str,
        include_vectors: bool = False,
    ) -> str:
        """Export context as .ovpack file."""
        return await self._service.pack.export_ovpack(
            uri,
            to,
            ctx=self._ctx,
            include_vectors=include_vectors,
        )

    async def backup_ovpack(self, to: str, include_vectors: bool = False) -> str:
        """Back up public scopes as a restore-only .ovpack file."""
        return await self._service.pack.backup_ovpack(
            to,
            ctx=self._ctx,
            include_vectors=include_vectors,
        )

    async def import_ovpack(
        self,
        file_path: str,
        parent: str,
        on_conflict: Optional[str] = None,
        vector_mode: Optional[str] = None,
    ) -> str:
        """Import .ovpack file."""
        return await self._service.pack.import_ovpack(
            file_path,
            parent,
            ctx=self._ctx,
            on_conflict=on_conflict,
            vector_mode=vector_mode,
        )

    async def restore_ovpack(
        self,
        file_path: str,
        on_conflict: Optional[str] = None,
        vector_mode: Optional[str] = None,
    ) -> str:
        """Restore backup .ovpack file."""
        return await self._service.pack.restore_ovpack(
            file_path,
            ctx=self._ctx,
            on_conflict=on_conflict,
            vector_mode=vector_mode,
        )

    # ============= Git Version Control =============

    async def git_commit(
        self,
        *,
        message: str,
        paths: Optional[List[str]] = None,
        branch: str = "main",
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a git snapshot. See VikingFS.commit for semantics."""
        return await self._service.fs.commit(
            message=message,
            paths=paths,
            branch=branch,
            author_name=author_name,
            author_email=author_email,
            ctx=self._ctx,
        )

    async def git_restore(
        self,
        *,
        project_dir: Optional[str] = None,
        source_commit: str,
        branch: str = "main",
        dry_run: bool = False,
        message: Optional[str] = None,
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Restore a subtree, or the full account tree when project_dir is omitted."""
        return await self._service.fs.restore(
            project_dir=project_dir,
            source_commit=source_commit,
            branch=branch,
            dry_run=dry_run,
            message=message,
            author_name=author_name,
            author_email=author_email,
            ctx=self._ctx,
        )

    async def git_show(
        self,
        target_ref: str,
        *,
        path: Optional[str] = None,
    ) -> Any:
        """Read a commit's metadata or a single blob."""
        return await self._service.fs.show(target_ref, path=path, ctx=self._ctx)

    async def git_log(
        self,
        *,
        branch: str = "main",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Walk back along parents[0] up to limit commits."""
        return await self._service.fs.log(branch=branch, limit=limit, ctx=self._ctx)

    # ============= Debug =============

    async def check_consistency(self, uri: str) -> Dict[str, Any]:
        """Check filesystem/vector-index consistency for a URI subtree."""
        return await self._service.check_consistency(
            uri=uri,
            ctx=self._ctx,
        )

    async def health(self) -> bool:
        """Check service health."""
        return True  # Local service is always healthy if initialized

    def session(self, session_id: Optional[str] = None, must_exist: bool = False) -> Any:
        """Create a new session or load an existing one.

        Args:
            session_id: Session ID, creates a new session if None.
            must_exist: Whether to raise an error if the session does not exist. Default False.
        Returns:
            Session object if exists, None otherwise.
        """

        if session_id:
            try:
                return run_async(
                    self._service.sessions.get(session_id, self._ctx, auto_create=False)
                )
            except NotFoundError:
                if must_exist:
                    raise NotFoundError(session_id, "session")

        session = self._service.sessions.session(self._ctx, session_id)
        run_async(session.ensure_exists())
        return session

    async def session_exists(self, session_id: str) -> bool:
        """Check whether a session exists in storage.

        Args:
            session_id: Session ID to check

        Returns:
            True if the session exists, False otherwise
        """
        try:
            await self._service.sessions.get(session_id, self._ctx, auto_create=False)
            return True
        except NotFoundError:
            return False

    def get_status(self) -> Any:
        """Get system status.

        Returns:
            SystemStatus containing health status of all components.
        """
        return self._service.debug.observer.system()

    def is_healthy(self) -> bool:
        """Quick health check (synchronous).

        Returns:
            True if all components are healthy, False otherwise.
        """
        return self._service.debug.observer.is_healthy()

    @property
    def observer(self) -> Any:
        """Get observer service for component status."""
        return self._service.debug.observer
