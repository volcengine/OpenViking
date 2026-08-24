"""OpenViking file system tools: read, write, list, search resources."""

import asyncio
import itertools
import json
import tempfile
import time
from abc import ABC
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Optional

import httpx
from loguru import logger

from vikingbot.agent.tools.base import Tool, ToolContext
from vikingbot.openviking_mount.ov_server import VikingClient

if TYPE_CHECKING:
    from vikingbot.config.schema import Config


def local_path_for_viking_uri(uri: str) -> str:
    """Map a viking:// URI to a workspace-relative path, dropping the namespace prefix.

    ``viking://resources/a/b`` -> ``a/b`` and
    ``viking://user/<id>/resources/a/b`` -> ``a/b``, so the materialized tree does not
    collide with the viking ``resources``/``skills`` namespace names.
    """
    path = str(uri).removeprefix("viking://").lstrip("/")
    for prefix in ("resources/", "skills/", "memories/", "sessions/"):
        if path.startswith(prefix):
            return path[len(prefix) :]
    parts = path.split("/")
    if (
        len(parts) >= 3
        and parts[0] in {"user", "agent"}
        and parts[2] in {"resources", "skills", "memories", "sessions"}
    ):
        return "/".join(parts[3:])
    return path


class OVFileTool(Tool, ABC):
    _memory_commit_counter = itertools.count(1)

    def __init__(self, config: "Config | None" = None):
        super().__init__()
        self._clients = {}
        self._config = config

    @staticmethod
    def _has_request_connection(tool_context: ToolContext) -> bool:
        return bool(getattr(tool_context, "openviking_connection", None))

    @staticmethod
    def _actor_peer_id(tool_context: ToolContext) -> str | None:
        return getattr(tool_context, "actor_peer_id", None) or getattr(
            tool_context, "sender_id", None
        )

    async def _get_client(self, tool_context: ToolContext):
        actor_peer_id = self._actor_peer_id(tool_context)
        if self._has_request_connection(tool_context):
            return await VikingClient.create(
                tool_context.workspace_id,
                connection=tool_context.openviking_connection,
                actor_peer_id=actor_peer_id,
                config=self._config,
            )
        if actor_peer_id:
            return await VikingClient.create(
                tool_context.workspace_id,
                actor_peer_id=actor_peer_id,
                config=self._config,
            )
        cache_key = str(tool_context.workspace_id or "__default__")
        client = self._clients.get(cache_key)
        if client is None:
            client = await VikingClient.create(tool_context.workspace_id, config=self._config)
            self._clients[cache_key] = client
        return client

    async def _release_client(self, tool_context: ToolContext, client: VikingClient | None) -> None:
        if client is not None and (
            self._has_request_connection(tool_context) or self._actor_peer_id(tool_context)
        ):
            close = getattr(client, "close", None)
            if callable(close):
                await close()

    @staticmethod
    def _normalize_uri(uri: str | None) -> str:
        normalized = (uri or "").strip()
        if normalized == "viking://":
            return normalized
        return normalized.rstrip("/")

    @staticmethod
    def _dedupe_strings(values: list[str | None]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not value:
                continue
            value = str(value).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            deduped.append(value)
        return deduped

    def _memory_peer_ids(self, tool_context: ToolContext) -> list[str]:
        return self._dedupe_strings(
            [
                self._actor_peer_id(tool_context),
                *(getattr(tool_context, "memory_peer_ids", None) or []),
            ]
        )

    def _is_default_memory_uri(self, uri: str | None) -> bool:
        normalized = self._normalize_uri(uri)
        # ``viking://user/memories`` is the legacy spelling of the current-user default
        # target; it is still accepted here because it survives in stored bot configs and
        # LLM output. New emissions always use the ``viking://~`` home alias.
        return normalized in {"", "viking://user/memories", "viking://~/memories"}

    def _is_default_root_uri(self, uri: str | None) -> bool:
        return self._normalize_uri(uri) in {"", "viking://", "viking://user", "viking://~"}

    def _peer_memory_uris(
        self,
        client: VikingClient,
        tool_context: ToolContext,
        peer_ids: list[str] | None = None,
    ) -> list[str]:
        builder = getattr(client, "build_current_memory_target_uris", None)
        if not callable(builder):
            return []
        return builder(
            peer_ids=peer_ids if peer_ids is not None else self._memory_peer_ids(tool_context),
            include_self=False,
        )

    def _fs_retrieval_uris(
        self,
        client: VikingClient,
        tool_context: ToolContext,
        uri: str | None,
    ) -> list[str]:
        if getattr(client, "actor_peer_id", None):
            if self._is_default_root_uri(uri):
                return [uri or "viking://"]
            if self._is_default_memory_uri(uri):
                return [uri or "viking://~/memories/"]
            return [uri or ""]

        if not self._is_default_memory_uri(uri):
            if not self._is_default_root_uri(uri):
                return [uri or ""]

            target_uris = [
                "viking://resources/",
                "viking://~/resources/",
                "viking://~/memories/",
                "viking://~/skills/",
                *self._peer_memory_uris(client, tool_context),
            ]
            return self._dedupe_strings(target_uris)

        builder = getattr(client, "build_current_memory_target_uris", None)
        if callable(builder):
            uris = builder(peer_ids=self._memory_peer_ids(tool_context))
            if uris:
                return uris
        return [uri or "viking://~/memories/"]


class VikingListTool(OVFileTool):
    """Tool to list Viking resources."""

    @property
    def name(self) -> str:
        return "openviking_list"

    @property
    def description(self) -> str:
        return "List resources in a OpenViking folder path."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "Optional parent Viking URI to list. Defaults to all visible OpenViking roots plus current peer memory.",
                    "default": "viking://",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Whether to list recursively",
                    "default": False,
                },
            },
            "required": [],
        }

    async def execute(
        self,
        tool_context: "ToolContext",
        uri: str = "viking://",
        recursive: bool = False,
        node_limit: int = 1000,
        **kwargs: Any,
    ) -> str:
        client = None
        try:
            client = await self._get_client(tool_context)
            entries = []
            target_uris = self._fs_retrieval_uris(client, tool_context, uri)
            for target_uri in target_uris:
                try:
                    entries.extend(
                        await client.list_resources(
                            path=target_uri,
                            recursive=recursive,
                            node_limit=node_limit,
                        )
                    )
                except Exception as exc:
                    if len(target_uris) == 1:
                        raise
                    logger.debug(f"Skip OpenViking list target {target_uri}: {exc}")
                    continue

            if not entries:
                return f"No resources found at {uri}"

            result = []
            for entry in entries:
                item = {
                    "name": entry["name"],
                    "size": entry["size"],
                    "uri": entry["uri"],
                    "isDir": entry["isDir"],
                }
                result.append(str(item))
            return "\n".join(result)
        except Exception as e:
            logger.exception(f"Error processing message: {e}")
            return f"Error listing Viking resources: {str(e)}"
        finally:
            await self._release_client(tool_context, client)


