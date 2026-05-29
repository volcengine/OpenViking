#!/usr/bin/env python3
"""Check that MCP add_resource does not ingest direct server-local paths.

The script connects to a running OpenViking /mcp endpoint, initializes an MCP
session, and calls add_resource(path=<local path>). A fixed server should return
upload instructions or an explicit local-path rejection. A vulnerable server
returns "Resource added" and ingests the local file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx


SAFE_MARKERS = (
    "upload required",
    "temp_upload_signed",
    "direct host filesystem paths are not allowed",
    "only accepts remote resource URLs",
)
VULNERABLE_MARKERS = ("Resource added", "root_uri")


def _decode_mcp_response(resp: httpx.Response) -> dict[str, Any] | None:
    if not resp.content:
        return None

    text = resp.text.strip()
    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type or text.startswith("event:"):
        events: list[dict[str, Any]] = []
        for block in text.split("\n\n"):
            data_lines = [
                line[len("data:") :].strip()
                for line in block.splitlines()
                if line.startswith("data:")
            ]
            if not data_lines:
                continue
            data = "\n".join(data_lines)
            if data == "[DONE]":
                continue
            events.append(json.loads(data))
        return events[-1] if events else None

    return resp.json()


def _headers(args: argparse.Namespace, session_id: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    api_key = args.api_key or os.environ.get("OPENVIKING_API_KEY")
    if api_key:
        headers["X-API-Key"] = api_key
    if args.account:
        headers["X-OpenViking-Account"] = args.account
    if args.user:
        headers["X-OpenViking-User"] = args.user
    if args.agent:
        headers["X-OpenViking-Agent"] = args.agent
    if session_id:
        headers["mcp-session-id"] = session_id
    return headers


def _text_from_result(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    if "error" in payload:
        return json.dumps(payload["error"], ensure_ascii=False)

    result = payload.get("result")
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)

    chunks: list[str] = []
    for item in result.get("content", []) or []:
        if isinstance(item, dict) and item.get("type") == "text":
            chunks.append(str(item.get("text", "")))
    if chunks:
        return "\n".join(chunks)
    return json.dumps(result, ensure_ascii=False)


def _post(
    client: httpx.Client,
    args: argparse.Namespace,
    payload: dict[str, Any],
    session_id: str | None = None,
) -> tuple[dict[str, Any] | None, httpx.Response]:
    resp = client.post(
        args.base_url.rstrip("/") + "/mcp",
        headers=_headers(args, session_id),
        json=payload,
    )
    resp.raise_for_status()
    return _decode_mcp_response(resp), resp


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:1933")
    parser.add_argument("--api-key", default=None, help="Defaults to OPENVIKING_API_KEY.")
    parser.add_argument("--account", default="default")
    parser.add_argument("--user", default="default")
    parser.add_argument("--agent", default="default")
    parser.add_argument(
        "--path",
        default="/app/.openviking/ov.conf",
        help="Server-local path to probe. Use the running server's ov.conf path for a strong check.",
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {
                "name": "openviking-mcp-add-resource-local-path-check",
                "version": "1.0.0",
            },
        },
    }
    initialized_payload = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }
    call_payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "add_resource",
            "arguments": {"path": args.path},
        },
    }

    try:
        with httpx.Client(timeout=args.timeout) as client:
            init_result, init_resp = _post(client, args, init_payload)
            session_id = init_resp.headers.get("mcp-session-id")
            if not session_id:
                print("ERROR: initialize did not return mcp-session-id", file=sys.stderr)
                print(json.dumps(init_result, ensure_ascii=False, indent=2), file=sys.stderr)
                return 2

            _post(client, args, initialized_payload, session_id)
            call_result, _ = _post(client, args, call_payload, session_id)
    except httpx.HTTPStatusError as exc:
        print(f"ERROR: HTTP {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    result_text = _text_from_result(call_result)
    print(result_text)

    if any(marker in result_text for marker in VULNERABLE_MARKERS):
        print("FAIL: MCP add_resource ingested a direct local path.", file=sys.stderr)
        return 1

    if any(marker in result_text for marker in SAFE_MARKERS):
        print("PASS: MCP add_resource did not ingest the direct local path.")
        return 0

    print("ERROR: Unexpected add_resource response; unable to classify.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
