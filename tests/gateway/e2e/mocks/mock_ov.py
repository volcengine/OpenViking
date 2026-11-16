"""OpenViking fixture with per-library data and inspectable fault controls.

GET /_records exposes requests and libraries. POST /_fail accepts on and status;
/_revoke and /_unorder accept key. /_auth_mode accepts mode; /_tool_delay accepts
seconds. Every library deliberately identifies as default/default.
"""

import argparse
import asyncio
import copy
import json
import os
import time

from aiohttp import web

KEYS = {"key-a": "library-a", "key-a-new": "library-a", "key-b": "library-b"}
TOOLS = [
    {
        "name": "search",
        "description": "Search this library's memories.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }
]


def ok(value):
    return web.json_response({"status": "ok", "result": value})


def failure(status, code):
    return web.json_response({"status": "error", "error": {"code": code}}, status=status)


def create_app():
    requests, libraries = [], {}
    revoked, unordered = set(), set()
    control = {"auth_mode": "api_key", "fail": None, "tool_delay": 0}

    def library(name):
        return libraries.setdefault(
            name, {"sessions": {}, "recalls": [], "tool_calls": [], "archives": {}}
        )

    async def controls(request):
        if request.path == "/_records":
            return web.json_response({"requests": requests, "libraries": libraries})
        if request.method != "POST":
            raise web.HTTPMethodNotAllowed(request.method, ["POST"])
        body = await request.json() if request.can_read_body else {}
        if request.path == "/_fail":
            control["fail"] = int(body.get("status", 503)) if body.get("on", True) else None
        elif request.path == "/_revoke":
            revoked.add(body["key"])
        elif request.path == "/_unorder":
            unordered.add(body["key"])
        elif request.path == "/_auth_mode":
            control["auth_mode"] = body["mode"]
        elif request.path == "/_tool_delay":
            control["tool_delay"] = float(body["seconds"])
        else:
            raise web.HTTPNotFound()
        return web.json_response({"status": "ok", **control})

    @web.middleware
    async def record_and_auth(request, handler):
        if request.path.startswith("/_"):
            return await controls(request)
        body = await request.json() if request.can_read_body else {}
        key = request.headers.get("X-API-Key", "")
        name = KEYS.get(key)
        record = {
            "source_pod": request.remote,
            "key": key,
            "method": request.method,
            "path": request.path,
            "body": body,
            "status": None,
        }
        requests.append(record)
        request["record"], request["body"] = record, body
        fault = control["fail"]
        known_path = request.path in {
            "/health",
            "/mcp",
            "/api/v1/system/status",
            "/api/v1/skills",
            "/api/v1/fs/ls",
            "/api/v1/content/read",
            "/api/v1/search/search",
            "/api/v1/sessions",
        } or request.path.startswith("/api/v1/sessions/")
        if fault:
            response = failure(fault, "UNAVAILABLE")
        elif not known_path:
            response = failure(404, "NOT_FOUND")
        elif control["auth_mode"] != "dev" and (key in revoked or (not name and key)):
            response = failure(401, "UNAUTHENTICATED")
        elif control["auth_mode"] != "dev" and key in unordered and request.path == "/health":
            response = failure(402, "ProductUnordered")
        elif not name and not key and request.path != "/health" and control["auth_mode"] != "dev":
            response = failure(401, "UNAUTHENTICATED")
        else:
            request["library"] = library(name or "library-a")
            request["library_name"] = name or "library-a"
            try:
                response = await handler(request)
            except web.HTTPException as error:
                record["status"] = error.status
                raise
        record["status"] = response.status
        return response

    def recall_result(request, body):
        name = request["library_name"]
        entry = {
            "uri": f"viking://user/default/memories/{name}.md",
            "text": f"Memory from {name}: blue cluster.",
            "score": 1.0,
        }
        entries = [] if entry["uri"] in body.get("exclude_uris", []) else [entry]
        return {
            "entries": entries,
            "rendered": "\n\n".join(e["uri"] + "\n" + e["text"] for e in entries),
        }

    async def handle(request):
        path, body, data = request.path, request["body"], request["library"]
        if path == "/health":
            result = {
                "status": "ok",
                "healthy": True,
                "version": "0.5.2",
                "auth_mode": control["auth_mode"],
            }
            if KEYS.get(request["record"]["key"]):
                result.update(account_id="default", user_id="default", role="admin")
            return web.json_response(result)
        if path == "/api/v1/system/status":
            return ok({"user": "default"})
        if path == "/api/v1/skills":
            return ok({"skills": []})
        if path == "/api/v1/fs/ls":
            return ok(
                [{"name": "default", "isDir": True}]
                if request.query.get("uri") == "viking://user"
                else []
            )
        if path == "/api/v1/search/search":
            result = recall_result(request, body)
            data["recalls"].append({**request["record"], "result": result, "status": 200})
            return ok(result)
        if path == "/api/v1/sessions" and request.method == "POST":
            sid = body["session_id"]
            if sid in data["sessions"]:
                return failure(409, "CONFLICT")
            data["sessions"][sid] = {
                "session_id": sid,
                "messages": [],
                "pending": [],
                "commits": [],
            }
            return ok({"session_id": sid})
        if path.startswith("/api/v1/sessions/"):
            parts = path.split("/")
            sid = parts[4]
            session = data["sessions"].get(sid)
            if session is None:
                return failure(404, "NOT_FOUND")
            if path.endswith("/messages/batch") and request.method == "POST":
                session["messages"].extend(copy.deepcopy(body["messages"]))
                session["pending"].extend(copy.deepcopy(body["messages"]))
                return ok(
                    {
                        "added": len(body["messages"]),
                        "pending_tokens": len(json.dumps(session["pending"])) // 3,
                    }
                )
            if path.endswith("/commit") and request.method == "POST":
                keep = max(0, int(body.get("keep_recent_count", 0)))
                archived = session["pending"][:-keep] if keep else session["pending"][:]
                session["pending"] = session["pending"][-keep:] if keep else []
                uri = f"viking://user/default/sessions/{sid}/history/archive_{len(session['commits']) + 1:03d}"
                commit = {
                    "archive_uri": uri,
                    "messages": archived,
                    "keep_recent_count": keep,
                    "key": request["record"]["key"],
                    "time": time.time(),
                }
                session["commits"].append(commit)
                data["archives"][uri] = archived
                return ok({"archive_uri": uri})
            if request.method == "GET":
                return ok(
                    {
                        "session_id": sid,
                        "pending_tokens": len(json.dumps(session["pending"])) // 3
                        if session["pending"]
                        else 0,
                        "messages": session["pending"],
                    }
                )
        if path == "/api/v1/content/read":
            uri = request.query.get("uri", "")
            if uri.removesuffix("/.done") in data["archives"] and uri.endswith("/.done"):
                return ok("")
            if uri in data["archives"]:
                return ok(json.dumps(data["archives"][uri]))
            return failure(404, "NOT_FOUND")
        if path == "/mcp":
            method, params = body.get("method"), body.get("params", {})
            if method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                call = {
                    **request["record"],
                    "name": params.get("name"),
                    "arguments": params.get("arguments", {}),
                }
                data["tool_calls"].append(call)
                await asyncio.sleep(control["tool_delay"])
                if params.get("name") != "search":
                    return web.json_response(
                        {
                            "jsonrpc": "2.0",
                            "id": body.get("id"),
                            "error": {"code": -32602, "message": "Unknown tool"},
                        }
                    )
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": recall_result(request, params.get("arguments", {}))["rendered"],
                        }
                    ],
                    "isError": False,
                }
            elif method == "initialize":
                result = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mock-ov", "version": "1"},
                }
            else:
                return web.json_response(
                    {
                        "jsonrpc": "2.0",
                        "id": body.get("id"),
                        "error": {"code": -32601, "message": "Method not found"},
                    }
                )
            return web.json_response({"jsonrpc": "2.0", "id": body.get("id"), "result": result})
        raise web.HTTPNotFound()

    app = web.Application(middlewares=[record_and_auth], client_max_size=32 * 1024**2)
    app.router.add_route("*", "/{path:.*}", handle)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "1933")))
    args = parser.parse_args()
    web.run_app(create_app(), host=args.host, port=args.port, access_log=None)
