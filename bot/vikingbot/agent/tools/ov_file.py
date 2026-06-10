"""OpenViking file system tools: read, write, list, search resources."""

import asyncio
import itertools
import json
import time
from abc import ABC
from pathlib import Path
from typing import Any, Optional

import httpx
from loguru import logger

from vikingbot.agent.tools.base import Tool, ToolContext
from vikingbot.openviking_mount.ov_server import VikingClient


class OVFileTool(Tool, ABC):
    _memory_commit_counter = itertools.count(1)

    def __init__(self):
        super().__init__()
        self._clients = {}

    @staticmethod
    def _has_request_connection(tool_context: ToolContext) -> bool:
        return bool(getattr(tool_context, "openviking_connection", None))

    async def _get_client(self, tool_context: ToolContext):
        if self._has_request_connection(tool_context):
            return await VikingClient.create(
                tool_context.workspace_id,
                connection=tool_context.openviking_connection,
            )
        cache_key = str(tool_context.workspace_id or "__default__")
        client = self._clients.get(cache_key)
        if client is None:
            client = await VikingClient.create(tool_context.workspace_id)
            self._clients[cache_key] = client
        return client

    async def _release_client(self, tool_context: ToolContext, client: VikingClient | None) -> None:
        if client is not None and self._has_request_connection(tool_context):
            await client.close()

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
                getattr(tool_context, "sender_id", None),
                *(getattr(tool_context, "memory_peer_ids", None) or []),
            ]
        )

    def _is_default_memory_uri(self, client: VikingClient, uri: str | None) -> bool:
        normalized = self._normalize_uri(uri)
        if normalized in {"", "viking://user/memories"}:
            return True
        try:
            return normalized == self._normalize_uri(client._memory_target_uri(None))
        except Exception:
            return False

    def _is_default_root_uri(self, uri: str | None) -> bool:
        return self._normalize_uri(uri) in {"", "viking://", "viking://user"}

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

    def _current_memory_uri(self, client: VikingClient) -> str:
        return client._memory_target_uri(None)

    def _current_skill_uri(self, client: VikingClient) -> str:
        memory_uri = self._current_memory_uri(client).rstrip("/")
        if memory_uri.endswith("/memories"):
            return f"{memory_uri[: -len('/memories')]}/skills/"
        return "viking://user/skills/"

    def _fs_retrieval_uris(
        self,
        client: VikingClient,
        tool_context: ToolContext,
        uri: str | None,
    ) -> list[str]:
        if not self._is_default_memory_uri(client, uri):
            if not self._is_default_root_uri(uri):
                return [uri or ""]

            target_uris = [
                "viking://resources/",
                self._current_memory_uri(client),
                self._current_skill_uri(client),
                *self._peer_memory_uris(client, tool_context),
            ]
            return self._dedupe_strings(target_uris)

        builder = getattr(client, "build_current_memory_target_uris", None)
        if callable(builder):
            uris = builder(peer_ids=self._memory_peer_ids(tool_context))
            if uris:
                return uris
        return [uri or "viking://user/memories/"]


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
        self, tool_context: "ToolContext", uri: str = "viking://", recursive: bool = False, **kwargs: Any
    ) -> str:
        client = None
        try:
            client = await self._get_client(tool_context)
            entries = []
            target_uris = self._fs_retrieval_uris(client, tool_context, uri)
            for target_uri in target_uris:
                try:
                    entries.extend(
                        await client.list_resources(path=target_uri, recursive=recursive)
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
                and client.should_sender_fanout()
                and (memory_owner_user_ids or legacy_memory_user_ids)
            ):
                user_ids = memory_owner_user_ids or legacy_memory_user_ids
                search_requests = [{"target_uri": "viking://resources/", "peer_id": None}]
                for user_id in self._dedupe_strings(list(user_ids or [])):
                    memory_uri = client._memory_target_uri(user_id)
                    skill_uri = (
                        f"{memory_uri.rstrip('/')[: -len('/memories')]}/skills/"
                        if memory_uri.rstrip("/").endswith("/memories")
                        else "viking://user/skills/"
                    )
                    search_requests.extend(
                        [
                            {"target_uri": memory_uri, "peer_id": None},
                            {"target_uri": skill_uri, "peer_id": None},
                        ]
                    )
            else:
                peer_ids = self._memory_peer_ids(tool_context)
                if not target_uri:
                    search_requests = (
                        [{"target_uri": "", "peer_id": peer_ids[0]}]
                        if peer_ids
                        else [{"target_uri": "", "peer_id": None}]
                    )
                    peer_memory_uris = self._peer_memory_uris(
                        client, tool_context, peer_ids=peer_ids[1:]
                    )
                    if peer_memory_uris:
                        search_requests.extend(
                            {"target_uri": peer_uri, "peer_id": None}
                            for peer_uri in peer_memory_uris
                        )
                    else:
                        search_requests.extend(
                            {"target_uri": "viking://user/memories/", "peer_id": peer_id}
                            for peer_id in peer_ids[1:]
                        )
                elif self._is_default_memory_uri(client, target_uri) and peer_ids:
                    memory_uris = self._dedupe_strings(
                        [
                            "viking://user/memories/",
                            *self._peer_memory_uris(client, tool_context, peer_ids=peer_ids),
                        ]
                    )
                    search_requests = [
                        {"target_uri": memory_uri, "peer_id": None}
                        for memory_uri in memory_uris
                    ]
                else:
                    search_requests = [{"target_uri": target_uri, "peer_id": None}]

            for request in search_requests:
                search_kwargs = {
                    "target_uri": request["target_uri"],
                    "limit": 10,
                }
                if request.get("peer_id") is not None:
                    search_kwargs["peer_id"] = request.get("peer_id")
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
            },
            "required": ["path", "description"],
        }

    async def execute(
        self,
        tool_context: "ToolContext",
        path: str,
        description: str,
        **kwargs: Any,
    ) -> str:
        client = None
        try:
            if path and not path.startswith("http"):
                local_path = Path(path).expanduser().resolve()
                if not local_path.exists():
                    return f"Error: File not found: {path}"
                if not local_path.is_file():
                    return f"Error: Not a file: {path}"

            client = await self._get_client(tool_context)
            result = await client.add_resource(path, description)

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
            if not tool_context.sender_id:
                return "Error: peer id is required for OpenViking memory commit."
            source_session_id = tool_context.session_key.safe_name()
            commit_seq = next(self._memory_commit_counter)
            timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            session_id = f"{source_session_id}__memory_commit__{timestamp}__{commit_seq:04d}"
            result = await client.commit(session_id, messages, peer_id=tool_context.sender_id)
            session_id = result.get("session_id", session_id) if isinstance(result, dict) else session_id
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

    @property
    def name(self) -> str:
        return "openviking_multi_read"

    @property
    def description(self) -> str:
        return "Read full content from multiple OpenViking resources concurrently. Returns complete content for all URIs with no truncation."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uris": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": 'List of Viking file URIs to read from (e.g., ["viking://resources/path/123.md", "viking://resources/path/456.md"])',
                },
            },
            "required": ["uris"],
        }

    async def execute(
        self,
        tool_context: ToolContext,
        uris: list[str],
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
                        content = await client.read_content(uri, level=level)
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
            result_lines = [f"Multi-read results for {len(uris)} resources (level: {level}):"]

            for result in results:
                uri = result["uri"]
                content = result["content"]
                success = result["success"]

                result_lines.append(f"\n--- START OF {uri} ---")
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
