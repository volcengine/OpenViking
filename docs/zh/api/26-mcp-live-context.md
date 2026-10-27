# MCP 与实时上下文 API

本模块用于注册 Streamable HTTP MCP Server、同步和检索工具目录、通过 OpenViking 调用工具，以及把工具挂载为实时上下文节点。只读挂载工具可通过 `read` 透明执行；显式声明为非只读或破坏性的工具必须显式调用。所有 REST 路径都以 `/api/v1/mcps` 开头，并使用[通用响应信封](01-overview.md#响应格式)。

> **适用范围**
>
> 本文只描述 **OpenViking-registered MCP Servers**：在 OpenViking 注册、用于工具发现、代理调用
> 和实时上下文挂载的 MCP 服务。本文不描述 OpenViking 对外提供给 MCP Client 的
> **OpenViking MCP Interface**，也不描述通过 `bot.tools.mcp_servers` 直接配置的
> **VikingBot-connected MCP Servers**。

## 使用前提与约束

- MCP Server 只支持 Streamable HTTP，地址可以使用 HTTP 或 HTTPS。HTTP 不提供传输加密，使用方需自行评估凭证和工具数据的暴露风险。
- Server 名称只能包含字母、数字、`_` 和 `-`，最长 64 个字符。工具名必须能作为 OpenViking 文件名使用。
- 注册时提交的 HTTP Header 按账号保存，但 API 只返回按字母排序的 `header_names`，不会返回 Header 值。
- 工具目录可以位于用户空间 `viking://~/tools/<server>/<tool>.json`，也可以位于账号共享空间 `viking://agent/tools/<server>/<tool>.json`。调用和挂载参数会按工具的 `inputSchema` 校验。
- `target_uri` 只接受 `viking://~/tools` 或 `viking://agent/tools`。注册及名称型操作未指定时使用用户空间；列出和检索未指定时合并两个空间。同名 Server 可以同时存在于两个空间。
- 两个 tools 目录都是受管目录，不能通过普通 `mkdir`、`rm`、`cp` 或 `mv` 修改，应使用 MCP 管理接口。
- 实时上下文节点 URI 必须是当前账号可访问的 Viking URI，并以 `.md` 结尾。
- `wait` 只控制目录或节点写入是否等待后台处理完成，不是 MCP 工具调用超时。`timeout_seconds` 是每次实时工具调用的超时时间。

## 端点总览

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/v1/mcps/validate` | 校验连接并发现工具，不保存配置 |
| POST | `/api/v1/mcps` | 注册 Server 并发布工具目录 |
| GET | `/api/v1/mcps` | 列出已注册 Server |
| GET | `/api/v1/mcps/{name}` | 查看 Server 和当前工具定义 |
| GET | `/api/v1/mcps/status?name=...` | 检查 Server 是否可访问 |
| POST | `/api/v1/mcps/{name}/sync` | 同步工具目录并报告受影响挂载 |
| DELETE | `/api/v1/mcps/{name}` | 删除 Server 和工具目录 |
| POST | `/api/v1/mcps/find` | 语义检索工具 |
| POST | `/api/v1/mcps/invoke` | 调用工具 |
| POST | `/api/v1/mcps/mount` | 创建或替换一个实时上下文节点 |
| POST | `/api/v1/mcps/batch-mount` | 批量创建或替换实时上下文节点 |
| DELETE | `/api/v1/mcps/mount` | 删除一个挂载及其节点 |
| POST | `/api/v1/mcps/batch-unmount` | 批量删除挂载及其节点 |
| GET | `/api/v1/mcps/mounts` | 查询 Server 或工具的挂载 |

## MCP Server 生命周期

### 校验和注册

`POST /api/v1/mcps/validate` 会连接 Server、完成 MCP 初始化并列出工具，但不会写入注册表或工具目录。`POST /api/v1/mcps` 执行相同校验，然后注册 Server 并发布工具定义。默认发布到 `viking://~/tools/<name>/`；通过 `target_uri` 可以选择 `viking://agent/tools/<name>/`。同一空间内的同名 Server 已存在时注册失败。

**请求体**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `name` | string | 是 | - | Server 名称 |
| `url` | string | 是 | - | Streamable HTTP 地址 |
| `headers` | object | 否 | `{}` | 发往上游的 HTTP Header |
| `target_uri` | string | 否 | `viking://~/tools` | 用户或共享 tools 根目录 |
| `wait` | boolean | 否 | `false` | 仅注册接口使用；是否等待工具目录处理完成 |

未知字段会返回 `INVALID_ARGUMENT`。凭证应从环境变量或密钥系统注入请求，不要写入资源文件或提交到代码仓库。

**Python SDK**

```python
import os
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933", api_key="your-key")

headers = {"Authorization": f"Bearer {os.environ['WEATHER_MCP_TOKEN']}"}
client.validate_mcp("weather", "https://mcp.example.com/mcp", headers=headers)
result = client.add_mcp(
    "weather",
    "https://mcp.example.com/mcp",
    headers=headers,
    target_uri="viking://agent/tools",
    wait=True,
)
```

**TypeScript SDK**

```typescript
const headers = { Authorization: `Bearer ${process.env.WEATHER_MCP_TOKEN}` };
await client.validateMCP("weather", "https://mcp.example.com/mcp", headers);
const result = await client.addMCP(
  "weather",
  "https://mcp.example.com/mcp",
  headers,
  { wait: true, targetUri: "viking://agent/tools" },
);
```

**Go SDK**

```go
headers := map[string]string{
    "Authorization": "Bearer " + os.Getenv("WEATHER_MCP_TOKEN"),
}
_, err := client.ValidateMCP(ctx, "weather", "https://mcp.example.com/mcp", headers)
if err != nil {
    return err
}
result, err := client.AddMCP(
    ctx,
    "weather",
    "https://mcp.example.com/mcp",
    headers,
    &openviking.WaitOptions{Wait: true, TargetURI: "viking://agent/tools"},
)
```

**HTTP API**

```bash
curl -X POST http://localhost:1933/api/v1/mcps/validate \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "weather",
    "url": "https://mcp.example.com/mcp",
    "headers": {"Authorization": "Bearer <token>"}
  }'

curl -X POST http://localhost:1933/api/v1/mcps \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "weather",
    "url": "https://mcp.example.com/mcp",
    "headers": {"Authorization": "Bearer <token>"},
    "target_uri": "viking://agent/tools",
    "wait": true
  }'
```

**CLI**

```bash
ov mcps validate '{"mcpServers":{"weather":{"url":"https://mcp.example.com/mcp","headers":{"Authorization":"Bearer <token>"}}}}'
ov mcps add -p viking://agent/tools --wait '{"mcpServers":{"weather":{"url":"https://mcp.example.com/mcp","headers":{"Authorization":"Bearer <token>"}}}}'
```

**校验响应**

```json
{
  "status": "ok",
  "result": {
    "name": "weather",
    "root_uri": "viking://agent/tools",
    "valid": true,
    "protocol_version": "2025-03-26",
    "server_info": {"name": "weather-server", "version": "1.0.0"},
    "tool_count": 1
  }
}
```

**注册响应**

```json
{
  "status": "ok",
  "result": {
    "name": "weather",
    "url": "https://mcp.example.com/mcp",
    "created_at": "2026-10-21T08:00:00+00:00",
    "updated_at": "2026-10-21T08:00:00+00:00",
    "header_names": ["Authorization"],
    "tool_count": 1,
    "protocol_version": "2025-03-26"
  }
}
```

`wait=false` 时，注册响应还可能包含底层目录写入返回的 `processing` 对象。

### 列出、查看和检查状态

`GET /api/v1/mcps` 未指定 `target_uri` 时合并用户空间和共享空间的 Server，每项用 `root_uri` 标识所属空间。`GET /api/v1/mcps/{name}` 和状态接口默认操作用户空间；使用同名共享 Server 时必须指定 `target_uri=viking://agent/tools`。连接失败仍是成功的 HTTP 响应，但 `result.status` 为 `error`。

**SDK 与 CLI**

```python
servers = client.list_mcps()
server = client.get_mcp("weather", target_uri="viking://agent/tools")
status = client.get_mcp_status("weather", target_uri="viking://agent/tools")
```

```typescript
const servers = await client.listMCPs();
const server = await client.getMCP("weather", "viking://agent/tools");
const status = await client.getMCPStatus("weather", "viking://agent/tools");
```

```bash
ov mcps list
ov mcps show -p viking://agent/tools weather
ov mcps status -p viking://agent/tools weather
```

**HTTP API**

```bash
curl -H "X-API-Key: your-key" http://localhost:1933/api/v1/mcps
curl -H "X-API-Key: your-key" \
  "http://localhost:1933/api/v1/mcps/weather?target_uri=viking%3A%2F%2Fagent%2Ftools"
curl -H "X-API-Key: your-key" \
  "http://localhost:1933/api/v1/mcps/status?name=weather&target_uri=viking%3A%2F%2Fagent%2Ftools"
```

**列表响应**

```json
{
  "status": "ok",
  "result": {
    "servers": [{
      "name": "weather",
      "root_uri": "viking://agent/tools",
      "url": "https://mcp.example.com/mcp",
      "created_at": "2026-10-21T08:00:00+00:00",
      "updated_at": "2026-10-21T08:00:00+00:00",
      "header_names": ["Authorization"],
      "tool_count": 1
    }],
    "total": 1
  }
}
```

查看响应在同一 Server 对象中增加 `tools`。每项包含 `uri`、`server`、`address`、`revision`、`input_revision` 和上游 MCP `tool` 定义。`revision` 跟踪完整 Tool 定义；`input_revision` 根据工具名称和 `inputSchema` 跟踪挂载兼容性。

**状态响应**

```json
{
  "status": "ok",
  "result": {
    "name": "weather",
    "status": "running",
    "protocol_version": "2025-03-26",
    "tool_count": 1
  }
}
```

连接失败时，`result` 为 `{"name":"weather","status":"error","error":"...","tool_count":1}`。客户端应同时检查 HTTP 信封和这个业务状态。

### 同步工具目录

`POST /api/v1/mcps/{name}/sync` 重新发现上游工具，只重写新增或 `revision` 变化的工具，并删除目录中上游已移除的工具。`changed` 和 `unchanged` 分类使用 `input_revision`，因此仅描述、annotations 或输出 schema 变化时会刷新目录，但仍归类为 `unchanged`。同步不会自动改写或删除已有实时上下文节点，而是在 `affected_mounts` 中报告：

| 状态 | 含义 |
|------|------|
| `REVIEW` | 工具 input revision 已变化，现有预设参数仍符合新 schema，需要人工确认行为 |
| `INVALID` | 工具 input revision 已变化，现有预设参数不再符合新 schema |
| `REMOVED` | 上游不再提供该工具 |

**请求体**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `wait` | boolean | 否 | `false` | 是否等待工具目录处理完成；也可以省略整个请求体 |
| `target_uri` | string | 否 | `viking://~/tools` | Server 所属 tools 根目录 |

**SDK、HTTP 与 CLI**

```python
result = client.sync_mcp("weather", target_uri="viking://agent/tools", wait=True)
```

```typescript
const result = await client.syncMCP("weather", {
  targetUri: "viking://agent/tools",
  wait: true,
});
```

```bash
curl -X POST http://localhost:1933/api/v1/mcps/weather/sync \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"target_uri":"viking://agent/tools","wait":true}'

ov mcps sync -p viking://agent/tools --wait weather
```

**响应契约**

```json
{
  "status": "ok",
  "result": {
    "name": "weather",
    "synced": true,
    "protocol_version": "2025-03-26",
    "tool_count": 1,
    "changes": {
      "added": [],
      "changed": ["current"],
      "unchanged": [],
      "removed": ["forecast"]
    },
    "affected_mounts": [{
      "node_uri": "viking://resources/weather/current.md",
      "tool_uri": "viking://agent/tools/weather/current.json",
      "tool": "current",
      "mounted_input_revision": "old-input-revision",
      "current_input_revision": "new-input-revision",
      "status": "REVIEW",
      "reason": "Tool input revision changed; mounted arguments remain valid under the current input schema"
    }]
  }
}
```

### 删除 Server

`DELETE /api/v1/mcps/{name}` 删除指定空间中的注册信息和工具目录。它不删除已挂载的实时上下文节点；这些节点之后无法执行，应先查询并卸载挂载。

```python
result = client.remove_mcp("weather", target_uri="viking://agent/tools")
```

```bash
ov mcps mounts -p viking://agent/tools --mcp weather
ov mcps remove -p viking://agent/tools weather
```

```json
{
  "status": "ok",
  "result": {"name": "weather", "removed": true}
}
```

## 工具发现与调用

### 语义检索工具

`POST /api/v1/mcps/find` 未指定 `target_uri` 时合并检索用户空间和共享空间；指定后只检索对应 tools 根目录。REST 和 SDK 响应包含完整 `tool` 定义，可据此只补充 `inputSchema` 要求的参数。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `query` | string | 是 | - | 非空的自然语言查询 |
| `limit` | integer | 否 | `10` | 返回上限，范围 1-100 |
| `target_uri` | string | 否 | 合并两个空间 | 限定用户或共享 tools 根目录 |

```python
result = client.find_mcp_tools("查询城市当前天气", limit=5)
```

```typescript
const result = await client.findMCPTools("current weather for a city", 5);
```

```bash
ov mcps find --limit 5 "查询城市当前天气"
```

```json
{
  "status": "ok",
  "result": {
    "tools": [{
      "uri": "viking://agent/tools/weather/current.json",
      "server": "weather",
      "name": "current",
      "description": "Get current weather",
      "revision": "sha256-revision",
      "input_revision": "sha256-input-revision",
      "score": 0.87,
      "tool": {
        "name": "current",
        "description": "Get current weather",
        "inputSchema": {
          "type": "object",
          "properties": {"city": {"type": "string"}},
          "required": ["city"]
        }
      }
    }],
    "total": 1
  }
}
```

### 调用工具

`POST /api/v1/mcps/invoke` 通过已注册 Server 调用工具。OpenViking 注入保存的上游 Header，不向调用方暴露凭证。当前调用不会自动重试；连接断开或超时时，错误会说明执行结果可能未知。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `uri` | string | 是 | - | `viking://agent/tools/<server>/<tool>.json` |
| `arguments` | object | 否 | `{}` | 符合工具 `inputSchema` 的参数 |

```python
result = client.invoke_mcp_tool(
    "viking://agent/tools/weather/current.json",
    {"city": "Beijing"},
)
```

```typescript
const result = await client.invokeMCPTool(
  "viking://agent/tools/weather/current.json",
  { city: "Beijing" },
);
```

```bash
ov mcps invoke viking://agent/tools/weather/current.json \
  --args '{"city":"Beijing"}'
```

```json
{
  "status": "ok",
  "result": {
    "content": [{"type": "text", "text": "Beijing: 18 C, clear"}],
    "isError": false
  }
}
```

`result` 保留 MCP `CallToolResult`，可能包含 `content`、`structuredContent` 和 `isError`。上游返回 `isError=true` 仍表示 HTTP 调用成功，调用方必须检查该字段。

## 实时上下文挂载

### 创建或替换挂载

`POST /api/v1/mcps/mount` 创建或替换一个 Markdown 实时上下文节点。`POST /api/v1/mcps/batch-mount` 原子校验整批请求后批量写入；同一批次不能包含重复 `node_uri`。

任何工具都可以挂载。为兼容未提供 annotations 的工具，annotations 缺失或为空时默认按只读处理。完整读取会拒绝执行 `annotations.readOnlyHint: false` 或 `destructiveHint: true` 的工具；错误会返回当前集成执行显式调用所需的 `tool_uri` 和已保存参数。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `tool_uri` | string | 是 | - | 已注册工具 URI |
| `node_uri` | string | 是 | - | 以 `.md` 结尾的目标 URI |
| `title` | string | 是 | - | 非空节点标题 |
| `description` | string/null | 否 | `null` | 节点说明 |
| `timeout_seconds` | integer | 否 | `60` | 单次工具调用超时，必须大于 0 |
| `arguments` | object | 否 | `{}` | 固定注入并按工具 schema 校验的参数 |
| `tags` | string[]/null | 否 | `null` | 节点检索标签；省略或传 `[]` 均保留原标签，清空标签需调用 `set_tags` 的 clear 模式 |
| `wait` | boolean | 否 | `false` | 是否等待节点处理完成；批量接口位于最外层 |

**Python SDK**

```python
result = client.mount_mcp_tool(
    "viking://agent/tools/weather/current.json",
    "viking://resources/live/weather-beijing.md",
    "北京实时天气",
    description="查询北京当前天气",
    arguments={"city": "Beijing"},
    tags=["source=mcp"],
    wait=True,
)
```

**TypeScript SDK**

```typescript
const result = await client.mountMCPTool(
  "viking://agent/tools/weather/current.json",
  "viking://resources/live/weather-beijing.md",
  "Beijing live weather",
  {
    description: "Query current weather in Beijing",
    arguments: { city: "Beijing" },
    tags: ["source=mcp"],
    wait: true,
  },
);
```

**HTTP API**

```bash
curl -X POST http://localhost:1933/api/v1/mcps/mount \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_uri": "viking://agent/tools/weather/current.json",
    "node_uri": "viking://resources/live/weather-beijing.md",
    "title": "北京实时天气",
    "description": "查询北京当前天气",
    "arguments": {"city": "Beijing"},
    "tags": ["source=mcp"],
    "wait": true
  }'
```

**CLI**

```bash
ov mcps mount \
  viking://agent/tools/weather/current.json \
  viking://resources/live/weather-beijing.md \
  --title "北京实时天气" \
  --description "查询北京当前天气" \
  --args '{"city":"Beijing"}' \
  --wait
```

当前 CLI 的 `mount` 不提供 `--tags`；需要标签时使用 SDK 或 HTTP。

**响应契约**

```json
{
  "status": "ok",
  "result": {
    "node_uri": "viking://resources/live/weather-beijing.md",
    "mcp": "weather",
    "tool": "current",
    "tool_uri": "viking://agent/tools/weather/current.json",
    "input_revision": "sha256-input-revision",
    "title": "北京实时天气",
    "description": "查询北京当前天气",
    "timeout_seconds": 60,
    "arguments": {"city": "Beijing"},
    "created_at": "2026-10-21T08:30:00+00:00",
    "mounted": true
  }
}
```

批量请求体为 `{"mounts":[<不含 wait 的单项参数>],"wait":false}`，至少包含一项。Python、TypeScript 和 Go SDK 分别提供 `batch_mount_mcp_tools`、`batchMountMCPTools` 和 `BatchMountMCPTools`。响应为：

```json
{
  "status": "ok",
  "result": {
    "mounts": [{"node_uri": "viking://resources/live/weather-beijing.md", "mounted": true}],
    "total": 1
  }
}
```

每个 `mounts` 项的完整字段与单项挂载响应一致。
挂载响应不返回 `tags`，因为向量索引处理可能仍在异步进行。需要当前标签时应调用查询挂载接口，以向量索引中的标签为准。

### 查询挂载

`GET /api/v1/mcps/mounts` 的 `mcp` 查询参数必填，`tool` 可选，`target_uri` 用于选择同名 Server 所属空间；未指定时使用用户空间。Server 必须仍处于已注册状态。
响应中的 `tags` 从向量索引读取，挂载数据库不再保存独立副本。

```python
result = client.list_mcp_mounts("weather", tool="current")
```

```bash
ov mcps mounts --mcp weather --tool current
```

```json
{
  "status": "ok",
  "result": {
    "mounts": [{
      "node_uri": "viking://resources/live/weather-beijing.md",
      "mcp": "weather",
      "tool": "current",
      "tool_uri": "viking://agent/tools/weather/current.json",
      "input_revision": "sha256-input-revision",
      "arguments": {"city": "Beijing"},
      "title": "北京实时天气",
      "description": "查询北京当前天气",
      "timeout_seconds": 60,
      "tags": ["source=mcp"],
      "created_at": "2026-10-21T08:30:00+00:00"
    }],
    "total": 1
  }
}
```

### 读取实时上下文

挂载后使用普通内容读取接口或 `ov read`。OpenViking 识别节点的 `OpenViking Live Context` 元数据，并对符合只读策略的工具按节点保存的 `tool_uri`、`arguments` 和 `timeout_seconds` 发起调用。

```bash
ov read viking://resources/live/weather-beijing.md
```

实时读取支持 MCP 文本结果和 `structuredContent`。多个文本块用换行连接；只有结构化结果时返回格式化 JSON。上游 `isError=true`、只有图片或音频等非文本内容，或者工具显式声明为非只读或破坏性时，读取返回 `PROCESSING_ERROR`。直接调用 `/invoke` 可以执行写工具，并仍会保留原生 MCP 内容。

### 删除挂载

`DELETE /api/v1/mcps/mount` 删除一个挂载记录和对应 Markdown 节点。批量接口 `POST /api/v1/mcps/batch-unmount` 接收至少一个且不重复的 `node_uris`；任一挂载不存在时整批失败。

```python
client.unmount_mcp_tool(
    "viking://resources/live/weather-beijing.md",
    wait=True,
)
```

```bash
ov mcps unmount viking://resources/live/weather-beijing.md --wait
```

```bash
curl -X DELETE \
  "http://localhost:1933/api/v1/mcps/mount?node_uri=viking%3A%2F%2Fresources%2Flive%2Fweather-beijing.md&wait=true" \
  -H "X-API-Key: your-key"
```

```json
{
  "status": "ok",
  "result": {
    "node_uri": "viking://resources/live/weather-beijing.md",
    "mcp": "weather",
    "tool": "current",
    "tool_uri": "viking://agent/tools/weather/current.json",
    "unmounted": true
  }
}
```

批量请求体为 `{"node_uris":["viking://resources/live/weather-beijing.md"],"wait":true}`，响应包含 `unmounts` 和 `total`，每个 `unmounts` 项与单项响应字段一致。

## 常见错误

| 错误码 | 常见原因 |
|--------|----------|
| `INVALID_ARGUMENT` | 名称或 URL 不合法、查询为空、URI 格式错误、节点不是 `.md`、参数不符合工具 schema、批量 URI 重复 |
| `ALREADY_EXISTS` | 注册了同名 MCP Server |
| `NOT_FOUND` | Server、工具或挂载不存在 |
| `UNAVAILABLE` | 无法连接 MCP Server、初始化失败、工具调用失败或工具调用超时 |

工具调用失败不会自动重试。收到 `UNAVAILABLE` 或超时后，不应假设上游没有执行具有副作用的操作。

## 相关文档

- [实时上下文接入和编排](../guides/24-live-context-arrangement.md)
- [MCP 集成](../guides/06-mcp-integration.md)
- [内容 API](12-content.md)
- [SDK、HTTP 与 CLI 约定](01-overview.md)
