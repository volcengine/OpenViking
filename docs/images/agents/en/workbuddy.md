## Step 1: Configure MCP

1. Open WorkBuddy, select **Experts · Skills · Connectors** in the sidebar, and open the **Connectors** tab.
![Open WorkBuddy Connectors](https://docs.openviking.net/agents/image/workbuddy/01-open-connectors.png)

2. Select **Custom Connector** in the upper-right corner to open MCP service management.
![Open Custom Connector](https://docs.openviking.net/agents/image/workbuddy/02-custom-connector.png)

3. Select **Configure MCP** to open the MCP configuration editor.
![Open the MCP configuration editor](https://docs.openviking.net/agents/image/workbuddy/03-configure-mcp.png)

4. Add this configuration:

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

5. Select **Save** in the upper-right corner. The configuration is saved when the green success message appears.
![Save the MCP configuration](https://docs.openviking.net/agents/image/workbuddy/04-save-config.png)

6. Return to the MCP list. If WorkBuddy asks you to trust this MCP service on first connection, select **Trust**.
![Trust the OpenViking MCP service](https://docs.openviking.net/agents/image/workbuddy/05-trust-server.png)

## Step 2: Verify

Return to the MCP list. Confirm that `OpenViking` appears under “My MCP,” is enabled, and shows enabled tools when expanded.

![Verify OpenViking MCP tools](https://docs.openviking.net/agents/image/workbuddy/06-verify-tools.png)

## Troubleshooting

| Issue | Resolution |
|---|---|
| `OpenViking` does not appear in the MCP list | Check the JSON syntax and save again |
| MCP connection status is abnormal | Refresh the connection; if it still fails, check the JSON configuration and network connectivity |
