# MCP 接入

### 步骤 1：MCP 配置

```json
{
  "mcpServers": {
    "ov-mcp-server": {
      "url": "https://api.vikingdb.cn-beijing.volces.com/openviking/mcp",
      "headers": {
        "Authorization": "Bearer ZGVmYXV********YzdlZjhiMg"
      }
    }
  }
}
```

**关键说明**：`Authorization` 的值需带上 `Bearer` 前缀（注意空格），完整格式为 `Bearer \&lt;API Key\&gt;`。
### 步骤 2：测试 MCP 工具连通性

输入 `ov health` 检查 ov 的版本和连接状态
```bash
ov health
```