class VikingSearchTool(OVFileTool):
    """Tool to search Viking resources."""

    @property
    def name(self) -> str:
        return "openviking_search"

    @property
    def description(self) -> str:
        return (
            "Using query to search for resources (knowledge, code, files, workflow, etc.) in OpenViking. "
            "Result: Only URIs and summaries are included here. To view the full content, use openviking_multi_read tool. "
            "This operation performs semantic retrieval, not full character matching. "
            "Avoid duplicate calls with the same intent in the same turn, but do search again for a new user question or a follow-up that asks for a different remembered fact. "
            "For questions about the user's memory, profile, preferences, or personal facts, use this tool before concluding no relevant record exists."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "target_uri": {
                    "type": "string",
                    "description": "Optional target URI to limit search scope, if is None, then search the entire range.(e.g., viking://resources/)",
                },
                "min_score": {
                    "type": "number",
                    "description": "Minimum relevance score threshold",
                    "default": 0.35,
                },
            },
            "required": ["query"],
        }

    @staticmethod
    def _extract_search_items(results: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        group_map = {
            "memories": "memory",
            "resources": "resource",
            "skills": "skill",
        }

        if isinstance(results, dict):
            for key, item_type in group_map.items():
                group = results.get(key, [])
                if not isinstance(group, list):
                    continue
                for item in group:
                    if isinstance(item, dict):
                        items.append({**item, "type": item.get("type", item_type)})
            return items

        if (
            hasattr(results, "memories")
            or hasattr(results, "resources")
            or hasattr(results, "skills")
        ):
            for key, item_type in group_map.items():
                for item in getattr(results, key, []) or []:
                    items.append(
                        {
                            "type": item_type,
                            "uri": getattr(item, "uri", ""),
                            "abstract": getattr(item, "abstract", ""),
                            "is_leaf": getattr(item, "is_leaf", False),
                            "score": getattr(item, "score", 0.0),
                        }
                    )
            return items

        if isinstance(results, list):
            for item in results:
                if isinstance(item, dict):
                    raw_type = str(item.get("type") or item.get("context_type") or "").lower()
                    item_type = "resource"
                    if "memory" in raw_type:
                        item_type = "memory"
                    elif "skill" in raw_type:
                        item_type = "skill"
                    items.append({**item, "type": item_type})
                else:
                    raw_type = str(getattr(item, "context_type", "")).lower()
                    item_type = "resource"
                    if "memory" in raw_type:
                        item_type = "memory"
                    elif "skill" in raw_type:
                        item_type = "skill"
                    items.append(
                        {
                            "type": item_type,
                            "uri": getattr(item, "uri", ""),
                            "abstract": getattr(item, "abstract", ""),
                            "is_leaf": getattr(item, "is_leaf", False),
                            "score": getattr(item, "score", 0.0),
                        }
                    )

        return items

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _filter_search_items(
        self, results: Any, min_score: float
    ) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {
            "memory": [],
            "resource": [],
            "skill": [],
        }
        for item in self._extract_search_items(results):
            score = self._to_float(item.get("score", 0.0))
            if score < min_score:
                continue
            item_type = str(item.get("type", "resource")).lower()
            if item_type not in grouped:
                item_type = "resource"
            grouped[item_type].append(
                {
                    "uri": str(item.get("uri", "") or ""),
                    "abstract": str(item.get("abstract", "") or ""),
                    "is_leaf": bool(item.get("is_leaf", False)),
                    "score": score,
                }
            )
        return grouped

    @staticmethod
    def _build_group_json(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        group_items: list[dict[str, Any]] = []
        for index, item in enumerate(items, 1):
            group_items.append(
                {
                    "index": index,
                    "uri": item["uri"],
                    "abstract": item["abstract"],
                    "is_leaf": item["is_leaf"],
                    "score": round(item["score"], 6),
                }
            )
        return group_items

    def _format_search_items_json(
        self, grouped_items: dict[str, list[dict[str, Any]]], min_score: float
    ) -> str:
        memories = self._build_group_json(grouped_items.get("memory", []))
        resources = self._build_group_json(grouped_items.get("resource", []))
        skills = self._build_group_json(grouped_items.get("skill", []))
        payload = {
            "count": len(memories) + len(resources) + len(skills),
            "memories": memories,
            "resources": resources,
            "skills": skills,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    async def execute(
        self,
        tool_context: "ToolContext",
        query: str,
        target_uri: Optional[str] = "",
        min_score: float = 0.35,
        **kwargs: Any,
    ) -> str:
        client = None
        try:
            client = await self._get_client(tool_context)
            memory_owner_user_ids = getattr(tool_context, "memory_owner_user_ids", None)
            legacy_memory_user_ids = getattr(tool_context, "memory_user_ids", None)

            grouped_items = {
                "memory": [],
                "resource": [],
                "skill": [],
            }

            if (
                not target_uri
                and not getattr(client, "actor_peer_id", None)
                and client.should_sender_fanout()
                and (memory_owner_user_ids or legacy_memory_user_ids)
            ):
                user_ids = memory_owner_user_ids or legacy_memory_user_ids
                search_targets: list[tuple[str, str | None]] = [("viking://resources/", None)]
                for user_id in self._dedupe_strings(list(user_ids or [])):
                    search_targets.extend(
                        [
                            ("viking://~/resources/", user_id),
                            ("viking://~/memories/", user_id),
                            ("viking://~/skills/", user_id),
                        ]
                    )
            else:
                peer_ids = self._memory_peer_ids(tool_context)
                if not target_uri:
                    actor_peer_id = getattr(client, "actor_peer_id", None)
                    if actor_peer_id and not peer_ids:
                        peer_ids = [actor_peer_id]
                    if actor_peer_id or peer_ids:
                        target_uris = self._dedupe_strings(
                            [
                                "viking://resources/",
                                "viking://~/resources/",
                                "viking://~/memories/",
                                "viking://~/skills/",
                                *self._peer_memory_uris(
                                    client,
                                    tool_context,
                                    peer_ids=peer_ids,
                                ),
                            ]
                        )
                    else:
                        target_uris = [""]
                elif (
                    self._is_default_memory_uri(target_uri)
                    and not getattr(client, "actor_peer_id", None)
                    and peer_ids
                ):
                    target_uris = self._dedupe_strings(
                        [
                            "viking://~/memories/",
                            *self._peer_memory_uris(client, tool_context, peer_ids=peer_ids),
                        ]
                    )
                else:
                    target_uris = [target_uri]

                search_targets = [(search_target_uri, None) for search_target_uri in target_uris]

            for search_target_uri, search_user_id in search_targets:
                search_kwargs = {
                    "target_uri": search_target_uri,
                    "limit": 10,
                }
                if search_user_id:
                    search_kwargs["user_id"] = search_user_id
                results = await client.search(query, **search_kwargs)
                filtered_items = self._filter_search_items(results, min_score=min_score)
                for item_type, items in filtered_items.items():
                    grouped_items[item_type].extend(items)

            total = sum(len(items) for items in grouped_items.values())
            if total == 0:
                return f"No results found for query: {query}"

            return self._format_search_items_json(grouped_items, min_score=min_score)
        except Exception as e:
            return f"Error searching Viking: {str(e)}"
        finally:
            await self._release_client(tool_context, client)


class VikingAddResourceTool(OVFileTool):
    """Tool to add a resource to Viking."""

    @property
    def name(self) -> str:
        return "openviking_add_resource"

    @property
    def description(self) -> str:
        return "Add a resource (url like pic, git code or local file path) to OpenViking.This is a asynchronous operation."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Url or local file path"},
                "description": {"type": "string", "description": "Description of the resource"},
                "to": {
                    "type": "string",
                    "description": "Optional exact target URI under viking://resources/. When omitted, OpenViking chooses the resource URI.",
                },
            },
            "required": ["path", "description"],
        }

    @property
    def resource_inputs(self) -> dict[str, str]:
        return {"path": "local_file"}

    async def execute(
        self,
        tool_context: "ToolContext",
        path: str,
        description: str,
        to: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        client = None
        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        try:
            upload_path = path
            if path and not path.startswith("http"):
                if tool_context.sandbox_manager is not None:
                    sandbox = await tool_context.sandbox_manager.get_sandbox(
                        tool_context.session_key
                    )
                    local_path = sandbox.local_file_path(path)
                    if local_path is not None:
                        upload_path = str(local_path)
                    else:
                        temp_dir = tempfile.TemporaryDirectory(prefix="vikingbot-add-resource-")
                        local_path = Path(temp_dir.name) / (Path(path).name or "resource")
                        await sandbox.export_file(path, local_path)
                        upload_path = str(local_path)
                else:
                    local_path = Path(path).expanduser().resolve()
                    if not local_path.exists():
                        return f"Error: File not found: {path}"
                    if not local_path.is_file():
                        return f"Error: Not a file: {path}"
                    upload_path = str(local_path)

            client = await self._get_client(tool_context)
            result = await client.add_resource(upload_path, description, to=to)

            if result:
                root_uri = result.get("root_uri", "")
                return f"Successfully added resource: {root_uri}"
            else:
                return "Failed to add resource"
        except httpx.ReadTimeout:
            return "Request timed out. The resource addition task may still be processing on the server side."
        except Exception as e:
            logger.warning(f"Error adding resource: {e}")
            return f"Error adding resource to Viking: {str(e)}"
        finally:
            await self._release_client(tool_context, client)
            if temp_dir is not None:
                temp_dir.cleanup()


class VikingGrepTool(OVFileTool):
    """Tool to search Viking resources using a regex pattern."""

    @property
    def name(self) -> str:
        return "openviking_grep"

    @property
    def description(self) -> str:
        return (
            "Search Viking resources using a regex pattern (like grep)."
            "Result: Only URIs and summaries are included here. To view the full content, use openviking_multi_read tool."
            "Avoid duplicate calls with the same intent in the same turn."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "Optional Viking URI to search within. Defaults to all visible OpenViking roots plus current peer memory.",
                    "default": "viking://",
                },
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for",
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case-insensitive search",
                    "default": False,
                },
            },
            "required": ["pattern"],
        }

    async def execute(
        self,
        tool_context: "ToolContext",
        pattern: str,
        uri: str = "viking://",
        case_insensitive: bool = False,
        **kwargs: Any,
    ) -> str:
        client = None
        try:
            client = await self._get_client(tool_context)
            matches = []
            target_uris = self._fs_retrieval_uris(client, tool_context, uri)
            for target_uri in target_uris:
                try:
                    result = await client.grep(
                        target_uri,
                        pattern,
                        case_insensitive=case_insensitive,
                    )
                except Exception as exc:
                    if len(target_uris) == 1:
                        raise
                    logger.debug(f"Skip OpenViking grep target {target_uri}: {exc}")
                    continue
                if isinstance(result, dict):
                    matches.extend(result.get("matches", []))
                else:
                    matches.extend(getattr(result, "matches", []))

            if not matches:
                return f"No matches found for pattern: '{pattern}'"

            merged_results: dict[str, list[tuple[int, str]]] = {}

            for match in matches:
                if isinstance(match, dict):
                    match_uri = match.get("uri", "unknown")
                    line = match.get("line", "?")
                    content = match.get("content", "")
                else:
                    match_uri = getattr(match, "uri", "unknown")
                    line = getattr(match, "line", "?")
                    content = getattr(match, "content", "")

                if match_uri not in merged_results:
                    merged_results[match_uri] = []
                merged_results[match_uri].append((line, content))

            result_lines = [
                f"Found {len(matches)} match{'es' if len(matches) != 1 else ''} for pattern '{pattern}':"
            ]

            for match_uri, uri_matches in merged_results.items():
                uri_matches.sort(key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0)
                result_lines.append(f"\n📄 {match_uri}")
                for line, content in uri_matches:
                    result_lines.append(f"   Line {line}:")
                    result_lines.append(f"   {content}")

            return "\n".join(result_lines)
        except Exception as e:
            return f"Error searching Viking with grep: {str(e)}"
        finally:
            await self._release_client(tool_context, client)


