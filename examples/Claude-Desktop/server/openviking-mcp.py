"""
openviking-mcp.py — Alternative MCP server for OpenViking
Exposes 5 higher-level memory_* tools instead of the 12 low-level ov_* tools.
Use this if you prefer a simpler interface in Claude Desktop.

To use this instead of openviking-bridge.py, update claude_desktop_config.json
to point to this file instead.
"""
import sys
import json
import urllib.request
import urllib.error
import os

OV_URL     = "http://localhost:1933"
OV_API_KEY = os.environ.get("OV_API_KEY", "YOUR_LOCAL_API_KEY")
OV_USER    = "default"
OV_ACCOUNT = "default"

sys.stderr = open(os.devnull, "w")

HEADERS = {
    "Content-Type":        "application/json",
    "Authorization":       "Bearer " + OV_API_KEY,
    "x-api-key":           OV_API_KEY,
    "x-openviking-user":   OV_USER,
    "x-openviking-account": OV_ACCOUNT,
}


def ov_post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(OV_URL + path, data=data, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def safe_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError("HTTP " + str(e.code) + ": " + body)


TOOLS = [
    {
        "name": "memory_search",
        "description": "Search your OpenViking memory store for relevant context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for"},
                "limit": {"type": "integer", "description": "Max results (default 6)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_store",
        "description": "Store new information in OpenViking memory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Content to store"},
                "path":    {"type": "string", "description": "viking:// URI path to store at"},
            },
            "required": ["content", "path"],
        },
    },
    {
        "name": "memory_read",
        "description": "Read a specific resource from OpenViking by URI.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "uri": {"type": "string", "description": "viking:// URI to read"},
            },
            "required": ["uri"],
        },
    },
    {
        "name": "memory_list",
        "description": "List resources in OpenViking at a given path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "uri": {"type": "string", "description": "viking:// URI to list (default: viking://)"},
            },
            "required": [],
        },
    },
    {
        "name": "memory_health",
        "description": "Check OpenViking server health.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]


def call_tool(name, args):
    if name == "memory_health":
        req = urllib.request.Request(OV_URL + "/health", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    elif name == "memory_search":
        body = {"query": args.get("query", ""), "limit": args.get("limit", 6)}
        result = safe_call(ov_post, "/api/v1/search/find", body)
        # Flatten resources and memories into a single list
        r = (result.get("result") or result or {})
        items = (r.get("resources") or []) + (r.get("memories") or [])
        return {"items": items, "total": len(items)}
    elif name == "memory_store":
        body = {"path": args.get("path"), "content": args.get("content"), "wait": True}
        return safe_call(ov_post, "/api/v1/resources", body)
    elif name == "memory_read":
        uri = args.get("uri", "viking://")
        req = urllib.request.Request(
            OV_URL + "/api/v1/content/read?uri=" + urllib.request.quote(uri, safe=":/"),
            headers=HEADERS
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    elif name == "memory_list":
        uri = args.get("uri", "viking://")
        req = urllib.request.Request(
            OV_URL + "/api/v1/fs/ls?uri=" + urllib.request.quote(uri, safe=":/"),
            headers=HEADERS
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    else:
        raise RuntimeError("Unknown tool: " + name)


def handle(msg):
    method = msg.get("method", "")
    msg_id = msg.get("id")
    if msg_id is None:
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    try:
        if method == "initialize":
            return ok({
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "openviking-mcp", "version": "1.0.0"},
            })
        elif method == "tools/list":
            return ok({"tools": TOOLS})
        elif method == "tools/call":
            params    = msg.get("params", {})
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", params.get("input", {}))
            result_text = json.dumps(call_tool(tool_name, tool_args))
            return ok({"content": [{"type": "text", "text": result_text}], "isError": False})
        elif method == "ping":
            return ok({})
        else:
            return err(-32601, "Method not found: " + method)
    except Exception as e:
        return err(-32000, str(e))


def write(obj):
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main():
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                write({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error: " + str(e)}, "id": None})
                continue
            response = handle(msg)
            if response is not None:
                write(response)
        except KeyboardInterrupt:
            break
        except Exception:
            pass


if __name__ == "__main__":
    main()
