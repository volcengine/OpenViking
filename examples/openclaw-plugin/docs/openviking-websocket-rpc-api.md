# OpenViking WebSocket RPC 调用指南

本文说明如何通过 OpenClaw Gateway 的 WebSocket RPC 访问 `@openviking/openclaw-plugin` 提供的 OpenViking 能力。

## 当前支持情况

当前代码**已支持**通过 WebSocket RPC 调用 OpenViking 工具，不需要插件额外启动 WebSocket server：

- 插件在 `index.ts` 中通过 `api.registerTool(...)` 注册工具。
- OpenClaw Gateway 会把已注册工具纳入运行时工具清单。
- 外部客户端通过 Gateway WebSocket RPC 的 `tools.invoke` 调用这些工具。

也就是说，WebSocket 入口属于 OpenClaw Gateway；OpenViking 插件自身仍是 OpenViking Server 的 HTTP 客户端。

## 连接与鉴权

连接到 Gateway 的 WebSocket 地址，通常是：

```text
ws://127.0.0.1:<gateway-port>
```

如果 Gateway 开启 TLS，则使用：

```text
wss://<gateway-host>:<gateway-port>
```

第一个客户端消息必须是 `connect`：

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
    "caps": [],
    "commands": [],
    "permissions": {},
    "auth": {
      "token": "<OPENCLAW_GATEWAY_TOKEN>"
    },
    "locale": "zh-CN",
    "userAgent": "openviking-rpc-client/1.0.0"
  }
}
```

成功后 Gateway 返回：

```json
{
  "type": "res",
  "id": "connect-1",
  "ok": true,
  "payload": {
    "type": "hello-ok",
    "protocol": 4,
    "features": {
      "methods": ["tools.effective", "tools.invoke"]
    }
  }
}
```

调用 OpenViking 工具需要：

- `operator.read`：查询工具清单，例如 `tools.effective`。
- `operator.write`：执行工具，例如 `tools.invoke`。

## 发现 OpenViking 工具

先用 `tools.effective` 查看当前 session 可用工具：

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

返回的 `payload.groups[].tools[]` 中，`source="plugin"` 且 `pluginId="openviking"` 的条目就是 OpenViking 插件工具。

也可以用 `tools.catalog` 查看 agent 的工具目录：

```json
{
  "type": "req",
  "id": "catalog-1",
  "method": "tools.catalog",
  "params": {
    "agentId": "main"
  }
}
```

## 通用调用格式

所有 OpenViking 工具都通过 `tools.invoke` 调用：

```json
{
  "type": "req",
  "id": "invoke-1",
  "method": "tools.invoke",
  "params": {
    "name": "ov_search",
    "sessionKey": "main",
    "args": {
      "query": "OpenViking 安装文档",
      "limit": 5
    }
  }
}
```

这里有两个容易混淆的 session 字段：

- `params.sessionKey`：`tools.invoke` 的外层参数，由 Gateway 使用，用来决定本次工具调用运行在哪个 OpenClaw session 上，并给插件工具提供运行时上下文。
- `params.args.sessionKey`：只有少数工具自己的业务参数会使用。对 `ov_recall_trace` 来说，它是显式 trace 查询过滤条件，只返回 trace 记录中 `entry.sessionKey` 完全等于该值的数据。

因此，查询当前 session 或按同一个 session key 查询历史 trace 时，只传外层 `params.sessionKey` 即可。插件会优先用外层 `sessionKey` 作为默认 trace 身份过滤条件，不会再叠加由 `sessionKey` 推导出的 `ovSessionId`。只有需要查另一个历史 session、或需要做边界验证时，才在 `params.args.sessionKey` 里传显式过滤条件。

线上排障时不要人为构造 `sessionKey` 来代表真实会话；应优先使用 OpenClaw 当前状态或调用方上下文里的真实 `sessionKey`。例如验证安装成功后的 trace 查询链路：

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

PARAMS="$(jq -cn \
  --arg sk "$SK" \
  '{
    name: "ov_recall_trace",
    sessionKey: $sk,
    args: {
      turn: "all",
      limit: 5
    }
  }'
)"

openclaw gateway call tools.invoke \
  --params "$PARAMS" \
  --json | jq .
```

