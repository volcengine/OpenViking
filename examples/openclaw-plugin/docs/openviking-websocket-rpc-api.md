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

## 本机验证记录（2026-06-05）

本节保留 #2613 中的 WebSocket RPC 实测记录和可复制命令，但按当前主线语义调整：

- 不使用旧 `agent_prefix` / `X-OpenViking-Agent` / `viking://agent/...` 路径。
- OpenViking 插件仍通过 OpenClaw Gateway 暴露工具，插件自身不启动 WebSocket server。
- 语义召回目标使用 `user`、`agent`、`resource`；session 历史不作为 vector recall target，应走 `ov_archive_search` / `ov_archive_expand`。
- 当前主线通过 OpenViking `context_type` 与 `X-OpenViking-Actor-Peer` 做检索和 actor peer 路由。

验证环境：

- OpenClaw：`2026.5.28`
- Gateway 节点：`SuperOpsByteDance.local`，macOS gateway mode
- CLI：`openclaw gateway call <method> --params '<json>' --json`

### Gateway RPC 基础接口

| ID | 方法 | 参数 | 预期 | 实测结果 | 结论 |
|---|---|---|---|---|---|
| WS-RPC-01 | `health` | `{}` | Gateway 健康检查成功 | `ok=true`、`runtimeVersion=2026.5.28`、`eventLoop.degraded=false` | 通过 |
| WS-RPC-02 | `status` | `{}` | 返回运行时和 session 状态 | `defaultAgentId=main`、`mainHeartbeatEnabled=true`、`eventLoopDegraded=false` | 通过 |
| WS-RPC-03 | `system-presence` | `{}` | 返回当前 Gateway/CLI presence | 返回 macOS gateway 节点与 CLI probe 节点 | 通过 |
| WS-RPC-04 | `tools.catalog` | `{"agentId":"main"}` | agent 工具目录包含 OpenViking 工具 | `group_count=14`、OpenViking 工具可见 | 通过 |
| WS-RPC-05 | `tools.effective` | `{"sessionKey":"<真实 sessionKey>"}` | 当前 session 可用工具包含 OpenViking 工具 | `agentId=main`、`profile=full`、OpenViking 工具可见 | 通过 |

常见 OpenViking 工具包括：

```text
add_skill, memory_forget, memory_recall, memory_store,
openviking_tool_result_list, openviking_tool_result_read, openviking_tool_result_search,
ov_archive_expand, ov_archive_search, ov_list, ov_multi_read, ov_read, ov_recall_trace, ov_search
```

注意：`add_resource` 默认不是 agent-visible tool；只有配置 `enableAddResourceTool=true` 并且工具策略允许时才会出现在 agent 工具集中。手动导入仍可走 slash command `/add-resource`。

### 获取真实 sessionKey

线上排障时不要人为构造 `sessionKey` 来代表真实会话。优先使用 OpenClaw 当前状态或调用方上下文里的真实 session key。

```bash
SK="$(openclaw status --json | jq -r '
  .sessionKey //
  .session.key //
  .currentSession.key //
  .current_session.key //
  empty
')"

if [ -z "$SK" ]; then
  echo "未从 openclaw status --json 取到 sessionKey" >&2
  openclaw status --json | jq .
  exit 1
fi

echo "$SK"
```

### OpenViking 工具调用

#### `ov_search`

```bash
PARAMS="$(jq -cn \
  --arg sk "$SK" \
  '{
    name: "ov_search",
    sessionKey: $sk,
    args: {
      query: "openclaw plugin config",
      limit: 2
    }
  }'
)"

openclaw gateway call tools.invoke \
  --params "$PARAMS" \
  --json | jq .
```

预期：

- `payload.ok=true`
- `toolName=ov_search`
- 返回 `viking://resources/...`、`viking://user/skills/...` 或 memory 相关结果，取决于当前 query config / target URI

#### `ov_read`

```bash
URI="viking://resources/openclaw-plugin-config/.abstract.md"

PARAMS="$(jq -cn \
  --arg sk "$SK" \
  --arg uri "$URI" \
  '{
    name: "ov_read",
    sessionKey: $sk,
    args: {
      uri: $uri
    }
  }'
)"

openclaw gateway call tools.invoke \
  --params "$PARAMS" \
  --json | jq .
```

`ov_read` 只接受完整 `viking://` URI。不要传带 `...` 或 `…` 的展示截断 URI，也不要把 `viking://` URI 当成本地文件路径交给文件读取工具。

