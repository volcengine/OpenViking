#!/usr/bin/env python3
"""
OpenViking MCP Bridge - Thin MCP server that proxies to OpenViking HTTP API.
No C++ extensions needed, just pure Python.
"""

import json
import os
import urllib.request
import urllib.error
from mcp.server.fastmcp import FastMCP

OV_URL = os.getenv("OV_URL", "http://localhost:1933")
OV_API_KEY = os.getenv("OV_API_KEY", "")

mcp = FastMCP(
    name="openviking",
    instructions="OpenViking context database. Use query/search to find information, add_resource to ingest docs.",
)


def _has_error(result) -> bool:
    """Check if a result dict contains an error."""
    return isinstance(result, dict) and "error" in result and result["error"]


def _api(method: str, path: str, body: dict = None):
    """Call OpenViking HTTP API. Returns the unwrapped 'result' field."""
    url = f"{OV_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if OV_API_KEY:
        req.add_header("Authorization", f"Bearer {OV_API_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            envelope = json.loads(resp.read())
            # OpenViking wraps responses: {"status": "ok/error", "result": {...}, "error": {...}}
            if envelope.get("status") == "error" and envelope.get("error"):
                err = envelope["error"]
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                return {"error": msg}
            # Unwrap the result field so tool functions get the actual data
            return envelope.get("result") if envelope.get("result") is not None else {}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else str(e)
        return {"error": f"HTTP {e.code}: {error_body}"}


@mcp.tool()
def add_resource(path: str) -> str:
    """Add a file, directory, or URL to OpenViking for indexing.

    Args:
        path: Local file path, directory path, or URL to add.
    """
    result = _api("POST", "/api/v1/resources", {"path": path})
    if _has_error(result):
        return f"Error: {result['error']}"
    # Check for nested status error (e.g. unsupported files)
    if isinstance(result, dict) and result.get("status") == "error":
        errors = result.get("errors", [])
        return f"Error: {'; '.join(errors)}" if errors else f"Error: {result}"
    source = result.get("source_path", "") if isinstance(result, dict) else ""
    # Wait for processing
    _api("POST", "/api/v1/system/wait", {"timeout": 300})
    return f"Resource added and indexed: {source}"


@mcp.tool()
def search(query: str, top_k: int = 5, target_uri: str = "") -> str:
    """Semantic search the OpenViking database for relevant content.

    Args:
        query: The search query.
        top_k: Number of results to return (default: 5).
        target_uri: Optional URI to scope search (default: viking://resources/ for code).
    """
    body = {"query": query, "limit": top_k}
    if target_uri:
        body["target_uri"] = target_uri
    result = _api("POST", "/api/v1/search/search", body)
    if _has_error(result):
        return f"Error: {result['error']}"

    resources = result.get("resources", []) if isinstance(result, dict) else []
    memories = result.get("memories", []) if isinstance(result, dict) else []
    # Prioritize resources over memories
    items = resources + memories

    if not items:
        return "No relevant results found."

    parts = []
    for i, r in enumerate(items[:top_k], 1):
        uri = r.get("uri", "")
        score = r.get("score", 0)
        abstract_text = r.get("abstract", "")
        preview = abstract_text[:200] + "..." if len(abstract_text) > 200 else abstract_text
        parts.append(f"[{i}] {uri} (score: {score:.4f})\n{preview}")

    return f"Found {len(items)} results:\n\n" + "\n\n".join(parts)


@mcp.tool()
def find(query: str, top_k: int = 5, target_uri: str = "") -> str:
    """Quick semantic search (faster than search, less smart).

    Args:
        query: The search query.
        top_k: Number of results to return (default: 5).
        target_uri: Optional URI to scope search.
    """
    body = {"query": query, "limit": top_k}
    if target_uri:
        body["target_uri"] = target_uri
    result = _api("POST", "/api/v1/search/find", body)
    if _has_error(result):
        return f"Error: {result['error']}"

    resources = result.get("resources", []) if isinstance(result, dict) else []
    if not resources:
        return "No results found."

    parts = []
    for i, r in enumerate(resources[:top_k], 1):
        uri = r.get("uri", "")
        score = r.get("score", 0)
        parts.append(f"[{i}] {uri} (score: {score:.4f})")

    return f"Found {len(resources)} results:\n\n" + "\n".join(parts)


@mcp.tool()
def ls(uri: str = "viking://resources/") -> str:
    """List contents of a directory in OpenViking.

    Args:
        uri: Viking URI to list (default: viking://resources/).
    """
    result = _api("GET", f"/api/v1/fs/ls?uri={urllib.request.quote(uri)}")
    if _has_error(result):
        return f"Error: {result['error']}"
    # result can be a list (entries directly) or a dict
    entries = result if isinstance(result, list) else result.get("entries", []) if isinstance(result, dict) else []
    if not entries:
        return f"Empty directory: {uri}"
    lines = []
    for e in entries:
        kind = "DIR " if e.get("isDir") else "FILE"
        entry_uri = e.get("uri", "")
        name = entry_uri.split("/")[-1] or entry_uri
        abstract_text = e.get("abstract", "")
        preview = f" - {abstract_text[:80]}..." if abstract_text and len(abstract_text) > 80 else f" - {abstract_text}" if abstract_text else ""
        lines.append(f"  {kind} {name}{preview}")
    return f"{uri}\n" + "\n".join(lines)


@mcp.tool()
def read_content(uri: str) -> str:
    """Read content of a file in OpenViking.

    Args:
        uri: Viking URI of the file to read.
    """
    result = _api("GET", f"/api/v1/content/read?uri={urllib.request.quote(uri)}")
    if _has_error(result):
        return f"Error: {result['error']}"
    if isinstance(result, str):
        return result
    return result.get("content", str(result))


@mcp.tool()
def abstract(uri: str) -> str:
    """Get the L0 abstract summary of a resource or directory.

    Args:
        uri: Viking URI to get abstract for.
    """
    result = _api("GET", f"/api/v1/content/abstract?uri={urllib.request.quote(uri)}")
    if _has_error(result):
        return f"Error: {result['error']}"
    if isinstance(result, str):
        return result
    return result.get("abstract", str(result))


@mcp.tool()
def overview(uri: str) -> str:
    """Get the L1 overview of a resource or directory.

    Args:
        uri: Viking URI to get overview for.
    """
    result = _api("GET", f"/api/v1/content/overview?uri={urllib.request.quote(uri)}")
    if _has_error(result):
        return f"Error: {result['error']}"
    if isinstance(result, str):
        return result
    return result.get("overview", str(result))


@mcp.tool()
def grep(pattern: str, uri: str = "viking://resources/") -> str:
    """Search for text pattern in OpenViking resources.

    Args:
        pattern: Text pattern to search for.
        uri: Viking URI to search within (default: all resources).
    """
    result = _api("POST", "/api/v1/search/grep", {"pattern": pattern, "uri": uri})
    if _has_error(result):
        return f"Error: {result['error']}"
    matches = result.get("matches", []) if isinstance(result, dict) else result if isinstance(result, list) else []
    if not matches:
        return f"No matches for '{pattern}'"
    parts = []
    for m in matches[:20]:
        parts.append(f"  {m.get('uri', '')}: {m.get('line', '')}")
    return f"Found {len(matches)} matches:\n" + "\n".join(parts)


@mcp.tool()
def status() -> str:
    """Get OpenViking system status."""
    result = _api("GET", "/api/v1/system/status")
    if _has_error(result):
        return f"Error: {result['error']}"
    return json.dumps(result, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