兼容性说明：旧版本曾只按 `args.sessionKey` 或由外层 `sessionKey` 派生出的 `ovSessionId` 查询；当线上 JSONL 中实际保存的是 `sessionId/ovSessionId` UUID，而查询端派生的是 `sha256(sessionKey)` 时会返回空。当前版本默认用外层 `params.sessionKey` 匹配 `entry.sessionKey`，未命中时再兼容 fallback 到当前 session 的 `sessionId/ovSessionId`，避免真实 web session 查询不到历史 trace。

典型返回：

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

如果工具不可用、被策略禁用或参数不合法，Gateway 仍可能返回 `type="res"`，但 `payload.ok=false`：

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

## 工具接口

### `memory_recall`

显式召回长期记忆、session 历史和资源知识。

```json
{
  "type": "req",
  "id": "memory-recall-1",
  "method": "tools.invoke",
  "params": {
    "name": "memory_recall",
    "sessionKey": "main",
    "args": {
      "query": "用户偏好的后端语言是什么",
      "limit": 5,
      "scoreThreshold": 0.2,
      "resourceTypes": ["user", "agent", "resource", "session"]
    }
  }
}
```

参数：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | 是 | 召回查询文本 |
| `limit` | number | 否 | 最终返回条数，默认使用插件配置 |
| `scoreThreshold` | number | 否 | 最低分数，范围 0-1 |
| `targetUri` | string | 否 | 指定单一搜索范围，例如 `viking://user/memories` |
| `resourceTypes` | string[] | 否 | 未指定 `targetUri` 时使用，支持 `resource`、`session`、`user`、`agent` |

### `memory_store`

把文本写入 OpenViking session，并立即触发记忆抽取。

```json
{
  "type": "req",
  "id": "memory-store-1",
  "method": "tools.invoke",
  "params": {
    "name": "memory_store",
    "sessionKey": "main",
    "args": {
      "text": "用户偏好使用 TypeScript 编写 OpenClaw 插件。",
      "role": "user"
    }
  }
}
```

参数：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `text` | string | 是 | 作为记忆来源的文本 |
| `role` | string | 否 | session 消息角色，默认 `user` |
| `sessionId` | string | 否 | 指定已有 OpenViking session；不传则使用临时 session |

### `memory_forget`

删除记忆。可以传精确 URI，也可以先按 query 搜索候选。

按 URI 删除：

```json
{
  "type": "req",
  "id": "memory-forget-1",
  "method": "tools.invoke",
  "params": {
    "name": "memory_forget",
    "sessionKey": "main",
    "args": {
      "uri": "viking://user/default/memories/memory_123"
    }
  }
}
```

按 query 查找候选：

```json
{
  "type": "req",
  "id": "memory-forget-2",
  "method": "tools.invoke",
  "params": {
    "name": "memory_forget",
    "sessionKey": "main",
    "args": {
      "query": "偏好 Python 后端",
      "targetUri": "viking://user/memories",
      "limit": 5,
      "scoreThreshold": 0.85
    }
  }
}
```

参数：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `uri` | string | 否 | 精确记忆 URI，仅允许 `viking://user/.../memories` 或 `viking://agent/.../memories` |
| `query` | string | 否 | 未提供 `uri` 时用于搜索候选 |
| `targetUri` | string | 否 | 搜索范围，默认使用插件配置 |
| `limit` | number | 否 | 候选展示数量，默认 5 |
| `scoreThreshold` | number | 否 | 候选最低分数 |

### `ov_search`

搜索 OpenViking resources 和 skills。

```json
{
  "type": "req",
  "id": "ov-search-1",
  "method": "tools.invoke",
  "params": {
    "name": "ov_search",
    "sessionKey": "main",
    "args": {
      "query": "安装 OpenViking 插件",
      "uri": "viking://resources",
      "limit": 10
    }
  }
}
```

参数：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | 是 | 搜索文本 |
| `uri` | string | 否 | 搜索范围；不传时并行搜索 `viking://resources` 和 `viking://agent/skills` |
| `limit` | number | 否 | 每个范围最多返回数量，默认 10 |

### `ov_read`

读取 `ov_search`、`memory_recall` 或 trace 返回的完整 `viking://` URI 内容。

```json
{
  "type": "req",
  "id": "ov-read-1",
  "method": "tools.invoke",
  "params": {
    "name": "ov_read",
    "sessionKey": "main",
    "args": {
      "uri": "viking://resources/project-docs/install.md"
    }
  }
}
```

参数：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `uri` | string | 是 | 完整 `viking://` URI，不能传省略号截断后的展示 URI |

