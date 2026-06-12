# OpenViking OpenClaw 插件参考文档

> 本文档汇总当前分支提供的插件配置参数、安装/配置命令、Slash 命令、Agent 可见 Tools、Gateway API 以及插件调用的 OpenViking 后端 API。适用于接入、运维、排障和二次开发。

## 1. 插件入口与运行结构

OpenViking 插件以 OpenClaw context-engine plugin 方式运行：

- 插件入口：`dist/index.js`
- Setup CLI 入口：`dist/commands/setup.js`
- 插件 ID：`openviking`
- Context engine slot：`plugins.slots.contextEngine = "openviking"`

运行期主要能力分为 6 层：

1. **配置层**：解析 `plugins.entries.openviking.config`，并支持部分环境变量兜底。
2. **Context Engine 层**：负责 session assemble、afterTurn 写入、compact、auto-recall、auto-capture。
3. **Agent Tools 层**：向模型暴露记忆、资源查询、归档检索、工具结果恢复等工具。
4. **Slash Commands 层**：向用户暴露 `/add-resource`、`/add-skill`、`/ov-search`、`/ov-recall-trace`。
5. **Gateway API 层**：向外提供 recall trace 查询接口。
6. **OpenViking Client 层**：封装 OpenViking Server HTTP API。

## 2. 配置文件位置与基本结构

默认配置文件：

```text
~/.openclaw/openclaw.json
```

如果设置了 `OPENCLAW_STATE_DIR`，则使用：

```text
$OPENCLAW_STATE_DIR/openclaw.json
```

典型配置结构：

```json
{
  "plugins": {
    "slots": {
      "contextEngine": "openviking"
    },
    "entries": {
      "openviking": {
        "config": {
          "mode": "remote",
          "baseUrl": "http://127.0.0.1:1933",
          "apiKey": "${OPENVIKING_API_KEY}",
          "autoCapture": true,
          "autoRecall": true,
          "recallTargetTypes": ["user", "agent"]
        }
      }
    }
  }
}
```

## 3. 配置参数参考

### 3.1 连接与认证

| 参数 | 类型 | 默认值 | 环境变量 | 说明 |
| --- | --- | --- | --- | --- |
| `mode` | string | `"remote"` | — | 当前仅支持远程模式。旧的 local mode 会被迁移到 remote。 |
| `baseUrl` | string | `http://127.0.0.1:1933` | `OPENVIKING_BASE_URL` / `OPENVIKING_URL` | OpenViking Server HTTP 地址；末尾 `/` 会自动去掉。 |
| `apiKey` | string | 空 | `OPENVIKING_API_KEY` | OpenViking API Key；请求时写入 `X-API-Key`。 |
| `accountId` | string | 空 | `OPENVIKING_ACCOUNT_ID` | 高级租户路由字段；请求时写入 `X-OpenViking-Account`。Root key 或 trusted 部署通常需要。 |
| `userId` | string | 空 | `OPENVIKING_USER_ID` | 高级租户路由字段；请求时写入 `X-OpenViking-User`。Root key 或 trusted 部署通常需要。 |
| `timeoutMs` | number | `15000` | — | OpenViking HTTP 请求超时，最低会 clamp 到 `1000`。 |

### 3.2 Peer 与检索隔离

| 参数 | 类型 | 默认值 | 环境变量 | 说明 |
| --- | --- | --- | --- | --- |
| `peer_role` | `"none"` \| `"assistant"` \| `"person"` | `"none"` | — | 控制 session message 和 recall/search 请求是否写入 `peer_id`。 |
| `peer_prefix` | string | 空 | — | `peer_role=assistant` 时 assistant `peer_id` 的可选前缀；仅允许字母、数字、`_`、`-`。 |

### 3.3 自动捕获与提交

| 参数 | 类型 | 默认值 | 环境变量 | 说明 |
| --- | --- | --- | --- | --- |
| `autoCapture` | boolean | `true` | — | 是否在会话过程中自动将消息写入 OpenViking session 并触发记忆抽取。 |
| `captureMode` | `"semantic"` \| `"keyword"` | `"semantic"` | — | 捕获模式。非法值会导致配置解析失败。 |
| `captureMaxLength` | number | `24000` | — | 自动捕获文本最大长度，范围 `200` 到 `200000`。 |
| `commitTokenThreshold` | number | `20000` | — | afterTurn 中 pending tokens 达到阈值后触发 commit；`0` 表示每轮都提交。 |
| `commitKeepRecentCount` | number | `10` | — | afterTurn commit 后保留最近消息数，范围 `0` 到 `1000`。compact 路径始终使用 `0`。 |

