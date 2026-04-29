# MCP 集成指南

OpenViking 服务器内置 [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) 端点，任何兼容 MCP 的客户端都可以通过 HTTP 直接访问其记忆和资源能力，无需部署额外进程。

## 前提条件

1. 已安装 OpenViking（`pip install openviking` 或从源码安装）
2. 有效的配置文件（参见[配置指南](01-configuration.md)）
3. `openviking-server` 正在运行（参见[部署指南](03-deployment.md)）

MCP 端点位于 `http://<server>:1933/mcp`，与 REST API 同进程、同端口。

## 已验证的接入平台

以下平台已成功接入并使用 OpenViking MCP：

| 平台 | 接入方式 |
|------|----------|
| **Claude Code** | `type: http` 接入 |
| **ChatGPT & Codex** | 标准 MCP 配置 |
| **Claude.ai / Claude Desktop** | 通过 MCP-Key2OAuth 代理接入 |
| **Manus** | 标准 MCP 配置 |
| **Trae** | 标准 MCP 配置 |

## 鉴权方式

MCP 端点的鉴权与 OpenViking REST API 完全一致，复用同一套 API-Key 认证系统。传入以下任一 header 即可：

- `X-Api-Key: <your-key>`
- `Authorization: Bearer <your-key>`

本地开发模式（服务器绑定 localhost）下无需认证。

## 客户端配置

### 通用 MCP 客户端

大多数支持 MCP 的平台（如 Trae、Manus、Cursor 等）使用标准的 `mcpServers` 配置格式：

```json
{
  "mcpServers": {
    "openviking": {
      "url": "https://your-server.com/mcp",
      "headers": {
        "Authorization": "Bearer your-api-key-here"
      }
    }
  }
}
```

### Claude Code

Claude Code 需要额外指定 `"type": "http"`。可通过命令行添加：

```bash
claude mcp add --transport http openviking \
  https://your-server.com/mcp \
  --header "Authorization: Bearer your-api-key-here"
```

或在 `.mcp.json` 中手动配置：

```json
{
  "mcpServers": {
    "openviking": {
      "type": "http",
      "url": "https://your-server.com/mcp",
      "headers": {
        "Authorization": "Bearer your-api-key-here"
      }
    }
  }
}
```

加 `--scope user` 可将配置设为全局（所有项目共享）。

### Claude.ai / Claude Desktop（OAuth 代理鉴权）

Claude.ai 和 Claude Desktop Connector 强制要求 MCP 服务器使用 OAuth 鉴权，无法直接传入 API Key。可借助 [MCP-Key2OAuth](https://mcp.767911.xyz/) 将 API Key 认证代理为 OAuth 流程：

1. 访问 https://mcp.767911.xyz/
2. 填入你的 OpenViking MCP 服务器 URL（如 `https://your-server.com/mcp`）
3. 生成代理后的新 URL
4. 在 Claude.ai / Claude Desktop 中填入生成的新 URL，连接时会跳转至代理站进行鉴权
5. 授权完成后即可正常使用

> ⚠️ MCP-Key2OAuth 为社区项目，OpenViking 不保证其安全性。


## 可用的 MCP 工具

连接后，OpenViking MCP 端点暴露 9 个工具：

| 工具 | 说明 | 主要参数 |
|------|------|----------|
| `search` | 语义搜索记忆、资源和技能 | `query`, `target_uri`(可选), `limit`, `min_score` |
| `read` | 读取一个或多个 `viking://` URI 的内容 | `uris`（单个字符串或数组） |
| `list` | 列出 `viking://` 目录下的条目 | `uri`, `recursive`(可选) |
| `store` | 存储消息到长期记忆（触发记忆提取） | `messages`（`{role, content}` 列表） |
| `add_resource` | 添加本地文件或 URL 作为资源 | `path`, `description`(可选) |
| `grep` | 在 `viking://` 文件中进行正则内容搜索 | `uri`, `pattern`（字符串或数组）, `case_insensitive` |
| `glob` | 按 glob 模式匹配文件 | `pattern`, `uri`(可选范围) |
| `forget` | 删除任意 `viking://` URI（先用 `search` 查找） | `uri` |
| `health` | 检查 OpenViking 服务健康状态 | 无 |

## 故障排除

### 连接被拒绝

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
