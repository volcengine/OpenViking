请为当前 Agent 安装或更新 OpenViking MCP，并安装最新官方 skills。

### 步骤 1：MCP 配置

使用以下连接配置：

```json
{
  "mcpServers": {
    "ov-mcp-server": {
      "url": "{{OPENVIKING_BASE_URL}}/mcp",
      "headers": {
        "Authorization": "Bearer {{OPENVIKING_API_KEY}}"
      }
    }
  }
}
```

自行识别客户端及其支持的配置方式（设置界面、配置文件或安装命令），不假设支持 JSON 合并。若已有 OpenViking MCP server，先让我选择“沿用现有连接”或“使用上述新配置”，选择后再操作；保留其他 MCP 服务，不重复添加同一服务。使用已提供的 Key，不重复索取，不回显或写入命令参数、日志与规则。

按官方方式升级已有的本地 MCP 组件；远程 HTTPS MCP 则更新所选连接并重新启用，不将重连称为升级了云端服务。

### 步骤 2：安装最新 skills

从 https://github.com/volcengine/OpenViking/tree/main/agent-plugins/skills 获取全部最新 skill 包，安装或更新到客户端支持的位置，保留完整附带文件；已有定制先说明差异再处理。

### 步骤 3：测试工具有效性

重新加载后，确认 MCP 工具列表可见，调用 OpenViking 的 `health` 工具校验有效性，再用 `list` 查看当前账号可访问的目录，并确认安装的 skills 可发现。`ov health` 只验证 CLI 连接，不能证明这个客户端的 MCP 已接通。
