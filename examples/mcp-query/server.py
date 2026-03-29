#!/usr/bin/env python3
"""
OpenViking MCP Server - Expose a shared OpenViking HTTP backend through MCP.
"""

import argparse
import asyncio
import json
import logging
import os
import tempfile
import uuid
import zipfile
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("openviking-mcp")

_backend_url: str = "http://127.0.0.1:1933"
_api_key: str = ""
_account: str = ""
_user: str = ""
_agent_id: str = "mcp"
_default_uri: str = ""


def _headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if _api_key:
        headers["X-API-Key"] = _api_key
    if _account:
        headers["X-OpenViking-Account"] = _account
    if _user:
        headers["X-OpenViking-User"] = _user
    if _agent_id:
        headers["X-OpenViking-Agent"] = _agent_id
    return headers


def _handle_response(response: httpx.Response) -> dict:
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") == "error":
        error = payload.get("error", {})
        raise RuntimeError(error.get("message", "OpenViking backend returned an error"))
    return payload.get("result", {})


def _zip_directory(dir_path: Path) -> Path:
    zip_path = Path(tempfile.gettempdir()) / f"openviking-mcp-{uuid.uuid4().hex}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file_path in dir_path.rglob("*"):
            if file_path.is_file():
                arcname = str(file_path.relative_to(dir_path)).replace("\\", "/")
                zipf.write(file_path, arcname=arcname)
    return zip_path


def _upload_temp_file(client: httpx.Client, file_path: Path) -> str:
    with file_path.open("rb") as handle:
        response = client.post(
            "/api/v1/resources/temp_upload",
            files={"file": (file_path.name, handle, "application/octet-stream")},
        )
    result = _handle_response(response)
    temp_file_id = result.get("temp_file_id")
    if not temp_file_id:
        raise RuntimeError("OpenViking temp upload did not return a temp_file_id")
    return temp_file_id


def _format_matches(result: dict) -> str:
    matches = sorted(
        [
            *result.get("memories", []),
            *result.get("resources", []),
            *result.get("skills", []),
        ],
        key=lambda item: item.get("score", 0),
        reverse=True,
    )
    if not matches:
        return "No relevant results found."

    output_parts = []
    for index, match in enumerate(matches, 1):
        preview_source = match.get("overview") or match.get("abstract") or ""
        preview = preview_source[:500] + "..." if len(preview_source) > 500 else preview_source
        output_parts.append(
            (
                f"[{index}] {match.get('uri', '')} "
                f"(type: {match.get('context_type', 'resource')}, "
                f"score: {match.get('score', 0):.4f})\n"
                f"{preview}"
            ).rstrip()
        )

    return f"Found {len(matches)} results:\n\n" + "\n\n".join(output_parts)


