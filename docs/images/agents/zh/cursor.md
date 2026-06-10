## 一、Cursor 接入 OpenViking 操作步骤

将 OpenViking 接入 Cursor 的流程，请在 Cursor 中按以下步骤操作：

### 步骤 1：打开 Cursor 设置

在 Cursor 主界面右上角点击 **设置（齿轮图标）**，进入设置面板

![打开 Cursor 设置](https://docs.openviking.net/agents/image/cursor/02-open-settings.png)


### 步骤 2：新增 MCP Server

在左侧菜单中选择 **Tools \&amp; MCPs**，进入 MCP Servers 管理页。

![进入 Tools and MCPs 页面](https://docs.openviking.net/agents/image/cursor/03-tools-and-mcps.png)


点击 **Add Custom MCP** 按钮。

![添加自定义 MCP Server](https://docs.openviking.net/agents/image/cursor/04-add-custom-mcp.png)


### 步骤 3：粘贴配置 JSON

在弹出的 **mcp\.json** 文件中粘贴以下 JSON，其中 `Authorization` 等字段已经自动填入 API Key 值：

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

### 步骤 4：确认并启用

保存 mcp\.json 配置文件并关闭后，Cursor 会自动建立 MCP 连接并加载工具列表。连接成功后，**`ov\-mcp\-server`** 会出现在 **Installed MCP Servers** 列表中，同时会显示已启用的工具数量（图中的 “10 tools enabled”）。配置完成后，可直接看到 `ov\-mcp\-server` 条目旁的开关呈绿色开启状态，代表服务已正常加载并就绪：

![确认并启用 MCP Server](https://docs.openviking.net/agents/image/cursor/06-enable-server.png)

### 步骤 5：MCP 连通性检查

接入后建议通过两个简单 query 快速验证 MCP 是否正常工作。在 Cursor 对话框中依次输入：

**① ** **`ov ls`** — 列出 OpenViking 根目录内容，确认连接畅通、可正确返回目录结构：
```bash
ov ls
```

![运行 ov ls 验证连接](https://docs.openviking.net/agents/image/cursor/07-ov-ls.png)

**② ****`ov health`** — 调用 health 工具，确认 OpenViking 服务端状态与当前用户身份：
```bash
ov health
```

![运行 ov health 验证服务状态](https://docs.openviking.net/agents/image/cursor/08-ov-health.png)


### 步骤 6：验收标准

`ov ls` 能返回 `agent / resources / session / user` 等目录；`ov health` 返回 `service initialized` 与当前用户名，即表示接入成功。



## 二、配置参数说明

|字段|必填|说明|
|---|---|---|
|`mcpServers`|是|MCP Server 配置根节点|
|`ov\-mcp\-server`|是|服务别名，可自定义；建议保持与上下文识别一致|
|`url`|是|OpenViking MCP 服务端点；CN 区固定为 `https://api\.vikingdb\.cn\-beijing\.volces\.com/openviking/mcp`|
|`headers\.Authorization`|是|格式 `Bearer \&lt;API Key\&gt;`，来源见第一章|

## 三、常见问题（FAQ）

|问题|解决建议|
|---|---|
|连接失败 / 401 Unauthorized|检查 `Authorization` 是否带 `Bearer` 前缀；确认 API Key 未过期或被重置|
|连接失败 / 网络超时<br>|确认网络可访问 `api\.vikingdb\.cn\-beijing\.volces\.com`；企业网络请配置代理白名单|
|Agent 无法识别工具|检查 MCP Server 是否已\&\#34;启用\&\#34;；部分客户端需重启进程后加载新配置|
