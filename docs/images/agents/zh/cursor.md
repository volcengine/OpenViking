
## 一、前置准备：获取 API Key

所有 MCP 客户端的接入都依赖同一个 **Authorization Token**，即 OpenViking 控制台中的 API Key。请先按以下步骤获取并妥善保存。

### 1\.1 操作路径

1. 在左侧菜单选择 **用户管理**

2. 在用户列表中找到对应用户（个人版默认为 `default` / `admin`），点击 API Key 列右侧的 **复制** 图标

3. 将复制得到的 `ZGV\.\.\.hiMg` 形式的字符串妥善保存，作为后续所有 Agent 接入的 `Authorization` 值

**安全提示**：API Key 等同于账户密钥，请勿提交到 Git 仓库或公开渠道。建议通过环境变量或加密配置注入。

![复制 OpenViking API Key](https://docs.openviking.net/agents/image/cursor/01-api-key.jpg)



## 二、接入 Cursor 指南

以下为 OpenViking 标准接入 Cursor 的流程。

### 2\.1 接入步骤

#### 步骤 1：打开设置

在 Cursor 主界面右上角点击 **设置（齿轮图标）**，进入设置面板

![打开 Cursor 设置](https://docs.openviking.net/agents/image/cursor/02-open-settings.png)

#### 步骤 2：新增 MCP Server

在左侧菜单中选择 **Tools \&amp; MCPs**，进入 MCP Servers 管理页。

![进入 Tools and MCPs 页面](https://docs.openviking.net/agents/image/cursor/03-tools-and-mcps.png)



点击 **Add Custom MCP** 按钮。

![添加自定义 MCP Server](https://docs.openviking.net/agents/image/cursor/04-add-custom-mcp.png)



#### 步骤 3：粘贴配置 JSON

在弹出的 **mcp\.json** 文件中粘贴以下 JSON，并将 `Authorization` 替换为第一章中复制的 API Key：

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

![粘贴 MCP JSON 配置](https://docs.openviking.net/agents/image/cursor/05-paste-mcp-json.jpg)

#### 步骤 4：确认并启用

保存 mcp\.json 配置文件并关闭后，Cursor 会自动建立 MCP 连接并加载工具列表。连接成功后，**`ov\-mcp\-server`** 会出现在 **Installed MCP Servers** 列表中，同时会显示已启用的工具数量（图中的 “10 tools enabled”）。配置完成后，可直接看到 `ov\-mcp\-server` 条目旁的开关呈绿色开启状态，代表服务已正常加载并就绪：

![确认并启用 MCP Server](https://docs.openviking.net/agents/image/cursor/06-enable-server.png)

#### 步骤 5：MCP 连通性检查

接入后建议通过两个简单 query 快速验证 MCP 是否正常工作。在 Cursor 对话框中依次输入：

**① ** **`ov ls`** — 列出 OpenViking 根目录内容，确认连接畅通、可正确返回目录结构。

![运行 ov ls 验证连接](https://docs.openviking.net/agents/image/cursor/07-ov-ls.png)

**② ****`ov health`** — 调用 health 工具，确认 OpenViking 服务端状态与当前用户身份。

![运行 ov health 验证服务状态](https://docs.openviking.net/agents/image/cursor/08-ov-health.png)

**验收标准**：`ov ls` 能返回 `agent / resources / session / user` 等目录；`ov health` 返回 `service initialized` 与当前用户名，即表示接入成功。



### 2\.2 配置参数说明

|字段|必填|说明|
|---|---|---|
|`mcpServers`|是|MCP Server 配置根节点|
|`ov\-mcp\-server`|是|服务别名，可自定义；建议保持与上下文识别一致|
|`url`|是|OpenViking MCP 服务端点；CN 区固定为 `https://api\.vikingdb\.cn\-beijing\.volces\.com/openviking/mcp`|
|`headers\.Authorization`|是|格式 `Bearer \&lt;API Key\&gt;`，来源见第一章|

---

## 三、常见问题（FAQ）

|问题|解决建议|
|---|---|
|连接失败 / 401 Unauthorized|检查 `Authorization` 是否带 `Bearer` 前缀；确认 API Key 未过期或被重置|
|连接失败 / 网络超时<br>|确认网络可访问 `api\.vikingdb\.cn\-beijing\.volces\.com`；企业网络请配置代理白名单|
|Agent 无法识别工具|检查 MCP Server 是否已\&\#34;启用\&\#34;；部分客户端需重启进程后加载新配置|