### 3.4 自动召回与显式召回

| 参数 | 类型 | 默认值 | 环境变量 | 说明 |
| --- | --- | --- | --- | --- |
| `autoRecall` | boolean | `true` | — | 是否在 assemble 阶段自动召回并注入上下文。 |
| `targetUri` | string | `viking://user/memories` | — | `memory_recall` / `memory_forget` 默认搜索范围。 |
| `recallTargetTypes` | string[] | `["user", "agent"]` | — | 自动召回和默认 `memory_recall` 的搜索类型。允许 `resource`、`user`、`agent`。 |
| `recallResources` | boolean | `false` | `OPENVIKING_RECALL_RESOURCES` | 旧兼容开关；仅在未显式配置 `recallTargetTypes` 时追加 `resource`。 |
| `recallLimit` | number | `6` | — | 最终召回条数下限为 `1`。内部请求通常放大为 `max(limit * 4, 20)`。 |
| `recallScoreThreshold` | number | `0.15` | — | 召回结果分数阈值，范围 `0` 到 `1`。 |
| `recallMaxInjectedChars` | number | `4000` | — | 自动召回注入模型上下文的总字符预算，范围 `100` 到 `50000`。 |
| `recallPreferAbstract` | boolean | `false` | — | 是否优先使用 abstract，减少读取完整内容的成本。 |
| `recallMaxContentChars` | number | `5000` | — | 已废弃兼容项。 |
| `recallTokenBudget` | number | 跟随 `recallMaxInjectedChars` | — | 已废弃别名；未配置 `recallMaxInjectedChars` 时可作为 fallback。 |

### 3.5 Recall Trace

| 参数 | 类型 | 默认值 | 环境变量 | 说明 |
| --- | --- | --- | --- | --- |
| `traceRecall` | boolean | `false` | — | 是否记录 recall/search trace。 |
| `traceRecallPersist` | boolean | `false` | — | 是否将 trace 写入本地 JSONL。 |
| `traceRecallDir` | string | `~/.openclaw/openviking/recall-traces` | — | trace 文件目录，支持 `~` 展开。 |
| `traceRecallRetentionDays` | number | `14` | — | 持久化 trace 保留天数，范围 `1` 到 `3650`。 |
| `traceRecallLoadRecentDays` | number | `2` | — | 启动时预加载最近 trace 天数，范围 `0` 到 `3650`。 |
| `traceRecallMaxEntries` | number | `1000` | — | 内存 ring buffer 最大条数，范围 `1` 到 `1000000`。 |
| `traceRecallMaxResultsPerSearch` | number | `20` | — | 每个子搜索最多记录候选数，范围 `1` 到 `1000`。 |
| `traceRecallPreviewChars` | number | `240` | — | trace 预览字符数，范围 `20` 到 `10000`。 |
| `traceRecallQueryMaxChars` | number | `4000` | — | trace 中保存 query 的最大字符数，范围 `200` 到 `200000`。 |
| `traceRecallQueryMaxDays` | number | `14` | — | 查询持久化 trace 时默认最多扫描天数，范围 `1` 到 `3650`。 |
| `traceRecallIncludeContentByDefault` | boolean | `false` | — | 查询 trace 时默认是否读取 selected URI 的内容预览。 |
| `traceRecallIncludeRawUserPreview` | boolean | `false` | — | 是否允许把原始用户输入预览持久化。默认关闭以降低隐私风险。 |

### 3.6 诊断、绕过与工具开关

| 参数 | 类型 | 默认值 | 环境变量 | 说明 |
| --- | --- | --- | --- | --- |
| `bypassSessionPatterns` | string[] \| string | `[]` | — | 匹配 sessionId / sessionKey 后绕过 OpenViking 链路；支持 `*` 和 `**`。 |
| `emitStandardDiagnostics` | boolean | `false` | — | 是否输出标准诊断日志。 |
| `logFindRequests` | boolean | `false` | `OPENVIKING_LOG_ROUTING` / `OPENVIKING_DEBUG` | 打印 find/session/commit 路由日志，不打印 API Key。 |
| `enableAddResourceTool` | boolean | `false` | — | Agent 可见 `add_resource` 的二级开关；手动 `/add-resource` 不受影响。 |
| `enabledTools` | string[] \| string | 默认 12 个 tools；若 `enableAddResourceTool=true` 则默认追加 `add_resource` | — | Agent 可见工具白名单，支持工具名或分组。 |
| `disabledTools` | string[] \| string | `[]`；当 `enableAddResourceTool=false` 时解析结果会包含 `add_resource` | — | Agent 可见工具黑名单，在 `enabledTools` 之后应用。 |