#### `memory_recall`

```bash
PARAMS="$(jq -cn \
  --arg sk "$SK" \
  '{
    name: "memory_recall",
    sessionKey: $sk,
    args: {
      query: "openclaw plugin config",
      limit: 2,
      resourceTypes: ["resource"]
    }
  }'
)"

openclaw gateway call tools.invoke \
  --params "$PARAMS" \
  --json | jq .
```

当前主线支持的 semantic recall target types：

| target | 说明 |
|---|---|
| `user` | 用户/peer 相关长期记忆 |
| `agent` | 兼容默认 memory context search 语义；当前实现不会恢复旧 `viking://agent/...` 路径 |
| `resource` | 资源知识库内容 |

`session` 不作为 semantic recall target。要查 session 历史，请使用：

- `ov_archive_search`
- `ov_archive_expand`

#### `ov_archive_search`

```bash
PARAMS="$(jq -cn \
  --arg sk "$SK" \
  '{
    name: "ov_archive_search",
    sessionKey: $sk,
    args: {
      query: "tcpdump"
    }
  }'
)"

openclaw gateway call tools.invoke \
  --params "$PARAMS" \
  --json | jq .
```

#### `ov_archive_expand`

```bash
PARAMS="$(jq -cn \
  --arg sk "$SK" \
  '{
    name: "ov_archive_expand",
    sessionKey: $sk,
    args: {
      archiveId: "archive_003"
    }
  }'
)"

openclaw gateway call tools.invoke \
  --params "$PARAMS" \
  --json | jq .
```

### Trace RPC 专项验证

以下命令均通过 `tools.invoke` 调用 OpenViking 工具 `ov_recall_trace`。

外层 `params.sessionKey` 是 Gateway 的执行上下文，也是默认 trace 查询身份。`args.sessionKey` 只用于“按 trace 记录里的 sessionKey 精确过滤”的场景；日常排查当前 session 时，优先只传外层 `sessionKey`。

```bash
# 1. 查询最新 trace
openclaw gateway call tools.invoke \
  --params "$(jq -cn --arg sk "$SK" '{name:"ov_recall_trace",sessionKey:$sk,args:{turn:"latest",limit:5}}')" \
  --json | jq .

# 2. 按 source 查询 ov_search trace
openclaw gateway call tools.invoke \
  --params "$(jq -cn --arg sk "$SK" '{name:"ov_recall_trace",sessionKey:$sk,args:{turn:"all",source:"ov_search",limit:10}}')" \
  --json | jq .

# 3. 按 traceId 精确查询
TRACE_ID="ov_search-1780635606119-h2fl11l5"
openclaw gateway call tools.invoke \
  --params "$(jq -cn --arg sk "$SK" --arg trace "$TRACE_ID" '{name:"ov_recall_trace",sessionKey:$sk,args:{traceId:$trace,limit:1}}')" \
  --json | jq .

# 4. 按 traceId 查询并展开 selected 内容预览
openclaw gateway call tools.invoke \
  --params "$(jq -cn --arg sk "$SK" --arg trace "$TRACE_ID" '{name:"ov_recall_trace",sessionKey:$sk,args:{traceId:$trace,includeContent:true,limit:1}}')" \
  --json | jq .

# 5. 按当前 sessionKey 过滤 trace
openclaw gateway call tools.invoke \
  --params "$(jq -cn --arg sk "$SK" '{name:"ov_recall_trace",sessionKey:$sk,args:{turn:"all",sessionKey:$sk,limit:20}}')" \
  --json | jq .

# 6. 查询不存在的 traceId，验证空结果边界
openclaw gateway call tools.invoke \
  --params "$(jq -cn --arg sk "$SK" '{name:"ov_recall_trace",sessionKey:$sk,args:{traceId:"not-exist-trace-20260605",limit:1}}')" \
  --json | jq .

# 7. 查询不匹配的 sessionKey，验证空结果边界
openclaw gateway call tools.invoke \
  --params "$(jq -cn --arg sk "$SK" '{name:"ov_recall_trace",sessionKey:$sk,args:{turn:"all",sessionKey:"agent:main:no-trace-session-20260605",limit:5}}')" \
  --json | jq .

# 8. 查询不匹配的 source，验证空结果边界
openclaw gateway call tools.invoke \
  --params "$(jq -cn --arg sk "$SK" '{name:"ov_recall_trace",sessionKey:$sk,args:{turn:"all",source:"not_a_source",limit:5}}')" \
  --json | jq .

# 9. limit=0 边界验证
openclaw gateway call tools.invoke \
  --params "$(jq -cn --arg sk "$SK" '{name:"ov_recall_trace",sessionKey:$sk,args:{turn:"all",limit:0}}')" \
  --json | jq .
```