class VikingGlobTool(OVFileTool):
    """Tool to find Viking resources using glob patterns."""

    @property
    def name(self) -> str:
        return "openviking_glob"

    @property
    def description(self) -> str:
        return (
            "Find Viking resources using glob patterns (like **/*.md, *.py)."
            "Result: Only URIs and summaries are included here. To view the full content, use openviking_multi_read tool."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match (e.g., **/*.md, *.py, src/**/*.js)",
                },
                "uri": {
                    "type": "string",
                    "description": "The whole Viking URI to search within (e.g., viking://resources/path/)",
                    "default": "",
                },
            },
            "required": ["pattern"],
        }

    async def execute(
        self, tool_context: "ToolContext", pattern: str, uri: str = "", **kwargs: Any
    ) -> str:
        client = None
        try:
            client = await self._get_client(tool_context)
            matches = []
            count = 0
            target_uris = self._fs_retrieval_uris(client, tool_context, uri or "viking://")
            for target_uri in target_uris:
                try:
                    result = await client.glob(pattern, uri=target_uri or "viking://")
                except Exception as exc:
                    if len(target_uris) == 1:
                        raise
                    logger.debug(f"Skip OpenViking glob target {target_uri}: {exc}")
                    continue

                if isinstance(result, dict):
                    batch_matches = result.get("matches", [])
                    batch_count = result.get("count", len(batch_matches))
                else:
                    batch_matches = getattr(result, "matches", [])
                    batch_count = getattr(result, "count", len(batch_matches))
                matches.extend(batch_matches)
                count += int(batch_count or 0)

            if not matches:
                return f"No files found for pattern: {pattern}"

            result_lines = [f"Found {count} file{'s' if count != 1 else ''}:"]
            for match_uri in matches:
                if isinstance(match_uri, dict):
                    match_uri = match_uri.get("uri", str(match_uri))
                result_lines.append(f"📄 {match_uri}")

            return "\n".join(result_lines)
        except Exception as e:
            return f"Error searching Viking with glob: {str(e)}"
        finally:
            await self._release_client(tool_context, client)


