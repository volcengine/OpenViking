# Cursor MCP 接入

# 一、前置准备：获取 API Key

所有 MCP 客户端的接入都依赖同一个 **Authorization Token**，即 OpenViking 控制台中的 API Key。请先按以下步骤获取并妥善保存。

## 1\.1 操作路径

1. 在左侧菜单选择 **用户管理**

2. 在用户列表中找到对应用户（个人版默认为 `default` / `admin`），点击 API Key 列右侧的 **复制** 图标

3. 将复制得到的 `ZGV\.\.\.hiMg` 形式的字符串妥善保存，作为后续所有 Agent 接入的 `Authorization` 值

**安全提示**：API Key 等同于账户密钥，请勿提交到 Git 仓库或公开渠道。建议通过环境变量或加密配置注入。

![Image](https://internal-api-drive-stream.larkoffice.com/space/api/box/stream/download/authcode/?code=MzZmZWNhZTllNzVkZjcwYTYwYzgwZjJhMTM2ZWE1YTlfODc5N2M4NTRmZjY2MzRmNTcyMDU1MzZmNDMyMzA2ZmNfSUQ6NzY0NTIxMzYzMzUwNDc5MTQ5NV8xNzgwMDQ5ODkzOjE3ODAxMzYyOTNfVjM)



# 二、Cursor 接入指南

以下为 OpenViking 标准接入 Cursor 的流程。

## 2\.1 接入步骤

### 步骤 1 · 打开设置

在 Cursor 主界面右上角点击 **设置（齿轮图标）**，进入设置面板

![Image](https://internal-api-drive-stream.larkoffice.com/space/api/box/stream/download/authcode/?code=ZDQ4MDJlNzdkY2MxZTNhNWUzMWI0YzllY2MyZTI1OTdfYTFhYmQ2ZDdjYTIzOWUyNWQ1NTk1YjUzN2ZlZTA3Y2NfSUQ6NzY0NTIxODE3NDk1MTQyNzAxNF8xNzgwMDQ5ODkzOjE3ODAxMzYyOTNfVjM)

### 步骤 2 · 新增 MCP Server

在左侧菜单中选择 **Tools \&amp; MCPs**，进入 MCP Servers 管理页。

![Image](https://internal-api-drive-stream.larkoffice.com/space/api/box/stream/download/authcode/?code=NGQzYzg1OWQ2MTQyMDRhZThhOTY2YzA5NGY1YjM5NjlfY2U3MjYyYmY2YWY1ODhiMzhjZGI0NjE3ZTA0ZGM1YzhfSUQ6NzY0NTIxNzA4NDg0MjI2NTgxNF8xNzgwMDQ5ODkzOjE3ODAxMzYyOTNfVjM)



点击 **Add Custom MCP** 按钮。

![Image](https://internal-api-drive-stream.larkoffice.com/space/api/box/stream/download/authcode/?code=YmUxYTg2YTY3YTY4YjY3YmIxY2I3YmI2YzZjODZmNjlfNzJhNjQ1NTYzMTE1ZDRlMWU4ZDMyZDVjYTdlYzE0MjhfSUQ6NzY0NTIyMDE3NTQyMzUyMzc4Ml8xNzgwMDQ5ODkzOjE3ODAxMzYyOTNfVjM)



### 步骤 3 · 粘贴配置 JSON

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

![Image](https://internal-api-drive-stream.larkoffice.com/space/api/box/stream/download/authcode/?code=YjUxYzk5M2Y2YzRmYTc4Mjg5YzVjMDFhNjk3OTFhZjNfZTg2Yjk0M2FkZmQ5ZjJlMWU1MjFkZjI0MmUzNTBmOTNfSUQ6NzY0NTIyMTYyNTA2Mzk4NDMyNF8xNzgwMDQ5ODkzOjE3ODAxMzYyOTNfVjM)

### 步骤 4 · 确认并启用

保存 mcp\.json 配置文件并关闭后，Cursor 会自动建立 MCP 连接并加载工具列表。连接成功后，**`ov\-mcp\-server`** 会出现在 **Installed MCP Servers** 列表中，同时会显示已启用的工具数量（图中的 “10 tools enabled”）。配置完成后，可直接看到 `ov\-mcp\-server` 条目旁的开关呈绿色开启状态，代表服务已正常加载并就绪：

![Image](https://internal-api-drive-stream.larkoffice.com/space/api/box/stream/download/authcode/?code=N2QxZDU1YmFlOTg0NDk0MmYxODFmMWNkY2M4NTk5ZTdfODM3MDM5MDhjMzMxM2YyYTk4Y2VlNjk5MWY0ZjQ1OGJfSUQ6NzY0NTIyMTg1OTMyMzg0MTQ3Ml8xNzgwMDQ5ODkzOjE3ODAxMzYyOTNfVjM)

### 步骤 5 · MCP 连通性检查

接入后建议通过两个简单 query 快速验证 MCP 是否正常工作。在 Cursor 对话框中依次输入：

**① ** **`ov ls`** — 列出 OpenViking 根目录内容，确认连接畅通、可正确返回目录结构。

![Image](https://internal-api-drive-stream.larkoffice.com/space/api/box/stream/download/authcode/?code=NmFkNDViNjhjYjBiMGFiYTllNWQxYmI3ODYxOWM5YzJfZjdlODhjY2ZkY2VjYjcwYjA2NDViN2E1MjQ3Nzg5MjdfSUQ6NzY0NTIzMjY2MTk5MjYyMzMwMF8xNzgwMDQ5ODkzOjE3ODAxMzYyOTNfVjM)

**② ****`ov health`** — 调用 health 工具，确认 OpenViking 服务端状态与当前用户身份。

![Image](https://internal-api-drive-stream.larkoffice.com/space/api/box/stream/download/authcode/?code=ZmFmYjQyMDAzNWI1MTJmNGQyYTczYzNhZmU0MWUwODZfMGFlM2E2MjNjZTA5NjE2MWQ4ZjY3NmVmNTYxYTc2OGZfSUQ6NzY0NTIzMzA5MTA2OTQxNDU5Nl8xNzgwMDQ5ODkzOjE3ODAxMzYyOTNfVjM)

**验收标准**：`ov ls` 能返回 `agent / resources / session / user` 等目录；`ov health` 返回 `service initialized` 与当前用户名，即表示接入成功。



## 2\.2 配置参数说明

|字段|必填|说明|
|---|---|---|
|`mcpServers`|是|MCP Server 配置根节点|
|`ov\-mcp\-server`|是|服务别名，可自定义；建议保持与上下文识别一致|
|`url`|是|OpenViking MCP 服务端点；CN 区固定为 `https://api\.vikingdb\.cn\-beijing\.volces\.com/openviking/mcp`|
|`headers\.Authorization`|是|格式 `Bearer \&lt;API Key\&gt;`，来源见第一章|

---

# 三、常见问题（FAQ）

|问题|解决建议|
|---|---|
|连接失败 / 401 Unauthorized|检查 `Authorization` 是否带 `Bearer` 前缀；确认 API Key 未过期或被重置|
|连接失败 / 网络超时<br>|确认网络可访问 `api\.vikingdb\.cn\-beijing\.volces\.com`；企业网络请配置代理白名单|
|Agent 无法识别工具|检查 MCP Server 是否已\&\#34;启用\&\#34;；部分客户端需重启进程后加载新配置|



