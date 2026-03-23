"""OpenViking file system tools: read, write, list, search resources."""

import asyncio
from abc import ABC
from pathlib import Path
from typing import Any, Optional, Union
import json

import httpx
from loguru import logger

from vikingbot.agent.tools.base import Tool, ToolContext
from vikingbot.openviking_mount.ov_server import VikingClient
from vikingbot.providers.litellm_provider import LiteLLMProvider
from vikingbot.config.loader import load_config


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
        return "List resources in a OpenViking path."

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
        return "Search for resources in OpenViking using a query"

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
            },
            "required": ["query"],
        }

    async def execute(
        self,
        tool_context: "ToolContext",
        query: str,
        target_uri: Optional[str] = "",
        **kwargs: Any,
    ) -> str:
        try:
            client = await self._get_client(tool_context)
            search_client = getattr(client, 'admin_user_client', client)
            results = await search_client.search(query, target_uri=target_uri)

            if not results:
                return f"No results found for query: {query}"
            if isinstance(results, list):
                result_strs = []
                for i, result in enumerate(results, 1):
                    result_strs.append(f"{i}. {str(result)}")
                return "\n".join(result_strs)
            else:
                return str(results)
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
        return "Search Viking resources using regex patterns (like grep). Supports multiple patterns to search concurrently."

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


class VikingSearchUserMemoryTool(OVFileTool):
    """Tool to search Viking user memories"""

    @property
    def name(self) -> str:
        return "user_memory_search"

    @property
    def description(self) -> str:
        return "Search for user memories in OpenViking using a query."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The search query"}},
            "required": ["query"],
        }

    async def execute(self, tool_context: ToolContext, query: str, **kwargs: Any) -> str:
        try:
            client = await self._get_client(tool_context)
            results = await client.search_user_memory(query, tool_context.sender_id)

            if not results:
                return f"No results found for query: {query}"
            return str(results)
        except Exception as e:
            return f"Error searching Viking: {str(e)}"


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

class VikingUserProfileTool(OVFileTool):
    """Tool to commit messages to OpenViking session."""

    @property
    def name(self) -> str:
        return "openviking_user_profile_read"

    @property
    def description(self) -> str:
        return "Read user's profile details from OpenViking."

    @property
    def parameters(self) -> dict[str, Any]:
        return {}

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
            profile = await client.read_user_profile(tool_context.sender_id, session_id)
            return f"{profile}"
        except Exception as e:
            logger.exception(f"Error processing message: {e}")
            return f"Error read user profile: {str(e)}"


class VikingSearchUserMemoryToolV2(OVFileTool):
    """Tool to search Viking user memories with enhanced query expansion and ranking."""

    def __init__(self):
        super().__init__()
        self._llm_provider = None
        self._config = load_config()

    @property
    def name(self) -> str:
        return "user_memory_search_v2"

    @property
    def description(self) -> str:
        return "Search for user memories in OpenViking using a query with enhanced query expansion and relevance ranking. Do not use the openviking_search tool to retrieve similar queries again."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "top_k": {"type": "integer", "description": "Number of top results to return", "default": 10},
            },
            "required": ["query"],
        }

    async def _get_llm_provider(self):
        """Get or initialize the LLM provider."""
        if self._llm_provider is None:
            llm_config = self._config.agents
            self._llm_provider = LiteLLMProvider(
                api_key=llm_config.api_key,
                api_base=llm_config.api_base,
                default_model=llm_config.model,
                extra_headers=llm_config.extra_headers if llm_config else None,
                provider_name=llm_config.provider,
            )
        return self._llm_provider

    async def _generate_search_queries(self, original_query: str) -> list[str]:
        """Generate multiple search queries from the original query using LLM."""
        llm = await self._get_llm_provider()

        system_prompt = """You are an expert in generating search queries. Based on the user's original query, split it into 3–5 different query phrases to retrieve the user's personal memories.
The generated queries should cover the intent of the original query from different perspectives, including synonyms, related concepts, various expressions, etc., and be as concise as possible.
Please return only a JSON array with no extra content. Example:
["Query 1", "Query 2", "Query 3"]"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"query: {original_query}\n"}
        ]

        try:
            response = await llm.chat(messages, temperature=0.3, max_tokens=512)
            content = response.content.strip()
            # 尝试解析JSON
            try:
                queries = json.loads(content)
                if isinstance(queries, list) and all(isinstance(q, str) for q in queries):
                    # 去重并保留原始查询
                    queries = list(set([original_query] + queries))
                    return queries[:5]  # 最多5个查询
            except json.JSONDecodeError:
                # 如果解析失败，尝试提取数组部分
                import re
                match = re.search(r'\[.*\]', content, re.DOTALL)
                if match:
                    try:
                        queries = json.loads(match.group())
                        if isinstance(queries, list) and all(isinstance(q, str) for q in queries):
                            queries = list(set([original_query] + queries))
                            return queries[:5]
                    except:
                        pass

            # 如果所有解析都失败，返回原始查询
            return [original_query]

        except Exception as e:
            logger.warning(f"Failed to generate search queries: {e}")
            return [original_query]

    async def execute(self, tool_context: ToolContext, query: str, top_k: int = 10, **kwargs: Any) -> str:
        try:
            # 1. 生成扩展查询列表
            search_queries = await self._generate_search_queries(query)
            logger.info(f"Generated search queries: {search_queries}")

            # 2. 并发调用搜索接口
            client = await self._get_client(tool_context)
            user_id = tool_context.sender_id

            if not user_id:
                return "Error: sender_id is required for memory search."

            # 检查用户是否存在
            user_exists = await client._check_user_exists(user_id)
            if not user_exists:
                return f"No user found for id: {user_id}"

            uri_user_memory = f"viking://user/{user_id}/memories/"

            # 并发搜索
            search_tasks = [
                client.search(q, target_uri=uri_user_memory)
                for q in search_queries
            ]
            search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

            # 3. 合并结果并去重
            all_memories = {}
            for result in search_results:
                if isinstance(result, Exception):
                    logger.warning(f"Search failed: {result}")
                    continue

                memories = result.get("memories", []) + result.get("resources", [])
                for memory in memories:
                    uri = memory.get("uri", "")
                    if not uri:
                        continue

                    score = memory.get("score", 0.0)
                    if uri not in all_memories or score > all_memories[uri].get("score", 0.0):
                        all_memories[uri] = memory

            # 4. 按相似度得分排序
            sorted_memories = sorted(
                all_memories.values(),
                key=lambda x: x.get("score", 0.0),
                reverse=True
            )[:top_k]

            # 5. 构造结果摘要
            if not sorted_memories:
                return f"No results found for query: {query}"

            result_lines = [f"Found {len(sorted_memories)} relevant memories:"]
            for i, memory in enumerate(sorted_memories, 1):
                title = memory.get("title", memory.get("uri", "Untitled"))
                abstract = memory.get("abstract", "")
                score = memory.get("score", 0.0)
                uri = memory.get("uri", "")

                result_lines.append(f"\n{i}. {title} (score: {score:.4f})")
                if abstract:
                    result_lines.append(f"   Abstract: {abstract}")
                result_lines.append(f"   URI: {uri}")

            return "\n".join(result_lines)

        except Exception as e:
            logger.exception(f"Error in VikingSearchUserMemoryToolV2: {e}")
            return f"Error searching Viking user memory: {str(e)}"


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