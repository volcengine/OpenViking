---
description: 接入远程 MCP Server，将工具挂载为实时上下文，并使用 CLI、SDK、Studio 和 Context as Code 编排节点。
---

# 实时上下文接入与编排

实时上下文（Live Context）把远程 MCP Tool 挂载为 `viking://resources/...` 下的 Markdown 节点。Agent 不需要了解上游地址、鉴权方式和固定参数，只需像读取普通上下文一样读取节点；OpenViking 会执行绑定的 Tool，并透明返回上游结果。

本页面向实现和维护上下文编排的开发者，说明 MCP 注册、Tool Catalog、实时节点、Agent Gateway、Studio 和 Context as Code（CaC）之间的关系。MCP 客户端连接方式和 OpenViking 内置 MCP Tool 的完整清单见 [MCP 工具与协议](06-mcp-integration.md)。

## 核心模型

这套能力分为控制面和数据面：

| 层次 | 职责 | 主要入口 |
| --- | --- | --- |
| MCP 注册与同步 | 保存 MCP Server 连接信息，将 Tool 定义同步进 OpenViking | `ov mcps add/validate/sync/status`、REST、SDK |
| Tool Catalog | 让 Tool 定义可检索、可读取，并保存完整定义和输入版本 | `viking://agent/tools/<server>/<tool>.json` |
| 实时上下文 | 将一个 Tool、固定参数和目标 URI 绑定为可读节点 | `ov mcps mount/unmount/mounts`、REST、SDK |
| Agent Gateway | 按意图发现低频 Tool，并安全代理调用 | MCP `find_mcp_tools`、`invoke_mcp_tool` |
| 上下文编排 | 声明一组期望节点，计算差异并持续收敛 | Studio Arranger、Python `ContextArranger` |

典型链路如下：

```text
Remote MCP Server
        |
        | validate / add / sync
        v
viking://agent/tools/<server>/<tool>.json
        |
        | mount + preset arguments
        v
viking://resources/<project>/<node>.md
        |
        | complete read
        v
OpenViking resolves credentials -> calls upstream Tool -> returns native Tool result
```

高频、参数稳定的场景适合预先挂载为实时上下文；低频或参数依赖当前问题的场景适合由 Agent 使用 `find_mcp_tools` 和 `invoke_mcp_tool` 动态发现并调用。

## 前提和安全边界

- 仅支持 Streamable HTTP MCP Server。
- 地址可以使用 HTTP 或 HTTPS。HTTP 不提供传输加密，使用方需自行评估凭证和工具数据的暴露风险。
- MCP Server 名称只能包含字母、数字、`_` 和 `-`，最长 64 个字符。
- Tool 名称必须能作为 OpenViking 文件名。
- 注册时会连接上游并执行 Tool discovery；无法连接或 Tool 定义不合法时不会写入注册信息。
- Header 由 OpenViking 服务端保存并用于上游调用。CLI 示例应从环境变量构造配置，不要把真实凭证提交到仓库。
- `invoke` 和实时节点读取可能产生外部副作用。OpenViking 不会自动重试未知结果的 Tool 调用。
- 挂载参数会按 Tool 的 `inputSchema` 校验；缺少必填参数或类型不匹配时挂载失败。

## 使用 `ov mcps`

### 验证并注册 Server

先验证连接和 Tool discovery，不写入配置：

```bash
MCP_TOKEN='replace-me'
ov mcps validate "$(jq -cn \
  --arg token "$MCP_TOKEN" \
  '{mcpServers:{weather:{url:"https://mcp.example.com/mcp",headers:{Authorization:("Bearer " + $token)}}}}')"
```

验证通过后注册。`add` 会立即写入发现到的 Tool Catalog，并默认让摘要和索引在后台继续处理：

```bash
ov mcps add "$(jq -cn \
  --arg token "$MCP_TOKEN" \
  '{mcpServers:{weather:{url:"https://mcp.example.com/mcp",headers:{Authorization:("Bearer " + $token)}}}}')"
```

需要确认 Tool 摘要和索引处理完成后再返回时，加 `-w` 或 `--wait`：

```bash
ov mcps add --wait "$(jq -cn \
  --arg token "$MCP_TOKEN" \
  '{mcpServers:{weather:{url:"https://mcp.example.com/mcp",headers:{Authorization:("Bearer " + $token)}}}}')"
```

查看注册状态和 Tool 定义：

```bash
ov mcps list
ov mcps show weather
ov mcps status weather
```

相关命令：

| 命令 | 用途 |
| --- | --- |
| `ov mcps validate <json>` | 验证连接和 Tool discovery，不持久化 |
| `ov mcps add <json> [-w\|--wait]` | 注册一个或多个 Server，并发布 Tool Catalog；默认异步处理摘要和索引 |
| `ov mcps list` | 列出当前账号注册的 Server |
| `ov mcps show <name>` | 查看 Server 元数据、Tool URI、参数和说明 |
| `ov mcps status <name>` | 实时检查连接状态；连接失败时仍返回已缓存 Tool 数量 |
| `ov mcps remove <name>` | 删除 Server 注册和 Tool Catalog |