### `add_skill`

导入 Agent Skill 到 `viking://agent/skills/...`。

```json
{
  "type": "req",
  "id": "add-skill-1",
  "method": "tools.invoke",
  "params": {
    "name": "add_skill",
    "sessionKey": "main",
    "args": {
      "source": "/absolute/path/to/my-skill",
      "wait": true,
      "timeout": 120
    }
  }
}
```

也可以传原始 skill 内容或 MCP tool dict：

```json
{
  "type": "req",
  "id": "add-skill-2",
  "method": "tools.invoke",
  "params": {
    "name": "add_skill",
    "sessionKey": "main",
    "args": {
      "data": "# My Skill\n\nSkill content...",
      "wait": true
    }
  }
}
```

参数：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source` | string | 二选一 | 本地 `SKILL.md` 文件或 skill 目录 |
| `data` | any | 二选一 | 原始 `SKILL.md` 内容或 MCP tool dict |
| `wait` | boolean | 否 | 是否等待服务端处理完成 |
| `timeout` | number | 否 | `wait=true` 时的超时时间，单位秒 |

### `add_resource`

导入文档、目录、URL 或 Git 仓库到 OpenViking resources。

注意：该工具默认不暴露给 Agent，必须在插件配置中设置 `enableAddResourceTool=true`，并且工具策略允许它，才能通过 `tools.invoke` 调用。未启用时可使用 slash command `/add-resource`。

```json
{
  "type": "req",
  "id": "add-resource-1",
  "method": "tools.invoke",
  "params": {
    "name": "add_resource",
    "sessionKey": "main",
    "args": {
      "source": "/absolute/path/to/docs",
      "parent": "viking://resources/project-docs",
      "reason": "导入项目文档",
      "instruction": "保留 API 示例和配置说明",
      "wait": true,
      "timeout": 300
    }
  }
}
```

参数：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source` | string | 是 | 本地文件、目录、OpenClaw media path、公开 URL 或 Git URL |
| `to` | string | 否 | 精确目标 URI，不能和 `parent` 同时使用 |
| `parent` | string | 否 | 父级 URI，不能和 `to` 同时使用 |
| `reason` | string | 否 | 导入原因或说明 |
| `instruction` | string | 否 | 服务端处理指令 |
| `wait` | boolean | 否 | 是否等待服务端处理完成 |
| `timeout` | number | 否 | `wait=true` 时的超时时间，单位秒 |

### `ov_archive_search`

在当前 session 已归档的原始消息中做关键词 grep。

```json
{
  "type": "req",
  "id": "archive-search-1",
  "method": "tools.invoke",
  "params": {
    "name": "ov_archive_search",
    "sessionKey": "main",
    "args": {
      "query": "tcpdump",
      "archiveId": "archive_003"
    }
  }
}
```

参数：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | 是 | 单个关键词或短语 |
| `archiveId` | string | 否 | 限定某个 archive，例如 `archive_003` |

### `ov_archive_expand`

展开某个归档，读取原始消息。

```json
{
  "type": "req",
  "id": "archive-expand-1",
  "method": "tools.invoke",
  "params": {
    "name": "ov_archive_expand",
    "sessionKey": "main",
    "args": {
      "archiveId": "archive_003"
    }
  }
}
```

参数：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `archiveId` | string | 是 | archive ID，例如 `archive_003` |

### `ov_recall_trace`

查询自动召回、显式召回和 `ov_search` 的 trace。

```json
{
  "type": "req",
  "id": "recall-trace-1",
  "method": "tools.invoke",
  "params": {
    "name": "ov_recall_trace",
    "sessionKey": "main",
    "args": {
      "turn": "latest",
      "source": "ov_search",
      "includeContent": true,
      "limit": 10
    }
  }
}
```

参数：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `turn` | string | 否 | `latest` 或 `all`，默认 `latest` |
| `traceId` | string | 否 | 精确 trace id |
| `sessionId` | string | 否 | OpenClaw session id |
| `sessionKey` | string | 否 | OpenClaw session key |
| `ovSessionId` | string | 否 | OpenViking session id |
| `source` | string | 否 | `auto_recall`、`memory_recall`、`ov_search` 或 `ov_archive_search` |
| `resourceTypes` | string[] | 否 | `resource`、`session`、`user`、`agent` |
| `since` | number | 否 | Unix timestamp 毫秒下界 |
| `until` | number | 否 | Unix timestamp 毫秒上界 |
| `includeContent` | boolean | 否 | 是否按需读取 URI 内容预览 |
| `limit` | number | 否 | 最多返回 trace 数量，默认 20 |