## 4. Agent Tools 开关

### 4.1 工具分组

`enabledTools` / `disabledTools` 支持以下分组：

| 分组 | 包含工具 |
| --- | --- |
| `default` | 默认 12 个 Agent tools。 |
| `all` | `add_resource` + 默认 12 个 Agent tools。注意 `add_resource` 仍需 `enableAddResourceTool=true`。 |
| `memory` | `memory_recall`、`memory_store`、`memory_forget`。 |
| `resource_query` | `ov_search`、`ov_read`。 |
| `import` | `add_resource`、`add_skill`。 |
| `recall_trace` | `ov_recall_trace`。 |
| `archive` | `ov_archive_search`、`ov_archive_expand`。 |
| `tool_result` | `openviking_tool_result_read`、`openviking_tool_result_search`、`openviking_tool_result_list`。 |

### 4.2 只保留资源查询工具

适用于“禁用记忆，但允许 Agent 查询 OpenViking 知识库资源”的场景：

```json
{
  "autoCapture": false,
  "autoRecall": false,
  "enabledTools": ["resource_query"]
}
```

注册结果：

- `ov_search`
- `ov_read`

不会注册：

- `memory_recall`
- `memory_store`
- `memory_forget`
- 其他默认 tools

### 4.3 保留默认工具但禁用记忆 Tools

```json
{
  "disabledTools": ["memory"]
}
```

会禁用：

- `memory_recall`
- `memory_store`
- `memory_forget`

保留默认工具中的资源查询、归档、trace、tool result 能力。

### 4.4 启用 `add_resource` Agent Tool

`add_resource` 需要双重 opt-in：

```json
{
  "enabledTools": ["add_resource"],
  "enableAddResourceTool": true
}
```

如果配置为：

```json
{
  "enabledTools": ["all"]
}
```

但没有设置 `enableAddResourceTool=true`，`add_resource` 仍不会注册。

## 5. Setup / CLI 命令

### 5.1 `openclaw openviking setup`

用途：配置插件连接 OpenViking Server，并激活 context-engine slot。

```bash
openclaw openviking setup [options]
```

参数：

| 参数 | 说明 |
| --- | --- |
| `--reconfigure` | 强制重新录入已有配置。 |
| `--zh` | 使用中文提示。 |
| `--base-url <url>` | OpenViking Server URL。传入后进入非交互模式。 |
| `--api-key <key>` | API Key。 |
| `--agent-prefix <prefix>` | Agent 路由前缀。 |
| `--account-id <id>` | Root API Key 场景下的 Account ID。 |
| `--user-id <id>` | Root API Key 场景下的 User ID。 |
| `--recall-target-types <types>` | 逗号分隔的召回类型，例如 `resource` 或 `user,agent,resource`。 |
| `--allow-offline` | 即使 Server 不可达也写入配置。 |
| `--force-slot` | 如果 contextEngine slot 已被其他插件占用，强制替换。 |
| `--json` | 输出 JSON。非交互模式下推荐使用；如果未传 `--base-url`，`--json` 会报错。 |

常见示例：

```bash
openclaw openviking setup --base-url http://127.0.0.1:1933 --api-key sk-xxx --json
```

Root key 场景：

```bash
openclaw openviking setup \
  --base-url http://127.0.0.1:1933 \
  --api-key root-xxx \
  --account-id acc_123 \
  --user-id user_456 \
  --json
```

只召回资源：

```bash
openclaw openviking setup \
  --base-url http://127.0.0.1:1933 \
  --api-key sk-xxx \
  --recall-target-types resource \
  --json
```

Server 暂不可达但仍写配置：

```bash
openclaw openviking setup \
  --base-url http://127.0.0.1:1933 \
  --api-key sk-xxx \
  --allow-offline \
  --json
```

替换已有 context-engine slot owner：

```bash
openclaw openviking setup \
  --base-url http://127.0.0.1:1933 \
  --api-key sk-xxx \
  --force-slot \
  --json
```

### 5.2 `openclaw openviking status`

用途：查看当前配置、连接状态、slot 是否激活。