### 查找和直接调用 Tool

Tool Catalog 支持语义检索：

```bash
ov mcps find "查询北京的天气" --limit 5
```

根据结果中的 Tool URI 和参数定义直接调用：

```bash
ov mcps invoke \
  viking://agent/tools/weather/current.json \
  --args '{"city":"Beijing"}'
```

`invoke` 由服务端解析 MCP Server 地址和 Header，并保留上游 Tool 的原生 `content`、`structuredContent` 和 `isError`。

### 挂载实时上下文

将固定参数绑定到一个 Markdown URI：

```bash
ov mcps mount \
  viking://agent/tools/weather/current.json \
  viking://resources/arrange/beijing-weather.md \
  --title "北京天气" \
  --description "北京地区的实时天气。" \
  --args '{"city":"Beijing"}' \
  --timeout-seconds 60 \
  --wait
```

读取节点会执行上游 Tool：

```bash
ov read viking://resources/arrange/beijing-weather.md
```

查看和删除挂载：

```bash
ov mcps mounts --mcp weather
ov mcps mounts --mcp weather --tool current
ov mcps unmount viking://resources/arrange/beijing-weather.md --wait
```

`mount` 对同一个 `node_uri` 是收敛式写入：再次执行会更新 Tool、参数、标题和描述，并保留最初的 `created_at`。删除节点应使用 `ov mcps unmount`，使 Markdown 节点和挂载记录一起删除。

### 同步 Tool Catalog

上游 Tool 定义变化后执行：

```bash
ov mcps sync weather
```

摘要和索引默认在后台继续处理。需要等待 Tool Catalog 的新增、更新、删除及索引刷新全部完成时执行：

```bash
ov mcps sync weather --wait
```

同步结果把 Tool 分为 `added`、`changed`、`unchanged` 和 `removed`，并列出受影响的实时节点：

| 挂载状态 | 含义 | 建议处理 |
| --- | --- | --- |
| `REVIEW` | Tool input revision 已变化，但现有参数仍符合新 schema | 检查语义变化后重新挂载确认 |
| `INVALID` | Tool input revision 已变化，现有参数不再符合新 schema | 修正参数并重新挂载 |
| `REMOVED` | 上游不再提供该 Tool | 改绑其他 Tool，或显式 unmount |

`sync` 不会自动删除受影响的实时节点。这样可以避免上游一次临时变更直接破坏已编排的目录；节点的清理仍由操作者或 CaC 的 `apply --prune` 决定。

当前 Tool 的 `revision` 覆盖完整的上游 Tool 定义；`input_revision` 根据 Tool 名称和 `inputSchema` 计算，用于判断参数契约是否变化。只修改描述、annotations 或输出 schema 时，Tool Catalog 会通过 `revision` 刷新，但不会改变 `input_revision`，也不会触发挂载检查。

## 实时节点的数据模型

挂载会生成一个带 YAML frontmatter 的 Markdown 描述文件。下面是等价结构的示意：

```markdown
---
type: OpenViking Live Context
title: 北京天气
description: 北京地区的实时天气。
x-openviking:
  schema_version: v1
  type: mcp
  definition: viking://agent/tools/weather/current.json
  input_revision: <tool-input-revision>
  args:
    city: Beijing
  timeout_seconds: 60
  retry:
    max_attempts: 3
    backoff_seconds: 1
    backoff_multiplier: 2
    max_backoff_seconds: 5
---
```

不要把这个文件当作普通 Markdown 手工维护。挂载关系还记录在账号级 `live_context_nodes.dat` 中，用于列举挂载、跟踪 input revision 和安全卸载；只修改文件会让描述文件与挂载记录失去一致性。

`retry` 是节点格式中的预留策略元数据。当前执行链路不会据此自动重试 Tool 调用，避免在结果未知时重复产生外部副作用。

Tool Catalog 和挂载数据库都属于账号级受管状态：

```text
viking://agent/tools/<server>/<tool>.json
<account>/_system/mcps.json
<account>/_system/live_context_nodes.dat
```

后两个位置是内部存储路径，不是面向用户的 Viking URI。

## 读取语义

实时执行只发生在完整读取：

- REST/CLI：`offset=0` 且 `limit=-1`。
- MCP `read`：默认完整读取。
- 返回值是上游 MCP Tool 结果，不附加节点 frontmatter 或额外标签。
- 未声明 annotations 的工具默认按只读处理。`readOnlyHint: false` 或 `destructiveHint: true` 的工具不会由 `read` 执行；错误会返回挂载 URI 和参数，供当前集成显式调用。
- 分页读取只返回已存储的描述文件，不调用上游 Tool。
- `find`、`search`、`list` 和 `tree` 不会隐式执行实时节点。
- REST `read?raw=true` 返回原始描述文件，不执行 Tool。

