
## 一、适用场景

使用 OpenViking 实现：

- 跨会话记住技术栈偏好（语言版本、框架、依赖管理工具、构建系统）

- 沉淀编码风格偏好（命名约定、注释风格、是否写单测、TDD/BDD 习惯）

- 记住常用工程上下文（monorepo 结构、构建命令、部署流程、环境差异）

- 记忆历史决策与\&\#34;踩坑笔记\&\#34;（为什么不用 X、上次用 Y 出过什么问题）

- 个人长期任务目标 / OKR / Roadmap 沉淀，Agent 在规划任务时自动对齐

---

## 二、前置准备：获取 API Key

所有 MCP 客户端的接入都依赖同一个 **Authorization Token**，即 OpenViking 控制台中的 API Key。请先按以下步骤获取并妥善保存：

1. 在左侧菜单选择 **用户管理**

2. 在用户列表中找到对应用户（个人版默认为 `default` / `admin`），点击 API Key 列右侧的 **复制** 图标

3. 将复制得到的 `ZGV\.\.\.hiMg` 形式的字符串妥善保存，作为后续所有 Agent 接入的 `Authorization` 值

**安全提示**：API Key 等同于账户密钥，请勿提交到 Git 仓库或公开渠道。建议通过环境变量或加密配置注入。

![获取 OpenViking API Key](https://docs.openviking.net/agents/image/trae/01-api-key.jpg)

---

## 三、Trae 接入指南

**Trae** 是字节跳动推出的 AI IDE，原生支持通过 MCP 协议加载外部工具与上下文服务。以下为 OpenViking 的标准接入流程。

### 3\.1 接入步骤

#### 步骤 1：打开设置

在 Trae 主界面右上角点击 **设置（齿轮图标）**，进入设置面板。

![打开 Trae 设置](https://docs.openviking.net/agents/image/trae/02-open-settings.jpg)

#### 步骤 2：进入 MCP 配置页

在左侧菜单中选择 **MCP**，进入 MCP Servers 管理页。

![进入 MCP 配置页](https://docs.openviking.net/agents/image/trae/03-mcp-settings.jpg)

#### 步骤 3：新增 MCP Server

点击右侧的 **\+ 添加** 按钮，在下拉菜单中选择 **手动配置**。

![添加 MCP Server](https://docs.openviking.net/agents/image/trae/04-add-mcp-server.jpg)

![选择手动配置](https://docs.openviking.net/agents/image/trae/05-manual-config.png)

#### 步骤 4：粘贴配置 JSON

在弹出的配置框中粘贴以下 JSON，并将 `Authorization` 替换为第二章中复制的 API Key：

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

![粘贴 MCP JSON 配置](https://docs.openviking.net/agents/image/trae/06-paste-mcp-json.jpg)

#### 步骤 5：确认并启用

点击 **确认** 按钮，Trae 会自动建立 MCP 连接并加载工具列表。连接成功后，`ov\-mcp\-server` 将出现在已配置的 MCP Servers 列表中。配置完成后，可在 MCP 管理页看到 `ov\-mcp\-server` 已加载并启用，右侧开关呈绿色：

![确认并启用 MCP Server](https://docs.openviking.net/agents/image/trae/07-enable-server.jpg)

#### 步骤 6：MCP 连通性检查

接入后建议通过两个简单 query 快速验证 MCP 是否正常工作。在 Trae 对话框中依次输入：

**① ****`ov ls`** — 列出 OpenViking 根目录内容，确认连接畅通、可正确返回目录结构。

![运行 ov ls 验证连接](https://docs.openviking.net/agents/image/trae/08-ov-ls.jpg)

**② ****`ov health`** — 调用 health 工具，确认 OpenViking 服务端状态与当前用户身份。

![运行 ov health 验证服务状态](https://docs.openviking.net/agents/image/trae/09-ov-health.jpg)

**验收标准**：`ov ls` 能返回 `agent / resources / session / user` 等目录；`ov health` 返回 `service initialized` 与当前用户名，即表示接入成功。



### 3\.2 配置参数说明

|字段|必填|说明|
|---|---|---|
|`mcpServers`|是|MCP Server 配置根节点|
|`ov\-mcp\-server`|是|服务别名，可自定义；建议保持与上下文识别一致|
|`url`|是|OpenViking MCP 服务端点；CN 区固定为 `https://api\.vikingdb\.cn\-beijing\.volces\.com/openviking/mcp`|
|`headers\.Authorization`|是|格式 `Bearer \&lt;API Key\&gt;`，来源见第二章|

---

## 四、常见问题（FAQ）

|问题|解决建议|
|---|---|
|连接失败 / 401 Unauthorized|检查 `Authorization` 是否带 `Bearer` 前缀；确认 API Key 未过期或被重置|
|连接失败 / 网络超时<br>|确认网络可访问 `api\.vikingdb\.cn\-beijing\.volces\.com`；企业网络请配置代理白名单|
|Agent 无法识别工具|检查 MCP Server 是否已\&\#34;启用\&\#34;；部分客户端需重启进程后加载新配置|
|mcp工具因 argument schema 与当前模型不兼容，请切换/修复 mcp server 或切换模型 \(4027\)|![Trae MCP schema 兼容性报错](https://docs.openviking.net/agents/image/trae/10-schema-error.png)<br>![Trae MCP schema 兼容性详情](https://docs.openviking.net/agents/image/trae/11-schema-error-detail.png)<br>尝试切换模型或升级到最新版 Trae|
