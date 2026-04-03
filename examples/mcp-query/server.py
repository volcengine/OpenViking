#!/usr/bin/env python3
"""
OpenViking MCP Server - Expose RAG query capabilities through Model Context Protocol

Provides MCP tools for:
  - query: Full RAG pipeline (search + LLM generation)
  - search: Semantic search only (no LLM)
  - add_resource: Add documents/URLs to the database

Usage:
  uv run server.py
  uv run server.py --config ./ov.conf --data ./data --port 2033
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.recipe import Recipe
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("openviking-mcp")

# Global state
_recipe: Optional[Recipe] = None
_config_path: str = "./ov.conf"
_data_path: str = "./data"
_server_url: str = ""
_ov_api_key: str = ""
_ov_account: str = ""
_ov_user: str = ""
_ov_agent_id: str = ""
_llm_api_key: str = ""
_timeout: float = 60.0
_default_uri: str = ""


def _format_timestamp(raw_value: Optional[str]) -> Optional[str]:
    """Format ISO-like timestamps into a user-friendly absolute time string."""
    if not raw_value:
        return None

    try:
        dt = datetime.fromisoformat(raw_value)
    except ValueError:
        return raw_value

    formatted = dt.strftime("%B %d, %Y %H:%M")
    if dt.tzinfo is not None:
        offset = dt.strftime("%z")
        if offset == "+0000":
            formatted += " UTC"
        elif offset:
            formatted += f" UTC{offset[:3]}:{offset[3:]}"
    return formatted


def _get_recipe() -> Recipe:
    """Get or create the Recipe (RAG pipeline) instance."""
    global _recipe
    if _recipe is None:
        _recipe = Recipe(
            config_path=_config_path,
            data_path=_data_path,
            server_url=_server_url or None,
            api_key=_ov_api_key or None,
            account=_ov_account or None,
            user=_ov_user or None,
            agent_id=_ov_agent_id or None,
            timeout=_timeout,
            llm_api_key=_llm_api_key or None,
        )
    return _recipe


def create_server(host: str = "127.0.0.1", port: int = 2033) -> FastMCP:
    """Create and configure the MCP server."""
    mcp = FastMCP(
        name="openviking-mcp",
        instructions=(
            "OpenViking MCP Server exposes OpenViking over MCP. "
            "Use 'search' to retrieve context from OpenViking and 'add_resource' to ingest files, "
            "directories, or URLs. Use 'query' only when this bridge also has local LLM config."
        ),
        host=host,
        port=port,
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool()
    async def query(
        question: str,
        top_k: int = 5,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        score_threshold: float = 0.2,
        system_prompt: Optional[str] = None,
    ) -> str:
        """
        Ask a question and get an answer using RAG (Retrieval-Augmented Generation).

        Searches the OpenViking database for relevant context, then generates an answer
        using an LLM with the retrieved context.

        Args:
            question: The question to ask.
            top_k: Number of search results to use as context (1-20, default: 5).
            temperature: LLM sampling temperature (0.0-1.0, default: 0.7).
            max_tokens: Maximum tokens in the response (default: 2048).
            score_threshold: Minimum relevance score for search results (0.0-1.0, default: 0.2).
            system_prompt: Optional system prompt to guide the LLM response style.
        """

        def _query_sync():
            recipe = _get_recipe()
            if not recipe.query_ready:
                return {
                    "answer": (
                        "Query is not configured on this MCP bridge. "
                        "Provide a local ov.conf with vlm.api_base and vlm.model, "
                        "or use the search tool and let Codex synthesize the answer."
                    ),
                    "context": [],
                    "timings": {},
                }
            return recipe.query(
                user_query=question,
                search_top_k=top_k,
                temperature=temperature,
                max_tokens=max_tokens,
                score_threshold=score_threshold,
                system_prompt=system_prompt or None,
            )

        result = await asyncio.to_thread(_query_sync)

        # Format response with answer and sources
        output = result["answer"]

        if result["context"]:
            output += "\n\n---\nSources:\n"
            for i, ctx in enumerate(result["context"], 1):
                uri_parts = ctx["uri"].split("/")
                filename = uri_parts[-1] if uri_parts else ctx["uri"]
                output += f"  {i}. {filename} (relevance: {ctx['score']:.4f})\n"

        timings = result.get("timings", {})
        if timings:
            output += (
                f"\n[search: {timings.get('search_time', 0):.2f}s, "
                f"llm: {timings.get('llm_time', 0):.2f}s, "
                f"total: {timings.get('total_time', 0):.2f}s]"
            )

        return output

    @mcp.tool()
    async def search(
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.2,
        target_uri: Optional[str] = None,
    ) -> str:
        """
        Search the OpenViking database for relevant content (no LLM generation).

        Performs semantic search and returns matching documents with relevance scores.
        Use this when you only need to find relevant documents without generating an answer.

        Args:
            query: The search query.
            top_k: Number of results to return (1-20, default: 5).
            score_threshold: Minimum relevance score (0.0-1.0, default: 0.2).
            target_uri: Optional URI to scope the search to a specific resource.
        """
        effective_uri = target_uri or _default_uri

        def _search_sync():
            recipe = _get_recipe()
            return recipe.search(
                query=query,
                top_k=top_k,
                score_threshold=score_threshold,
                target_uri=effective_uri or None,
            )

        results = await asyncio.to_thread(_search_sync)

        if not results:
            return "No relevant results found."

        output_parts = []
        for i, r in enumerate(results, 1):
            preview = r["content"][:500] + "..." if len(r["content"]) > 500 else r["content"]
            timestamp_parts = []
            if r.get("updated_at"):
                timestamp_parts.append(f"updated: {_format_timestamp(r['updated_at'])}")
            if r.get("created_at"):
                timestamp_parts.append(f"created: {_format_timestamp(r['created_at'])}")
            timestamp_block = ""
            if timestamp_parts:
                timestamp_block = "\n" + "\n".join(timestamp_parts)
            output_parts.append(
                f"[{i}] {r['uri']} (score: {r['score']:.4f}){timestamp_block}\n{preview}"
            )

        return f"Found {len(results)} results:\n\n" + "\n\n".join(output_parts)

    @mcp.tool()
    async def add_resource(resource_path: str) -> str:
        """
        Add a document, file, directory, or URL to the OpenViking database.

        The resource will be parsed, chunked, and indexed for future search/query operations.
        Supported formats: PDF, Markdown, Text, HTML, and more.
        URLs are automatically downloaded and processed.

        Args:
            resource_path: Path to a local file/directory, or a URL to add.
        """
        def _add_sync():
            recipe = _get_recipe()
            return recipe.add_resource(resource_path)

        return await asyncio.to_thread(_add_sync)

    @mcp.tool()
    async def memory_start_session() -> dict:
        """
        Create a new OpenViking session for manual memory capture.

        Call this once at the beginning of a task you want to remember, then use
        `memory_add_turn` for the important exchanges and `memory_commit_session`
        when you want OpenViking to extract memories.
        """

        def _start_sync():
            recipe = _get_recipe()
            return recipe.create_memory_session()

        return await asyncio.to_thread(_start_sync)

    @mcp.tool()
    async def memory_get_session(session_id: str) -> dict:
        """
        Inspect an existing OpenViking memory session.

        Use this to recover the current message count or verify that a session id
        is still valid before appending or committing.
        """

        def _get_sync():
            recipe = _get_recipe()
            return recipe.get_memory_session(session_id)

        return await asyncio.to_thread(_get_sync)

    @mcp.tool()
    async def memory_add_turn(
        session_id: str,
        user_message: Optional[str] = None,
        assistant_message: Optional[str] = None,
        note: Optional[str] = None,
    ) -> dict:
        """
        Append one important turn into an OpenViking memory session.

        Typical usage:
        - Put the user's message in `user_message`
        - Put the assistant's reply or short summary in `assistant_message`
        - Put any extra context you want to preserve in `note`
        """

        def _add_turn_sync():
            recipe = _get_recipe()
            return recipe.add_memory_turn(
                session_id=session_id,
                user_message=user_message,
                assistant_message=assistant_message,
                note=note,
            )

        return await asyncio.to_thread(_add_turn_sync)

    @mcp.tool()
    async def memory_commit_session(session_id: str) -> dict:
        """
        Commit an OpenViking session so memories are extracted and indexed.

        This is the manual equivalent of the Claude plugin's session-end commit step.
        """

        def _commit_sync():
            recipe = _get_recipe()
            return recipe.commit_memory_session(session_id)

        return await asyncio.to_thread(_commit_sync)

    @mcp.tool()
    async def memory_delete_session(session_id: str) -> dict:
        """
        Delete an OpenViking memory session.

        Useful if you started a session by mistake and do not want to keep it.
        """

        def _delete_sync():
            recipe = _get_recipe()
            return recipe.delete_memory_session(session_id)

        return await asyncio.to_thread(_delete_sync)

    @mcp.resource("openviking://status")
    def server_status() -> str:
        """Get the current server status and configuration."""
        info = {
            "mode": "http" if _server_url else "local",
            "config_path": _config_path,
            "data_path": _data_path,
            "server_url": _server_url or None,
            "default_uri": _default_uri or None,
            "status": "running",
        }
        return json.dumps(info, indent=2)

    return mcp


def parse_args():
    parser = argparse.ArgumentParser(
        description="OpenViking MCP Server - RAG query capabilities via MCP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start in embedded/local mode
  uv run server.py

  # Bridge to an existing remote OpenViking HTTP server
  uv run server.py --url http://192.168.1.50:1933 --api-key sk-xxx

  # Bridge to a remote OpenViking HTTP server with root-key tenant headers
  uv run server.py --url http://192.168.1.50:1933 --api-key root-key --account acme --user alice

  # Remote OpenViking for search/add-resource, but local ov.conf for the optional query tool
  uv run server.py --url http://192.168.1.50:1933 --config ./ov.conf

  # Use stdio transport (for Claude Desktop integration)
  uv run server.py --transport stdio

  # Connect from Codex
  codex mcp add openviking --url http://127.0.0.1:2033/mcp

  # With default search scope
  uv run server.py --url http://192.168.1.50:1933 --default-uri viking://user/memories

Environment variables:
  OV_CONFIG       Path to local ov.conf for the optional query tool (default: ./ov.conf)
  OV_DATA         Path to local OpenViking data directory (default: ./data)
  OV_PORT         MCP bridge port (default: 2033)
  OV_SERVER_URL   Remote OpenViking HTTP server URL
  OV_API_KEY      Remote OpenViking HTTP API key
  OV_ACCOUNT      Remote OpenViking account header (root-key access only)
  OV_USER         Remote OpenViking user header (root-key access only)
  OV_AGENT_ID     Remote OpenViking agent header
  OV_LLM_API_KEY  Override the local query LLM API key from ov.conf
  OV_DEFAULT_URI  Default target URI for search scoping
  OV_TIMEOUT      Timeout in seconds for OpenViking and query LLM calls
  OV_DEBUG        Enable debug logging (set to 1)
        """,
    )
    parser.add_argument(
        "--config",
        type=str,
        default=os.getenv("OV_CONFIG", "./ov.conf"),
        help="Path to config file (default: ./ov.conf)",
    )
    parser.add_argument(
        "--data",
        type=str,
        default=os.getenv("OV_DATA", "./data"),
        help="Path to data directory (default: ./data)",
    )
    parser.add_argument(
        "--url",
        type=str,
        default=os.getenv("OV_SERVER_URL", ""),
        help="Remote OpenViking HTTP server URL (default: local embedded mode)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("OV_PORT", "2033")),
        help="Port to listen on (default: 2033)",
    )
    parser.add_argument(
        "--transport",
        type=str,
        choices=["streamable-http", "stdio"],
        default="streamable-http",
        help="Transport type (default: streamable-http)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.getenv("OV_API_KEY", ""),
        help="API key for remote OpenViking HTTP authentication (default: $OV_API_KEY)",
    )
    parser.add_argument(
        "--account",
        type=str,
        default=os.getenv("OV_ACCOUNT", ""),
        help="Remote OpenViking account header (needed with root key access)",
    )
    parser.add_argument(
        "--user",
        type=str,
        default=os.getenv("OV_USER", ""),
        help="Remote OpenViking user header (needed with root key access)",
    )
    parser.add_argument(
        "--agent-id",
        type=str,
        default=os.getenv("OV_AGENT_ID", ""),
        help="Remote OpenViking agent header",
    )
    parser.add_argument(
        "--llm-api-key",
        type=str,
        default=os.getenv("OV_LLM_API_KEY", ""),
        help="Override the local query LLM API key from ov.conf",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("OV_TIMEOUT", "60")),
        help="Timeout in seconds for OpenViking and query LLM calls",
    )
    parser.add_argument(
        "--default-uri",
        type=str,
        default=os.getenv("OV_DEFAULT_URI", ""),
        help="Default target URI for search scoping (default: search all)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    global _config_path, _data_path, _server_url, _ov_api_key, _ov_account, _ov_user
    global _ov_agent_id, _llm_api_key, _timeout, _default_uri
    _config_path = args.config
    _data_path = args.data
    _server_url = args.url
    _ov_api_key = args.api_key
    _ov_account = args.account
    _ov_user = args.user
    _ov_agent_id = args.agent_id
    _llm_api_key = args.llm_api_key
    _timeout = args.timeout
    _default_uri = args.default_uri

    if os.getenv("OV_DEBUG") == "1":
        logging.getLogger().setLevel(logging.DEBUG)

    if not _server_url and not os.path.exists(_config_path):
        raise SystemExit(
            f"Config file not found: {_config_path}. "
            "Create a local ov.conf or pass --url to bridge to a remote OpenViking HTTP server."
        )

    logger.info("OpenViking MCP Server starting")
    logger.info(f"  mode:   {'http-bridge' if _server_url else 'local'}")
    logger.info(f"  config: {_config_path}")
    logger.info(f"  data:   {_data_path}")
    if _server_url:
        logger.info(f"  ov url: {_server_url}")
        if _ov_account or _ov_user:
            logger.info(f"  tenant: account={_ov_account or '-'} user={_ov_user or '-'}")
    logger.info(f"  transport: {args.transport}")

    mcp = create_server(host=args.host, port=args.port)

    if args.transport == "streamable-http":
        logger.info(f"  endpoint: http://{args.host}:{args.port}/mcp")
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