### `openviking_tool_result_list`

列出当前 session 中被 OpenViking 外置的大工具结果。

```json
{
  "type": "req",
  "id": "tool-result-list-1",
  "method": "tools.invoke",
  "params": {
    "name": "openviking_tool_result_list",
    "sessionKey": "main",
    "args": {
      "tool_name": "RunCommand",
      "limit": 20
    }
  }
}
```

参数：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tool_name` | string | 否 | 按工具名过滤，也兼容 `toolName` |
| `limit` | number | 否 | 最多返回数量，默认 50 |

### `openviking_tool_result_search`

在外置的大工具结果中搜索关键词。

```json
{
  "type": "req",
  "id": "tool-result-search-1",
  "method": "tools.invoke",
  "params": {
    "name": "openviking_tool_result_search",
    "sessionKey": "main",
    "args": {
      "tool_output_ref": "viking://session/<session_id>/tool-results/<tool_result_id>",
      "query": "error",
      "limit": 10,
      "context_chars": 300
    }
  }
}
```

参数：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tool_output_ref` | string | 是 | `viking://session/.../tool-results/...` URI，也兼容 `ref` 或 `uri` |
| `query` | string | 是 | 搜索关键词或精确文本 |
| `limit` | number | 否 | 最多匹配数，默认 20 |
| `context_chars` | number | 否 | 每个命中周围保留字符数，默认 300，也兼容 `contextChars` |

### `openviking_tool_result_read`

分页读取外置的大工具结果完整内容。

```json
{
  "type": "req",
  "id": "tool-result-read-1",
  "method": "tools.invoke",
  "params": {
    "name": "openviking_tool_result_read",
    "sessionKey": "main",
    "args": {
      "tool_output_ref": "viking://session/<session_id>/tool-results/<tool_result_id>",
      "offset": 0,
      "limit": 20000
    }
  }
}
```

参数：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tool_output_ref` | string | 是 | `viking://session/.../tool-results/...` URI，也兼容 `ref` 或 `uri` |
| `offset` | number | 否 | Unicode 字符偏移，默认 0 |
| `limit` | number | 否 | 最多读取字符数，默认 20000；`-1` 表示由服务端决定 |

## Slash command 与 HTTP route

插件还注册了 slash command：

- `/add-resource`
- `/add-skill`
- `/ov-search`
- `/ov-recall-trace`
- `/ov-query-config`

这些命令主要给对话内使用。外部自动化应优先使用 `tools.invoke` 调对应工具。

插件还注册了部分 Gateway HTTP route：

- `GET /api/openviking/recall-traces`
- `GET /api/openviking/recall-traces/<traceId>`
- `GET /api/openviking/uri-detail`
- `GET /api/openviking/recall-traces/latest-ov-search-list`

这些是 HTTP route，不是 WebSocket RPC 方法。通过 WebSocket 获取相同核心数据时，优先调用 `ov_recall_trace`、`ov_search` 和 `ov_read`。

## 本机验证记录（2026-06-05）

验证环境：

- OpenClaw：`2026.5.28`
- Gateway 节点：`SuperOpsByteDance.local`，macOS gateway mode
- 测试 session key：`agent:main:ov-install-verify-jsonl-20260605`
- CLI：`openclaw gateway call <method> --params '<json>' --json`

### Gateway RPC 基础接口

| ID | 方法 | 参数 | 预期 | 实测结果 | 结论 |
|---|---|---|---|---|---|
| WS-RPC-01 | `health` | `{}` | Gateway 健康检查成功 | `ok=true`、`runtimeVersion=2026.5.28`、`eventLoop.degraded=false` | 通过 |
| WS-RPC-02 | `status` | `{}` | 返回运行时和 session 状态 | `defaultAgentId=main`、`mainHeartbeatEnabled=true`、`eventLoopDegraded=false` | 通过 |
| WS-RPC-03 | `system-presence` | `{}` | 返回当前 Gateway/CLI presence | 返回 macOS gateway 节点与 CLI probe 节点 | 通过 |
| WS-RPC-04 | `tools.catalog` | `{"agentId":"main"}` | agent 工具目录包含 OpenViking 工具 | `group_count=14`、OpenViking 工具 12 个 | 通过 |
| WS-RPC-05 | `tools.effective` | `{"sessionKey":"agent:main:ov-install-verify-jsonl-20260605"}` | 当前 session 可用工具包含 OpenViking 工具 | `agentId=main`、`profile=full`、`group_count=2`、OpenViking 工具 12 个 | 通过 |

