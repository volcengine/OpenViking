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
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.recipe import Recipe
from mcp.server.fastmcp import FastMCP

import openviking as ov
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("openviking-mcp")

# Global state
_recipe: Optional[Recipe] = None
_config_path: str = "./ov.conf"
_data_path: str = "./data"


def _get_recipe() -> Recipe:
    """Get or create the Recipe (RAG pipeline) instance."""
    global _recipe
    if _recipe is None:
        _recipe = Recipe(config_path=_config_path, data_path=_data_path)
    return _recipe


def create_server(host: str = "127.0.0.1", port: int = 2033) -> FastMCP:
    """Create and configure the MCP server."""
    mcp = FastMCP(
        name="openviking-mcp",
        instructions=(
            "OpenViking MCP Server provides RAG (Retrieval-Augmented Generation) capabilities. "
            "Use 'query' for full RAG answers, 'search' for semantic search only, "
            "and 'add_resource' to ingest new documents."
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
        system_prompt: str = "",
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
        target_uri: str = "",
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

        def _search_sync():
            recipe = _get_recipe()
            return recipe.search(
                query=query,
                top_k=top_k,
                score_threshold=score_threshold,
                target_uri=target_uri or None,
            )

        results = await asyncio.to_thread(_search_sync)

        if not results:
            return "No relevant results found."

        output_parts = []
        for i, r in enumerate(results, 1):
            preview = r["content"][:500] + "..." if len(r["content"]) > 500 else r["content"]
            output_parts.append(f"[{i}] {r['uri']} (score: {r['score']:.4f})\n{preview}")

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
        config_path = _config_path
        data_path = _data_path

        def _add_sync():
            with open(config_path, "r") as f:
                config_dict = json.load(f)

            config = OpenVikingConfig.from_dict(config_dict)
            client = ov.SyncOpenViking(path=data_path, config=config)

            try:
                client.initialize()

                path = resource_path
                if not path.startswith("http"):
                    resolved = Path(path).expanduser()
                    if not resolved.exists():
                        return f"Error: File not found: {resolved}"
                    path = str(resolved)

                result = client.add_resource(path=path)

                if result and "root_uri" in result:
                    root_uri = result["root_uri"]
                    client.wait_processed(timeout=300)
                    return f"Resource added and indexed: {root_uri}"
                elif result and result.get("status") == "error":
                    errors = result.get("errors", [])[:3]
                    error_msg = "\n".join(f"  - {e}" for e in errors)
                    return (
                        f"Resource had parsing issues:\n{error_msg}\n"
                        "Some content may still be searchable."
                    )
                else:
                    return "Failed to add resource."
            finally:
                client.close()

        return await asyncio.to_thread(_add_sync)

    @mcp.resource("openviking://status")
    def server_status() -> str:
        """Get the current server status and configuration."""
        info = {
            "config_path": _config_path,
            "data_path": _data_path,
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
  # Start with defaults
  uv run server.py

  # Custom config and port
  uv run server.py --config ./ov.conf --data ./data --port 9000

  # Use stdio transport (for Claude Desktop integration)
  uv run server.py --transport stdio

  # Connect from Claude CLI
  claude mcp add --transport http openviking http://localhost:2033/mcp

Environment variables:
  OV_CONFIG    Path to config file (default: ./ov.conf)
  OV_DATA      Path to data directory (default: ./data)
  OV_PORT      Server port (default: 2033)
  OV_DEBUG     Enable debug logging (set to 1)
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
    return parser.parse_args()


def main():
    args = parse_args()

    global _config_path, _data_path
    _config_path = args.config
    _data_path = args.data

    if os.getenv("OV_DEBUG") == "1":
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("OpenViking MCP Server starting")
    logger.info(f"  config: {_config_path}")
    logger.info(f"  data:   {_data_path}")
    logger.info(f"  transport: {args.transport}")

    mcp = create_server(host=args.host, port=args.port)

    if args.transport == "streamable-http":
        logger.info(f"  endpoint: http://{args.host}:{args.port}/mcp")
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
