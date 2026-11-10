DeerFlow 支持通过 MCP Server 接入 OpenViking。MCP 接入的核心价值是打通知识检索能力，让 DeerFlow Agent 能够在任务执行过程中主动搜索、读取和使用 OpenViking 中的记忆与知识。

## 步骤 1：配置 OpenViking 鉴权信息

在 DeerFlow 项目根目录下编辑 `.env` 文件，把 API Key 填进去：

{{OPENVIKING_API_KEY_BLOCK}}

## 步骤 2：创建 MCP 配置文件

复制 `extensions_config.example.json` 到项目根目录下的 `extensions_config.json`，用于配置 DeerFlow 可加载的 MCP Server：

```bash
cp extensions_config.example.json extensions_config.json
```

## 步骤 3：配置 OpenViking MCP Server

打开项目根目录下的 `extensions_config.json`，在 `mcpServers` 中添加 OpenViking 配置：

```json
{
  "mcpServers": {
    "openviking": {
      "enabled": true,
      "type": "http",
      "url": "{{OPENVIKING_BASE_URL}}/mcp",
      "headers": {
        "X-API-Key": "$OPENVIKING_API_KEY"
      }
    }
  }
}
```

## 步骤 4：重启 DeerFlow

保存 `.env` 和 `extensions_config.json` 后，重新启动 DeerFlow：

```bash
make dev
```

## 故障排查

| 现象 | 原因 | 修复 |
|------|------|------|
| DeerFlow 启动后未加载 OpenViking MCP Server | `extensions_config.json` 未配置、配置格式错误，或 `enabled` 未设置为 `true` | 检查 `mcpServers.openviking` 配置，并确认 JSON 格式正确 |
| OpenViking MCP 工具未出现在 Agent 可用工具中 | MCP 配置未生效，或服务未重启 | 保存配置后重启 DeerFlow，或刷新 MCP 配置缓存 |
| 调用 OpenViking MCP 工具失败，返回 401 或 403 | API Key 缺失、错误或无权限 | 检查 `.env` 中的 `OPENVIKING_API_KEY` 是否正确，并确认 Header 使用 `X-API-Key` |
| MCP Server 连接失败 | `url` 配置错误，或 DeerFlow Gateway 无法访问 OpenViking MCP Server | 检查 OpenViking MCP Server 地址、网络连通性和 Docker 网络配置 |
| 修改 `.env` 后仍然使用旧鉴权信息 | 环境变量未重新加载，或 MCP 配置缓存未刷新 | 重启 DeerFlow，或调用 `/api/mcp/cache/reset` 刷新缓存 |
| Agent 没有主动调用 OpenViking 工具 | MCP 工具由模型按需调用，不是自动记忆后端 | 在提示词中明确要求使用 OpenViking 工具，或改用 MemoryManager 接入实现自动召回 |