| ID | 场景 | 参数摘要 | 预期 / 实测关注点 | 结论 |
|---|---|---|---|---|
| WS-RPC-10 | 最新 trace | `turn=latest`、`limit=5` | 返回最近 trace；无 trace 时 `count=0` 不算失败 | 通过 |
| WS-RPC-11 | source 过滤 | `turn=all`、`source=ov_search` | 只返回 `ov_search` trace | 通过 |
| WS-RPC-12 | traceId 精确查询 | `traceId=<id>`、`limit=1` | 返回指定 trace 或空结果 | 通过 |
| WS-RPC-13 | includeContent | `includeContent=true` | selected 项可包含内容预览 | 通过 |
| WS-RPC-14 | sessionKey 过滤 | `args.sessionKey=$SK` | 返回该 session 的 trace | 通过 |
| WS-RPC-15 | 不存在 traceId | `traceId=not-exist...` | `ok=true`、`count=0`、`entries=[]` | 通过 |
| WS-RPC-16 | 不匹配 sessionKey | `sessionKey=no-trace...` | `ok=true`、`count=0`、`entries=[]` | 通过 |
| WS-RPC-17 | 不匹配 source | `source=not_a_source` | `ok=true`、`count=0`、`entries=[]` | 通过 |
| WS-RPC-18 | `limit=0` 边界 | `turn=all`、`limit=0` | Gateway 不异常；按工具默认/边界策略返回 | 通过 |

`ov_recall_trace` 的 RPC 响应中，关键结构位于 `output.details`：

```json
{
  "action": "queried",
  "count": 1,
  "lookupLayer": "memory|persistent",
  "warnings": [],
  "entries": ["RecallTraceEntry"]
}
```

无匹配数据时不视为调用失败，而是返回：

```json
{
  "ok": true,
  "toolName": "ov_recall_trace",
  "output": {
    "details": {
      "count": 0,
      "entries": []
    }
  }
}
```

### 边界用例

| ID | 场景 | 参数 / 命令 | 预期 | 结论 |
|---|---|---|---|---|
| WS-RPC-19 | 未知 RPC method | `openclaw gateway call does.not.exist --params '{}' --json` | Gateway 返回 unknown method | 通过 |
| WS-RPC-20 | 不存在 session key | `tools.effective` + 不存在的 `sessionKey` | Gateway 返回 unknown session key | 通过 |
| WS-RPC-21 | 不存在工具 | `tools.invoke` + `name=not_a_tool` | `payload.ok=false`、`error.code=not_found` | 通过 |
| WS-RPC-22 | 缺少 `ov_search.query` | `tools.invoke` + `name=ov_search` + `args={"limit":2}` | 工具参数错误，RPC 不应导致 Gateway 崩溃 | 通过 |
| WS-RPC-23 | `ov_read` 非法 URI | `tools.invoke` + `name=ov_read` + `args={"uri":"not-viking"}` | 工具返回参数错误 | 通过 |

### 常见问题

#### `tools.invoke` 返回 `Tool not available`

检查：

1. OpenViking 插件是否安装并启用。
2. Gateway 是否已重启。
3. `openclaw.plugin.json` 中工具 contract 是否包含该工具。
4. 插件配置 `enabledTools` / `disabledTools` 是否过滤了该工具。
5. Gateway 工具策略是否允许该工具。
6. `add_resource` 是否已设置 `enableAddResourceTool=true`。

#### `ov_read` 报 URI 无效

`ov_read` 只接受完整 `viking://` URI。不要传带 `...` 或 `…` 的展示截断 URI。

#### `ov_recall_trace` 查询为空

优先确认：

1. 是否开启 `traceRecall`。
2. 是否使用真实外层 `sessionKey`。
3. 是否错误叠加了 `args.sessionKey`、`sessionId`、`ovSessionId` 等过滤条件。
4. `traceRecallPersist` 开启时，持久化目录下是否存在对应 JSONL。
5. 查询窗口是否被 `since`、`until`、`limit` 或 retention 配置截断。