```bash
openclaw openviking status [--zh] [--json]
```

参数：

| 参数 | 说明 |
| --- | --- |
| `--zh` | 使用中文输出。 |
| `--json` | 输出 JSON。 |

示例：

```bash
openclaw openviking status --json
```

### 5.3 Runtime Slash Alias

Manifest 中声明了 runtime slash alias：

| Alias | CLI 映射 | 说明 |
| --- | --- | --- |
| `setup` | `openviking setup` | 打开配置向导或执行配置。 |
| `status` | `openviking status` | 查看状态。 |

具体可用性取决于当前 OpenClaw runtime 是否支持该 alias 类型。

## 6. Slash Commands

### 6.1 `/add-resource`

用途：手动把文件、目录、URL、Git 仓库或 OpenClaw media attachment 导入 OpenViking resources。

```text
/add-resource <source> [--to URI] [--parent URI] [--reason TEXT] [--instruction TEXT] [--wait] [--timeout SEC]
```

参数：

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `<source>` | 是 | 本地文件、目录、URL、Git URL 或 media attachment 路径。 |
| `--to URI` | 否 | 目标 resource URI。 |
| `--parent URI` | 否 | 父目录 URI。不能和 `--to` 同时使用。 |
| `--reason TEXT` | 否 | 导入原因。 |
| `--instruction TEXT` | 否 | 导入处理指令。 |
| `--wait` | 否 | 等待服务端处理完成。 |
| `--timeout SEC` | 否 | `--wait` 时的等待超时秒数。 |

示例：

```text
/add-resource ./README.md --to viking://resources/project-readme --reason "project docs" --wait
```

注意：`/add-resource` 是手动命令，不受 `enableAddResourceTool=false` 限制。

### 6.2 `/add-skill`

用途：手动导入 `SKILL.md` 文件或 skill 目录。

```text
/add-skill <source> [--wait] [--timeout SEC]
```

参数：

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `<source>` | 是 | `SKILL.md` 文件或 skill 目录。 |
| `--wait` | 否 | 等待服务端处理完成。 |
| `--timeout SEC` | 否 | `--wait` 时的等待超时秒数。 |

示例：

```text
/add-skill ./skills/my-skill --wait --timeout 30
```

### 6.3 `/ov-search`

用途：搜索 OpenViking resources 和 skills。

```text
/ov-search <query> [--uri URI] [--limit N]
```

参数：

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `<query>` | 是 | 搜索 query。支持多词 query。 |
| `--uri URI` | 否 | 搜索目标 URI。未指定时默认搜索 resources 和 user skills。 |
| `--limit N` | 否 | 每个搜索范围返回条数，默认 `10`。 |

示例：

```text
/ov-search "OpenViking install" --uri viking://resources --limit 5
```

返回的 `viking://...` 是 OpenViking 虚拟 URI，不是本地文件路径。如需读取完整内容，请使用 `ov_read` Agent Tool。

### 6.4 `/ov-recall-trace`

用途：查询 recall trace，排查 auto-recall、`memory_recall`、`ov_search`、`ov_archive_search` 的召回链路。

```text
/ov-recall-trace [--turn latest|all] [--trace-id ID] [--session-id ID] [--session-key KEY] [--ov-session-id ID] [--source SOURCE] [--resource-types TYPES] [--since TS] [--until TS] [--include-content] [--limit N]
```

参数：

| 参数 | 说明 |
| --- | --- |
| `--turn latest|all` | 查询最近一轮或全部，默认通常为 latest。 |
| `--trace-id ID` | 精确 trace id。 |
| `--session-id ID` | OpenClaw session id。 |
| `--session-key KEY` | OpenClaw session key。 |
| `--ov-session-id ID` | OpenViking session id。 |
| `--source SOURCE` | `auto_recall`、`memory_recall`、`ov_search`、`ov_archive_search`。 |
| `--resource-types TYPES` | 逗号分隔资源类型，如 `resource,user`。 |
| `--since TS` | 毫秒时间戳下界。 |
| `--until TS` | 毫秒时间戳上界。 |
| `--include-content` | 查询时读取 selected/displayed URI 内容预览。 |
| `--limit N` | 最大返回 trace 数量。 |

示例：

```text
/ov-recall-trace --source ov_search --include-content --limit 10
```

## 7. Agent-visible Tools

### 7.1 默认 Tools