def create_server(host: str = "127.0.0.1", port: int = 2033) -> FastMCP:
    mcp = FastMCP(
        name="openviking-mcp",
        instructions=(
            "OpenViking MCP Server exposes a shared OpenViking HTTP backend. "
            "Use 'search' for semantic retrieval, 'add_resource' to ingest "
            "content, and 'get_status' to inspect backend health."
        ),
        host=host,
        port=port,
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool()
    async def search(
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.2,
        target_uri: str = "",
    ) -> str:
        """Search the shared OpenViking backend for relevant content."""
        effective_uri = target_uri or _default_uri

        def _search_sync() -> str:
            with httpx.Client(base_url=_backend_url, headers=_headers(), timeout=60.0) as client:
                response = client.post(
                    "/api/v1/search/search",
                    json={
                        "query": query,
                        "target_uri": effective_uri,
                        "limit": top_k,
                        "score_threshold": score_threshold,
                    },
                )
                return _format_matches(_handle_response(response))

        return await asyncio.to_thread(_search_sync)

    @mcp.tool()
    async def add_resource(
        resource_path: str,
        reason: str = "MCP add resource",
        to: str = "",
        parent: str = "",
        wait: bool = True,
    ) -> str:
        """Add a resource through the shared OpenViking backend."""

        def _add_sync() -> str:
            with httpx.Client(base_url=_backend_url, headers=_headers(), timeout=300.0) as client:
                request_data = {
                    "to": to or None,
                    "parent": parent or None,
                    "reason": reason,
                    "wait": wait,
                }

                if resource_path.startswith("http"):
                    request_data["path"] = resource_path
                else:
                    resolved = Path(resource_path).expanduser()
                    if not resolved.exists():
                        return f"Error: File not found: {resolved}"
                    if resolved.is_dir():
                        zip_path = _zip_directory(resolved)
                        try:
                            request_data["temp_file_id"] = _upload_temp_file(client, zip_path)
                        finally:
                            zip_path.unlink(missing_ok=True)
                    else:
                        request_data["temp_file_id"] = _upload_temp_file(client, resolved)

                response = client.post("/api/v1/resources", json=request_data)
                result = _handle_response(response)
                root_uri = result.get("root_uri")
                if root_uri:
                    return f"Resource added and indexed: {root_uri}"
                return json.dumps(result, indent=2)

        return await asyncio.to_thread(_add_sync)

    @mcp.tool()
    async def get_status() -> str:
        """Get health and observer status from the shared OpenViking backend."""

        def _status_sync() -> str:
            with httpx.Client(base_url=_backend_url, headers=_headers(), timeout=30.0) as client:
                response = client.get("/api/v1/observer/system")
                return json.dumps(_handle_response(response), indent=2)

        return await asyncio.to_thread(_status_sync)

    @mcp.resource("openviking://status")
    def server_status() -> str:
        return json.dumps(
            {
                "backend_url": _backend_url,
                "account": _account,
                "user": _user,
                "agent_id": _agent_id,
                "default_uri": _default_uri,
                "status": "running",
            },
            indent=2,
        )

    return mcp


def parse_args():
    parser = argparse.ArgumentParser(
        description="OpenViking MCP Server - shared HTTP backend via MCP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run server.py
  uv run server.py --backend-url http://127.0.0.1:1933 --port 2033
  uv run server.py --account brianle --user brianle --agent-id mcp

Environment variables:
  OV_BACKEND_URL OpenViking backend URL (default: http://127.0.0.1:1933)
  OV_PORT        Server port (default: 2033)
  OV_API_KEY     API key for OpenViking server authentication
  OV_ACCOUNT     OpenViking account header
  OV_USER        OpenViking user header
  OV_AGENT_ID    OpenViking agent header
  OV_DEFAULT_URI Default target URI for search scoping
  OV_DEBUG       Enable debug logging (set to 1)
        """,
    )
    parser.add_argument(
        "--backend-url",
        type=str,
        default=os.getenv("OV_BACKEND_URL", "http://127.0.0.1:1933"),
        help="OpenViking backend URL (default: http://127.0.0.1:1933)",
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
        help="API key for OpenViking server authentication",
    )
    parser.add_argument(
        "--account",
        type=str,
        default=os.getenv("OV_ACCOUNT", ""),
        help="OpenViking account header",
    )
    parser.add_argument(
        "--user",
        type=str,
        default=os.getenv("OV_USER", ""),
        help="OpenViking user header",
    )
    parser.add_argument(
        "--agent-id",
        type=str,
        default=os.getenv("OV_AGENT_ID", "mcp"),
        help="OpenViking agent header (default: mcp)",
    )
    parser.add_argument(
        "--default-uri",
        type=str,
        default=os.getenv("OV_DEFAULT_URI", ""),
        help="Default target URI for search scoping",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    global _backend_url, _api_key, _account, _user, _agent_id, _default_uri
    _backend_url = args.backend_url
    _api_key = args.api_key
    _account = args.account
    _user = args.user
    _agent_id = args.agent_id
    _default_uri = args.default_uri

    if os.getenv("OV_DEBUG") == "1":
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("OpenViking MCP Server starting")
    logger.info("  backend: %s", _backend_url)
    logger.info("  account: %s", _account or "(unset)")
    logger.info("  user: %s", _user or "(unset)")
    logger.info("  agent: %s", _agent_id or "(unset)")
    logger.info("  transport: %s", args.transport)

    mcp = create_server(host=args.host, port=args.port)

    if args.transport == "streamable-http":
        logger.info("  endpoint: http://%s:%s/mcp", args.host, args.port)
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
