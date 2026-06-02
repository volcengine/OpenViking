"""
openviking-bridge.py - MCP stdio bridge for OpenViking REST API
Translates MCP JSON-RPC protocol into OpenViking HTTP calls.

Exposes 12 ov_* tools to Claude Desktop via stdio MCP protocol.
Configure OV_API_KEY to match server.root_api_key in your ov.conf.
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

# Redirect stderr to suppress Windows URI handler noise
sys.stderr = open(os.devnull, "w")

HEADERS = {
    "Content-Type":        "application/json",
    "Authorization":       "Bearer " + OV_API_KEY,
    "x-api-key":           OV_API_KEY,
    "x-openviking-user":   OV_USER,
    "x-openviking-account": OV_ACCOUNT,
}


def ov_get(path, params=None):
    url = OV_URL + path
    if params:
        qs = "&".join(k + "=" + urllib.request.quote(str(v)) for k, v in params.items())
        url = url + "?" + qs
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


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


# ---------------------------------------------------------------------------
# MCP tool definitions exposed to Claude Desktop
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "ov_health",
        "description": "Check if OpenViking server is healthy and ready.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ov_status",
        "description": "Get OpenViking system status.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ov_search",
        "description": "Semantic search across OpenViking knowledge base.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query":      {"type": "string",  "description": "Search query"},
                "target_uri": {"type": "string",  "description": "Scope URI (default: viking://)"},
                "limit":      {"type": "integer", "description": "Max results (default 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ov_find",
        "description": "Semantic search without session context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query":      {"type": "string",  "description": "Search query"},
                "target_uri": {"type": "string",  "description": "Scope URI"},
                "limit":      {"type": "integer", "description": "Max results (default 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ov_ls",
        "description": "List directory contents in the OpenViking filesystem.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "uri":       {"type": "string",  "description": "Viking URI (e.g. viking://)"},
                "recursive": {"type": "boolean", "description": "List recursively"},
            },
            "required": ["uri"],
        },
    },
    {
        "name": "ov_read",
        "description": "Read file content from OpenViking.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "uri":    {"type": "string",  "description": "Viking URI of the file"},
                "offset": {"type": "integer", "description": "Starting line (0-indexed)"},
                "limit":  {"type": "integer", "description": "Number of lines (-1 = all)"},
            },
            "required": ["uri"],
        },
    },
    {
        "name": "ov_add_resource",
        "description": "Add a file or directory as a resource to OpenViking.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path":   {"type": "string", "description": "Local file/folder path"},
                "reason": {"type": "string", "description": "Why this resource is being added"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "ov_mkdir",
        "description": "Create a directory in the OpenViking filesystem.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "uri": {"type": "string", "description": "Viking URI for the new directory"},
            },
            "required": ["uri"],
        },
    },
    {
        "name": "ov_create_session",
        "description": "Create a new OpenViking session for context tracking.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ov_add_message",
        "description": "Add a message to an OpenViking session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session ID"},
                "role":       {"type": "string", "description": "user or assistant"},
                "content":    {"type": "string", "description": "Message content"},
            },
            "required": ["session_id", "role", "content"],
        },
    },
    {
        "name": "ov_commit_session",
        "description": "Commit an OpenViking session to extract and store memories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session ID to commit"},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "ov_grep",
        "description": "Search file content by pattern in OpenViking.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "uri":              {"type": "string",  "description": "Viking URI"},
                "pattern":          {"type": "string",  "description": "Search pattern"},
                "case_insensitive": {"type": "boolean", "description": "Case insensitive"},
            },
            "required": ["uri", "pattern"],
        },
    },
]


def call_tool(name, args):
    if name == "ov_health":
        return json.dumps(safe_call(ov_get, "/health"))
    elif name == "ov_status":
        return json.dumps(safe_call(ov_get, "/api/v1/system/status"))
    elif name == "ov_search":
        body = {"query": args["query"]}
        if "target_uri" in args: body["target_uri"] = args["target_uri"]
        if "limit" in args:      body["limit"] = args["limit"]
        return json.dumps(safe_call(ov_post, "/api/v1/search/search", body))
    elif name == "ov_find":
        body = {"query": args["query"]}
        if "target_uri" in args: body["target_uri"] = args["target_uri"]
        if "limit" in args:      body["limit"] = args["limit"]
        return json.dumps(safe_call(ov_post, "/api/v1/search/find", body))
    elif name == "ov_ls":
        params = {"uri": args["uri"]}
        if args.get("recursive"): params["recursive"] = "true"
        return json.dumps(safe_call(ov_get, "/api/v1/fs/ls", params))
    elif name == "ov_read":
        params = {"uri": args["uri"]}
        if "offset" in args: params["offset"] = args["offset"]
        if "limit" in args:  params["limit"]  = args["limit"]
        return json.dumps(safe_call(ov_get, "/api/v1/content/read", params))
    elif name == "ov_add_resource":
        body = {"path": args["path"], "reason": args.get("reason", ""), "wait": True}
        return json.dumps(safe_call(ov_post, "/api/v1/resources", body))
    elif name == "ov_mkdir":
        return json.dumps(safe_call(ov_post, "/api/v1/fs/mkdir", {"uri": args["uri"]}))
    elif name == "ov_create_session":
        return json.dumps(safe_call(ov_post, "/api/v1/sessions"))
    elif name == "ov_add_message":
        return json.dumps(safe_call(
            ov_post,
            "/api/v1/sessions/" + args["session_id"] + "/messages",
            {"role": args["role"], "content": args["content"]},
        ))
    elif name == "ov_commit_session":
        return json.dumps(safe_call(ov_post, "/api/v1/sessions/" + args["session_id"] + "/commit"))
    elif name == "ov_grep":
        body = {
            "uri": args["uri"],
            "pattern": args["pattern"],
            "case_insensitive": args.get("case_insensitive", False),
        }
        return json.dumps(safe_call(ov_post, "/api/v1/search/grep", body))
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
                "serverInfo": {"name": "openviking-bridge", "version": "1.0.0"},
            })
        elif method == "tools/list":
            return ok({"tools": TOOLS})
        elif method == "tools/call":
            params    = msg.get("params", {})
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", params.get("input", {}))
            result_text = call_tool(tool_name, tool_args)
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
