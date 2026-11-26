# MCP 与实时上下文 API

本模块用于注册 Streamable HTTP MCP Server、同步和检索工具目录、通过 OpenViking 调用工具，以及把工具挂载为实时上下文节点。只读挂载工具可通过 `read` 透明执行；显式声明为非只读或破坏性的工具必须显式调用。所有 REST 路径都以 `/api/v1/mcps` 开头，并使用[通用响应信封](01-overview.md#响应格式)。

> **适用范围**
>
> 本文只描述 **OpenViking-registered MCP Servers**：在 OpenViking 注册、用于工具发现、代理调用
> 和实时上下文挂载的 MCP 服务。本文不描述 OpenViking 对外提供给 MCP Client 的
> **OpenViking MCP Interface**，也不描述通过 `bot.tools.mcp_servers` 直接配置的
> **VikingBot-connected MCP Servers**。

## 使用前提与约束

- MCP Server 只支持 Streamable HTTP，地址支持 HTTP 和 HTTPS。
- Server 名称只能包含字母、数字、`_` 和 `-`，最长 64 个字符。工具名必须能作为 OpenViking 文件名使用。
- 注册时提交的机密 HTTP Header 和 URL 查询参数值保存至当前用户 Privacy，注册表和 API 中只包含普通值和相对 Privacy 引用，不包含机密明文。
- 工具目录可以位于用户空间 `viking://~/tools/<server>/<tool>.json`，也可以位于账号共享空间 `viking://agent/tools/<server>/<tool>.json`。调用和挂载参数会按工具的 `inputSchema` 校验。
- `target_uri` 只接受 `viking://~/tools` 或 `viking://agent/tools`。注册未指定时使用用户空间；`show`、`status`、`sync`、`remove` 和指定 `mcp` 的 `mounts` 未指定时先找个人 MCP，个人不存在才找公共 MCP，同名时操作个人 MCP。显式指定后只操作目标空间，未找到时不回退。列出和检索未指定时合并两个空间，同名 Server 均保留。
- 按名称操作的响应通过 `root_uri` 标识实际命中的空间。已找到个人 MCP 后，权限不足、凭证缺失或连接失败不会回退到公共 MCP。
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

`POST /api/v1/mcps/validate` 仅接收原生连接信息 `name`、`url`、`headers`，连接 Server、完成 MCP 初始化并列出工具，不读取或写入 Privacy，也不写入注册表或工具目录。下表为 `POST /api/v1/mcps` 的注册参数；注册时解析或提取 Privacy 凭证，检查连接并发布工具定义。默认发布到 `viking://~/tools/<name>/`；通过 `target_uri` 可以选择 `viking://agent/tools/<name>/`。同一空间内的同名 Server 已存在时注册失败。

**请求体**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `name` | string | 是 | - | Server 名称 |
| `url` | string | 是 | - | Streamable HTTP 地址；可包含查询参数，注册时自动提取其中的凭证 |
| `headers` | object | 否 | `{}` | 原生 Header；自动将含机密信息的 Header 值提取到当前用户 Privacy |
| `target_uri` | string | 否 | `viking://~/tools` | 用户或共享 tools 根目录 |
| `wait` | boolean | 否 | `false` | 仅注册接口使用；是否等待工具目录处理完成 |

未知字段会返回 `INVALID_ARGUMENT`。凭证应从环境变量或密钥系统注入请求，不要写入资源文件或提交到代码仓库。

### 自动提取凭证与公共 MCP

`ov mcps add` 沿用原生 `mcpServers` 配置，不需要额外凭证参数。认证 Header（如 `Authorization`、`X-API-Key`、`Cookie`）以及 Skills 同款规则识别出机密信息的 Header，自动将完整值保存至当前用户的 Privacy。`Accept`、地域等普通 Header 保留在注册表中。

个人 MCP 默认位于 `viking://~/tools`，凭证目标为 `credential/mcp.user.<name>`。公共 MCP 发布到 `viking://agent/tools`，凭证目标为每个用户自己的 `credential/mcp.agent.<name>`。两者的 Privacy 字段名都是 Header 名；注册表仅保存机密 Header 的相对引用，连接时使用调用者当前生效的 Privacy 版本。

例如，Alice 使用自己的 token 注册公共 MCP：

```bash
ov mcps add -p viking://agent/tools --wait "{
  \"mcpServers\": {
    \"weather\": {
      \"url\": \"https://mcp.example.com/mcp\",
      \"headers\": {
        \"Authorization\": \"Bearer $WEATHER_MCP_TOKEN\",
        \"Accept\": \"application/json\"
      }
    }
  }
}"
```

Bob 使用此公共 MCP 前，只需以自己的身份配置对应凭证，无需重复注册：

```bash
ov privacy upsert credential mcp.agent.weather \
  --field "Authorization=Bearer $WEATHER_MCP_TOKEN"
```

缺失目标或字段时返回 `NOT_FOUND`，不会回退使用 Alice 的凭证。本版本不支持共享用户凭证；公共工具的调用仍遵循工具 ACL。无认证 MCP 无需配置 Privacy。`sync` 和 `remove` 根据目录 ACL 校验写权限。

自动提取沿用 Skills 的 Privacy 写入流程：保留已有其他字段，相同值不产生新版本，拒绝用已轮换的历史值覆盖当前凭证。`headers` 也可以使用 Skills 同款 `{{ov_privacy:<当前用户>:viking://~/privacy/<category>/<target>#/values/<field>}}` 引用，或在引用前加 `Bearer `；注册时检查引用所属用户，再转换为相对引用。公共 MCP 的其他用户仍需在自己的同名目标和字段中配置凭证。

所有 Header 统一保存在 `headers` 中，例如机密值替换为 `{{ov_privacy:viking://~/privacy/credential/mcp.agent.weather#/values/Authorization}}`，普通值保持原样。`mcps show` 返回这些安全的 Header 值和引用，连接时才按调用者解析；引用前后的文本会保留。

URL 查询参数也会自动提取凭证：非空的 `key`、名称包含 `api_key`、`token`、`secret`、`password` 等认证字段，或值被识别为机密的参数，保存至 `credential/mcp.user.<name>.query`（个人 MCP）或 `credential/mcp.agent.<name>.query`（公共 MCP），字段名为查询参数名。注册后的 `url` 不含查询串，参数单独保存在 `query_params` 中，机密值使用相对 Privacy 引用，普通值保持原样；连接时再解析并拼回 URL。`query_params` 是返回字段，不是注册请求参数，仍通过 `url` 传入查询参数。

例如注册 URL 为 `https://mcp.example.com/mcp?key=<token>&region=cn` 的公共 MCP 后，其他用户应在自己的 Privacy 中配置 `ov privacy upsert credential mcp.agent.weather.query --field "key=$WEATHER_MCP_TOKEN"`。查询参数中的显式 Privacy 引用同样按当前用户检查和解析，凭证轮换与撤销规则与 Header 一致。

Python、TypeScript、Go SDK 的 `add` 参数保持原样，直接传入 `headers`。CLI `add` 支持 `-p`，也可在 Server 配置中填写 `target_uri`。`validate` 继续直接使用原生 URL 和 headers 测试连接，不涉及 Privacy。

**轮换与撤销**

更新或激活自己的 Privacy 版本后，下一次连接即使用新值，无需 `mcps sync`。删除自己的凭证目标或字段后，后续调用失败；已发出的请求不会被取消。

删除 MCP 会保留 Privacy 及独立的实时上下文节点，凭证是否删除需通过 `ov privacy delete` 显式管理。

调用时使用已注册工具定义校验参数，实时读取也按该定义检查只读标记；解析当前调用者凭证后直接调用上游工具，不额外执行 `list_tools` 预检。上游 MCP Server 负责最终权限校验。工具定义变化时通过 `mcps sync` 更新。

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

`GET /api/v1/mcps` 未指定 `target_uri` 时合并用户空间和共享空间的 Server，每项用 `root_uri` 标识所属空间。`GET /api/v1/mcps/{name}` 和 `GET /api/v1/mcps/status?name=...` 默认先找用户空间，未找到才找共享空间；因此仅存在公共 MCP 时，`ov mcps show weather` 无需额外参数。同名时默认操作个人 MCP，指定 `target_uri=viking://agent/tools` 可操作公共 MCP。连接失败仍是成功的 HTTP 响应，但 `result.status` 为 `error`。

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
| `target_uri` | string | 否 | 个人优先，公共回退 | 指定后只操作该 tools 根目录 |

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

`DELETE /api/v1/mcps/{name}` 删除命中空间中的注册信息和工具目录。未指定 `target_uri` 时个人优先，个人不存在才删除公共 MCP；指定后只操作目标空间。它不删除已挂载的实时上下文节点；这些节点之后无法执行，应先查询并卸载挂载。

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
| `timeout_seconds` | number | 否 | `60` | 单次工具调用超时，必须为有限正数；与客户端 HTTP 请求超时独立 |

```python
result = client.invoke_mcp_tool(
    "viking://agent/tools/weather/current.json",
    {"city": "Beijing"},
    timeout_seconds=120,
)
```

```typescript
const result = await client.invokeMCPTool(
  "viking://agent/tools/weather/current.json",
  { city: "Beijing" },
  120,
);
```

```bash
ov mcps invoke viking://agent/tools/weather/current.json \
  --args '{"city":"Beijing"}' --timeout-seconds 120
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

`POST /api/v1/mcps/mount` 创建或替换一个 Markdown 实时上下文节点。`POST /api/v1/mcps/batch-mount` 逐项校验参数和目标写权限，跳过不合格的节点，按请求顺序写入有效节点并立即保存挂载记录。空批次和重复 `node_uri` 会在任何修改前拒绝整批请求。

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
    "total": 1,
    "succeeded": ["viking://resources/live/weather-beijing.md"],
    "failed": [],
    "not_attempted": []
  }
}
```

每个 `mounts` 项的完整字段与单项挂载响应一致。
两个批量接口均跳过失败项并继续处理独立节点，保留已完成的文件操作和挂载记录。响应仍为 HTTP 200；调用方必须检查 `failed`，其中也包含单节点参数和权限检查错误。`succeeded` 为成功节点 URI，`failed` 为错误项（`node_uri`、`code`、`message`、`applied`），两者均保持请求顺序。正常完成时 `not_attempted` 为空。空批次和重复 URI 返回 `INVALID_ARGUMENT`，且不会修改文件。

`failed[].applied=true` 表示文件修改已经生效并已保存对应挂载记录，但后续处理失败；该节点也出现在 `mounts` 或 `unmounts` 中，`total` 为实际生效的项数，后续节点仍会执行。批量卸载不会重建已删除节点，避免正文补偿改变原有 ACL。挂载记录保存失败时只重试当前数据库快照一次，不重新执行文件操作；如果仍失败，则停止整批并返回 `PROCESSING_ERROR`，错误详情包含 `node_uri`、`reconciliation_required=true`，以及 `succeeded`、`failed` 和 `not_attempted`。需要核对该节点与记录；`not_attempted` 列出后续尚未开始文件操作的有效节点。
挂载响应不返回 `tags`，因为向量索引处理可能仍在异步进行。需要当前标签时应调用查询挂载接口，以向量索引中的标签为准。

### 查询挂载

`GET /api/v1/mcps/mounts` 的 `mcp` 和 `tool` 查询参数均可选。不传 `mcp` 时列出当前用户个人空间和公共空间的全部可读挂载节点，包括 Server 删除后仍保留的节点；传入 `target_uri` 后只查询该空间。`tool` 可以独立筛选工具名。
传入 `mcp` 时，未指定 `target_uri` 则先找个人 MCP，个人不存在才找公共 MCP，同名时只返回个人 MCP 的挂载。显式指定后只查询目标空间；此时 Server 必须仍处于已注册状态。查询两个空间时响应返回 `root_uris`，查询单一空间时返回 `root_uri`。
响应中的 `tags` 从向量索引读取，挂载数据库不再保存独立副本。

查询会根据本地工具定义动态计算状态，不连接上游 MCP，也不保存状态文件。同一个工具定义在一次查询中只读取一次；只有 input revision 变化时才重新校验预设参数，和 `sync` 共用判断逻辑。状态反映最近一次同步到本地的工具定义。

| 状态 | 含义 |
|------|------|
| `VALID` | 本地工具 input revision 与挂载时一致 |
| `REVIEW` | input revision 已变化，预设参数仍符合新 schema |
| `INVALID` | input revision 已变化，预设参数不再符合新 schema |
| `REMOVED` | 本地工具定义已不存在 |
| `UNKNOWN` | 本地工具定义无法读取或损坏，无法判断状态 |

`status=INVALID`（CLI：`--status INVALID`）按状态筛选，支持逗号分隔的多个值，例如 `status=INVALID,REMOVED,UNKNOWN`；省略时返回全部状态。支持 `VALID`、`REVIEW`、`INVALID`、`REMOVED`、`UNKNOWN`，大小写不敏感。每项包含 `status` 和 `reason`。`total` 为返回的节点数，`matched_total` 为按 MCP、工具、空间和权限筛选后的节点总数，`counts` 为这些节点的各状态统计；状态筛选不改变 `matched_total` 和 `counts`。

CLI 显示总数、各状态数量和实际显示数量；`VALID` 节点省略 `status` 和 `reason`，其他状态仍显示原因。JSON 响应保留全部状态字段。

```python
result = client.list_mcp_mounts()
result = client.list_mcp_mounts("weather", tool="current")
```

```bash
ov mcps mounts
ov mcps mounts -p viking://agent/tools
ov mcps mounts --mcp weather --tool current
ov mcps mounts --status INVALID
ov mcps mounts --status INVALID,REMOVED,UNKNOWN
```

TypeScript 使用 `client.listMCPMounts()` 查询全部挂载；Go 使用 `client.ListMCPMounts(ctx, "", "")` 省略 MCP 和工具筛选。

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
      "status": "VALID",
      "reason": "Tool input revision matches the mounted definition",
      "tags": ["source=mcp"],
      "created_at": "2026-10-21T08:30:00+00:00"
    }],
    "total": 1,
    "matched_total": 1,
    "counts": {"VALID": 1, "REVIEW": 0, "INVALID": 0, "REMOVED": 0, "UNKNOWN": 0}
  }
}
```

### 读取实时上下文

挂载后使用普通内容读取接口或 `ov read`。OpenViking 识别节点的 `OpenViking Live Context` 元数据，并对符合只读策略的工具按节点保存的 `tool_uri`、`arguments` 和 `timeout_seconds` 发起调用。

```bash
ov read viking://resources/live/weather-beijing.md
```

实时读取保留节点定义（YAML frontmatter、预设参数和正文指引），然后在 `## Live Result` 下追加工具执行结果。支持 MCP 文本结果和 `structuredContent`：多个文本块用换行连接；只有结构化结果时，追加格式化 JSON。REST `read?raw=true` 和 MCP `read(raw=true)` 只返回已存储的定义，不调用工具；分页读取也不执行工具。上游 `isError=true`、只有图片或音频等非文本内容，或者工具显式声明为非只读或破坏性时，读取返回 `PROCESSING_ERROR`。直接调用 `/invoke` 可以执行写工具，并仍会保留原生 MCP 内容。