| Tool | 参数 | 用途 |
| --- | --- | --- |
| `add_skill` | `source?`、`data?`、`wait?`、`timeout?` | 导入或注册 OpenViking skill。 |
| `ov_search` | `query`、`uri?`、`limit?` | 搜索 OpenViking resources 和 skills。 |
| `ov_read` | `uri` | 读取精确 `viking://...` OpenViking URI 的完整内容。 |
| `memory_recall` | `query`、`limit?`、`scoreThreshold?`、`targetUri?`、`resourceTypes?` | 显式召回长期记忆或资源。 |
| `ov_recall_trace` | `turn?`、`traceId?`、`sessionId?`、`sessionKey?`、`ovSessionId?`、`source?`、`resourceTypes?`、`since?`、`until?`、`includeContent?`、`limit?` | 查询 recall trace。 |
| `memory_store` | `text`、`role?`、`sessionId?` | 将文本写入 session 并触发记忆抽取。 |
| `memory_forget` | `uri?`、`query?`、`targetUri?`、`limit?`、`scoreThreshold?` | 删除记忆 URI，或先搜索后删除唯一高置信候选。 |
| `ov_archive_search` | `query`、`archiveId?` | 在当前 session 的 archived 原始消息中关键词搜索。 |
| `ov_archive_expand` | `archiveId` | 展开某个 archive 的原始消息。 |
| `openviking_tool_result_read` | `tool_output_ref`、`offset?`、`limit?` | 读取外置工具结果的完整或分页内容。 |
| `openviking_tool_result_search` | `tool_output_ref`、`query`、`limit?`、`context_chars?` | 在外置工具结果中搜索关键词。 |
| `openviking_tool_result_list` | `tool_name?`、`limit?` | 列出当前 session 中被外置的工具结果。 |

### 7.2 Opt-in Tool：`add_resource`

| Tool | 参数 | 默认 | 开启方式 | 用途 |
| --- | --- | --- | --- | --- |
| `add_resource` | `source`、`to?`、`parent?`、`reason?`、`instruction?`、`wait?`、`timeout?` | 不注册 | `enableAddResourceTool=true`，且被 `enabledTools` 选中 | 允许 Agent 导入资源。 |

安全边界：搜索、读取、trace 和资源消费路径应优先使用 `ov_search` / `ov_read`，不要让 Agent 在检索阶段自动导入新资源，除非用户明确授权。

## 8. Gateway API

### 8.1 `GET /api/openviking/recall-traces`

用途：查询 recall trace 列表。

Query 参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `turn` | string | `latest` 或 `all`。 |
| `traceId` | string | 精确 trace id。 |
| `sessionId` | string | OpenClaw session id。 |
| `sessionKey` | string | OpenClaw session key。 |
| `ovSessionId` | string | OpenViking session id。 |
| `source` | string | `auto_recall`、`memory_recall`、`ov_search`、`ov_archive_search`。 |
| `resourceTypes` | string | 逗号分隔资源类型。 |
| `since` | number | 毫秒时间戳下界。 |
| `until` | number | 毫秒时间戳上界。 |
| `includeContent` | boolean | 是否读取 selected/displayed URI 内容预览。 |
| `limit` | number | 最大返回条数。 |

示例：

```bash
curl 'http://127.0.0.1:<gateway-port>/api/openviking/recall-traces?source=ov_search&includeContent=true&limit=10'
```

### 8.2 `GET /api/openviking/recall-traces/:traceId`

用途：按 trace id 查询单条或相关 trace。

示例：

```bash
curl 'http://127.0.0.1:<gateway-port>/api/openviking/recall-traces/ov_search-1780329600000-a1b2c3d4'
```

### 8.3 Gateway route 不可用时的替代方式

如果当前 OpenClaw Gateway 没有提供 route adapter，插件会跳过 HTTP route 注册。此时可使用：

- Agent Tool：`ov_recall_trace`
- Slash Command：`/ov-recall-trace`

## 9. OpenViking 后端 API 封装

插件通过 `OpenVikingClient` 调用 OpenViking Server。统一 Header：

| Header | 来源 | 说明 |
| --- | --- | --- |
| `X-API-Key` | `apiKey` | API Key。 |
| `X-OpenViking-Account` | `accountId` | 租户 account。 |
| `X-OpenViking-User` | `userId` | 租户 user。 |

`peer_role=assistant/person` 时，插件通过请求 body 的 `peer_id` 字段隔离 session 写入和 recall/search；当前实现不发送 `X-OpenViking-Agent` header。

后端 API 封装清单：

