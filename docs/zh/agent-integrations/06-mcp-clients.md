# MCP 客户端

任何兼容 [MCP](https://modelcontextprotocol.io/) 的客户端都可以直接连接 OpenViking 内置的 `/mcp` 端点——无需安装插件或启动额外进程。适用于 Cursor、Trae、Manus、Claude Desktop、ChatGPT 等。

## 快速配置

大多数 MCP 客户端使用标准 `mcpServers` 格式：

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

本地服务未配置 `root_api_key` 时（dev 模式）无需认证。

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

### Trae / Cursor / ChatGPT / Codex / OpenCode

使用上面的标准 `mcpServers` 配置即可——均已通过 API Key 鉴权验证。

### Claude Desktop / Claude.ai (OAuth)

这些客户端要求 OAuth 2.1——无法直接传 API Key。OpenViking 自带原生 OAuth 2.1 实现，无需外部代理。

如果你已经为 OpenViking 服务配好了 HTTPS，直接连接 `https://your-server.com/mcp` 端点即可——客户端会自动引导你完成 OAuth 授权流程。

HTTPS 配置、部署模板和完整授权流程详见 [OAuth 2.1 指南](../guides/11-oauth.md) 和 [公网访问指南](../guides/12-public-access.md)。

## 可用工具

连接后 OpenViking 暴露 14 个工具：

| 工具 | 说明 |
|------|------|
| `search` | 跨记忆、资源、技能的语义搜索 |
| `read` | 读取一个或多个 `viking://` URI |
| `list` | 列出 `viking://` 目录下的条目 |
| `store` | 存储消息到长期记忆 |
| `add_resource` | 添加本地文件或 URL 作为资源 |
| `grep` | 正则内容搜索 |
| `glob` | 按 glob 模式查找文件 |
| `forget` | 删除 `viking://` URI |
| `code_outline` | 展示文件的符号结构 |
| `code_search` | 跨目录搜索符号名 |
| `code_expand` | 返回单个符号的完整源码 |
| `health` | 检查 OpenViking 服务状态 |
| `list_watches` | 列出自动刷新订阅 |
| `cancel_watch` | 取消 watch 任务 |

工具参数、渐进式文件上传和高级配置见 [MCP 集成指南](../guides/06-mcp-integration.md)。

## 故障排查

| 现象 | 修复 |
|------|------|
| 连接被拒绝 | 确认 `openviking-server` 正在运行：`curl http://localhost:1933/health` |
| 认证错误 | 确保客户端配置中的 API Key 与服务端一致。见 [鉴权指南](../guides/04-authentication.md) |

## 参见

- [MCP 集成指南](../guides/06-mcp-integration.md) — 工具参数、渐进式上传、`OPENVIKING_PUBLIC_BASE_URL`
- [OAuth 2.1 指南](../guides/11-oauth.md) — 用于 Claude Desktop、Claude.ai、Cursor
- [MCP 规范](https://modelcontextprotocol.io/)
