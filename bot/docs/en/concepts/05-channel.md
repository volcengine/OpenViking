## 💬 Chat Applications

Talk to your vikingbot through Telegram, Discord, WhatsApp, Feishu, Mochat, DingTalk, Slack, email, or QQ—wherever you are.

| Channel | Setup difficulty |
|---------|------------------|
| **Telegram** | Easy (one token) |
| **Discord** | Easy (bot token + permissions) |
| **WhatsApp** | Medium (scan a QR code) |
| **Feishu** | Medium (app credentials) |
| **Mochat** | Medium (claw token + WebSocket) |
| **DingTalk** | Medium (app credentials) |
| **Slack** | Medium (bot + app tokens) |
| **Email** | Medium (IMAP/SMTP credentials) |
| **QQ** | Easy (app credentials) |

<details>
<summary><b>Telegram</b> (recommended)</summary>

**1. Create a bot**

- Open Telegram and search for `@BotFather`.
- Send `/newbot` and follow the prompts.
- Copy the token.

**2. Configure it**

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

> You can find your **user ID** in Telegram settings. It is displayed as `@yourUserId`.
> Copy the value **without the `@` symbol** into the configuration file.

**3. Run it**

```bash
vikingbot gateway
```

</details>

<details>
<summary><b>Mochat (Claw IM)</b></summary>

Mochat uses a **Socket.IO WebSocket** connection by default, with HTTP polling as a fallback.

**1. Ask vikingbot to set up Mochat for you**

Send the following message to vikingbot, replacing `xxx@xxx` with your actual email address:

```
Read https://raw.githubusercontent.com/HKUDS/MoChat/refs/heads/main/skills/vikingbot/skill.md and register on MoChat. My Email account is xxx@xxx Bind me as your owner and DM me on MoChat.
```

vikingbot automatically registers, updates `~/.vikingbot/config.json`, and connects to Mochat.

**2. Restart the gateway**

```bash
vikingbot gateway
```

That is all—vikingbot handles the rest.

<br>

<details>
<summary>Manual configuration (advanced)</summary>

If you prefer to configure Mochat manually, add the following to `~/.vikingbot/config.json`:

> Keep `claw_token` secret. It should only be sent to your Mochat API endpoint in the `X-Claw-Token` header.

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

**1. Create a bot**

- Go to https://discord.com/developers/applications.
- Create an application, open **Bot**, and add a bot.
- Copy the bot token.

**2. Enable intents**

- In the bot settings, enable **MESSAGE CONTENT INTENT**.
- Optional: enable **SERVER MEMBERS INTENT** if you plan to use an allowlist based on member data.

**3. Get your user ID**

- In Discord, open **Settings → Advanced** and enable **Developer Mode**.
- Right-click your avatar and select **Copy User ID**.

**4. Configure it**

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

**5. Invite the bot**

- Open **OAuth2 → URL Generator**.
- Select the `bot` scope.
- Grant the bot `Send Messages` and `Read Message History` permissions.
- Open the generated URL and add the bot to your server.

**6. Run it**

```bash
vikingbot gateway
```

</details>

<details>
<summary><b>WhatsApp</b></summary>

Requires **Node.js 18 or later**.

**1. Link a device**

```bash
vikingbot channels login
# In WhatsApp, scan the QR code from Settings → Linked devices
```

**2. Configure it**

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

**3. Run it** in two terminals:

```bash
# Terminal 1
vikingbot channels login

# Terminal 2
vikingbot gateway
```

</details>

<details>
<summary><b>Feishu</b></summary>

Feishu uses a persistent **WebSocket** connection, so no public IP address is required.

**1. Create a Feishu bot**