已确认 OpenViking 工具清单：

```text
add_skill, memory_forget, memory_recall, memory_store,
openviking_tool_result_list, openviking_tool_result_read, openviking_tool_result_search,
ov_archive_expand, ov_archive_search, ov_read, ov_recall_trace, ov_search
```

### OpenViking 工具调用

| ID | 方法 | 工具 | 参数摘要 | 实测结果 | 结论 |
|---|---|---|---|---|---|
| WS-RPC-06 | `tools.invoke` | `ov_search` | `query="openclaw plugin config"`、`limit=2` | `ok=true`，返回 2 条 resource，首条 `viking://resources/openclaw-plugin-config/.abstract.md` | 通过 |
| WS-RPC-07 | `tools.invoke` | `ov_read` | `uri="viking://resources/openclaw-plugin-config/.abstract.md"` | `ok=true`，返回 OpenViking content，文本长度 335 | 通过 |
| WS-RPC-08 | `tools.invoke` | `memory_recall` | `query="openclaw plugin config"`、`limit=2`、`resourceTypes=["resource"]` | `ok=true`、`toolName=memory_recall`、`total=19` | 通过 |
| WS-RPC-09 | `tools.invoke` | `ov_recall_trace` | `turn="latest"`、`limit=5` | `ok=true`，返回 `memory_recall-1780635643409-v243scn9` | 通过 |

### Trace RPC 专项验证

以下命令均可在本机直接粘贴执行，用于通过 OpenClaw Gateway WebSocket RPC 验证 trace 相关接口。命令统一通过 `tools.invoke` 调用 OpenViking 工具 `ov_recall_trace`。

注意：示例里的外层 `sessionKey` 是 Gateway 的执行上下文，也是默认 trace 查询身份。`args.sessionKey` 只用于第 5、7 条这类“按 trace 记录里的 sessionKey 精确过滤”的场景；日常排查空结果时，优先只传外层 `sessionKey`。如果 `grep ~/.openclaw/openviking/recall-traces/*.jsonl` 能按该值命中，`ov_recall_trace` 默认也应能查到。

```bash
# 1. 查询最新 trace
openclaw gateway call tools.invoke \
  --params '{"name":"ov_recall_trace","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","args":{"turn":"latest","limit":5}}' \
  --json

# 2. 按 source 查询 ov_search trace
openclaw gateway call tools.invoke \
  --params '{"name":"ov_recall_trace","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","args":{"turn":"all","source":"ov_search","limit":10}}' \
  --json

# 3. 按 traceId 精确查询
openclaw gateway call tools.invoke \
  --params '{"name":"ov_recall_trace","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","args":{"traceId":"ov_search-1780635606119-h2fl11l5","limit":1}}' \
  --json

# 4. 按 traceId 查询并展开 selected 内容预览
openclaw gateway call tools.invoke \
  --params '{"name":"ov_recall_trace","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","args":{"traceId":"ov_search-1780635606119-h2fl11l5","includeContent":true,"limit":1}}' \
  --json

# 5. 按 sessionKey 过滤 trace
openclaw gateway call tools.invoke \
  --params '{"name":"ov_recall_trace","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","args":{"turn":"all","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","limit":20}}' \
  --json

# 6. 查询不存在的 traceId，验证空结果边界
openclaw gateway call tools.invoke \
  --params '{"name":"ov_recall_trace","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","args":{"traceId":"not-exist-trace-20260605","limit":1}}' \
  --json

# 7. 查询不匹配的 sessionKey，验证空结果边界
openclaw gateway call tools.invoke \
  --params '{"name":"ov_recall_trace","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","args":{"turn":"all","sessionKey":"agent:main:no-trace-session-20260605","limit":5}}' \
  --json

# 8. 查询不匹配的 source，验证空结果边界
openclaw gateway call tools.invoke \
  --params '{"name":"ov_recall_trace","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","args":{"turn":"all","source":"not_a_source","limit":5}}' \
  --json

# 9. limit=0 边界验证
openclaw gateway call tools.invoke \
  --params '{"name":"ov_recall_trace","sessionKey":"agent:main:ov-install-verify-jsonl-20260605","args":{"turn":"all","limit":0}}' \
  --json
```

