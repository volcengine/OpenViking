# OpenViking WebSocket RPC Guide

This document explains how to call the OpenViking OpenClaw plugin through the OpenClaw Gateway WebSocket RPC surface.

The plugin does not start its own WebSocket server. OpenViking tools are registered through the OpenClaw plugin API, and the Gateway exposes them through standard tool RPC methods.

## Supported Flow

1. Connect to the OpenClaw Gateway WebSocket endpoint.
2. Call `tools.effective` for a real `sessionKey` to inspect tools available in the current session.
3. Call `tools.invoke` with an OpenViking tool name and JSON arguments.

Typical endpoint:

```text
ws://127.0.0.1:<gateway-port>
```

If TLS is enabled, use `wss://`.

## Connect

The first message is a `connect` request. Exact auth fields depend on the Gateway deployment.

```json
{
  "type": "req",
  "id": "connect-1",
  "method": "connect",
  "params": {
    "minProtocol": 3,
    "maxProtocol": 4,
    "client": {
      "id": "openviking-rpc-client",
      "version": "1.0.0",
      "platform": "macos",
      "mode": "operator"
    },
    "role": "operator",
    "scopes": ["operator.read", "operator.write"],
    "auth": {
      "token": "<OPENCLAW_GATEWAY_TOKEN>"
    },
    "locale": "zh-CN",
    "userAgent": "openviking-rpc-client/1.0.0"
  }
}
```

The Gateway returns `hello-ok` when the connection is accepted.

## Discover Tools

Use the current OpenClaw session key. Do not invent a synthetic session key for production debugging.

```json
{
  "type": "req",
  "id": "tools-1",
  "method": "tools.effective",
  "params": {
    "sessionKey": "main"
  }
}
```

OpenViking plugin tools are entries with `source="plugin"` and `pluginId="openviking"`.

## Invoke Tools

All OpenViking tools use `tools.invoke`.

```json
{
  "type": "req",
  "id": "invoke-1",
  "method": "tools.invoke",
  "params": {
    "name": "ov_search",
    "sessionKey": "main",
    "args": {
      "query": "OpenViking installation",
      "limit": 5
    }
  }
}
```

`params.sessionKey` is the Gateway/session routing field. It tells OpenClaw which session context the tool call belongs to.

`params.args.sessionKey` is a tool argument only when a specific OpenViking tool defines it. For example, `ov_recall_trace` can use it as an explicit trace filter. For the current session's trace, pass only the outer `params.sessionKey` unless you intentionally want a different filter.

## Common Tools

### `ov_search`

Search OpenViking resources, skills, and memories.

```json
{
  "name": "ov_search",
  "sessionKey": "main",
  "args": {
    "query": "runtime query config",
    "limit": 5,
    "uri": "viking://resources"
  }
}
```

### `ov_read`

Read an exact `viking://` URI.

```json
{
  "name": "ov_read",
  "sessionKey": "main",
  "args": {
    "uri": "viking://resources/project/spec.md"
  }
}
```

### `ov_multi_read`

Read multiple exact URIs in one tool call.

```json
{
  "name": "ov_multi_read",
  "sessionKey": "main",
  "args": {
    "uris": [
      "viking://resources/project/spec.md",
      "viking://resources/project/faq.md"
    ]
  }
}
```

### `memory_recall`

Recall semantic memories and resources. Current semantic recall target types are `user`, `agent`, and `resource`. Session history is not a vector recall target; use `ov_archive_search` and `ov_archive_expand` for archived session history.

```json
{
  "name": "memory_recall",
  "sessionKey": "main",
  "args": {
    "query": "what did we decide about install verification",
    "limit": 5,
    "resourceTypes": ["user", "agent", "resource"]
  }
}
```

### `ov_recall_trace`

Inspect recall traces when `traceRecall` is enabled.

```json
{
  "name": "ov_recall_trace",
  "sessionKey": "main",
  "args": {
    "turn": "latest",
    "limit": 5,
    "includeContent": false
  }
}
```

## Response Shape

Successful tool invocation usually returns a Gateway response whose payload contains the plugin tool output.

```json
{
  "type": "res",
  "id": "invoke-1",
  "ok": true,
  "payload": {
    "ok": true,
    "toolName": "ov_search",
    "source": "plugin",
    "output": {
      "content": [
        {
          "type": "text",
          "text": "Found 2 OpenViking results ..."
        }
      ],
      "details": {
        "action": "searched",
        "total": 2
      }
    }
  }
}
```

If the Gateway accepted the RPC request but the tool failed, the outer `ok` can still be `true` while `payload.ok` is `false`.

```json
{
  "type": "res",
  "id": "invoke-1",
  "ok": true,
  "payload": {
    "ok": false,
    "toolName": "ov_search",
    "error": {
      "code": "not_found",
      "message": "Tool not available: ov_search"
    }
  }
}
```

## Notes

- Use `tools.effective` before invoking a tool in a live session.
- Use exact `viking://` URIs with `ov_read` and `ov_multi_read`.
- Do not use deprecated agent URI paths for memory routing. Current routing is based on OpenViking context type and actor peer identity.
- For recall trace HTTP routes, see `openviking-recall-trace-api.md`.
