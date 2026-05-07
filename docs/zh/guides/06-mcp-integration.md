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
| **Claude.ai / Claude Desktop** | 原生 OAuth 2.1（见 [11-oauth](11-oauth.md)） |
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

### Claude.ai / Claude Desktop / ChatGPT / Cursor（OAuth）

这些客户端只接受 OAuth 2.1，不接受 API Key。OpenViking 已经原生实现 OAuth 2.1（DCR + PKCE + opaque token，SQLite 后端，配合 console 驱动的 OTP 授权页），不再需要外部代理。

**详见 [OAuth 2.1 接入指南](11-oauth.md)**：

- 端到端流程（device-flow 风格：authorize 页显示 6 字符码，用户在 console 确认）
- HTTP（本地）与 HTTPS（生产）两阶段部署，包含 Caddy / nginx 反代模板和 docker-compose 示例
- Claude.ai / Claude Desktop / Cursor / ChatGPT 接入步骤
- `OPENVIKING_PUBLIC_BASE_URL` 与 `oauth` 配置项
- Token 模型（`ovat_` / `ovrt_` / `ovac_` 前缀）与撤销

> 社区项目 [MCP-Key2OAuth](https://github.com/t0saki/MCP-Key2OAuth) Cloudflare Worker 代理仍可作为第三方备选方案，但现在更推荐原生流程：无需额外部署单元，也不会引入第三方对 API Key 的信任面。


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
