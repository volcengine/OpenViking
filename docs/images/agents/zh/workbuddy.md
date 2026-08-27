## 步骤1：配置 MCP

1. 打开 WorkBuddy，点击左侧导航栏的 **专家·技能·连接器**，进入 **连接器** 标签页。
![打开 WorkBuddy 连接器](https://docs.openviking.net/agents/image/workbuddy/01-open-connectors.png)

2. 点击右上角的 **自定义连接器**，进入 MCP 服务管理面板。
![打开自定义连接器](https://docs.openviking.net/agents/image/workbuddy/02-custom-connector.png)

3. 点击 **配置 MCP**，进入 MCP 配置编辑器。
![进入 MCP 配置编辑器](https://docs.openviking.net/agents/image/workbuddy/03-configure-mcp.png)

4. 在配置文件中填入以下内容：

   ```json
   {
     "mcpServers": {
       "OpenViking": {
         "url": "https://api.vikingdb.cn-beijing.volces.com/openviking/mcp",
         "headers": {
           "Authorization": "Bearer {{OPENVIKING_API_KEY}}"
         }
      }
     }
   }
   ```

5. 点击右上角的 **保存**。顶部出现“配置保存成功”的绿色提示后，配置即已保存。
![保存 MCP 配置](https://docs.openviking.net/agents/image/workbuddy/04-save-config.png)

6. 返回 MCP 列表。如果系统提示“首次连接此 MCP 服务需要您的信任确认”，点击 **信任** 完成接入。
![信任 OpenViking MCP 服务](https://docs.openviking.net/agents/image/workbuddy/05-trust-server.png)

## 步骤2：验证

返回 MCP 列表页，确认 `OpenViking` 出现在“我的 MCP”中、状态为开启，展开后可看到已启用工具。

![验证 OpenViking MCP 工具](https://docs.openviking.net/agents/image/workbuddy/06-verify-tools.png)

## 故障排查

| 问题 | 处理 |
|---|---|
| MCP 列表中未出现 `OpenViking` | 检查 JSON 配置格式并重新保存 |
| MCP 连接状态异常 | 刷新连接；若仍异常，检查 JSON 配置及网络连通性 |