### 删除挂载

`DELETE /api/v1/mcps/mount` 删除一个挂载记录和对应 Markdown 节点。批量接口 `POST /api/v1/mcps/batch-unmount` 接收至少一个且不重复的 `node_uris`；挂载不存在或删除权限不足时，将该节点记入 `failed`，其他节点继续执行。每删除一个节点立即保存记录，部分失败使用上文的 `succeeded`、`failed` 和 `not_attempted` 契约，实际完成项位于 `unmounts`。

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
| `CONFLICT` | 重新注册时提交了已轮换字段的旧凭证值 |
| `NOT_FOUND` | Server、工具或挂载不存在 |
| `PERMISSION_DENIED` | 目录或工具 ACL 不允许访问、越权引用 Privacy、当前凭证不可用该工具 |
| `UNAVAILABLE` | 无法连接 MCP Server、初始化失败、工具调用失败或工具调用超时 |

工具调用失败不会自动重试。收到 `UNAVAILABLE` 或超时后，不应假设上游没有执行具有副作用的操作。

## 相关文档

- [实时上下文接入和编排](../guides/24-live-context-arrangement.md)
- [MCP 集成](../guides/06-mcp-integration.md)
- [Privacy API](10-privacy.md)
- [内容 API](12-content.md)
- [SDK、HTTP 与 CLI 约定](01-overview.md)
