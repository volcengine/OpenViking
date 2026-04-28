"""OpenViking file system tools: read, write, list, search resources."""

import asyncio
import json
from abc import ABC
from pathlib import Path
from typing import Any, Optional, Union

import httpx
from loguru import logger

from vikingbot.agent.tools.base import Tool, ToolContext
from vikingbot.openviking_mount.ov_server import VikingClient


class OVFileTool(Tool, ABC):
    def __init__(self):
        super().__init__()
        self._client = None

    async def _get_client(self, tool_context: ToolContext):
        if self._client is None:
            self._client = await VikingClient.create(tool_context.workspace_id)
        return self._client

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
                    "description": "The parent Viking uri to list (e.g., viking://resources/)",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Whether to list recursively",
                    "default": False,
                },
            },
            "required": ["uri"],
        }

    async def execute(
        self, tool_context: "ToolContext", uri: str, recursive: bool = False, **kwargs: Any
    ) -> str:
        try:
            client = await self._get_client(tool_context)
            entries = await client.list_resources(path=uri, recursive=recursive)

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


class VikingSearchTool(OVFileTool):
    """Tool to search Viking resources."""

    @property
    def name(self) -> str:
        return "openviking_search"

    @property
    def description(self) -> str:
        return ("Using query to search for resources (knowledge, code, files, workflow, etc.) in OpenViking. "
                "This operation performs semantic retrieval, not full character matching. Please avoid repeated calls with similar queries as much as possible."
                "bad-case: after searching with ‘Nate Joanna dog playdate 3:00 pm', another search was performed using 'Nate Joanna dog playdate'.")

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

        if hasattr(results, "memories") or hasattr(results, "resources") or hasattr(results, "skills"):
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

    def _filter_search_items(self, results: Any, min_score: float) -> dict[str, list[dict[str, Any]]]:
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

    def _format_search_items_json(self, grouped_items: dict[str, list[dict[str, Any]]], min_score: float) -> str:
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
        try:
            client = await self._get_client(tool_context)
            search_client = getattr(client, "admin_user_client", client)

            # If no target_uri specified, use memory_user_ids to search specific user memories
            if not target_uri and tool_context.memory_user_ids:
                all_results = []
                for user_id in tool_context.memory_user_ids:
                    user_uri = f"viking://user/{user_id}/memories/"
                    logger.info(f"openviking_search: searching {user_uri} for query: {query}")
                    results = await search_client.search(query, target_uri=user_uri, limit=20)
                    if results:
                        # Extract memories from results
                        if isinstance(results, dict):
                            memories = results.get("memories", [])
                        elif isinstance(results, list):
                            memories = results
                        else:
                            memories = []
                        all_results.extend(memories)

                if not all_results:
                    return f"No results found for query: {query}"

                # Reuse unified formatting pipeline
                grouped_items = self._filter_search_items(all_results, min_score=min_score)
                total = sum(len(items) for items in grouped_items.values())
                if total == 0:
                    return f"No results found for query: {query}"
                return self._format_search_items_json(grouped_items, min_score=min_score)
            else:
                results = await search_client.search(query, target_uri=target_uri, limit=20)

            if not results:
                return f"No results found for query: {query}"

            grouped_items = self._filter_search_items(results, min_score=min_score)
            total = sum(len(items) for items in grouped_items.values())
            if total == 0:
                return f"No results found for query: {query}"

            content = self._format_search_items_json(grouped_items, min_score=min_score)
            return content
        except Exception as e:
            return f"Error searching Viking: {str(e)}"


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

            client = await VikingClient.create(tool_context.workspace_id)
            result = await client.add_resource(path, description)

            if result:
                root_uri = result.get("root_uri", "")
                return f"Successfully added resource: {root_uri}"
            else:
                return "Failed to add resource"
        except httpx.ReadTimeout:
            return f"Request timed out. The resource addition task may still be processing on the server side."
        except Exception as e:
            logger.warning(f"Error adding resource: {e}")
            return f"Error adding resource to Viking: {str(e)}"
        finally:
            if client:
                await client.close()


