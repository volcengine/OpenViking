# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
OpenViking MCP Server implementation.

Provides MCP tools for interacting with OpenViking context database.
"""

import json
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from openviking import AsyncOpenViking
from openviking.message import TextPart
from openviking.session import Session
from openviking.utils import get_logger

from .config import get_config

logger = get_logger(__name__)

# Initialize FastMCP server
mcp = FastMCP("OpenViking")

# Global client instance
_client: Optional[AsyncOpenViking] = None
_sessions: Dict[str, Session] = {}


async def get_client() -> AsyncOpenViking:
    """
    Get or create OpenViking client instance.

    Returns:
        AsyncOpenViking: Client instance
    """
    global _client
    if _client is None:
        config = get_config()
        config_obj = config.load_openviking_config()
        _client = AsyncOpenViking(config=config_obj)
        await _client.initialize()
        logger.info("OpenViking client initialized")
    return _client


# ============= Memory Management Tools =============


@mcp.tool()
async def initialize_memory() -> str:
    """
    Initialize OpenViking memory system.

    This must be called before using any other tools. It sets up the vector database,
    AGFS file system, and all necessary indexes.

    Returns:
        str: Success message with initialization status
    """
    try:
        client = await get_client()
        return json.dumps(
            {"status": "success", "message": "OpenViking memory system initialized successfully"}
        )
    except Exception as e:
        logger.error(f"Failed to initialize memory: {e}")
        return json.dumps({"status": "error", "message": f"Initialization failed: {str(e)}"})


@mcp.tool()
async def get_status() -> str:
    """
    Get current status of OpenViking system.

    Returns information about the client state, processing queues, and system health.

    Returns:
        str: JSON string with status information
    """
    try:
        client = await get_client()

        # Get queue status
        queue_status = await client.wait_processed(timeout=0.1)

        status = {
            "status": "success",
            "initialized": client._initialized,
            "user": client.user,
            "active_sessions": len(_sessions),
            "queue_status": queue_status,
        }

        return json.dumps(status, indent=2)
    except Exception as e:
        logger.error(f"Failed to get status: {e}")
        return json.dumps({"status": "error", "message": f"Failed to get status: {str(e)}"})


# ============= Resource Management Tools =============


@mcp.tool()
async def add_resource(
    path: str,
    target: str = "",
    reason: str = "",
    instruction: str = "",
    wait: bool = False,
    timeout: float = 180.0,
) -> str:
    """
    Add a resource (file, directory, or URL) to OpenViking memory.

    The resource will be parsed, indexed, and made searchable. Supports local files,
    directories, and remote URLs.

    Args:
        path: Path to resource (local file/directory or URL)
        target: Target Viking URI (optional, auto-generated if not provided)
        reason: Reason for adding this resource
        instruction: Special instructions for processing
        wait: Whether to wait for processing to complete
        timeout: Maximum wait time in seconds (default: 180)

    Returns:
        str: JSON string with resource information including root_uri
    """
    try:
        client = await get_client()
        result = await client.add_resource(
            path=path,
            target=target or None,
            reason=reason,
            instruction=instruction,
            wait=wait,
            timeout=timeout if wait else None,
        )
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Failed to add resource: {e}")
        return json.dumps({"status": "error", "message": f"Failed to add resource: {str(e)}"})


@mcp.tool()
async def semantic_search(
    query: str, target_uri: str = "", limit: int = 10, score_threshold: float = 0.0
) -> str:
    """
    Perform semantic search across indexed resources.

    Searches for content semantically similar to the query using vector embeddings.
    Returns ranked results with relevance scores.

    Args:
        query: Search query string
        target_uri: Limit search to specific Viking URI (optional)
        limit: Maximum number of results to return (default: 10)
        score_threshold: Minimum relevance score (0.0-1.0, default: 0.0)

    Returns:
        str: JSON string with search results including URIs and scores
    """
    try:
        client = await get_client()
        results = await client.find(
            query=query, target_uri=target_uri, limit=limit, score_threshold=score_threshold
        )

        # Convert results to serializable format
        output = {
            "status": "success",
            "query": query,
            "results": [
                {
                    "uri": r.uri,
                    "score": r.score,
                    "content": r.content[:500] if hasattr(r, "content") else "",
                }
                for r in results.resources
            ],
        }

        return json.dumps(output, indent=2)
    except Exception as e:
        logger.error(f"Semantic search failed: {e}")
        return json.dumps({"status": "error", "message": f"Search failed: {str(e)}"})


@mcp.tool()
async def get_abstract(uri: str) -> str:
    """
    Get L0 abstract (high-level summary) of a resource.

    Returns a concise abstract that summarizes the main purpose and content
    of the resource. This is the highest level of abstraction.

    Args:
        uri: Viking URI of the resource

    Returns:
        str: Abstract text
    """
    try:
        client = await get_client()
        abstract = await client.abstract(uri)
        return json.dumps({"status": "success", "uri": uri, "abstract": abstract}, indent=2)
    except Exception as e:
        logger.error(f"Failed to get abstract: {e}")
        return json.dumps({"status": "error", "message": f"Failed to get abstract: {str(e)}"})


@mcp.tool()
async def get_overview(uri: str) -> str:
    """
    Get L1 overview (detailed summary) of a resource.

    Returns a more detailed overview than the abstract, including structure
    and key components of the resource.

    Args:
        uri: Viking URI of the resource

    Returns:
        str: Overview text
    """
    try:
        client = await get_client()
        overview = await client.overview(uri)
        return json.dumps({"status": "success", "uri": uri, "overview": overview}, indent=2)
    except Exception as e:
        logger.error(f"Failed to get overview: {e}")
        return json.dumps({"status": "error", "message": f"Failed to get overview: {str(e)}"})


# ============= File System Tools =============


@mcp.tool()
async def read_content(uri: str) -> str:
    """
    Read the full content of a file resource.

    Returns the complete text content of the specified file.

    Args:
        uri: Viking URI of the file

    Returns:
        str: File content
    """
    try:
        client = await get_client()
        content = await client.read(uri)
        return json.dumps({"status": "success", "uri": uri, "content": content}, indent=2)
    except Exception as e:
        logger.error(f"Failed to read content: {e}")
        return json.dumps({"status": "error", "message": f"Failed to read content: {str(e)}"})


@mcp.tool()
async def list_directory(uri: str, recursive: bool = False) -> str:
    """
    List contents of a directory.

    Returns information about files and subdirectories in the specified directory.

    Args:
        uri: Viking URI of the directory
        recursive: If True, list all subdirectories recursively (default: False)

    Returns:
        str: JSON string with directory listing
    """
    try:
        client = await get_client()
        entries = await client.ls(uri, recursive=recursive, simple=False)
        return json.dumps({"status": "success", "uri": uri, "entries": entries}, indent=2)
    except Exception as e:
        logger.error(f"Failed to list directory: {e}")
        return json.dumps({"status": "error", "message": f"Failed to list directory: {str(e)}"})


@mcp.tool()
async def glob_search(pattern: str, uri: str = "viking://") -> str:
    """
    Search for files matching a glob pattern.

    Supports standard glob patterns like *.py, **/*.md, etc.

    Args:
        pattern: Glob pattern (e.g., "**/*.py", "*.md")
        uri: Base Viking URI to search from (default: "viking://")

    Returns:
        str: JSON string with matching file URIs
    """
    try:
        client = await get_client()
        result = await client.glob(pattern=pattern, uri=uri)
        return json.dumps(
            {
                "status": "success",
                "pattern": pattern,
                "base_uri": uri,
                "matches": result.get("matches", []),
            },
            indent=2,
        )
    except Exception as e:
        logger.error(f"Glob search failed: {e}")
        return json.dumps({"status": "error", "message": f"Glob search failed: {str(e)}"})


# ============= Session Management Tools =============


@mcp.tool()
async def create_session(session_id: str = "") -> str:
    """
    Create a new conversation session.

    Sessions maintain context across multiple interactions and can be used
    for context-aware search and resource management.

    Args:
        session_id: Optional session ID (auto-generated if not provided)

    Returns:
        str: JSON string with session information
    """
    try:
        client = await get_client()
        session = client.session(session_id=session_id or None)
        _sessions[session.session_id] = session

        return json.dumps(
            {"status": "success", "session_id": session.session_id, "user": session.user}, indent=2
        )
    except Exception as e:
        logger.error(f"Failed to create session: {e}")
        return json.dumps({"status": "error", "message": f"Failed to create session: {str(e)}"})


@mcp.tool()
async def add_message_to_session(session_id: str, role: str, content: str) -> str:
    """
    Add a message to an existing session.

    Messages are used to build conversation context for context-aware operations.

    Args:
        session_id: Session ID
        role: Message role ("user", "assistant", or "system")
        content: Message content

    Returns:
        str: JSON string with confirmation
    """
    try:
        if session_id not in _sessions:
            return json.dumps({"status": "error", "message": f"Session not found: {session_id}"})

        session = _sessions[session_id]
        parts = [TextPart(text=content)]
        session.add_message(role=role, parts=parts)

        return json.dumps(
            {"status": "success", "session_id": session_id, "message_count": len(session.messages)},
            indent=2,
        )
    except Exception as e:
        logger.error(f"Failed to add message: {e}")
        return json.dumps({"status": "error", "message": f"Failed to add message: {str(e)}"})


@mcp.tool()
async def commit_session(session_id: str) -> str:
    """
    Commit (save) a session to persistent storage.

    This saves the session and all its messages to the database for later retrieval.

    Args:
        session_id: Session ID to commit

    Returns:
        str: JSON string with commit confirmation
    """
    try:
        if session_id not in _sessions:
            return json.dumps({"status": "error", "message": f"Session not found: {session_id}"})

        session = _sessions[session_id]
        result = session.commit()

        return json.dumps(
            {
                "status": "success",
                "session_id": session_id,
                "message": "Session committed successfully",
            },
            indent=2,
        )
    except Exception as e:
        logger.error(f"Failed to commit session: {e}")
        return json.dumps({"status": "error", "message": f"Failed to commit session: {str(e)}"})
