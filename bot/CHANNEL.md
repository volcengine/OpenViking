## 💬 聊天应用

通过 Telegram、Discord、WhatsApp、飞书、Mochat、钉钉、Slack、邮件或 QQ 与您的 vikingbot 对话 —— 随时随地。

| 渠道 | 设置难度 |
|---------|-------|
| **Telegram** | 简单（只需一个令牌） |
| **Discord** | 简单（机器人令牌 + 权限） |
| **WhatsApp** | 中等（扫描二维码） |
| **飞书** | 中等（应用凭证） |
| **Mochat** | 中等（claw 令牌 + websocket） |
| **钉钉** | 中等（应用凭证） |
| **Slack** | 中等（机器人 + 应用令牌） |
| **邮件** | 中等（IMAP/SMTP 凭证） |
| **QQ** | 简单（应用凭证） |

<details>
<summary><b>Telegram</b>（推荐）</summary>

**1. 创建机器人**
- 打开 Telegram，搜索 `@BotFather`
- 发送 `/newbot`，按照提示操作
- 复制令牌

**2. 配置**

```json
{
  "channels": [
    {
      "type": "telegram",
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["YOUR_USER_ID"]
    }
  ]
}
```

> 您可以在 Telegram 设置中找到您的 **用户 ID**。它显示为 `@yourUserId`。
> 复制这个值**不带 `@` 符号**并粘贴到配置文件中。


**3. 运行**

```bash
vikingbot gateway
```

</details>

<details>
<summary><b>Mochat (Claw IM)</b></summary>

默认使用 **Socket.IO WebSocket**，并带有 HTTP 轮询回退。

**1. 让 vikingbot 为您设置 Mochat**

只需向 vikingbot 发送此消息（将 `xxx@xxx` 替换为您的真实邮箱）：

```
Read https://raw.githubusercontent.com/HKUDS/MoChat/refs/heads/main/skills/vikingbot/skill.md and register on MoChat. My Email account is xxx@xxx Bind me as your owner and DM me on MoChat.
```

vikingbot 将自动注册、配置 `~/.vikingbot/config.json` 并连接到 Mochat。

**2. 重启网关**

```bash
vikingbot gateway
```

就这么简单 —— vikingbot 处理剩下的一切！

<br>

<details>
<summary>手动配置（高级）</summary>

如果您更喜欢手动配置，请将以下内容添加到 `~/.vikingbot/config.json`：

> 请保密 `claw_token`。它只应在 `X-Claw-Token` 头中发送到您的 Mochat API 端点。

```json
{
  "channels": [
    {
      "type": "mochat",
      "enabled": true,
      "base_url": "https://mochat.io",
      "socket_url": "https://mochat.io",
      "socket_path": "/socket.io",
      "claw_token": "claw_xxx",
      "agent_user_id": "6982abcdef",
      "sessions": ["*"],
      "panels": ["*"],
      "reply_delay_mode": "non-mention",
      "reply_delay_ms": 120000
    }
  ]
}
```


</details>

</details>

<details>
<summary><b>Discord</b></summary>

**1. 创建机器人**
- 访问 https://discord.com/developers/applications
- 创建应用 → 机器人 → 添加机器人
- 复制机器人令牌

**2. 启用意图**
- 在机器人设置中，启用 **MESSAGE CONTENT INTENT**
- （可选）如果您计划使用基于成员数据的允许列表，启用 **SERVER MEMBERS INTENT**

**3. 获取您的用户 ID**
- Discord 设置 → 高级 → 启用 **开发者模式**
- 右键点击您的头像 → **复制用户 ID**

**4. 配置**

```json
{
  "channels": [
    {
      "type": "discord",
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["YOUR_USER_ID"]
    }
  ]
}
```

**5. 邀请机器人**
- OAuth2 → URL 生成器
- 范围：`bot`
- 机器人权限：`发送消息`、`读取消息历史`
- 打开生成的邀请 URL 并将机器人添加到您的服务器

