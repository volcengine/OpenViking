# Vika

把你本机上的 coding agent 接进飞书、微信、Telegram、Slack 和 QQ，也可以接收邮箱事件。Core 维护一个共享的 Agent session 和 OpenViking 空间；每个消息源由独立 Connector 子进程接入。

适合想把 Codex、TraeX、Claude 或 Pi 变成长期协作者的人。上下文沉淀在 OpenViking，不用每次重新解释项目背景；代码、凭据和本地工具依然留在自己的机器上。

## 怎么用

1. 在 Mac 或 Linux 服务器上常驻运行 daemon，把 bot 加进你信任的私聊和群。
2. 直接发任务，不需要每条消息都 `@`。比如“查一下这个报错”“把 README 改短一点并提 PR”“整理今天的讨论”。
3. 需要展开讨论就开 thread。它能执行 coding task、调用 subagent，也能在必要时去其他已授权聊天继续工作。

它默认不抢话：明确问题、任务、纠正才回复；“收到”“谢谢”、闲聊和人与人之间的对话通常保持静默。所有聊天共享一份工作上下文，所以它能接住跨群、跨天的续聊。

## 为什么用它

- **消息源是入口，agent 还是 coding agent。**不用再把需求复制到另一个终端窗口；需要代码、调研、文档或协作动作时，直接在对话里交代。
- **上下文不靠聊天记录硬塞。**项目知识、长期记忆和任务资料放在 OpenViking；每个 agent 有自己的 workspace 和记忆配置。
- **本机可控。**daemon 只是把可信聊天消息和本地 agent 接起来，不把本机工作区搬到云端。
- **可以有多个 agent。**每个 agent 使用独立的飞书 profile、workspace、数据目录和 OpenViking 配置；共享同一个 Web 和 updater。

## 架构，够用就行

Core 根据 manifest 启动 Connector 子进程。飞书 Connector 通过 `lark-cli` 长连接收消息；微信 Connector 通过腾讯 `openclaw-weixin` 的 iLink HTTP 协议长轮询。Telegram Connector 通过官方 Bot API 的 `getUpdates` 长轮询。Slack Connector 通过 Socket Mode 的 WebSocket 长连接；QQ Connector 通过官方 Bot API 的 WebSocket 接收私聊。它们输出统一的 `EVENT` 信封，并进入同一个 `AgentRuntime`。每个 Connector 单独失败、单独重连：微信登录过期这类问题只让微信停下，飞书和 runtime 照常运行。出站方向，所有 runtime 都调用同一套 Agent 工具（`send_message`、`reply_message` 等）：Codex/TraeX/Claude/Pi 均经同一个 MCP 适配器，Durable 由自己的 host 进程直接调用，两条路都只负责把调用转发给 Core 的本机 socket。Core 统一校验运行权、新鲜度、路由与内容，写入唯一的 `outbox` 表后再由对应 Connector 执行；daemon 自己的失败通知和 slash 命令回复走同一条路径。协议和生命周期见 [Connector protocol 0.2](docs/connector-protocol.md)。

Core 的入站模型同时支持聊天与非聊天事件：所有来源写入 `connector_events`，按来源实例和事件 ID 去重；聊天、消息和回复路由字段可选。GitHub issue、Calendar 提醒等可以使用 `kind`、`subject`、标题与正文进入 runtime，无须包装成飞书消息。内置 Connector 为飞书、微信、Telegram、Slack、QQ 和邮箱（IMAP/SMTP）；新增来源需实现 Connector。非聊天事件只能经 `notify_owner` 通知 owner（你本人）：Agent 不选目的地，Core 发到主渠道（`channel`）上你的私聊。主渠道就是机器人主动找你时用的 IM：你没先找它、它直接找你的消息（新邮件提醒、待你确认的邮件回复等）都发到这里；你在哪个 IM 里找它，它就在哪里回你。owner 身份由 Connector 自检时给出：飞书是该 profile 用 lark-cli 做 user 登录的账号，微信是扫码登录的账号，Telegram 是通过一次性 Start 链接验证的账号，Slack 是在私聊里给机器人发一次性口令的账号，QQ 是通过一次性私聊验证码验证的账号。协议已更新为 0.2，本次为未上线产品的直接 schema 变更，使用新的数据目录，不迁移旧 `inbound` 表。

## 安装

一条命令：`curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/vika/install.sh | sh`，装好后 `vika setup` 接飞书、微信、Telegram、Slack、QQ 或邮箱；界面有中文和英文（`--lang zh|en`）。机器上不需要 Node。细节看 [install.md](./install.md)：依赖、setup、升级回滚、开机自启、多 agent 和发布新版本。

### 微信单聊 MVP

可以在 `config.json` 中增加一个微信实例。`channel` 是兼容字段；`connectors` 声明该 Core 启动的 Connector。`profile` 是飞书 Connector 的 lark-cli profile，纯微信实例不填（填了会被拒绝）；`ovConfig` 必填，`ovAutoRecall` 默认为 `false`：

```json
{
  "id": "weixin",
  "displayName": "Weixin Pi",
  "channel": "weixin",
  "connectors": ["weixin"],
  "port": 3741,
  "dataDir": "/Users/you/.vika/data/weixin",
  "workspace": "/Users/you/Documents/work/weixin-workspace",
  "ovConfig": "/Users/you/.openviking/ovcli.conf.weixin",
  "model": "pi"
}
```

微信实例可以使用 Codex、TraeX、Claude 或 Pi runtime（示例用 Pi）。要让同一个 session 和 OV 空间同时接收飞书与微信，在现有实例上配置：

```json
{
  "channel": "lark",
  "connectors": ["lark", "weixin"],
  "model": "pi"
}
```

随后执行一次 `vika login weixin` 扫码登录（有多个实例时会让你选，或写上实例名）。给已有实例补接微信用 `vika setup`，选"修改"这个实例。不配置 `connectors` 时仍按旧的 `channel` 启动单个 Connector。

安装依赖后扫码，再以前台方式启动这个实例：

```bash
npm install
vika login weixin weixin
VIKA_INSTANCE_ID=weixin npm run start:daemon
```

终端会显示二维码和备用链接。用微信扫码并在手机确认后，凭证写入该实例的 `dataDir/weixin/state.sqlite3`；随后可以给新出现的机器人联系人发送文本、图片或文件。扫码后先在微信里给机器人发一句话（比如「你好」）：微信只送达你发过消息之后机器人发的消息，在这之前它没法主动找你（新邮件提醒、待你确认的邮件回复都发不到微信），每次重新扫码登录后都一样。一个微信号同时只能连一个机器人，给另一个实例扫码会让这个实例的微信掉线。带有微信语音转写的消息会以转写文本进入 Agent。机器人只和扫码的那个微信号单聊，不能和别人聊，也不能进群。