class VikingGrepTool(OVFileTool):
    """Tool to search Viking resources using regex patterns."""

    @property
    def name(self) -> str:
        return "openviking_grep"

    @property
    def description(self) -> str:
        return ("Search Viking resources using regex patterns (like grep). Supports multiple patterns to search concurrently."
                "Please avoid repeated calls with similar queries as much as possible.")

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "The whole Viking URI to search within (e.g., viking://resources/)",
                },
                "pattern": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Regex pattern or array of regex patterns to search for",
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case-insensitive search",
                    "default": False,
                },
            },
            "required": ["uri", "pattern"],
        }

    async def execute(
        self,
        tool_context: "ToolContext",
        uri: str,
        pattern: Union[str, list[str]],
        case_insensitive: bool = False,
        **kwargs: Any,
    ) -> str:
        try:
            client = await self._get_client(tool_context)
            patterns = [pattern] if isinstance(pattern, str) else pattern

            # Limit concurrent requests to avoid overwhelming the server and memory
            max_concurrent = 10
            semaphore = asyncio.Semaphore(max_concurrent)

            async def run_grep(p: str) -> tuple[str, list[Any]]:
                async with semaphore:
                    try:
                        result = await client.grep(uri, p, case_insensitive=case_insensitive)
                        if isinstance(result, dict):
                            matches = result.get("matches", [])
                        else:
                            matches = getattr(result, "matches", [])
                        return (p, matches)
                    except Exception as e:
                        logger.warning(f"Error searching for pattern '{p}': {e}")
                        return (p, [])

            tasks = [run_grep(p) for p in patterns]
            results = await asyncio.gather(*tasks)

            # Merge results by URI
            merged_results: dict[str, list[tuple[int, str, str]]] = {}
            total_matches = 0

            for p, matches in results:
                if not matches:
                    continue
                total_matches += len(matches)
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
                    merged_results[match_uri].append((line, content, p))

            if not merged_results:
                pattern_str = ", ".join(f"'{p}'" for p in patterns)
                return f"No matches found for patterns: {pattern_str}"

            # Format output
            result_lines = [f"Found {total_matches} match{'es' if total_matches != 1 else ''} across {len(patterns)} pattern{'s' if len(patterns) != 1 else ''}:"]

            for match_uri, matches in merged_results.items():
                # Sort matches by line number
                matches.sort(key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0)
                result_lines.append(f"\n📄 {match_uri}")
                for line, content, pattern_name in matches:
                    result_lines.append(f"   Line {line} (pattern: '{pattern_name}'):")
                    result_lines.append(f"   {content}")

            return "\n".join(result_lines)
        except Exception as e:
            return f"Error searching Viking with grep: {str(e)}"


class VikingGlobTool(OVFileTool):
    """Tool to find Viking resources using glob patterns."""

    @property
    def name(self) -> str:
        return "openviking_glob"

    @property
    def description(self) -> str:
        return "Find Viking resources using glob patterns (like **/*.md, *.py)."

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
        try:
            client = await self._get_client(tool_context)
            result = await client.glob(pattern, uri=uri or None)

            if isinstance(result, dict):
                matches = result.get("matches", [])
                count = result.get("count", 0)
            else:
                matches = getattr(result, "matches", [])
                count = getattr(result, "count", 0)

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

class VikingMemoryCommitTool(OVFileTool):
    """Tool to commit messages to OpenViking session."""

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
        try:
            if not tool_context.sender_id:
                return "Error committed, sender_id is required."
            client = await self._get_client(tool_context)
            session_id = tool_context.session_key.safe_name()
            await client.commit(session_id, messages, tool_context.sender_id)
            return f"Successfully committed to session {session_id}"
        except Exception as e:
            logger.exception(f"Error processing message: {e}")
            return f"Error committing to Viking: {str(e)}"

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
                    "description": "List of Viking file URIs to read from (e.g., [\"viking://resources/path/123.md\", \"viking://resources/path/456.md\"])",
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

            for i, result in enumerate(results, 1):
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