**6. 运行**

```bash
vikingbot gateway
```

</details>

<details>
<summary><b>WhatsApp</b></summary>

需要 **Node.js ≥18**。

**1. 链接设备**

```bash
vikingbot channels login
# 使用 WhatsApp 扫描二维码 → 设置 → 链接设备
```

**2. 配置**

```json
{
  "channels": [
    {
      "type": "whatsapp",
      "enabled": true,
      "allowFrom": ["+1234567890"]
    }
  ]
}
```

**3. 运行**（两个终端）

```bash
# 终端 1
vikingbot channels login

# 终端 2
vikingbot gateway
```

</details>

<details>
<summary><b>飞书</b></summary>

使用 **WebSocket** 长连接 —— 不需要公网 IP。

**1. 创建飞书机器人**
- 访问 [飞书开放平台](https://open.feishu.cn/app)
- 创建新应用 → 启用 **机器人** 功能
- **权限**：添加 `im:message`（发送消息）
- **事件**：添加 `im.message.receive_v1`（接收消息）
  - 选择 **长连接** 模式（需要先运行 vikingbot 来建立连接）
- 从「凭证与基础信息」获取 **App ID** 和 **App Secret**
- 发布应用

**2. 配置**

```json
{
  "channels": [
    {
      "type": "feishu",
      "enabled": true,
      "appId": "cli_xxx",
      "appSecret": "xxx",
      "encryptKey": "",
      "verificationToken": "",
      "allowFrom": []
    }
  ]
}
```

> 长连接模式下，`encryptKey` 和 `verificationToken` 是可选的。
> `allowFrom`：留空以允许所有用户，或添加 `["ou_xxx"]` 以限制访问。

**3. 运行**

```bash
vikingbot gateway
```

> [!TIP]
> 飞书使用 WebSocket 接收消息 —— 不需要 webhook 或公网 IP！

</details>

<details>
<summary><b>QQ（QQ单聊）</b></summary>

使用 **botpy SDK** 配合 WebSocket —— 不需要公网 IP。目前仅支持 **私聊**。

**1. 注册并创建机器人**
- 访问 [QQ 开放平台](https://q.qq.com) → 注册为开发者（个人或企业）
- 创建新的机器人应用
- 进入 **开发设置** → 复制 **AppID** 和 **AppSecret**

**2. 设置沙箱测试环境**
- 在机器人管理控制台中，找到 **沙箱配置**
- 在 **在消息列表配置** 下，点击 **添加成员** 并添加您自己的 QQ 号
- 添加完成后，用手机 QQ 扫描机器人的二维码 → 打开机器人资料卡 → 点击「发消息」开始聊天

**3. 配置**

> - `allowFrom`：留空以供公开访问，或添加用户 openid 以限制。您可以在用户向机器人发消息时在 vikingbot 日志中找到 openid。
> - 生产环境：在机器人控制台提交审核并发布。查看 [QQ 机器人文档](https://bot.q.qq.com/wiki/) 了解完整发布流程。

```json
{
  "channels": [
    {
      "type": "qq",
      "enabled": true,
      "appId": "YOUR_APP_ID",
      "secret": "YOUR_APP_SECRET",
      "allowFrom": []
    }
  ]
}
```

**4. 运行**

```bash
vikingbot gateway
```

现在从 QQ 向机器人发送消息 —— 它应该会回复！

</details>

<details>
<summary><b>钉钉</b></summary>

使用 **流模式** —— 不需要公网 IP。

**1. 创建钉钉机器人**
- 访问 [钉钉开放平台](https://open-dev.dingtalk.com/)
- 创建新应用 -> 添加 **机器人** 功能
- **配置**：
  - 打开 **流模式**
- **权限**：添加发送消息所需的权限
- 从「凭证」获取 **AppKey**（客户端 ID）和 **AppSecret**（客户端密钥）
- 发布应用

**2. 配置**

```json
{
  "channels": [
    {
      "type": "dingtalk",
      "enabled": true,
      "clientId": "YOUR_APP_KEY",
      "clientSecret": "YOUR_APP_SECRET",
      "allowFrom": []
    }
  ]
}
```

> `allowFrom`：留空以允许所有用户，或添加 `["staffId"]` 以限制访问。

**3. 运行**

```bash
vikingbot gateway
```

</details>

<details>
<summary><b>Slack</b></summary>

使用 **Socket 模式** —— 不需要公网 URL。

**1. 创建 Slack 应用**
- 访问 [Slack API](https://api.slack.com/apps) → **创建新应用** →「从零开始」
- 选择名称并选择您的工作区

**2. 配置应用**
- **Socket 模式**：打开 → 生成一个具有 `connections:write` 范围的 **应用级令牌** → 复制它（`xapp-...`）
- **OAuth 与权限**：添加机器人范围：`chat:write`、`reactions:write`、`app_mentions:read`
- **事件订阅**：打开 → 订阅机器人事件：`message.im`、`message.channels`、`app_mention` → 保存更改
- **应用主页**：滚动到 **显示标签页** → 启用 **消息标签页** → 勾选 **"允许用户从消息标签页发送斜杠命令和消息"**
- **安装应用**：点击 **安装到工作区** → 授权 → 复制 **机器人令牌**（`xoxb-...`）

**3. 配置 vikingbot**

```json
{
  "channels": [
    {
      "type": "slack",
      "enabled": true,
      "botToken": "xoxb-...",
      "appToken": "xapp-...",
      "groupPolicy": "mention"
    }
  ]
}
```

**4. 运行**

```bash
vikingbot gateway
```

直接向机器人发送私信或在频道中 @提及它 —— 它应该会回复！

> [!TIP]
> - `groupPolicy`：`"mention"`（默认 —— 仅在 @提及時回复）、`"open"`（回复所有频道消息）或 `"allowlist"`（限制到特定频道）。
> - 私信策略默认为开放。设置 `"dm": {"enabled": false}` 以禁用私信。

</details>

<details>
<summary><b>邮件</b></summary>

给 vikingbot 一个自己的邮箱账户。它通过 **IMAP** 轮询收件箱并通过 **SMTP** 回复 —— 就像一个个人邮件助手。

**1. 获取凭证（Gmail 示例）**
- 为您的机器人创建一个专用的 Gmail 账户（例如 `my-vikingbot@gmail.com`）
- 启用两步验证 → 创建 [应用密码](https://myaccount.google.com/apppasswords)
- 将此应用密码用于 IMAP 和 SMTP

**2. 配置**

> - `consentGranted` 必须为 `true` 以允许邮箱访问。这是一个安全门 —— 设置为 `false` 以完全禁用。
> - `allowFrom`：留空以接受来自任何人的邮件，或限制到特定发件人。
> - `smtpUseTls` 和 `smtpUseSsl` 分别默认为 `true` / `false`，这对 Gmail（端口 587 + STARTTLS）是正确的。无需显式设置它们。
> - 如果您只想读取/分析邮件而不发送自动回复，请设置 `"autoReplyEnabled": false`。

```json
{
  "channels": [
    {
      "type": "email",
      "enabled": true,
      "consentGranted": true,
      "imapHost": "imap.gmail.com",
      "imapPort": 993,
      "imapUsername": "my-vikingbot@gmail.com",
      "imapPassword": "your-app-password",
      "smtpHost": "smtp.gmail.com",
      "smtpPort": 587,
      "smtpUsername": "my-vikingbot@gmail.com",
      "smtpPassword": "your-app-password",
      "fromAddress": "my-vikingbot@gmail.com",
      "allowFrom": ["your-real-email@gmail.com"]
    }
  ]
}
```


**3. 运行**

```bash
vikingbot gateway
```

</details>