这个 MVP 使用一个微信账号，支持单聊文本、入站图片和文件、微信提供的语音转写，以及出站图片和文件。入站媒体经腾讯 CDN 下载和 AES 解密后保存在该实例的 `dataDir/weixin/media`，单个文件和出站 workspace 文件上限均为 20 MiB，并随 daemon 的保留周期清理。原始语音解码/ASR、视频、群聊、引用和 reaction 尚未接入。协议实现固定对齐 `Tencent/openclaw-weixin` 2.4.9，来源与许可证见 [微信协议说明](packages/daemon/src/weixin/NOTICE.md) 和 [MIT 许可证](packages/daemon/src/weixin/LICENSE.openclaw-weixin)。

### QQ 私聊

使用腾讯官方 QQ Bot API v2 和 WebSocket，不需要公网 Webhook，也不是个人 QQ 账号协议。在包含 QQ 功能的 release 中：

1. 先在 [QQ 开放平台](https://q.qq.com/#/) 按页面引导注册开发者账号（已有可跳过），没有开发者账号无法创建机器人。再到腾讯 [QQ 机器人入口](https://q.qq.com/qqbot/openclaw/) 创建独立机器人，取得 AppID / AppSecret，并用本人 QQ 添加机器人。这个入口提供的机器人凭证可以用于 Vika；无需运行 OpenClaw。
2. 运行 `vika setup`，新建或修改实例，勾选 QQ。输入 AppID 和 AppSecret（密钥隐藏输入），确认机器人已上线并具备单聊消息权限。
3. 向机器人私聊发送终端显示的完整 `vika-bind …` 验证码，三分钟内有效。收到匹配的私聊后才保存新账号；其他人先发消息不会成为 owner。
4. 实例启动后在私聊中发送任务。重新绑定或换密钥使用 `vika login qq [实例]`；验证期间暂时停止实例，成功或取消后恢复启动，失败不覆盖旧凭证。

首版只处理绑定 owner 的私聊，支持文本、图片、文件、引用回复与现有 slash 命令。文本按纯文本发送，最多 4000 字符；文件和下载最多 20 MiB，5 MB 起走分片上传。普通文件发送取决于机器人是否具有对应权限。不支持群聊、个人号代收、语音、视频、交互按钮或历史搜索。只使用 MIT 开源的腾讯 Node SDK 处理 WebSocket，未引入独立的扫码领取凭证 SDK。

QQ 可以作为主渠道接收主动通知和带编号的邮件回信审批；需允许主动消息且有剩余配额。被动回复序号与次数在本地持久化，最多四次，时限按消息产生时间计算、上限 60 分钟；官方文档同页仍有“5 分钟”的字段说明，因此服务端提前判过期也会直接返回错误。超限、权限拒绝、配额耗尽或结果不明时不会自动改成主动发送，也不会自动重试消息。详见腾讯的[单聊发送规则](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_messages.post.html)。

纯 QQ 实例使用 `"channel": "qq"`、`"connectors": ["qq"]`，不填 `profile`；也可与其他 IM 共用实例。支持 Codex、TraeX、Claude、Pi。AppSecret、owner OpenID 和回复路由保存在 `<dataDir>/qq/state.sqlite3`（0600，父目录 0700），不写入 manifest 或日志；附件保存在 `<dataDir>/qq/media` 并随保留周期清理。一个实例只能有一个本地接收者，一个机器人应只绑定一个实例。WebSocket 断线期间在同一进程内尝试 Resume；不承诺进程停机期间补回所有消息，未改变公共 ACK/outbox 机制。

### Telegram 私聊

使用官方 [Telegram Bot API](https://core.telegram.org/bots/api) 长轮询，不需要公网 Webhook。运行 Vika 的机器需要能通过 HTTPS 访问 `api.telegram.org`，手机或桌面端需要能打开 Telegram。

1. 在官方 [@BotFather](https://t.me/BotFather) 中发送 `/newbot`，为这个实例创建独立 Bot，复制 token。
2. 运行 `vika setup`，新建或修改实例，勾选 Telegram；可把它选为主渠道，也可与飞书、微信、邮箱共用一个实例。
3. 在终端的隐藏输入框粘贴 token。打开随后生成的链接或扫描二维码，在 Bot 私聊中点击 **Start**，三分钟内完成绑定。
4. 实例启动后，在这个 Bot 的私聊中发送任务。更换 token 或重新绑定用 `vika login telegram [实例]`；验证期间实例会暂时停止，完成或取消后恢复启动。新绑定成功前保留旧凭证。

只有用本次随机链接完成绑定的账号能触发 Agent；其他账号、群聊、旧的绑定命令都忽略。普通 Bot 私聊不是端到端加密对话。每个实例使用独立 Bot，勿让其他程序同时轮询同一个 Bot；已有 Webhook 时接入会拒绝，不会替你删除原服务的配置。

支持文本、图片、文件和引用回复，主渠道通知与邮件编号审批沿用现有流程。文本以纯文本发送，单条最多 4096 字符；工作区文件沿用 Core 的 **20 MiB** 上限，JPEG/PNG 不超过 **10 MB** 时作为图片发送，更大时作为文件发送；入站媒体按需下载，托管 Bot API 上限 **20 MB**。暂不支持群聊、语音转写、视频、按钮卡片、reaction 或聊天历史检索。

纯 Telegram 实例使用 `"channel": "telegram"`、`"connectors": ["telegram"]`，不填 `profile`；混合实例可用 `"connectors": ["lark", "telegram", "email"]`，有飞书时仍须配置 `profile`。Codex、TraeX、Claude、Pi 均可用。

Bot token、owner/chat ID、接收游标及回复路由保存在 `<dataDir>/telegram/state.sqlite3`（0600，父目录 0700），token 不写入 manifest 或日志；媒体保存在 `<dataDir>/telegram/media` 并随保留周期清理。Telegram 最多保存 24 小时的待接收更新；当前接收仍使用 protocol 0.2 的 stdout 投递与游标机制，没有新增 Core 持久化 ACK 保证。发送只对明确的 `429 retry_after` 做有限重试，网络中断等结果不明的发送不会自动重发。

### Slack

使用你自己工作区里的内部 Slack App，通过 [Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode) 收消息，不需要公网回调地址。运行 Vika 的机器需要能通过 HTTPS/WSS 访问 `slack.com`。每个 Vika 实例用一个独立的 App：Slack 会把事件随机分给同一个 App 的所有连接，两个程序共用一个 App 会各丢一部分消息，Vika 发现时会停下并提示。

1. 运行 `vika setup`，新建或修改实例，勾选 Slack。
2. 打开终端里给出的链接，选择工作区，用预填的配置创建 App（链接打不开时，在 [api.slack.com/apps](https://api.slack.com/apps) 选 Create New App → From a manifest，粘贴 `<dataDir>/slack/app-manifest.json`）。工作区开启了应用审批时，安装要等管理员批准。
   也可以用已有的 App：对照这份 manifest 打开 Socket Mode、在 App Home 允许用户发消息，补齐事件订阅和 Bot Token Scopes 后重新安装。开了 Token Rotation 的 App 不能用；原来接收它消息的程序要先停掉。
3. 点 **Install to Workspace**，在 OAuth & Permissions 复制 Bot User OAuth Token（`xoxb-`）；在 Basic Information → App-Level Tokens 生成一个带 `connections:write` 的 token（`xapp-`）。两个 token 都在终端的隐藏输入框粘贴。
4. 终端显示一个一次性口令（`vika-xxxxxxxx`），在 Slack 里私聊这个机器人发送它，5 分钟内完成绑定；发送口令的人就是 owner。之后要换 token 或换 owner，用 `vika login slack [实例]`，验证期间实例暂停，期间别人发给机器人的消息会在恢复后补上。

和飞书一样，工作区里任何人都可以私聊机器人；频道要先用 `/invite @机器人` 把它拉进去才会收到消息，群里不用 @ 它，是否回复由 Agent 判断。和其他公司共享的 Slack Connect 频道照常接收，消息行会标上 `external=true`，按外部群的信息边界处理。其他 App 发的消息（GitHub、告警、其他 AI 机器人）也会交给 Agent，行内标 `sender=bot` 和 `bots_since_human=N`（自上一条真人消息以来这里有几条 bot 消息）。要不要回、和别的机器人协作几轮，都由 Agent 按「有必要才说话」判断，不需要 @。只有失控时才有一根保险丝：同一个频道或线程里一直没有真人说话、Agent 已经回应了 20 条 bot 消息，再回就会被拦下，并私聊通知你一次；有人在那里说一句话就恢复。阈值用 `VIKA_BOT_REPLY_FUSE` 调，设为 `0` 关闭。飞书也是同一套规则。

Slack 没有引用回复：Agent 在频道里回某条消息时会回到它的线程里，线程里的消息自然也回到线程。Agent 写的 Markdown 发送前转成 Slack 的格式，单条最多 12000 字符，更长的发文件；`@channel`/`@here` 不会被发出。支持文本、文件（工作区文件最多 20 MiB，入站文件按需下载）、@提及、reaction 和撤回；暂不支持按钮卡片和搜索，邮件审批用编号消息（「发送 N」）。Slack 会把 `/` 开头的消息当成 Slack 命令拦下，要用 `/new`、`/model` 这些命令时前面加一个空格，发 ` /new`。

Agent 可用 `list_chat_members` 分页查询当前频道或私聊的成员，获取用户 ID、显示名，以及已知的机器人、本人和停用标记；查到的名字可以直接用于 `mention_names`，重名时使用返回的用户 ID。默认每页 100 人、最多 200 人，通过 `next_cursor` 翻页；个别用户资料不可用时仍返回其 ID，并明确标记未知，不会把资料缺失的成员当作真人。新建 App 的模板包含成员查询权限；旧 App 需要补授权，步骤见 [Slack 接入](./install.md#slack-接入)。缺权限只影响这个查询，不会停掉消息收发。

基础查询统一走 `Agent → Core → Slack Connector → 官方 Slack CLI`：`list_chats` 列出 bot 已加入的群会话，`list_chat_messages` 从新到旧读会话历史，`list_thread_messages` 从旧到新读线程（含根消息），成员与用户资料也由 CLI 查询。每次返回一页，`has_more` 为真时用 `next_cursor` 继续；按名称过滤后即使本页为空也可能还有下一页。历史与线程每页最多 50 条，只支持 bot 身份，权限范围内可以读取绑定前的消息和 bot 自己的消息。查询结果里的完整 `message_id` / `thread_id` 可用于后续回复、线程查询和附件下载；历史只作上下文，不会再次进入入站队列，也不能充当 `decision_id`。混合 IM 实例可以显式指定 `source: "slack"`。

官方 [Slack CLI](https://github.com/slackapi/slack-cli) 固定为 4.10.0，随发布包安装到 `bin/slack`，源码开发运行 `npm run setup` 安装。Connector 通过 `slack api` 调用白名单内的只读接口，独立临时配置、bot token 环境变量与进程超时由 Connector 管理；Core 不执行 CLI，Agent 不获取 bot token。Socket Mode 收消息及发送、reaction、撤回、文件传输继续由 Connector 的 Web API 实现。

纯 Slack 实例使用 `"channel": "slack"`、`"connectors": ["slack"]`，不填 `profile`；Codex、TraeX、Claude、Pi 均可用。两个 token、owner、回复路由、显示名缓存和待处理消息保存在 `<dataDir>/slack/state.sqlite3`（0600，父目录 0700），token 不写入 manifest 或日志。收到的消息先存进这个库再向 Slack 确认，所以 Connector 重启不会丢掉已确认的消息；daemon 整体停机期间 Slack 只短暂重试，停得久了会漏消息。Slack 没有幂等键，结果不明的发送不会自动重发。

### 邮箱

邮箱 Connector 用 IMAP 只读监听收件箱（不会把邮件标成已读，也不移动、不删除），用 SMTP 回信。一个实例可以同时接多个邮箱，每个邮箱是一个独立的 Connector 实例（`email-gmail`、`email-qq` 这样的名字），各跑一个子进程：一个邮箱密码失效或被限流，只有它自己退避重连，其他邮箱照常收信。新邮件到达后，Agent 判断要不要告诉你，要的话发到你在主渠道上的私聊。Agent 从不直接发邮件：无论是你让它回，还是它觉得该回，它都只能起草，Core 把草稿（发件邮箱、收件人、主题和正文）发到主渠道等你确认，只有你确认了才发出：

- 主渠道是飞书：草稿是一张卡片，正文可以直接在卡片上改，点"发送"按卡片上的内容发出，点"不发了"就作废。
- 主渠道是微信、Telegram、Slack 或 QQ：草稿是一条带编号的消息，回复「发送 3」发出，「不发 3」作废，「修改 3 新的正文」把正文换成你写的内容（改完会把新草稿再发你一遍，还要回「发送 3」才发出）。也可以直接告诉 Agent 怎么改，它会重新起草一份，旧编号随之作废。这几句回复由 Core 处理，不经过 Agent，只认你本人在私聊里发的、整条消息正好是这个格式的回复。

每封邮件在交给 Agent 之前先存进 OpenViking 里你的私有区（`viking://user/<你>/resources/emails/<邮件>/`）：`body.md` 是主题、收发件人、时间、附件清单和完整正文；附件全文在 `attachments/<编号>-<文件名>/full/` 下，PDF、Word、Excel、PPT、HTML、文本等由 OV 解析成 Markdown，转发的邮件和日历邀请这类文本附件原样存，图片、压缩包等不存，内嵌在正文里的图片也不存。OV 一个文件只建一条向量、只看开头约 4096 token，所以长正文和长附件另外由 OV 切成小块存一份（同级的 `chunks/`），只用于召回；Agent 读的永远是全文，召回命中某一块时从这一块在全文中的位置读起。OV 只为这些内容建向量（`vectors_only`），不调 LLM 生成摘要。Agent 收到的只有收发件人、存储位置、附件清单和正文开头 500 字，需要细看时用 `read_email` 读全文或某个附件。OV 暂时不可用时，这封邮件照旧把正文（前 6000 字）直接交给 Agent，附件打不开。

草稿只有你能确认，只认第一次，三天后过期。首次启动只从之后到达的邮件开始；订阅和群发邮件标为 `kind=email.list`，自动回复（含多数退信）和它自己的回信直接跳过；程序自动发出的通知（安全告警、任务失败等）照常交给 Agent 判断。

在 `vika setup` 里勾选邮箱接入（新建或修改实例都行，可以连续接几个）：输入邮箱地址和专用密码，Gmail、QQ、163/126/yeah.net 自动带出服务器，其他邮箱手填。先实际登录一次 IMAP 和 SMTP，成功才保存。已经接了邮箱的实例，在 `vika setup` 修改它时会给一个邮箱菜单：再接一个、给某个邮箱换密码，或者移除某个；只是换密码的话，`vika login email` 更快。回信从邮件所在的那个邮箱发出，待确认的草稿上会写明发件邮箱。

每个邮箱的账号存在 `~/.vika/email/<实例>/<邮箱名>.json`（0600），实例配置里是它们的列表：

```json
{
  "id": "primary",
  "channel": "lark",
  "connectors": ["lark", "email"],
  "emailAccounts": [
    { "id": "email-gmail", "config": "/Users/you/.vika/email/primary/email-gmail.json" },
    { "id": "email-qq", "config": "/Users/you/.vika/email/primary/email-qq.json" }
  ]
}
```

邮箱名是 `email-` 加小写字母、数字和 `-`。账号文件格式如下，手写时文件权限必须是 0600，不要写进 `env`：

```json
{
  "address": "you@gmail.com",
  "password": "<客户端专用密码>",
  "imap": { "host": "imap.gmail.com", "port": 993 },
  "smtp": { "host": "smtp.gmail.com", "port": 465 }
}
```

`password` 是邮箱给第三方客户端用的专用密码，按邮箱账号生成一次，IMAP 和 SMTP 共用：Gmail 叫应用密码（需先开两步验证，填写时去掉空格），QQ/163 叫授权码（需先在设置里开启 IMAP/SMTP）。SMTP 在 465 端口用直连 TLS，其他端口用 STARTTLS，可用 `smtp.secure` 覆盖；可选 `username`（默认同 `address`）和 `name`（回信的发件人名）。只用 OAuth 的邮箱（如 Outlook）暂不支持。方舟 Self-hosted Ark 用不了 Agent 工具，因此不支持邮箱。

## 文档附件与交互卡片

`send_message` / `reply_message` 的正文可选择 `markdown`、`file_path`、`file_key`、`card` 或 `choices`，每次只选一种（微信、Telegram、Slack、QQ 只支持 `markdown` 与 `file_path`）。文件相对于 Agent workspace，最多 20 MiB，图片按图片发送；自定义卡片支持 inline Card 1.0/2.0，最多 30 KiB。话题内的文件和卡片使用 reply + `reply_in_thread: true`。

`choices` 生成带按钮的单选问题，发送立即返回；用户点击后，daemon 校验答题人、有效期与重复提交，将答案作为新输入交给 Agent，并尽力更新原卡片。问题状态与话题路由持久化到 SQLite。普通卡片消息也会进入 Agent，正文缺失时显示 `[card]`。完整参数、限制和调用示例见 [消息格式指南](packages/daemon/agent/lark-message-formats.md)。这些出站参数对所有 runtime 一致；只有经 Agent 工具发出的卡片才有回调登记。

部署前必须在飞书应用的「回调配置」启用 `card.action.trigger` 长连接，详见 [安装说明](install.md#配飞书)。

## 交互规则

- 在已接入 IM 的机器人私聊里发送 `/primary`，把当前 IM 设为主渠道；`/primary status` 查看当前主 IM 和聊天所在 IM。两个命令都只接受该 IM 已绑定的 owner，群聊不执行；Slack 前面加一个空格，发 ` /primary` 或 ` /primary status`。切换会保存到当前实例配置并立即生效，无需重启，也不打断正在进行的任务。后续主动通知和新的邮件审批发到新主 IM，已有待审批消息仍在原 IM 处理。重复选择当前主 IM 不重复修改配置；连接不可用、owner 未绑定或保存失败时保留原设置。微信会提示主动通知依赖会话上下文，长时间未互动后可能需要再发一条消息恢复。若同时打开了 `vika setup`，命令修改配置后旧的 setup 会拒绝覆盖，请重新运行 setup。
- 所有聊天统一具有工作区写权限，可让 Codex 在配置的工作区里完成任务；因此只应把 Agent 加入你信任的聊天。
- 所有聊天共享一个全局 runtime/session，不同 chat 的消息会交错进入同一上下文，用每行的 `chat=` 标签区分归属。
- 群聊的 400ms 从首条待处理消息开始计算，后续补充不会重置时间。不同 chat/thread 各自排队，先到期的先开 turn。Codex/TraeX/Claude/Pi/Durable 已有 turn 时使用即时 steer；Ark 的后续消息进入下一 turn。
- 明确问题、任务、纠正和有效续聊通常会回复；“收到”“谢谢”、闲聊、状态播报、人与人之间的对话、已经有人答好的问题通常保持静默。
- 图片、文件和音视频会以受限资源引用进入 Agent；Codex/TraeX/Claude/Pi/Durable 可用 `get_message_media` 按消息中的 image_key/file_key（微信、Telegram、QQ 按消息 ID，Slack 按消息 ID 和文件 ID）取回本地文件，图片通过工具结果直接交给模型，内容本身不会直接塞进入站 prompt。Self-hosted Ark 不继承 daemon 的 Agent 工具，只有 Worker 环境另行提供等价本地能力时才能读取。未知消息类型不会触发回复。
- 本机 slash 命令在私聊里直接发 `/` 开头的消息，由 daemon 优先处理并回复，不等待当前模型 turn：`/new`、`/help`、`/status`、`/runtime [名称]`、`/model [名称]`、`/effort [档位]`、`/primary [status]`。`/new` 开启干净会话；对 Ark 会先成功创建远端 session，再提交本地 epoch。`/runtime`（可简写 `/run`）查看或切换 Codex、TraeX、Claude、Pi、Durable、Ark，例如 `/run claude`、`/run durable`、`/run ark`；重复选择当前 runtime 保留会话。切换成功后同时保存到 `config.json` 和 SQLite，因此与 `vika setup` 选择 runtime 的结果一致，实例重启后不会回到旧 runtime；setup 重启实例时会把配置中的期望 runtime 协调成 SQLite 中的实际 runtime。跨 runtime 切换退役旧 native session，恢复目标 runtime 上次选择的模型，并在第一条真实消息里接上 OV session 的近期原始消息，更早的由模型按需查 OV archive。`/model` 只查看或切换当前 runtime 的模型，例如切到 Claude 后 `/model sonnet`，切到 Pi 或 Durable 后 `/model provider/id`；不会隐式切 runtime，也不会新建底层会话。Claude 模型列表来自 CLI，展示名称、实际版本 ID 和支持的 effort。`/model` 和 `/effort` 在下一轮生效，保留当前 turn 和上下文。`/effort` 只展示、校验当前模型的档位；能力信息不可用时不猜测候选项。切换模型时一起校验原 effort：仍受支持就保留，否则优先采用目标模型声明的默认档位，再选其支持的 medium 或首个档位，并在回执中说明；明确不支持 effort 时取消额外配置。目标能力未知时保留原设置并提示重试。切 runtime 时，保存的模型已不可用会回退该 runtime 的默认模型；目录发现失败不等于模型不存在，成功切换后才保存回退结果。Ark 的 model/effort 由远端 Agent 配置管理。`/status` 只看主模型是否正在采样及持续时间；主模型等工具或 subagent 时显示空闲。prompt 和代码更新通过当前实例自己的 `vika upgrade <instance-id>` 发布。
- 连续 10 分钟没有成功 outbound delivery 后，daemon 只在后续入站消息的可信 metadata 里加 `idle=<seconds>s`，不会预先 reaction。Agent 仍先按正常规则判断是否要处理；Codex/TraeX/Claude/Pi/Durable 只有决定处理且预计是长工作时，才调用 `react_to_message` 的 `temporary: true` 发一个 `OnIt`。Ark 的 Worker/CLI 路径不使用 daemon 的临时 reaction helper，只支持最终 reaction。无需回复的群消息和短任务不会产生额外噪音；仅查看后保持静默也不会消费 idle 窗口。Core 按 chat/thread 在内存保存 exact `reaction_id`，同 scope 下一次成功的最终文字或 reaction 发出后立即删除，这一轮模型 turn 结束时（成功、失败或被放弃）也会全部删除，不轮询、不扫 DB。删除失败或进程被 `SIGKILL` 时可能残留，这是 best-effort UX 的明确边界。可用 `VIKA_IDLE_REACTION_AFTER_MS=0` 关闭 hint。
- 每个实例有一个角色，决定它是谁、什么时候说话、怎么说：`config.json` 里该实例的 `agents[].role` 填 `colleague`（AI 同事：你的 AI 分身，在工作群里像你一样答疑、跟进，和同事及其他 bot 协作）、`assistant`（个人助理：只为你工作，记事、提醒，主动把值得知道的事告诉你）或 `qa`（答疑助手：在答疑群里查证后把问题讲明白，注意隐私，不接无关话题），也可以填一个 Markdown 文件的绝对路径，用你自己写的角色。`vika setup` 里可以选，选「自己写 prompt」时可以从任一个内置角色复制一份再改。内置角色随版本更新，自己的文件不会被改动；不填就只用内置的默认规则。关于主人和所答产品的信息不写在角色里，agent 会去 OpenViking 查，学到的存回去。改完 `vika restart <实例>` 生效，模型会开一次新会话。
- 去 AI 味的写作规则默认开启，`agents[].naturalWriting` 设为 `false` 关闭。
- 实例的开关都在 `vika setup` →修改实例→「其他设置」里：分段回复、自然写作、显式不回复、OpenViking 自动召回、会议纪要入库，逐个切换，保存后重启实例生效；新建实例时用默认值（开自然写作；第一个接飞书的 AI 同事或个人助理还开会议纪要入库），确认页会列出来。也可以直接改 `config.json` 里该实例的同名字段。
- 会议纪要入库（`agents[].meetingNotes`）：你参加的飞书会议只要开了智能纪要，智能纪要和文字记录会存进该实例 OpenViking 的私有区 `viking://user/<OV user>/resources/meetings/<meeting_id>/`（`note.md` 是会议信息加纪要正文，`transcript.md` 是文字记录，长文另有 `chunks/` 供召回），agent 用 `read_meeting_note` 读全文。做法是定时补扫：实例启动时和之后每 15 分钟，以你的身份查一次最近的会议（第一次往前补 30 天），新会议和还没等到纪要的会议逐个问一下有没有纪要，有就入库；纪要一般在会后 1–4 分钟生成，所以会后大约 15–20 分钟内入库；会议结束 2 小时后还没有纪要就不再问。不订阅任何飞书事件，飞书应用不需要额外配置，入库不叫醒 agent，也不发消息。妙记没有读权限，不存；纪要里链接的文档也不复制，用到时 agent 现读。一台机器上只能有一个实例开这个开关（每个开着的实例都会把每份纪要再存一遍）：新建的第一个接飞书的 AI 同事或个人助理默认开，答疑助手默认关；想换到另一个实例，先在原来那个实例上关掉。状态在 `<dataDir>/meeting-notes/meetings.sqlite3`（每场会一行：waiting/pending/stored/none/skipped/failed）。
- 显式不回复按实例可选，**默认关闭**：`config.json` 里该实例的 `agents[].explicitSilence` 设为 `true` 才开启，重启实例生效。开启后 Codex/TraeX/Claude/Pi/Durable 不回复时调用 `stay_silent`，每条聊天消息（各 IM 的私聊和群都算）都要有一个决定：投递或 `stay_silent`，记在 SQLite `decisions` 表；一轮结束还没表态的消息会带 `undecided=1` 再给模型一次，只提醒一次。关闭时 prompt、工具和 policyHash 与没有这个功能时完全一样；开关一改，该实例的 policyHash 就变，已存的 native thread 不再 resume。Ark（Volc）没有 Core 工具，不支持这个功能：开着开关跑在 Ark 上时，daemon 启动时（以及 `/runtime` 切到 Ark 时）打一条 warn 日志 `explicit_silence_not_applied`。
- 分段回复按实例可选，**默认关闭**：`agents[].stagedReplies` 设为 `true`（或在「其他设置」里打开）。打开后，能即答的问题仍只回一条；需要真正动手（查日志、读代码、跑命令）的任务，模型先贴 `OnIt` 表情表示在做（微信、Telegram 没有表情，改发一句短话），过程中只在拿到查实的阶段性结论时发消息，最后发一条能单独读懂的最终答复。需要对方确认才能继续时，提问就是这一轮的最终答复，对方的回答作为新消息进来再接着做，多久没回都不算同意。阶段性消息走 `send_message`/`reply_message` 的 `interim: true`：只能是文字、只能发到请求所在的会话（请求在话题里就发在同一话题），不算这个 decision 的最终投递（显式不回复开着时仍要求最后表态），也不会摘掉 `OnIt`。关闭时 prompt 用原来「一个 decision 一次最终投递」的规则，工具也没有 `interim`；开关一改 policyHash 就变。Ark（Volc）没有 Core 工具，不支持，开着跑 Ark 时打 warn `staged_replies_not_applied`。
- 有人引用回复 agent 自己的消息时，入站行除了 `reply_to=me` 还带 `quote=`：被引用那条消息的开头，隔很久才回的答复也能对上当初的问题。

- 某一轮模型调用失败时，daemon 会程序化地引用回复一条失败提示（含真实报错），消息不会无声消失。

Codex/TraeX/Claude/Pi（MCP）与 Durable 的发消息、回复、reaction、撤回都经 Core 的 Agent 工具，统一受 runtime authority、freshness fence、幂等 outbox 与投递账本约束；prompt 禁止它们改用 `lark-cli` 发消息。`lark-cli` 仍以固定 bot profile 留给文档、日历等非消息类操作（auth/config/admin 操作始终禁止）。Self-hosted Ark 例外：官方 Environment Worker 无法访问本机 socket，仍由 Worker 的 bash 调用固定的本地 `lark-cli` 回复，不经过 freshness fence 与 outbox；回复可以完全由工具完成，`end_turn` 不要求再返回 `agent.message`。

## 飞书前置条件

机器人应用要建在手机号注册的飞书账号（注册时会建一个免费企业，你是管理员）或公司的飞书账号下。用豆包账号登录的飞书能建应用、能发布，但开放平台不给应用发 `tenant_access_token`（`code=21001`，访问控制策略拒绝），机器人收发不了消息；`vika setup` 遇到时会说明原因，不写配置。

仓库根目录的 [`permission.json`](./permission.json) 是完整功能权限清单。首次配置应用时，在飞书开放平台进入「开发配置 → 权限管理 → 批量开通」，导入该文件；具体格式和审核规则见 [官方权限申请说明](https://open.feishu.cn/document/server-docs/application-scope/introduction?lang=zh-CN)。清单覆盖 daemon 可调用的文档、消息、任务、会议等完整能力，并非最小权限集；正式环境应按实际用途删减权限、限制应用可见范围。新增的需审核权限只有在创建应用版本、发布并经管理员审核后才会生效。

只运行基础消息链路至少需要：

1. 使用长连接订阅 `im.message.receive_v1`。
2. 私聊消息权限 `im:message.p2p_msg`（或对应只读权限）。
3. 群聊不 `@` 也要收到消息，需要敏感权限 `im:message.group_msg`；只有 group-at 权限时，平台不会投递普通群消息。要收到群里其他机器人的消息，还需要 `im:message.group_msg.include_bot:read`（只有 @ 权限时对应 `im:message.group_at_msg.include_bot:readonly`）；`vika setup` 建应用时已经申请。
4. 机器人发消息权限 `im:message:send_as_bot`。
5. 获取机器人所在群列表权限 `im:chat:read`。
6. 把机器人加入需要参与的群，并把应用可见范围限制为自己的日常使用范围。

升级后新版本可能要更多权限或事件：`vika setup` 一开始会核对每个接了飞书的实例（应用的权限、接收消息事件、卡片回调，以及你给它的用户授权）。缺东西的实例在「要做什么」里标出「飞书应用缺 N 项」；进去修改它，「飞书应用」一项会写明缺什么，选「扫码补齐缺的 N 项」再保存，保存时扫一次码补齐应用（你要是应用的所有者或管理员），授权也缺的话再扫一次码重新授权，然后照常重启。不用记任何命令。

截至 `lark-cli 1.0.86`，CLI 没有导入或发布开放平台应用权限清单的命令：`lark-cli application` 只管理 slash command，`lark-cli apps` 管理的是妙搭应用。不要用 `lark-cli auth login` 处理 bot 缺权；bot scope 必须在开发者后台导入、发布并完成审核。清单中的 user scope 还需要对应用户执行 OAuth 登录授权。

本机 `lark-cli` 的目标 profile 必须已配置且 bot identity 为 `ready`；只有使用 user scope 时才需要额外 OAuth 登录。当前选择的本机 agent CLI（例如 `codex` 或 `traecli`）需要已经登录。daemon 会固定启动时选中的 lark profile，不会跟随后续默认 profile 的变化。

## 本地开发验证

```bash
cd vika
mkdir -p ~/.vika
cp config.example.json ~/.vika/config.json
npm test
npm run check
npm run start:daemon   # 127.0.0.1:3738
npm run start:web      # 127.0.0.1:3737
```

源码目录应使用独立的小工作区，避免测试中的 Codex 改到 daemon 自身。正式安装在 `~/.vika/releases/`，由 Vika 自己的 pm2 托管；每个实例的工作区由 release 之外的持久 manifest 独立配置。

## 日常运维

```bash
vika status
vika logs <instance-id>
curl http://127.0.0.1:3737/ready
vika upgrade <instance-id>
vika upgrade all
```

`vika-updater` 监听权限为 `0600` 的本机 Unix socket，不开放 TCP 端口，也不读取 Agent 的源码 checkout。它只接受 manifest 中的 agent id 和 `all`，每次请求都重读 manifest；请求不能传版本、路径、下载地址或 shell。它只装下载地址上 `latest` 指向的已发布版本：核对 SHA-256、用安装包自带的 Node 冒烟检查后才原子切换 `current`，随后从同一份 ecosystem config 滚动目标 daemon，`all` 还会滚动共享 Web，最后从新版本重启自己。相同版本的第二次请求直接复用。整个过程和安装脚本共用同一把 deploy lock。

### 可横向扩展的多 Agent 部署

OpenViking 是 Vika 的必需组件：setup 新建实例时，会在接入 IM 前列出已检测连接，并始终提供火山引擎 OpenViking 托管服务（只需填 API Key，在 OpenViking 控制台「用户管理 → API Key」获取）、手动填写地址与凭据、导入配置文件的入口，支持远端和自定义端口；验证通过才继续。用 API Key 的服务只按 key 认身份，实例配置里记下的 user 就是这把 key 登录的 user；手动换了地址时不会沿用之前的 Account、User。setup 修改已有实例时，直接进入实例设置菜单，选择与其他配置项并列的 `OpenViking` 才打开连接配置；修改保留原 actor，选择「保存」后生效，返回菜单或放弃修改不覆盖原配置。安装脚本不提问：终端里的首次安装直接进入 setup 配置 OV；其余安装（已有实例、`--no-setup` 或无终端）要求 OV 已经可用，否则不切换版本。daemon 启动时 OV 拒绝配置或凭据会退出；只是暂时连不上则先不带历史启动，恢复后自动同步。每个实例必须提供独立的 `ovConfig`。配置中的 `agents[].ovAutoRecall` 是布尔值，默认 `false`；改为 `true` 后启用 runtime OV 插件的自动召回，固定使用 `actor` 范围。自动 capture、compact 时 commit、daemon session 历史同步不受该开关影响。修改后重启实例生效，runtime 需已加载最新 OV 插件。

新建实例的连接列表来自环境变量指定的配置、Vika 保存的连接和 OpenViking CLI 配置，不列出其他实例的配置；修改实例时额外提供该实例当前的配置。选定连接后，新实例会生成自己的配置文件，`actor_peer_id` 固定为新实例 ID。已有配置指向本机默认地址时，不再重复进行无身份探测。Trusted 模式需要填写 Account 和 User；缺少这些字段会提示服务已启动但需要配置身份，补齐后通过身份检查才能继续。

运行中 OV 请求失败不会退出 daemon，也不主动向聊天发送故障通知。新原生会话可跳过 OV 历史继续处理消息；本地同步账本保留，后台恢复后重试。已有会话不会在恢复后突然补入历史；下次新会话可再次读取 OV。归档完整性错误仍会阻止不完整的历史交接，不回退到 SQLite 回放。

每个 Agent 是独立 daemon 进程，不是另一个 Agent 的子 agent；manifest 可以增加多个 daemon，它们共享一份 release、Web 和 updater。唯一配置源是 JSON manifest，完整格式见 [`config.example.json`](./config.example.json)。顶层字段是 `version`、`defaultInstance`、`env`、`agents`；每个 agent 必须完整提供 `id`、`displayName`、`profile`、`port`、`dataDir`、`workspace`、`ovConfig`、`model`，可用 `channel` 选择 `lark` 或 `weixin`，省略时默认 `lark`。

`env` 是所有 daemon 共用的运行参数，只认示例中列出的 key，所有 JSON value 都必须是字符串（包括布尔值和数字；空字符串允许）。同名系统进程环境变量优先于 manifest `env`，可用于临时覆盖。Agent identity、channel、Lark profile、端口、dataDir、workspace、OV config、model，以及 Web/updater 控制面字段不能放进 `env`；未知或保留的 key 会被丢弃，不会传给任何进程。每个 agent 的 `profile` 会同时固定注入 `VIKA_LARK_PROFILE` 和 `LARKSUITE_CLI_PROFILE`；没有接入飞书的实例，其 Agent 子进程不会继承这两个飞书变量。

manifest 里当前版本不认识的字段（顶层、agent、`env` 等任何一层）会被忽略：daemon 启动日志记 `config_fields_ignored`，`vika status` 和 `vika setup` 会列出这些字段，所以旧版本写的配置升级后照样能起来。已知字段仍严格校验：启动 preflight 拒绝相对路径、非法 id/端口，以及重复的 id、profile、port、dataDir、workspace、OV config；任意 agent 的 dataDir/workspace 互相嵌套也会拒绝。ecosystem 据此生成全部 `vika-<id>`、Web routes 和 updater allowlist，每个 daemon 都被固定注入自己的 Lark profile、CLI profile、数据、工作区、OV 和 model。

`npm run start:daemon` 默认运行 `defaultInstance`；临时前台跑另一只可设置 `VIKA_INSTANCE_ID=<id>`。添加、删除 agent，或修改 id/port 等 topology/allowlist 时，先保存 manifest，再执行 `vika upgrade all`（或 `vika start <新实例>` 加 `vika restart web`）。`upgrade all` 在新拓扑全部验活后，会删除同一受管 release 中已不在 manifest 的旧 daemon PM2 app，不删除数据目录，也不触碰外部同名前缀进程。若重命名 id 时还要立即复用旧端口，先核对旧 app 的 executable 确属本部署，再精确 stop/delete 旧 app；代码不提供 alias 或 migration。普通 daemon 代码升级仍用 `vika upgrade <id>`。

删除实例也可以运行 `vika setup`，选择「删除一个实例」，选定后确认（默认取消）。setup 会移除实例配置和对应的受管 PM2 进程、保存进程列表，并刷新控制台；工作目录、历史数据、登录凭据、OpenViking 记忆和各平台的机器人均保留。删除默认实例时，可选择新的默认实例（只剩一个时自动选用）。删除最后一个实例时，也会移除 Web 和 updater 进程，将配置备份到原路径加 `.bak` 后移走原文件，下次 `vika setup` 可重新新建实例。部署或升级进行中不能删除；进程删除失败时保留配置，可修好后重试。

代码升级先由维护者发布新版本（见 install.md），再调用 `vika upgrade <instance-id>`。升级承载当前 turn 的自身进程也由 updater 继续完成，但当前 CLI/模型连接可能随进程退出，结果应用 `/status` 或本机 health check 验收。

## Activity 页面

打开 [http://127.0.0.1:3737/](http://127.0.0.1:3737/) 可以查看本机活动账本：消息是否收到、Agent 每一轮做了什么（思考、工具调用）、选择了哪种投递方式，以及发送是否成功。

页面没有登录，只监听本机。Vika 跑在服务器上时，用 SSH 端口转发在自己电脑上打开：`ssh -L 3737:127.0.0.1:3737 <服务器>`。

- 默认进入当前认知会话。`/new` 开启新的认知会话；`/runtime` 更换原生 session，但保留认知会话；同一 runtime 的 `/model` 和 `/effort` 保留原生 session 和认知会话，下一轮生效。顶栏可切换历史会话，或查看全部会话的完整账本。
- 下方是按轨道排列的时间轴（回合 / 消息 / 助手 / 投递 / 命令 / 系统）；上方详情列表与播放头双向同步。
- 失败保留在原时间位置并标红；没有“需要处理”队列。
- 历史会话不跟随实时更新，可一键回到当前会话；当前会话在拖离尾部后可回到最新。
- 页面每 4 秒在本机刷新一次；没有外部资源或云端服务。

这些活动作为保留历史的事件账本写入同一个 SQLite；普通状态清理不会删除 activity。监听启动记录在新进程接管或正常停止时会收口，因此历史启动仍可见，但只有当前监听显示“进行中”。网页 API 只返回脱敏后的来源/会话引用、认知 epoch 和裁剪后的正文，不暴露飞书 message/chat ID、Codex/TraeX/Claude/Ark session、幂等键或 raw event。

## Runtime 模块边界

daemon 和 slash commands 只通过 `AgentRuntime` 的 `start`、`submit`、`snapshot`、`transition`、`stop` 接口控制模型。`runtime-catalog` 统一 model alias、effort 与 capability；`runtime-adapter` 把 Codex、TraeX、Claude、Pi、Durable、Ark 的 transport lifecycle 归一；`runtime-input` 生成认证过的输入 envelope。具体 Adapter 只保留协议差异：Codex JSON-RPC、Claude stream-json、Pi RPC JSONL、Durable host JSONL、Ark Sessions/SSE。Self-hosted Ark 的输出和工具执行归 Worker/runtime 所有。`submit` 返回 `accepted: false` 时 caller 仍持有整批消息；返回 `accepted: true` 后 ownership 才交给 runtime。

### Runtime 工具接入

随包 CLI 的发现由 `packages/daemon/src/agent-clis.mjs` 统一登记，目前包括 `lark-cli` 和官方 Slack CLI（命令名 `slack`）。Codex/TraeX/Claude/Pi/Durable 的子进程 PATH 优先加入这些 CLI 的目录，系统提示词同时列出实际命令路径；安装 release 或源码执行 `npm run setup` 后，无须全局安装或手工修改 PATH。`LARK_CLI_BIN` / `SLACK_CLI_BIN` 覆盖会同时用于 PATH 和提示词，改名及含空格的路径使用经过 shell 转义的实际命令；相对路径以 Vika 包根目录解析。消息投递、profile 和连接器凭证的边界不变，Slack bot token 仍由 Connector 管理。

以后接入新的随包 CLI，在完成固定版本安装后向该清单添加命令名、二进制路径及版本检查参数，即可复用所有本地 runtime 的 PATH、发现提示词和 release 冒烟检查，无须用户额外设置。`node packages/daemon/scripts/check-agent-clis.mjs` 在临时 HOME、独立工作目录和干净 PATH 中检查 sh/bash 能否找到并执行所有登记的 CLI；release 构建会自动运行。

Pi 作为精确版本的 npm 依赖随 release 安装和升级，默认不使用全局 `pi`。daemon 通过会话扩展注册 `personal_agent` MCP（`direct` exposure），不修改用户的 MCP 配置；工具未加载成功则启动失败。Codex/TraeX/Claude/Pi/Durable 共用 conversation policy，不再保留 Pi 的 Bash/CLI 工具教程。安装和模型登录见 [install.md](./install.md)。

Durable 基于 pi-durable，同样作为精确版本的 npm 依赖随 release 发布。daemon 管着一个 host 子进程，它把对话、模型轮次和工具调用先写进 `<dataDir>/durable/session.sqlite`，一轮中途进程退出时，重启后从最后的检查点接着跑完。模型复用 Pi 的 `~/.pi/agent` 配置（也认 provider 的 API key 环境变量），Agent 工具由 host 直接从 `actions.sock` 注册，另带 pi-durable 的 `bash`、`read`、`write`、`edit`。协议、模型选择顺序和恢复机制见 [Durable runtime](docs/pi-durable-runtime.md)。

Agent 工具只有一份定义与实现，在 Core 的 `ActionService`。Codex/TraeX/Claude/Pi 均挂本机 stdio MCP 适配器（`actions/mcp.mjs`），Durable 的 host 用同一个 socket 客户端（`actions/client.mjs`），统一转发到 `<dataDir>/actions.sock`，runtime 子进程经 `PERSONAL_AGENT_SOCKET` / `PERSONAL_AGENT_AUTHORITY` 拿到地址和运行权。Self-hosted Ark 使用 Worker 的 bash 调用固定 `lark-cli`，因为 Worker 不在 daemon 的进程树里。Ark 的调用发生在官方 Environment Worker 进程里；Worker 是唯一工具执行面，也只有这条路径能在同一个原生 turn 中多次调用 Environment 内的 CLI。daemon 只观察远端 `agent.tool_use`、`user.tool_result` 与最终状态。云沙箱的严格 JSON Tool Call bridge 已移除；理论上可把 CLI credential 同步到云沙箱重建类似链路，但这不是当前支持或建议的接入方式。Ark 的直接 CLI 路径使用稳定幂等键，但不经过 daemon 的 authority/freshness 与 outbox，因此其可靠性边界与其他 runtime 不同。

## 可靠性边界

- 消息先写 SQLite（状态直接 DONE），再喂给当前常驻 runtime。Codex/TraeX 用 `turn/start` / `turn/steer`，Claude 用 persistent stream-json input，Durable 的 host 先把输入写进 pi-durable 的 SQLite 再请求模型，火山方舟用 session events + SSE，并在断线后以 event history + event id 去重补洞。Ark 会持久化本轮 `user.message` fence；daemon 重启后从远端历史恢复已接受的 turn，不会重复 POST。若 POST 是否被接受无法证明，旧 session 会被持久化隔离，克隆新 session 并原子轮换 epoch/authority 后才恢复队列，避免迟到输出串到下一条消息。
- 400ms 群聊窗口只做 transport batching，不创建 chat 专属的 Codex session、history 或 memory。进程在窗口内停止或崩溃，仍可能让已落库消息没来得及喂给 Codex；这版没有加入 durable replay 或 delivery ledger。
- slash command 和后续消息经过同一条全局 ingress barrier，但命令本身不等待模型；只读命令并行回答，会话变更命令先中断旧 turn。会话变更先 prepare 新 session，再用一次 SQLite CAS 原子提交 epoch、session、authority 与 model 设置，最后做不可失败的本地 activation；prepare 或 CAS 失败时旧认知状态不变。跨 runtime switch 同样先 preflight 目标进程再交接，目标启动失败时仍留在旧 runtime。`/model` 和 `/effort` 只保存当前 runtime 的模型和推理档位，不中断当前 turn、不更换会话；下一轮开始时 Codex/TraeX 按轮传参、Pi 调用 `set_model` / `set_thinking_level` RPC、Claude 换进程后恢复同一个 session，应用成功后才投递输入。重复设置当前模型或档位不做变更。
- SQLite 保留 active runtime/session、投递幂等账本和 OV 同步 outbox。跨 runtime 的历史上下文只来自 OV session 的近期原始消息，更早的由模型按需查 OV archive 里的原始消息；较旧的 OV（包括火山托管服务）默认会为每个归档生成 Working Memory 摘要，Vika 不改 OV 的这项设置，只是从不使用它；同一 native session 继续使用原生 compaction。OV 不可用或返回异常时新 session 不带历史继续，不回放本地 journal。详见 [OV session context](docs/ov-session-context.md)。
- 收到的每条消息压缩成一行 `[chat=… msg=… event=… time=… from=…] 正文`；`chat=`/`msg=`/`event=` 为完整 id（作为工具参数使用），thread 后缀与 `from=` 为短 stub。`msg=` 是回复目标，`event=` 是 `decision_id`；日历、issue 等非聊天事件没有 `msg=`，同样用 `event=` 发通知。新线程首轮注入跨聊天历史，以 `--- history above, new messages below ---` 分隔。
- Codex/TraeX/Claude/Pi/Durable 通过 Core 的 Agent 工具执行消息动作。
- raw 飞书事件不会整包落库，避免保存事件 header 中的 token；正文只为会话上下文保留。
- Activity 账本保留脱敏后的短摘要（含收消息、turn 开始/结束、steer、agent 输出）；老记录由网页折叠，不因业务表的清理而消失。
- 单个 turn 没有总时长 watchdog；Codex/TraeX/Claude 若长时间不结束一轮，该轮会一直 active。

当前固定为 raw 事件、text 回复；不支持 compact 订阅。富文本 post 中的图片会作为 image_key 资源引用解析，不解析 markdown 图片语法。

## 飞书端验收

1. 私聊发一个明确任务，应执行并回复。
2. 私聊只发“收到，谢谢”，应保持静默。
3. 群里不 `@` 发一个尚未解决的问题；Agent 有增量价值时回复。
4. 群里发闲聊或人与人的对话，应保持静默。
5. Codex/TraeX 空闲时，在同一群聊或话题里快速连发一条任务和一条纠正（间隔小于 400ms）；两条应进入同一个 `turn/start`，最终回复采用纠正后的要求。
6. Codex/TraeX/Claude 思考时立即补一句纠正；该补充会 steer 进同一轮。
7. 私聊要求 Agent 向另一个已加入群发送测试消息；Agent 应先解析群名，再通过专用工具发送，并在当前私聊确认结果。