| ID | 场景 | 参数摘要 | 实测结果 | 结论 |
|---|---|---|---|---|
| WS-RPC-10 | 最新 trace | `ov_recall_trace` + `turn=latest`、`limit=5` | `count=1`、`lookupLayer=memory`、返回 `memory_recall-1780635643409-v243scn9` | 通过 |
| WS-RPC-11 | source 过滤 | `ov_recall_trace` + `turn=all`、`source=ov_search`、`limit=10` | `count=1`、返回 `ov_search-1780635606119-h2fl11l5` | 通过 |
| WS-RPC-12 | traceId 精确查询 | `ov_recall_trace` + `traceId=ov_search-1780635606119-h2fl11l5`、`limit=1` | `count=1`、`source=ov_search`、`stats.selectedCount=2` | 通过 |
| WS-RPC-13 | includeContent | `ov_recall_trace` + `traceId=...`、`includeContent=true`、`limit=1` | `lookupLayer=persistent`，`selected[0]` 包含 `contentPreview` | 通过 |
| WS-RPC-14 | sessionKey 过滤 | `ov_recall_trace` + `sessionKey=agent:main:ov-install-verify-jsonl-20260605`、`turn=all` | `count=1`，返回该 session 最近 trace | 通过 |
| WS-RPC-15 | 不存在 traceId | `ov_recall_trace` + `traceId=not-exist-trace-20260605` | `ok=true`、`count=0`、`entries=[]` | 通过 |
| WS-RPC-16 | 不匹配 sessionKey | `ov_recall_trace` + `sessionKey=agent:main:no-trace-session-20260605` | `ok=true`、`count=0`、`entries=[]` | 通过 |
| WS-RPC-17 | 不匹配 source | `ov_recall_trace` + `source=not_a_source` | `ok=true`、`count=0`、`entries=[]` | 通过 |
| WS-RPC-18 | `limit=0` 边界 | `ov_recall_trace` + `turn=all`、`limit=0` | `ok=true`，返回最近 1 条 trace，Gateway 未异常 | 通过 |

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

| ID | 场景 | 参数/命令 | 实测结果 | 结论 |
|---|---|---|---|---|
| WS-RPC-19 | 未知 RPC method | `openclaw gateway call does.not.exist --params '{}' --json` | `GatewayClientRequestError: unknown method: does.not.exist` | 通过 |
| WS-RPC-20 | 不存在 session key | `tools.effective` + `sessionKey=agent:main:not-exists-openviking-rpc-20260605` | `GatewayClientRequestError: unknown session key ...` | 通过 |
| WS-RPC-21 | 不存在工具 | `tools.invoke` + `name=not_a_tool` | `ok=false`、`error.code=not_found` | 通过 |
| WS-RPC-22 | 缺少 `ov_search.query` | `tools.invoke` + `name=ov_search` + `args={"limit":2}` | `ok=false`、`error.code=internal_error` | 通过 |
| WS-RPC-23 | `ov_read` 非法 URI | `tools.invoke` + `name=ov_read` + `args={"uri":"not-viking"}` | `ok=false`、`error.code=internal_error` | 通过 |

注意：`tools.effective` 必须传入 Gateway 已知的 session key；如果只是查看某个 agent 的完整工具目录，应优先使用 `tools.catalog`。

## 常见问题

### `tools.invoke` 返回 `Tool not available`

检查：

1. OpenViking 插件是否安装并启用。
2. Gateway 是否已重启。
3. `openclaw.plugin.json` 中工具是否在 contracts 里。
4. 插件配置 `enabledTools` / `disabledTools` 是否过滤了该工具。
5. Gateway 工具策略是否允许该工具。
6. `add_resource` 是否已设置 `enableAddResourceTool=true`。

### `ov_read` 报 URI 无效

`ov_read` 只接受完整 `viking://` URI。不要传带 `...` 或 `…` 的展示截断 URI。

### 工具结果读取报 session mismatch

`openviking_tool_result_read/search/list` 只允许读取当前 session 的外置工具结果。请用同一个 `sessionKey` 调用，并确认 ref 中的 session id 与当前 session 对应的 OpenViking session 一致。