这些规则保证检索和目录浏览保持无副作用，同时允许把写工具挂载为 Agent 指引。Agent 应先通过检索定位节点，再调用 `read` 获取实时结果；写工具必须通过具备独立权限的 `invoke_mcp_tool` 调用。

## Agent 动态发现

OpenViking MCP endpoint 额外提供两个数据面工具：

1. `find_mcp_tools(query, limit=10)`：语义检索 Tool，返回 `uri`、`score` 和完整 `tool` 定义，包括 `inputSchema` 与 annotations。
2. `invoke_mcp_tool(tool_uri, arguments={})`：代理调用选中的 Tool，服务端解析连接信息和凭证。

典型 Agent 流程：

```text
用户意图
  -> find_mcp_tools
  -> 根据 tool.inputSchema 填充 arguments
  -> invoke_mcp_tool
  -> 使用原生 MCP Tool 结果
```

这两个接口不暴露 Server 注册、同步和挂载等控制面操作。动态发现适合低频需求；高频场景应挂载成语义稳定、参数已预绑定的实时节点，减少 Agent 的决策和试错。

## SDK 编排

Python、Go 和 TypeScript SDK 都提供 MCP 注册、发现、调用、挂载和同步 API。Python 还提供 `ContextArranger`，用于声明期望状态并批量收敛实时节点。

### 直接使用 SDK

```python
import os

from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(
    url=os.environ.get("OPENVIKING_URL", "http://127.0.0.1:1933"),
    api_key=os.environ.get("OPENVIKING_API_KEY"),
)
client.initialize()

client.mount_mcp_tool(
    tool_uri="viking://agent/tools/weather/current.json",
    node_uri="viking://resources/arrange/beijing-weather.md",
    title="北京天气",
    arguments={"city": "Beijing"},
    tags=["project=weather"],
    wait=True,
)
```

批量场景使用 `batch_mount_mcp_tools` 和 `batch_unmount_mcp_tools`。批量接口先校验整批声明，再按资源根目录写入，减少网络往返；同一批次不能重复声明 `node_uri`。

### Context as Code

```python
import os

from openviking_sdk import ContextArranger, SyncHTTPClient

client = SyncHTTPClient(
    url=os.environ.get("OPENVIKING_URL", "http://127.0.0.1:1933"),
    api_key=os.environ.get("OPENVIKING_API_KEY"),
)
client.initialize()

arranger = ContextArranger(
    client,
    name="weather-live-context",
    root_uri="viking://resources/arrange",
)

arranger.live_context(
    "beijing-weather.md",
    tool_uri="viking://agent/tools/weather/current.json",
    title="北京天气",
    arguments={"city": "Beijing"},
    tags=["domain=weather"],
)

raise SystemExit(arranger.main())
```

脚本自带 CLI：

```bash
python arrange.py plan
python arrange.py plan --output json
python arrange.py apply --dry-run
python arrange.py apply --wait
python arrange.py apply --prune --wait
```

`ContextArranger` 的行为：

- `plan` 始终展示 `CREATE`、`UPDATE`、`DELETE`、`UNCHANGED` 和 `CONFLICT`。
- `apply` 默认只创建和更新；待删除项显示为 `PENDING DELETE`。
- `apply --prune` 才删除由当前 arrangement 拥有、但不再声明的节点。
- `--dry-run` 不修改远端状态；`--wait` 等待索引处理完成。
- 默认输出适合人阅读的表格，`--output json` 适合自动化。
- 通过 `ov-cac-owner`、`ov-cac-kind` 和规格指纹标签识别所有权与漂移。
- 目标 URI 已存在但不归当前 arrangement 管理时返回冲突，不会覆盖。
- `root_uri` 可以位于账号共享的 `viking://resources` 下，也可以位于规范化的用户私有
  `viking://user/<user_id>/resources` 下；调用方必须具备所选根目录的访问权限。

一个 `ContextArranger` 只管理自己 `root_uri` 下、带有其 owner 标签的实时节点。不要让两个 arrangement 使用同一个 name 和重叠的 root 管理不同声明。

## Studio Arranger

OpenViking Studio 的 **Arranger** 页面提供同一模型的可视化入口：

- 浏览和语义检索已同步的 MCP Tool。
- 根据 `inputSchema` 配置固定参数。
- 创建、更新和删除实时上下文节点。
- 在上下文图中查看 Tool 与目标节点的关系。
- 在执行变更前检查目标路径和已有实时节点，避免覆盖不相关资源。