class VikingMemoryCommitTool(OVFileTool):
    """Tool to commit messages to OpenViking session."""

    async def _get_commit_task_result(
        self,
        client: VikingClient,
        task_id: str | None,
        attempts: int = 20,
        interval: float = 0.5,
    ) -> dict[str, Any] | None:
        if not task_id:
            return None
        get_task = getattr(client.client, "get_task", None)
        if not callable(get_task):
            return None

        task = None
        for _ in range(attempts):
            task = await get_task(task_id)
            if isinstance(task, dict) and task.get("status") in {"completed", "failed"}:
                return task
            await asyncio.sleep(interval)
        return task if isinstance(task, dict) else None

    @staticmethod
    def _extract_memory_diff_uris(diff: Any) -> dict[str, list[str]]:
        operations = diff.get("operations", {}) if isinstance(diff, dict) else {}
        return {
            "added_uris": [
                item["uri"]
                for item in operations.get("adds", [])
                if isinstance(item, dict) and item.get("uri")
            ],
            "updated_uris": [
                item["uri"]
                for item in operations.get("updates", [])
                if isinstance(item, dict) and item.get("uri")
            ],
            "deleted_uris": [
                item["uri"]
                for item in operations.get("deletes", [])
                if isinstance(item, dict) and item.get("uri")
            ],
        }

    @staticmethod
    def _format_commit_error(error: Exception) -> str:
        message = str(error)
        if "<title>403 Forbidden</title>" in message or "<h1>403 Forbidden</h1>" in message:
            return "HTTP 403 Forbidden"
        return message

    @property
    def name(self) -> str:
        return "openviking_memory_commit"

    @property
    def description(self) -> str:
        return "When user has personal information needs to be remembered, Commit messages to OpenViking."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "messages": {
                    "type": "array",
                    "description": "List of messages to commit, each with role, content",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string", "enum": ["user", "assistant"]},
                            "content": {"type": "string"},
                        },
                        "required": ["role", "content"],
                    },
                },
            },
            "required": ["messages"],
        }

    async def execute(
        self,
        tool_context: ToolContext,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> str:
        client = None
        try:
            client = await self._get_client(tool_context)
            actor_peer_id = self._actor_peer_id(tool_context)
            if not actor_peer_id:
                return "Error: peer id is required for OpenViking memory commit."
            source_session_id = tool_context.session_key.safe_name()
            commit_seq = next(self._memory_commit_counter)
            timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            session_id = f"{source_session_id}__memory_commit__{timestamp}__{commit_seq:04d}"
            result = await client.commit(session_id, messages, peer_id=actor_peer_id)
            session_id = (
                result.get("session_id", session_id) if isinstance(result, dict) else session_id
            )
            commit_result = result.get("commit", {}) if isinstance(result, dict) else {}
            archive_uri = commit_result.get("archive_uri")
            memory_diff_uri = f"{archive_uri}/memory_diff.json" if archive_uri else None
            task_id = commit_result.get("task_id")
            task = await self._get_commit_task_result(client, task_id)
            changed_uris = {"added_uris": [], "updated_uris": [], "deleted_uris": []}

            if task and task.get("status") == "completed" and memory_diff_uri:
                raw_diff = await client.read_content(memory_diff_uri, level="read")
                if raw_diff:
                    try:
                        changed_uris = self._extract_memory_diff_uris(json.loads(raw_diff))
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse memory diff from {memory_diff_uri}")

            return json.dumps(
                {
                    "status": "success",
                    "session_id": session_id,
                    "memory_commit_session_id": session_id,
                    "source_session_id": source_session_id,
                    "message_count": len(messages),
                    "archived": commit_result.get("archived"),
                    **changed_uris,
                    "archive_uri": archive_uri,
                    "memory_diff_uri": memory_diff_uri,
                    "task_id": task_id,
                    "task_status": task.get("status") if isinstance(task, dict) else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        except Exception as e:
            logger.exception(f"Error processing message: {e}")
            return f"Error: committing to Viking failed: {self._format_commit_error(e)}"
        finally:
            await self._release_client(tool_context, client)


class VikingMultiReadTool(OVFileTool):
    """Tool to read content from multiple Viking resources concurrently."""

    _FULL_READ_WARN_BYTES = 512 * 1024

    @property
    def name(self) -> str:
        return "openviking_multi_read"

    @property
    def description(self) -> str:
        return (
            "Read content from multiple OpenViking resources concurrently. By default returns "
            "complete content. Large files (over 512 KB) are not returned in full: first use "
            "openviking_grep to locate relevant lines, then read a bounded window with offset "
            "and limit (line numbers, 0-indexed)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uris": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": 'List of Viking file URIs to read from (e.g., ["viking://resources/path/123.md"])',
                },
                "offset": {
                    "type": "integer",
                    "description": "Starting line number (0-indexed) for bounded reads; default 0.",
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of lines to read; -1 reads to the end (default). Use a small value to page through large files.",
                    "default": -1,
                },
            },
            "required": ["uris"],
        }

    async def execute(
        self,
        tool_context: ToolContext,
        uris: list[str],
        offset: int = 0,
        limit: int = -1,
        **kwargs: Any,
    ) -> str:
        level = "read"  # 默认获取完整内容
        client = None
        try:
            if not uris:
                return "Error: No URIs provided."

            client = await self._get_client(tool_context)
            max_concurrent = 10
            semaphore = asyncio.Semaphore(max_concurrent)

            async def read_single_uri(uri: str) -> dict:
                async with semaphore:
                    try:
                        if limit == -1:
                            try:
                                stat = await client.stat(uri)
                                size = stat.get("size")
                            except Exception:
                                size = None
                            if isinstance(size, int) and size > self._FULL_READ_WARN_BYTES:
                                return {
                                    "uri": uri,
                                    "content": (
                                        f"File is {size} bytes; reading it fully would exceed "
                                        "the tool result budget. Use openviking_grep to locate "
                                        "relevant lines, then read a window with "
                                        "openviking_multi_read offset/limit."
                                    ),
                                    "success": False,
                                }
                        content = await client.read_content(
                            uri, level=level, offset=offset, limit=limit
                        )
                        skill_runtime = getattr(tool_context, "skill_runtime", None)
                        if skill_runtime is not None:
                            active_skill = await skill_runtime.activate_from_read(uri, content)
                            if active_skill is not None:
                                content = skill_runtime.render_skill_content(active_skill)
                        return {
                            "uri": uri,
                            "content": content,
                            "success": True,
                        }
                    except Exception as e:
                        logger.warning(f"Error reading from {uri}: {e}")
                        return {
                            "uri": uri,
                            "content": f"Error reading from Viking: {str(e)}",
                            "success": False,
                        }

            # 并发读取所有URI
            read_tasks = [read_single_uri(uri) for uri in uris]
            results = await asyncio.gather(*read_tasks)

            # 构建结果
            range_note = (
                ""
                if limit == -1
                else f" (lines {offset}..{offset + limit - 1 if limit > 0 else 'end'})"
            )
            result_lines = [f"Multi-read results for {len(uris)} resources (level: {level}):"]

            for result in results:
                uri = result["uri"]
                content = result["content"]
                success = result["success"]

                result_lines.append(f"\n--- START OF {uri}{range_note} ---")
                if success:
                    result_lines.append(content)
                else:
                    result_lines.append(f"ERROR: {content}")
                result_lines.append(f"--- END OF {uri} ---")

            return "\n".join(result_lines)

        except Exception as e:
            logger.exception(f"Error in VikingMultiReadTool: {e}")
            return f"Error multi-reading Viking resources: {str(e)}"
        finally:
            await self._release_client(tool_context, client)


class VikingExportTool(OVFileTool):
    """Materialize viking:// files into the task sandbox so shell tools can process them.

    The OpenViking ``exec``/``write_file`` tools operate on the task sandbox filesystem,
    which does not contain viking:// source files by default. This tool downloads a
    viking:// file or directory into the sandbox workspace (under ``compile_resources/``
    by default), so the agent can then run arbitrary shell commands on them with ``exec``
    (``jq``, ``wc``, ``grep``, ``head``, ``python``, ...) or inspect them with
    ``read_file``. It is intentionally data-format agnostic: the agent decides which
    files to pull and how to process them.
    """

    _MAX_FILES = 1000
    _MAX_TOTAL_BYTES = 1024 * 1024 * 1024  # 1 GiB
    _DEFAULT_DEST = "compile_resources"

    @property
    def name(self) -> str:
        return "openviking_export"

    @property
    def description(self) -> str:
        return (
            "Download a viking:// file or directory into the task sandbox workspace "
            "(under `compile_resources/` by default) so you can process it with "
            "exec/read_file — for example `jq`, `wc`, `grep`, `head`, `python`. Use this "
            "instead of openviking_multi_read for large or structured files that you want "
            "to filter or aggregate with shell tools."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "Viking URI of a file or directory to materialize into the workspace.",
                },
                "dest": {
                    "type": "string",
                    "description": "Base directory under the workspace to write into (default 'compile_resources').",
                    "default": self._DEFAULT_DEST,
                },
            },
            "required": ["uri"],
        }

    async def _collect_uris(self, client: VikingClient, uri: str) -> tuple[list[str], bool]:
        """Return (file URIs to export, whether the listing hit the file-count cap).

        The second element lets the caller warn that files beyond the cap were
        not exported, instead of dropping them silently.
        """
        try:
            stat = await client.stat(uri)
        except Exception:
            return [], False
        if not stat.get("isDir"):
            return [uri], False
        entries = await client.list_resources(path=uri, recursive=True, node_limit=self._MAX_FILES)
        uris = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            entry_uri = str(entry.get("uri") or "")
            if not entry_uri:
                continue
            uris.append(entry_uri)
            if len(uris) >= self._MAX_FILES:
                break
        truncated = len(entries) >= self._MAX_FILES
        return uris, truncated

    @staticmethod
    def _local_path(uri: str) -> str:
        return local_path_for_viking_uri(uri)

    async def execute(
        self,
        tool_context: ToolContext,
        uri: str,
        dest: str = "compile_resources",
        **kwargs: Any,
    ) -> str:
        del kwargs
        client = None
        try:
            if tool_context.sandbox_manager is None:
                return "Error: openviking_export requires a task sandbox to write into."
            client = await self._get_client(tool_context)
            file_uris, listing_truncated = await self._collect_uris(client, uri)
            if not file_uris:
                return f"No files found under {uri}"

            sandbox = await tool_context.sandbox_manager.get_sandbox(tool_context.session_key)
            dest = dest.strip("/") or self._DEFAULT_DEST
            exported: list[str] = []
            skipped_binary = 0
            total_bytes = 0
            byte_limit_hit = False
            for file_uri in file_uris:
                try:
                    stat = await client.stat(file_uri)
                    size = stat.get("size")
                    if isinstance(size, int) and total_bytes + size > self._MAX_TOTAL_BYTES:
                        byte_limit_hit = True
                        break
                    payload = await client.download_bytes(file_uri)
                except Exception as exc:
                    logger.warning(f"openviking_export failed to download {file_uri}: {exc}")
                    continue
                total_bytes += len(payload)
                relative = f"{dest}/{self._local_path(file_uri)}"
                try:
                    text = payload.decode("utf-8")
                except UnicodeDecodeError:
                    skipped_binary += 1
                    continue
                await sandbox.write_file(relative, text)
                exported.append(f"{relative}  ({len(payload)} bytes)")

            if not exported:
                if byte_limit_hit:
                    return (
                        f"No text files exported from {uri}: the first file alone would "
                        f"exceed the total-byte budget ({self._MAX_TOTAL_BYTES} bytes)."
                    )
                return f"No text files exported from {uri}."
            lines = [
                f"Exported {len(exported)} file(s) into the workspace under '{dest}/':"
            ]
            lines.extend(f"- {line}" for line in exported)
            if skipped_binary:
                lines.append(f"Skipped {skipped_binary} non-UTF-8 (binary) file(s).")
            if byte_limit_hit:
                lines.append(
                    "NOTE: stopped before the total-byte budget "
                    f"({self._MAX_TOTAL_BYTES} bytes) was exceeded; remaining files were "
                    "not exported. Narrow the uri or ask to raise the limit if you need them."
                )
            elif listing_truncated:
                lines.append(
                    f"NOTE: the source listing was capped at {self._MAX_FILES} entries; "
                    "files beyond that were not exported."
                )
            lines.append(
                "You can now process these with exec (jq/wc/grep/head/python) or read_file."
            )
            return "\n".join(lines)

        except Exception as e:
            logger.exception("Error in VikingExportTool: %s", e)
            return f"Error exporting Viking resources: {str(e)}"
        finally:
            await self._release_client(tool_context, client)