| Client 方法 | HTTP API | 用途 |
| --- | --- | --- |
| `healthCheck()` | `GET /health` | 健康检查。 |
| `getRuntimeIdentity()` | `GET /api/v1/system/status` | 获取运行时用户身份。 |
| `find()` | `POST /api/v1/search/find` | 语义搜索 memories/resources/skills。 |
| `read()` | `GET /api/v1/content/read?uri=...` | 读取 `viking://...` URI 内容。 |
| `readToolResult()` | `GET /api/v1/sessions/{sessionId}/tool-results/{toolResultId}` | 读取外置工具结果。 |
| `searchToolResult()` | `GET /api/v1/sessions/{sessionId}/tool-results/{toolResultId}/search?q=...` | 搜索外置工具结果。 |
| `listToolResults()` | `GET /api/v1/sessions/{sessionId}/tool-results` | 列出外置工具结果。 |
| `uploadTempFile()` | `POST /api/v1/resources/temp_upload` | 本地文件或目录上传前的临时上传。 |
| `addResource()` | `POST /api/v1/resources` | 导入 resource。 |
| `addSkill()` | `POST /api/v1/skills` | 导入 skill。 |
| `addSessionMessage()` | `POST /api/v1/sessions/{sessionId}/messages` | 写入 session message。 |
| `getSession()` | `GET /api/v1/sessions/{sessionId}` | 获取 session 状态。 |
| `commitSession()` | `POST /api/v1/sessions/{sessionId}/commit` | 提交 session，触发 archive / memory extraction。 |
| `getTask()` | `GET /api/v1/tasks/{taskId}` | 轮询异步任务。 |
| `getSessionContext()` | `GET /api/v1/sessions/{sessionId}/context?token_budget=...` | 读取 session context。 |
| `getSessionArchive()` | `GET /api/v1/sessions/{sessionId}/archives/{archiveId}` | 读取 archive 详情。 |
| `grepSessionArchives()` | `POST /api/v1/search/grep` | 在 archive 中 grep。 |
| `deleteSession()` | `DELETE /api/v1/sessions/{sessionId}` | 删除 session。 |
| `deleteUri()` | `DELETE /api/v1/fs?uri=...&recursive=false` | 删除指定 URI，主要用于 `memory_forget`。 |

## 10. 常见配置组合

### 10.1 记忆与资源默认模式

```json
{
  "autoCapture": true,
  "autoRecall": true,
  "recallTargetTypes": ["user", "agent"]
}
```

### 10.2 自动召回资源库，不启用记忆写入

```json
{
  "autoCapture": false,
  "autoRecall": true,
  "recallTargetTypes": ["resource"],
  "enabledTools": ["resource_query"]
}
```

### 10.3 完全禁用自动记忆，只保留手动资源查询

```json
{
  "autoCapture": false,
  "autoRecall": false,
  "enabledTools": ["resource_query"]
}
```

### 10.4 开启 Recall Trace 排障

```json
{
  "traceRecall": true,
  "traceRecallPersist": true,
  "traceRecallDir": "~/.openclaw/openviking/recall-traces",
  "traceRecallRetentionDays": 14,
  "traceRecallMaxEntries": 1000,
  "traceRecallMaxResultsPerSearch": 20,
  "traceRecallPreviewChars": 240
}
```

### 10.5 Root Key / Trusted 部署

```json
{
  "baseUrl": "https://openviking.example.com",
  "apiKey": "${OPENVIKING_API_KEY}",
  "accountId": "${OPENVIKING_ACCOUNT_ID}",
  "userId": "${OPENVIKING_USER_ID}"
}
```

## 11. 注意事项

1. `viking://...` 是 OpenViking 虚拟 URI，不是本地文件路径。
2. 读取 OpenViking 搜索结果全文应使用 `ov_read` 或 `/api/v1/content/read`，不要交给本地文件读取工具。
3. `add_resource` Agent Tool 默认禁用；手动 `/add-resource` 始终可用。
4. 如果只想保留资源查询能力，优先设置 `autoCapture=false`、`autoRecall=false`、`enabledTools=["resource_query"]`。
5. 如果要排查召回未命中，开启 `traceRecall=true`，并使用 `/ov-recall-trace` 或 Gateway recall trace API。
6. `logFindRequests` 会输出路由、target URI、query 等信息，但不会输出 API Key。
7. Root key 场景通常必须配置 `accountId` 和 `userId`，否则服务端可能无法确定租户上下文。