Studio、CLI、REST 和 SDK 共用同一组服务端 API 和挂载数据库，因此可以组合使用；但长期维护的一组节点建议选择一个声明式来源作为事实源。

## REST API

SDK 和 Studio 使用以下接口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/mcps` | 注册 MCP Server |
| `DELETE` | `/api/v1/mcps/{name}` | 删除 MCP Server |
| `GET` | `/api/v1/mcps` | 列出 Server |
| `GET` | `/api/v1/mcps/{name}` | 查看 Server 和 Tool |
| `POST` | `/api/v1/mcps/validate` | 验证连接 |
| `POST` | `/api/v1/mcps/{name}/sync` | 同步 Tool Catalog |
| `GET` | `/api/v1/mcps/status?name=...` | 检查上游状态 |
| `POST` | `/api/v1/mcps/find` | 语义检索 Tool |
| `POST` | `/api/v1/mcps/invoke` | 调用 Tool |
| `POST` | `/api/v1/mcps/mount` | 挂载单个实时节点 |
| `POST` | `/api/v1/mcps/batch-mount` | 批量挂载 |
| `DELETE` | `/api/v1/mcps/mount?node_uri=...` | 卸载单个节点 |
| `POST` | `/api/v1/mcps/batch-unmount` | 批量卸载 |
| `GET` | `/api/v1/mcps/mounts?mcp=...&tool=...` | 查询挂载 |

接口使用当前 OpenViking 用户的账号上下文。Server 注册、Tool Catalog 和挂载记录按账号隔离；目标资源 URI 仍按正常的身份和 ACL 规则校验。

## 运维建议

- 注册前先执行 `validate`，注册后执行 `status` 和一次无副作用 Tool 调用。
- 上游升级后执行 `sync`，重点处理 `INVALID` 和 `REMOVED` 挂载。
- 自动化环境使用 JSON 输出，并保留 `plan` 作为变更审计记录。
- 生产 CaC 执行先 `plan` 或 `apply --dry-run`，确认后再 `apply --prune`。
- 需要读取结果后立刻检索到最新节点时使用 `--wait`；批量发布但不依赖即时索引时可异步处理。
- 删除 Server 前先查询并处理其挂载。删除 Server 会移除 Tool Catalog，但不会代替业务方重新编排节点。
- 定期备份 OpenViking 账号数据；MCP 注册和挂载记录与其他账号数据一起持久化。

## 故障排查

| 现象 | 常见原因 | 处理 |
| --- | --- | --- |
| 注册或验证失败 | URL 无效、网络不可达、Header 错误、不是 Streamable HTTP | 检查 URL 和 Header，并用 `ov mcps validate` 重试 |
| `find` 没有结果 | Tool Catalog 未发布、索引尚未完成、查询不匹配 | `ov mcps show` 确认 Tool，执行 `sync`，必要时等待索引完成 |
| 挂载提示参数不合法 | 参数不符合当前 `inputSchema` | 从 `show` 或 `find` 结果重新生成参数 |
| `read` 返回描述文件 | 使用了分页读取或 `raw=true` | 使用默认完整读取 |
| `read` 调用失败且结果未知 | 上游超时或连接在调用中断开 | 先确认上游实际状态；OpenViking 不会自动重试可能有副作用的调用 |
| `sync` 返回 `REVIEW` | Tool input revision 改变但参数仍合法 | 检查 Tool 语义后重新挂载 |
| `sync` 返回 `INVALID` | 新 schema 不再接受已绑定参数 | 修改参数并重新挂载 |
| `sync` 返回 `REMOVED` | Tool 已从上游删除 | 绑定替代 Tool，或显式 unmount |
| CaC 返回 `CONFLICT` | 目标已存在但不归当前 arrangement 所有 | 更换 URI，或先确认并迁移现有节点 |
| 普通 `apply` 没删除节点 | 删除默认受保护 | 查看 `PENDING DELETE`，确认后执行 `apply --prune` |

## 设计取舍

| 设计 | 原因 | 代价 |
| --- | --- | --- |
| Tool 定义发布到 VikingFS | Tool 可被统一检索、读取和版本化 | 上游变化后需要显式 `sync` |
| 参数在挂载时预绑定 | 高频场景调用稳定，Agent 不需重复推断固定参数 | 参数变化需要重新挂载 |
| 完整 `read` 才执行 | 检索、列表和分页浏览保持无副作用 | 调试描述文件需要分页或 raw 读取 |
| 同步不自动删除节点 | 上游变化不会直接破坏业务目录 | 操作者必须处理 `REVIEW/INVALID/REMOVED` |
| CaC 默认不 prune | 避免声明遗漏造成意外删除 | 清理需要显式 `--prune` |
| 调用不自动重试 | 避免未知结果下重复产生副作用 | 瞬时故障需要业务侧判断后重试 |
