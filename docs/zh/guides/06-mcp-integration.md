# MCP 集成指南

OpenViking 可以作为 [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) 服务器使用，任何兼容 MCP 的客户端都可以访问其记忆和资源能力。

## 传输模式

OpenViking 支持两种 MCP 传输模式：

| | HTTP (SSE) | stdio |
|---|---|---|
| **工作方式** | 单个长期运行的服务器进程；客户端通过 HTTP 连接 | 宿主为每个会话生成一个新的 OpenViking 进程 |
| **多会话安全** | ✅ 是 — 单进程，无锁竞争 | ⚠️ **否** — 多进程争用同一数据目录 |
| **推荐用于** | 生产环境、多 Agent、多会话 | 仅限单会话本地开发 |
| **配置复杂度** | 需要单独运行 `openviking-server` | 零配置 — 宿主管理进程 |

### 选择合适的传输模式

- **使用 HTTP**：如果你的宿主会打开多个会话、运行多个 Agent，或需要并发访问。
- **使用 stdio**：仅在单会话、单 Agent 的本地环境中，且追求简单时使用。

> ⚠️ **重要提示：** 当 MCP 宿主为每个会话生成独立的 stdio OpenViking 进程时（例如每个聊天会话一个进程），所有实例会争用同一底层数据目录。这会导致存储层（AGFS 和 VectorDB）的 **锁/资源竞争**。
>
> 表现为以下误导性错误：
> - `Collection 'context' does not exist`
> - `Transport closed`
> - 间歇性搜索失败
>
> **根因不是索引损坏** — 而是多个进程争用同一存储文件。切换到 HTTP 模式即可解决。详见[故障排除](#故障排除)。

## 配置

### 前提条件

1. 已安装 OpenViking（`pip install openviking` 或从源码安装）
2. 有效的配置文件（参见[配置指南](01-configuration.md)）
3. HTTP 模式需要：`openviking-server` 正在运行（参见[部署指南](03-deployment.md)）

### HTTP 模式（推荐）

首先启动 OpenViking 服务器：

```bash
openviking-server --config /path/to/config.yaml
# 默认地址：http://localhost:1933
```

然后配置你的 MCP 客户端通过 HTTP 连接。

### stdio 模式

无需单独启动服务器 — MCP 宿主直接启动 OpenViking。

## 客户端配置

### Claude Code (CLI)

**HTTP 模式：**

```bash
claude mcp add openviking \
  --transport sse \
  "http://localhost:1933/mcp"
```

**stdio 模式：**

```bash
claude mcp add openviking \
  --transport stdio \
  -- python -m openviking.server --transport stdio \
     --config /path/to/config.yaml
```

### Claude Desktop

编辑 `claude_desktop_config.json`：

**HTTP 模式：**

```json
{
  "mcpServers": {
    "openviking": {
      "url": "http://localhost:1933/mcp"
    }
  }
}
```

**stdio 模式：**

```json
{
  "mcpServers": {
    "openviking": {
      "command": "python",
      "args": [
        "-m", "openviking.server",
        "--transport", "stdio",
        "--config", "/path/to/config.yaml"
      ]
    }
  }
}
```

### Cursor

在 Cursor 设置 → MCP 中配置：

**HTTP 模式：**

```json
{
  "mcpServers": {
    "openviking": {
      "url": "http://localhost:1933/mcp"
    }
  }
}
```

**stdio 模式：**

```json
{
  "mcpServers": {
    "openviking": {
      "command": "python",
      "args": [
        "-m", "openviking.server",
        "--transport", "stdio",
        "--config", "/path/to/config.yaml"
      ]
    }
  }
}
```

### OpenClaw

在 OpenClaw 配置文件（`openclaw.json` 或 `openclaw.yaml`）中：

**HTTP 模式（推荐）：**

```json
{
  "mcp": {
    "servers": {
      "openviking": {
        "url": "http://localhost:1933/mcp"
      }
    }
  }
}
```

**stdio 模式：**

```json
{
  "mcp": {
    "servers": {
      "openviking": {
        "command": "python",
        "args": [
          "-m", "openviking.server",
          "--transport", "stdio",
          "--config", "/path/to/config.yaml"
        ]
      }
    }
  }
}
```

## 可用的 MCP 工具

连接后，OpenViking 提供以下 MCP 工具：

| 工具 | 说明 |
|------|------|
| `search` | 跨记忆和资源的语义搜索 |
| `add_memory` | 存储新记忆 |
| `add_resource` | 添加资源（文件、文本、URL） |
| `get_status` | 检查系统健康状态和组件状态 |
| `list_memories` | 浏览已存储的记忆 |
| `list_resources` | 浏览已存储的资源 |

完整参数详情请参考 OpenViking 的工具文档。

## 故障排除

### `Collection 'context' does not exist`

**可能原因：** 多个 stdio MCP 实例争用同一数据目录。

**解决方案：** 切换到 HTTP 模式。如果必须使用 stdio，请确保同一时间只有一个 OpenViking 进程访问给定的数据目录。

### `Transport closed`

**可能原因：** MCP stdio 进程因资源竞争而崩溃或被终止。也可能发生在后端重启后客户端持有过期连接时。

**解决方案：**
1. 切换到 HTTP 模式以避免竞争。
2. 如果使用 HTTP：在客户端中重新加载 MCP 连接（重启会话或重新连接）。

### HTTP 端点连接被拒绝

**可能原因：** `openviking-server` 未运行，或运行在不同端口上。

**解决方案：** 验证服务器是否正在运行：

```bash
curl http://localhost:1933/health
# 预期返回：{"status": "ok"}
```

### 认证错误

**可能原因：** 客户端配置与服务器配置中的 API 密钥不匹配。

**解决方案：** 确保 MCP 客户端配置中的 API 密钥与 OpenViking 服务器配置中的一致。参见[认证指南](04-authentication.md)。

## 参考

- [MCP 规范](https://modelcontextprotocol.io/)
- [OpenViking 配置](01-configuration.md)
- [OpenViking 部署](03-deployment.md)
- [相关 Issue：stdio 竞争问题 (#473)](https://github.com/volcengine/OpenViking/issues/473)