- Go to the [Feishu Open Platform](https://open.feishu.cn/app).
- Create an application and enable the **Bot** capability.
- Under **Permissions**, add `im:message` for sending messages.
- Under **Events**, add `im.message.receive_v1` for receiving messages.
  - Select **Long Connection** mode. vikingbot must be running first so it can establish the connection.
- Copy the **App ID** and **App Secret** from **Credentials & Basic Info**.
- Publish the application.

**2. Configure it**

```json
{
  "channels": [
    {
      "type": "feishu",
      "enabled": true,
      "appId": "cli_xxx",
      "appSecret": "xxx",
      "botName": "",
      "encryptKey": "",
      "verificationToken": "",
      "allowFrom": [],
      "threadRequireMention": true
    }
  ]
}
```

> In long-connection mode, `encryptKey` and `verificationToken` are optional.
> `allowFrom`: leave it empty to allow every user, or add `["ou_xxx"]` to restrict access.
> `botName`: replaces `@<open_id>` mentions with the bot name in group-chat context sent to the model, and labels messages sent by the bot itself. When empty, it falls back to `"Bot"`.
> `threadRequireMention`: controls whether group messages must mention the bot. The default is `true`, meaning every message in regular groups and topic groups requires an `@` mention. When set to `false`, regular groups do not require a mention, and only the first message in a topic group can omit it; later replies still require an `@` mention outside `DEBUG` mode.

**3. Run it**

```bash
vikingbot gateway
```

> [!TIP]
> Feishu receives messages over WebSocket, so you do not need a webhook or public IP address.

</details>

<details>
<summary><b>QQ (direct messages)</b></summary>

QQ uses the **botpy SDK** over WebSocket, so no public IP address is required. Currently, only **direct messages** are supported.

**1. Register and create a bot**

- Go to the [QQ Open Platform](https://q.qq.com) and register as an individual or organization developer.
- Create a bot application.
- Open **Development Settings** and copy the **AppID** and **AppSecret**.

**2. Set up the sandbox**

- In the bot management console, open **Sandbox Configuration**.
- Under **Message List Configuration**, select **Add Member** and add your own QQ account.
- After adding it, scan the bot's QR code with the QQ mobile app, open the bot profile, and select **Send Message**.

**3. Configure it**

> - `allowFrom`: leave it empty for public access, or add user openids to restrict access. You can find a user's openid in the vikingbot logs after that user messages the bot.
> - For production, submit the bot for review and publish it in the console. See the [QQ Bot documentation](https://bot.q.qq.com/wiki/) for the complete release process.

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

**4. Run it**

```bash
vikingbot gateway
```

Send the bot a message from QQ. It should reply.

</details>

<details>
<summary><b>DingTalk</b></summary>

DingTalk uses **Stream Mode**, so no public IP address is required.

**1. Create a DingTalk bot**

- Go to the [DingTalk Open Platform](https://open-dev.dingtalk.com/).
- Create an application and add the **Bot** capability.
- Under **Configuration**, enable **Stream Mode**.
- Add the permissions required to send messages.
- Copy the **AppKey** (client ID) and **AppSecret** (client secret) from **Credentials**.
- Publish the application.

**2. Configure it**

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

> `allowFrom`: leave it empty to allow every user, or add `["staffId"]` to restrict access.

**3. Run it**

```bash
vikingbot gateway
```

</details>

<details>
<summary><b>Slack</b></summary>

Slack uses **Socket Mode**, so no public URL is required.

**1. Create a Slack app**

- Go to the [Slack API](https://api.slack.com/apps), select **Create New App**, and choose **From scratch**.
- Enter a name and select your workspace.

**2. Configure the app**

- **Socket Mode**: enable it, generate an **app-level token** with the `connections:write` scope, and copy the token (`xapp-...`).
- **OAuth & Permissions**: add the bot scopes `chat:write`, `reactions:write`, and `app_mentions:read`.
- **Event Subscriptions**: enable them, subscribe to the bot events `message.im`, `message.channels`, and `app_mention`, then save your changes.
- **App Home**: under **Show Tabs**, enable the **Messages Tab**, then select **Allow users to send Slash commands and messages from the messages tab**.
- **Install App**: select **Install to Workspace**, authorize the app, and copy the **bot token** (`xoxb-...`).

**3. Configure vikingbot**

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

**4. Run it**

```bash
vikingbot gateway
```

Send the bot a direct message, or mention it in a channel. It should reply.

> [!TIP]
> - `groupPolicy`: `"mention"` (default; reply only when mentioned), `"open"` (reply to every channel message), or `"allowlist"` (restrict replies to selected channels).
> - Direct messages are enabled by default. Set `"dm": {"enabled": false}` to disable them.

</details>

<details>
<summary><b>Email</b></summary>

Give vikingbot its own email account. It polls the inbox over **IMAP** and replies over **SMTP**, acting as a personal email assistant.

**1. Get credentials (Gmail example)**

- Create a dedicated Gmail account for your bot, such as `my-vikingbot@gmail.com`.
- Enable two-step verification and create an [app password](https://myaccount.google.com/apppasswords).
- Use the app password for both IMAP and SMTP.

**2. Configure it**

> - `consentGranted` must be `true` to allow mailbox access. This is a safety gate; set it to `false` to disable access completely.
> - `allowFrom`: leave it empty to accept email from anyone, or restrict it to specific senders.
> - `smtpUseTls` and `smtpUseSsl` default to `true` and `false`, respectively. Those defaults are correct for Gmail on port 587 with STARTTLS, so you do not need to set them explicitly.
> - If you only want to read and analyze email without sending automatic replies, set `"autoReplyEnabled": false`.

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

**3. Run it**

```bash
vikingbot gateway
```

</details>
