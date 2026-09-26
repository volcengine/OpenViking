# MCP 客户端

支持 [MCP](https://modelcontextprotocol.io/) Streamable HTTP 的客户端可直接连接 OpenViking 的 `/mcp` 端点。仅支持 stdio 的客户端可使用 [Agent Plugins 包](./15-agent-plugins.md)中的代理。

## 快速配置

以下示例适用于接受 `mcpServers` 和自定义请求头的客户端。其他客户端按下方对应说明配置：

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

服务运行在 `dev` 模式时无需认证，应仅监听本机回环地址。认证模式下，即使从本机访问也需要凭据。

## 各平台注意事项

### Claude Code

Claude Code 需要额外指定 `"type": "http"`，通过命令行添加：

```bash
claude mcp add --transport http openviking \
  https://your-server.com/mcp \
  --header "Authorization: Bearer your-api-key-here"
```

加 `--scope user` 使配置全局生效。

> 如果你需要免工具调用的自动召回与自动捕获，请使用 [Claude Code 记忆插件](./02-claude-code.md)。

<a id="trae-cursor-chatgpt"></a>

### Trae / Cursor

在客户端的 MCP 配置中添加上述服务 URL 和 API Key。

### ChatGPT

通过开发者模式创建自定义 App，再完成 OAuth 授权，见 [OAuth 指南](../guides/11-oauth.md#chatgpt)。

### Codex

Codex 请使用 [Codex 记忆插件](./04-codex.md)。插件通过 manifest 提供 stdio MCP 代理，并让 MCP 与生命周期 hooks 共用同一套凭据配置。

### OpenCode

在 `~/.config/opencode/opencode.json` 中使用 OpenCode 原生 `mcp` 配置：

```json
{
  "mcp": {
    "openviking": {
      "type": "remote",
      "url": "https://your-server.com/mcp",
      "enabled": true,
      "oauth": false,
      "headers": {
        "Authorization": "Bearer your-api-key-here"
      }
    }
  }
}
```

### Claude Desktop / Claude.ai (OAuth)

托管的远程连接器流程使用 OpenViking 原生 OAuth；在授权页填写已有的 OpenViking User/Admin Key。Claude Desktop 的本地 stdio 配置是另一种接入方式。

在服务端启用 `oauth.enabled` 并配置 HTTPS 后，让客户端连接 `https://your-server.com/mcp`，在浏览器中完成授权。

HTTPS 配置、部署模板和完整授权流程详见 [OAuth 2.1 指南](../guides/11-oauth.md) 和 [公网访问指南](../guides/12-public-access.md)。

## 可用工具

连接后，OpenViking 会提供检索、记忆、资源、watch 和文件系统工具。完整工具清单、参数、渐进式文件上传和高级配置见 [MCP 集成指南](../guides/06-mcp-integration.md#可用的-mcp-工具)。

## 故障排查

| 现象 | 修复 |
|------|------|
| 连接被拒绝 | 确认 `openviking-server` 正在运行：`curl http://localhost:1933/health` |
| 认证错误 | 检查客户端是否使用有效的 user/admin key。见 [鉴权指南](../guides/04-authentication.md) |

## 参见

- [集成能力参考](./16-capability-reference.md)
- [MCP 集成指南](../guides/06-mcp-integration.md) — 工具参数、渐进式上传、`OPENVIKING_PUBLIC_BASE_URL`
- [OAuth 2.1 指南](../guides/11-oauth.md) — 用于 Claude Desktop、Claude.ai、Cursor
- [MCP 规范](https://modelcontextprotocol.io/)
