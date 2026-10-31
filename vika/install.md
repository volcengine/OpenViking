# 安装 Vika

这份文档只讲把 daemon 跑起来。想先了解它解决什么问题，读 [README](./README.md)。

## 一键安装

```bash
curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/vika/install.sh | sh
```

安装脚本识别系统（Linux、macOS，x64 / arm64；Windows 用 WSL2），下载这台机器对应的安装包，核对 SHA-256，解到 `~/.vika/releases/<版本>`，把 `~/.vika/bin` 加进 shell 的 PATH，然后进入 `vika setup`。机器上只需要 sh、curl、tar 和 sha256sum（或 shasum）：安装包自带固定版本的 Node、pm2、lark-cli 和 Pi，不用、也不改机器上已有的 Node、npm、pm2 或 git。新版本先试跑自带的 Node 才切换，跑不起来（系统库太旧、CPU 不匹配）就保留原有安装。不支持 musl（Alpine 等）。

安装脚本和 `vika` 命令都有中文和英文两种界面。`vika --lang en`（或 `zh`）切换界面语言，之后的命令都用它；安装时写成 `curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/vika/install.sh | sh -s -- --lang en`。写了 `--lang` 的都会记在 `~/.vika/language`；环境变量 `VIKA_LANG` 只管当次。都没有时默认中文。

在终端里运行时，下载进度是一条小帆船从左驶到右，之后每一步收成一行 ✔；输出不是终端或设了 `NO_COLOR` 时就是纯文本。这台机器还没有实例（没有 `~/.vika/config.json`）时，再跑一次安装命令也会回到 setup；已有实例时只装新版本，并提示 `vika restart`。

## 准备

- 一个 coding agent：Codex、Claude Code 或 TraeX 先装好并登录；不装也行，Vika 自带 Pi 和 Durable，两者都用你 `~/.pi/agent` 里的模型配置，没配过就运行 `vika pi` 后 `/login`。
- OpenViking（必需），用来保存跨会话上下文。`vika setup` 会列出已检测的连接、来源与检查结果，无论是否可用，都提供「手动填写／修改地址和凭据」「指定 OV 配置文件」「重新检测」和「退出」。可填写本机或远端 URL、自定义端口、API Key（隐藏输入）以及可选的 Account / User / Agent。验证失败留在配置界面重试，通过后才继续接入 IM。在终端里首次安装时，安装脚本装完直接进入 setup，OV 就在那里配置。
- 接飞书的话，要一个手机号注册的飞书账号或公司的飞书账号，用它扫码建机器人。现在用手机号注册飞书会顺带建一个免费企业（名称随便填，你是管理员），个人用也是这样注册。**用豆包账号登录的飞书不行**：它能在开放平台建应用、也能发布，但平台不给应用发访问凭证（`tenant_access_token` 返回 `code=21001`），机器人收发不了消息。setup 碰到这种情况会说明原因并回到上一步，可以换账号重扫，或者先只接微信、Telegram 等其他渠道。

检测来源包括正在修改的实例的 `ovConfig`、`OPENVIKING_CLI_CONFIG_FILE`、Vika 保存的连接 `~/.vika/ov-connection.json`、`~/.openviking/ovcli.conf` 和默认本机地址 `http://127.0.0.1:1933`，不会扫描其他端口。手动导入的文件保持原样；setup 保存实例后，把手动填写或导入的连接以 0600 权限保存在 Vika 目录，供之后新建实例使用，本来就会被检测到的配置文件不复制。

安装脚本从不提问。除了紧接着进入 setup 的首次安装，都会在切换版本前检查 OV：已有实例按各自的 `ovConfig` 检查；`--no-setup` 或无终端的首次安装按环境变量指定文件、Vika 已保存连接、OpenViking CLI 配置、本机默认地址依次选择，无配置时仅接受本机默认端口的 dev-mode OV。检查不过就不切换版本。daemon 启动时 OV 拒绝配置或凭据会退出；只是暂时连不上（比如开机时 OV 还没起来）则记一条 activity、先不带历史启动，恢复后自动同步。

修改已有实例的 OV 连接时，会保留其原有记忆身份和召回设置，创建新的配置副本；setup 选「保存」才更新 manifest，选「放弃」清理副本。

Codex、TraeX、Claude、Pi 都要先完成各自 CLI 登录；Durable 不用单独登录，Pi 能用的模型它都能用（见下文）。完整配置字段看 [config.example.json](./config.example.json)。

TraeX 的命令可能叫 `traex`、`traecli` 或 `trae-cli`，装了哪个都行；Vika 用它的 `app-server` 协议，太旧、只有 `acp serve` 的版本不能用。setup 会依次检查 `traex`、`traecli`、`trae-cli`，解析软链接并去重，只有 stdio `initialize` 握手和 `login status` 都通过才提供该选项；探测不会创建会话或调用模型。显式设置 `TRAECLI_BIN`（兼容旧变量 `TRAEX_BIN`，优先级更高）时只检查指定文件，失败不会悄悄换另一个 CLI。setup 保存通过检查的实际路径，不修改用户的命令别名。

如果启动失败，先运行 `vika logs <实例>` 查看具体错误。app-server 握手失败会附上有长度上限、经过脱敏的 stderr。`vika start <实例>` 对已经 online 的 PM2 进程也会验证所属 release 和 ready 状态，服务未就绪会返回失败，不会重启或覆盖该进程。PATH 提示找不到 `vika` 时，可先用 `~/.vika/bin/vika`；安装器依据 shell 写 rc，macOS 的 bash 使用 `.bash_profile`，Linux 的 bash 使用 `.bashrc`，zsh 使用 `.zshrc`，当前终端需加载对应文件或重新打开。

Claude Code 需要 2.1.259 或以上，支持 `--permission-prompts none`；启动预检会检查该参数。daemon 是无人值守会话：Pi 扩展对话框、Codex/TraeX 的审批和用户输入请求会被明确取消或拒绝；Claude 使用官方无人值守设置，并拒绝意外的控制请求。需要向人提问时，模型应通过消息工具发文字或支持的选项卡，结束当前轮次，再等待用户消息或卡片答案。用户的扩展、provider 和其他会话配置不因此被关闭。

Pi 1.4.0 在启动扩展完成后才开始读取 RPC 回复，因此扩展在 `session_start` 中要求人工输入时，daemon 会立即拒绝启动并提示关闭该启动交互；运行期间的对话框则正常返回取消。

Pi 的确切版本由 `packages/daemon/package.json` 和 lockfile 固定，随安装包发布。`vika pi` 打开这份 Pi，可用来 `/login`；已有 Pi 的模型登录信息直接复用，不会升级全局 `pi`。`PI_BIN` 留空时用安装包内的版本；显式指定其他路径时，该程序必须是本 release 验证的同一版本，否则启动会给出修复指引。

daemon 启动 Pi 时只为该会话注册 `personal_agent` MCP server，使用 `direct` exposure，让模型直接看到各个工具和参数。它不会修改 `~/.pi/agent/mcp.json` 或工作区 `.pi/mcp.json`，也不会给用户其他 Pi 会话添加该 server。若这些文件已有同名 server、内置 MCP 被禁用，或工具没有成功加载，daemon 会拒绝启动 Pi；不回退到 Bash/CLI。普通用户的其他 MCP server 配置仍由 Pi 自己加载。

Durable 是基于 [pi-durable](https://github.com/earendil-works/pi) 的内置运行时，和 Pi 一样随安装包发布，不用装 CLI。对话、模型轮次和工具调用先写进 `<dataDir>/durable/session.sqlite` 再显示，进程在一轮中途退出时，重启后从最后的检查点接着跑完。模型和 Pi 共用一份配置：pi-ai 内置的 provider，加上 `~/.pi/agent` 里的 `models.json`（自定义 provider，例如火山方舟的 OpenAI 兼容接口）、`auth.json`（`vika pi` 里 `/login` 存的 key）和 `settings.json` 里的默认模型。也可以只给内置 provider 配 API key：把 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`DEEPSEEK_API_KEY` 等写进 `~/.vika/secrets.env`，不要写进 `config.json`。没指定模型时用 Pi 的默认模型；运行中用 `/model`、`/effort` 切换，也可以在 `config.json` 的 `env` 里设 `VIKA_DURABLE_MODEL`、`VIKA_DURABLE_EFFORT`。Durable 和其他运行时一样经 Agent 工具发消息，另外带 pi-durable 的 `bash`、`read`、`write`、`edit`，在实例的工作目录里执行。协议和恢复机制见 [Durable runtime](docs/pi-durable-runtime.md)。

## 手动配飞书（不用 setup 时）

`vika setup` 扫码新建或选择已有应用时会自动完成下面这些；手写 `config.json`，或在 setup 里手动填 App ID 和 App Secret 时，才需要自己做（setup 会列出读得到的缺项）。

1. 在飞书开放平台创建 bot，在「事件配置」中开启长连接订阅 `im.message.receive_v1`；在独立的「回调配置」中开启长连接并添加 `card.action.trigger`。只订阅消息事件可以收到卡片正文，但收不到按钮点击。
2. 导入仓库根目录的 [permission.json](./permission.json)，发布应用并完成权限审核。
3. 把 bot 加入你信任的聊天，并限制应用可见范围。群里不 @ 也要收消息时，记得开 `im:message.group_msg`；要收到群里其他机器人的消息，再开 `im:message.group_msg.include_bot:read`。
4. 用 `lark-cli` 配好 bot profile。只有要以用户身份读个人资源时，才额外做 OAuth 登录。

文档上传需要 `im:resource`，发送需要 `im:message:send_as_bot`，读取卡片需要 `im:message:readonly`，更新已回答卡片需要 `im:message:update`；仓库权限清单已包含这些 scope。请使用支持 `card.action.trigger` 的 lark-cli（本实现按 1.0.94 的命令与事件 schema 验证）。CLI 能正常启动不代表开放平台已启用卡片回调，该控制台配置仍须核对。无需额外公网回调地址，CLI 会及时自动 ACK，模型随后异步处理答案。

`permission.json` 是完整能力清单，不是最小权限集；正式环境按实际用途收窄权限和可见范围。

## 从源码运行（开发）

源码运行需要 Node.js 22.19.0 或以上。

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking/vika
npm run setup          # 按 lockfile 装依赖，取回固定版本的 lark-cli、Slack CLI，检查托管 Pi
npm test && npm run check
mkdir -p ~/.vika && cp config.example.json ~/.vika/config.json
# 编辑 config.json：至少填 profile（飞书实例）、dataDir、workspace、model、ovConfig；ovAutoRecall 默认 false
npm run start:daemon
```

把源码打成安装包装到本机试，走的是和用户一样的安装路径：

```bash
node scripts/build-release.mjs                    # 只打已提交的代码，产物在 dist/
sh scripts/install.sh --file dist/vika-<版本>-<平台>.tar.gz
vika restart
```

默认配置是 `~/.vika/config.json`。如需放在其他位置，给所有启动、部署和升级命令设置同一个绝对路径的 `VIKA_CONFIG`。

### 飞书图片、文件收发的临时目录

`lark-cli` 的路径保护会影响 `/root` 下的附件文件操作。安装时 `vika setup` 检测到 `dataDir` 是 `/root` 或其子目录，且没有显式临时目录配置时，会自动在实例配置中写入 `"tempDir": "/tmp/vika-<实例名>"`，例如 `/tmp/vika-primary`；不同实例使用独立目录。数据库、会话和工作目录保持原位，`TMPDIR` 不会改变这个默认值。用户仍可编辑 `~/.vika/config.json` 的 `agents[].tempDir`，setup 会保留显式设置。

已运行的实例不会仅因升级而自动迁移：可在 setup 修改并保存该实例时补上默认值，或手动增加上述 `tempDir` 字段后执行 `vika restart primary`。不要把整个 `dataDir` 迁到 `/tmp`。

也可以设置 `VIKA_TEMP_DIR=/tmp/vika-primary`，或将其放入配置顶层的 `env`；这是所有实例共享的默认值，多实例部署应分别设置 `agents[].tempDir`，该字段优先。路径必须为绝对路径，未配置时仍使用 `<dataDir>/tmp`，`TMPDIR` 不影响它。临时目录也承载发送文件的临时副本。

上传和下载共用该临时根目录，均以 `0700` 创建并校验目录；已存在的目录必须属于 daemon 用户、仅该用户可访问，且不能是符号链接。使用专用目录，不要直接配置为 `/tmp`。上传先把工作区文件复制到 `<tempDir>/outbox` 中独立的私有子目录，以相对路径交给 lark-cli；图片、文件的发送、回复和主人通知均走此流程，成功或失败后删除副本。下载文件保存在 `<tempDir>/media`，供 MCP 和后续工具读取，按 `VIKA_RETENTION_MS`（默认 90 天）在 daemon 维护时清理过期下载。

重启后重新读取一条之前失败的图片消息，并发送、回复一张工作区图片，确认图片内容正常。`lark-cli --dry-run` 只能验证本地参数，不能代替真实上传、下载验证。无需重启服务器或 OpenViking，也无需清空数据库。

### 微信扫码与单聊链路

给微信单独增加一个 agent 时，设置 `"channel": "weixin"`、`"connectors": ["weixin"]`，`model` 可选 Codex、TraeX、Claude 或 Pi，并使用独立的 `port`、`dataDir`、`workspace`、`ovConfig`。要让一个实例的 session 和 OV 同时接收飞书和微信，保留 `"channel": "lark"` 并设置 `"connectors": ["lark", "weixin"]`。纯微信实例不填 `profile`，填了会被拒绝。完整片段见 [README 的微信单聊 MVP](./README.md#微信单聊-mvp)。

```bash
vika login weixin weixin
VIKA_INSTANCE_ID=weixin npm run start:daemon
```

登录命令按实例读取 manifest，在终端输出二维码。手机确认后，token、机器人账号、扫码用户和长轮询游标保存在该实例的私有 SQLite 中。Core 随后启动独立微信 Connector；它调用 `notifyStart`，持续请求 `getupdates`，把入站文本、图片、文件和微信已有语音转写封装成 `EVENT` 交给 Agent。媒体保存在 `dataDir/weixin/media`。Agent 通过统一的 Agent 工具（Codex/TraeX/Claude/Pi 共用 MCP 适配器）把查询和发送请求经 `dataDir/actions.sock` 交给 Core；Core 先写 outbox，再由 Connector 携带 `context_token` 和由幂等键派生的 `client_id` 调用 `sendmessage`。

机器人只和扫码的微信号单聊，其他发送者的消息直接忽略。一个微信号同时只能连一个机器人：给另一个实例扫码，这个实例的微信会报会话过期。扫码后要先给机器人发一句话，微信才会送达它主动发的消息；Vika 在收到你的第一句话之前不会尝试主动发微信，直接报错。会话过期（`ret=-14`）时只有微信 Connector 停下，飞书照常工作；Core 按 5 秒、15 秒、1 分钟、之后每 5 分钟的间隔重试。重新运行扫码命令即可，下一次重试会用上新登录，不需要重启实例。当前支持微信单聊文本、图片、文件和微信已有语音转写；不处理原始语音、视频或群聊。

### QQ 机器人接入

在包含 QQ 功能的 release 中运行 `vika setup`，勾选 QQ。先在 [QQ 开放平台](https://q.qq.com/#/) 按页面引导注册开发者账号（没有开发者账号无法创建机器人），再到腾讯 [QQ 机器人入口](https://q.qq.com/qqbot/openclaw/) 创建机器人并取得 AppID / AppSecret，开启单聊消息权限、用本人 QQ 添加机器人。向导会隐藏输入 AppSecret，再要求在机器人私聊中发送一次性 `vika-bind …` 验证码，三分钟内有效；验证成功后才保存。主机需能访问 `https://api.bot.qq.com`、腾讯 WebSocket 网关和附件 CDN，不需要公网监听端口。

支持 owner 私聊的文本、图片、文件、引用回复与 slash 命令，也可以选为主渠道接收通知和编号邮件审批；主动通知取决于平台权限与配额。纯 QQ 实例不填 `profile`，支持 Codex、TraeX、Claude、Pi，暂不支持 Ark。每个实例使用独立机器人，凭证保存在 `<dataDir>/qq/state.sqlite3`，不写入 `config.json`。

重新授权已接入的实例用 `vika login qq [实例]`：先停实例，验证新凭证和本人私聊，再恢复启动。取消或失败保留原账号。文件上限、被动回复限制与停机期间的消息边界见 [README 的 QQ 私聊](./README.md#qq-私聊)。此功能随 release 发布后安装器才会显示，工作区修改不会直接更新线上 `latest`。

### Telegram Bot 接入

在包含 Telegram 功能的 release 中运行 `vika setup`，新建或修改实例，勾选 Telegram。先在官方 [@BotFather](https://t.me/BotFather) 用 `/newbot` 创建独立 Bot，再在安装向导的隐藏输入框粘贴 token。打开向导生成的链接或扫描二维码，在 Bot 私聊中点击 Start；绑定有效期三分钟，成功后才保存新凭证并启动实例。没有公网 Webhook 或手机号登录步骤，Vika 主机须能访问 `https://api.telegram.org`。

只处理绑定 owner 的私聊，支持文本、图片、文件与引用回复。Telegram 可以作为主渠道接收新邮件提醒和带编号的回信审批。纯 Telegram 实例不填飞书 `profile`；混合实例只在接入飞书时填写。凭证和游标在 `<dataDir>/telegram/state.sqlite3`，token 不写入 `config.json`。完整范围与文件大小限制见 [README 的 Telegram 私聊](./README.md#telegram-私聊)。

重新授权已接入的实例：

```bash
vika login telegram [实例]
```

此命令先停止实例，再验证新 token 和 Start 绑定，最后恢复启动；失败或取消时保留旧凭证，并尝试恢复原实例。每个实例使用独立 Bot，不能让其他程序同时轮询它。若 Bot 已有 Webhook，向导会拒绝接入，请使用独立 Bot 或先在原服务中移除 Webhook。支持 Codex、TraeX、Claude 和 Pi，不支持方舟。

### Slack 接入

运行 `vika setup`，新建或修改实例，勾选 Slack。Slack 不能扫码建应用：向导给出一个预填好配置的链接，在你的工作区里创建一个内部 App 并安装（也可以用已有的 App，按向导生成的 manifest 补齐配置，条件见 [README 的 Slack](./README.md#slack)），再把 Bot User OAuth Token（`xoxb-`）和带 `connections:write` 的 App-Level Token（`xapp-`）粘贴到隐藏输入框。接着在 Slack 里私聊机器人发送终端显示的一次性口令，绑定 owner；成功后才保存凭证并启动实例。收消息走 Socket Mode，不需要公网地址，Vika 主机须能访问 `slack.com`。每个实例用独立的 App。凭证在 `<dataDir>/slack/state.sqlite3`，不写入 `config.json`。范围与限制见 [README 的 Slack](./README.md#slack)。

成员查询使用 `conversations.members` 和 `users.info`。新建 App 的模板已包含 `channels:read`（公开频道）、`groups:read`（私有频道）、`mpim:read`（多人私聊）、`im:read`（私聊）和 `users:read`（姓名与机器人标记）。已有 App 升级 Vika 不会自动获得新权限：到 **OAuth & Permissions → Bot Token Scopes** 添加对应会话类型的权限，再 **Reinstall to Workspace** 完成授权。查询报 `missing_scope` 时会提示这些步骤，原有收发消息能力仍可使用。

会话列表使用 `users.conversations`，历史与线程使用 `conversations.history` / `conversations.replies`，都由 Slack Connector 调用官方 Slack CLI 4.10.0。历史和线程需要对应类型的 `channels:history`、`groups:history`、`im:history` 或 `mpim:history`；这些 scope 已包含在新建 App 模板中。只读 bot 已获授权的会话，不能用个人 Slack 账号的登录状态扩大范围。发布包自带 CLI，源码开发可单独运行 `node scripts/install-slack-cli.mjs`；Linux/macOS 的 x64/arm64 二进制按固定 SHA-256 校验，并保留官方许可证。开发调试可用 `SLACK_CLI_BIN` 指定兼容官方 CLI 的绝对路径。

重新绑定已有 Slack，或重新安装后 Bot token 发生变化时，运行：

```bash
vika login slack [实例]
```

输入同一个目标 App 的两个 token，并在机器人私聊发送终端显示的口令。这个命令也可以更换为另一个 App/bot；验证期间暂停所选实例，完成后重启。实例名是配置中的 ID，不是机器人的显示名。新增权限后 token 未变化则不必重新绑定。

## 用 vika setup 添加、修改和删除实例

发布过 release（见下一节）之后，`vika setup` 能代替手写 `config.json` 和在开放平台手工配置。机器上已经有实例时，它先问是新建一个实例还是修改某个已有实例。修改时列出这个实例的全部配置项和现在的值（机器人名字、工作目录、运行时、接入的 IM、主渠道、邮箱），用 ↑↓ 选一项、回车进去改，改完回到列表，改过的项标"（已改）"，没进去改的保持原样；实例名不能改。选"保存"后列出改动，只给新接的 IM 完成授权（Telegram 为 token + Start 绑定，Slack 为两个 token + 私聊口令，QQ 为 AppID/AppSecret + 私聊验证码），然后写配置并重启这个实例；选"放弃修改"什么都不动。接入新的 IM、加减邮箱、切换主渠道都在这里做。

要移除实例，选择「删除一个实例」，选定后确认（默认取消）。实例配置和后台进程会移除，工作目录、历史数据、凭据和 OpenViking 记忆保留，原配置备份为同路径的 `.bak` 文件。删除默认实例时指定新的默认实例；只剩一个时自动选用。删除最后一个实例也会停止控制台和自动升级服务，并移走配置文件，下次 setup 可重新新建实例。

帆船停在屏幕顶部，整个 setup 期间一直在动，问题在它下面滚动；扫码时它让出整屏，扫完再回来。窗口不到 30 行时先放一段完整动画，再换成顶部一条小帆船。`VIKA_NO_ANIMATION=1` 只画一帧。最先定界面语言，默认中文：locale 是中文就不问；否则（服务器上常见的 `en_US`、`C`，不代表用的人读什么）在终端里先问一次 Language / 语言，预选中文，答案存进 `~/.vika/language`，之后的 setup、login、status 等都用它，不再问；`vika --lang zh|en` 随时换。然后检查本机有哪些 agent。问答阶段随时可以按 ←（或 Esc）回到上一步，之前的答案作为默认值；按 → 沿用当前的值往下走（文本框里只在还没输入时生效）。退回去的步骤会从屏幕上消失，屏幕上只留当前这条路。修改实例时，进入某一项后按 ← 回到配置列表、这一项不变；在列表上按 ← 回到「要做什么」。二维码在等你扫的时候也可以按 ← 回到确认页（或配置列表），新实例的临时 OV 配置会在取消时清理；一旦有一次扫码确认了（飞书应用已建好、微信或 Telegram 已绑定），就不能再返回。一个都不能用时，可以当场打开内置 Pi 输入 `/login` 登录模型，`/quit` 回来接着走；也可以先退出去登录 Codex 或 Claude Code。

1. 第一个实例直接叫 `primary`，第二个起才问实例名。先列出已知 OV 连接并提供手动配置入口，连接验证通过后，依次问机器人名字、工作目录、运行时（只列出已安装并登录的 Codex / Claude Code，以及内置 Pi 和 Durable）。随后生成选定连接的实例专属配置，再用该身份检查服务和凭据；配置写入或检查失败会立即退出，不进入 IM 选择。通过后才问接哪些 IM（飞书、微信、Telegram、Slack、QQ、邮箱多选；邮箱不能单独选；飞书一项旁边写着要手机号注册的或公司的飞书账号，豆包账号登录的不行），主渠道（选了多个聊天 IM 时让你选，只选一个时就是它；机器人主动找你的消息，比如新邮件提醒、待你确认的邮件回复，都发到主渠道）。选了飞书就问机器人从哪来：扫码新建一个（默认）、扫码选择已有的（列出你是所有者或管理员的企业自建应用），或者手动填 App ID 和 App Secret（不是所有者或管理员、在测试企业等扫不了码时用；当场向开放平台核对，不对可以重填）。用已有的应用时注意：Vika 只接用长连接接收消息的机器人，用 webhook 的先在开放平台「事件与回调」把事件和回调改成长连接（原来的服务随之收不到）再来接，Vika 不替你切；飞书长连接每条消息只随机推给其中一个连接，这个应用也不能同时被别的程序（OpenClaw、botmux、另一台机器上的 Vika）用来收消息。扫码选择已有应用时，飞书确认页列出要补的权限、事件和回调，确认后发布新版本；应用若有版本正在审核，确认页会先撤回它。OpenViking 不提供跳过选项。开始扫码、输入 Bot token 或邮箱凭据之前，还会复查实例的 OV 连接；取消或失败时清理尚未保存到 manifest 的新 OV 配置。选了邮箱就接着问邮箱地址和专用密码，当场登录 IMAP 和 SMTP 验证，可以连续接几个；Gmail、QQ、163/126/yeah.net 不用填服务器。
2. 飞书扫两次码：第一次创建或选择机器人应用，按 `permission.json` 申请完整权限、订阅长连接事件和卡片回调，选的是已有应用时确认页只列出它缺的部分，平台自动发版（可能要等管理员审核）；第二次授权 owner 的用户身份，只申请应用已开通的用户权限。手动填 App ID 和 App Secret 时只扫第二次，Vika 不改应用的配置：它读出应用缺的权限、`im.message.receive_v1` 事件和长连接的 `card.action.trigger` 回调并列出来，事件走长连接还是 webhook 读不到，要自己在开放平台确认。卡片回调走 webhook 的应用直接拒绝（事件走哪种方式没有接口能读，回调走 webhook 说明它在用 webhook）：手动填的当场拒绝、可以换一个，扫码选择的在确认页之后拒绝、回到上一步。平台不给应用发访问凭证（`code=21001`，豆包账号登录的飞书就是这样）时同样不写任何配置：手动填的当场说明、可以换一个，扫码的回到确认页（修改实例时回到配置列表）并说明原因，已答的问题都保留；扫码新建的应用已经留在开放平台上，可以去删掉。应用已被本机另一个实例使用时先警告，确认后才继续。应用名和你填的机器人名字不同时问一次用哪个，默认用飞书里的名字（群里 @ 它用的就是这个），选否保留你填的；Vika 不会改飞书里的应用名。微信扫一次码；Telegram 输入 Bot token 后打开一次性链接点击 Start；Slack 按链接建 App、粘贴两个 token，再私聊机器人发口令；QQ 输入 AppID/AppSecret 后发送一次性私聊验证码。所有问题问完才开始接入聊天 IM；全部验证成功后才写入实例 manifest。扫码页先画二维码，码下面一行是备用链接，可以发到对应 IM 里打开；飞书第一次的链接带着全部权限、有一千多个字符，按 `l` 才显示。飞书第一次的码也大，终端放不下时提示所需尺寸，并给出二维码图片的路径（图片不受终端字体、行距和字符渲染影响，扫码结束后自动删除），拉大窗口或缩小字体后自动重绘。VS Code 终端里按 `i` 打开图片，有桌面的机器上按 `o` 用浏览器打开链接。有颜色的终端用黑白配色和完整留白，黑块用背景色画，终端字符之间的缝不会把黑块切开。
3. 写入 `~/.vika/config.json`，启动这个实例、重启共享 Web、确保 updater 在跑，然后机器人在飞书私聊里打个招呼。在终端里时，接着请你在飞书里回它一句，等实例真的收到这条消息（最多 3 分钟，按 `s` 跳过），确认消息进得来；没等到就列出排查清单（长连接与消息事件、版本是否发布、应用是否被别的程序占用、`vika logs`）。最后问要不要开机自启，并列出接下来做什么（找机器人说话、控制台地址、常用命令）。

其他命令：

```bash
vika start|stop|restart [实例|web|updater]   # 用本机已安装的 release，不拉代码
vika status
vika logs [实例]
vika login lark|weixin|email|telegram [实例]         # 登录过期或改了密码后重新授权
vika pi                                     # 打开内置 Pi，例如 /login 配模型
```

`vika login` 只给实例已经接入的 IM 重新授权：飞书先核对应用的权限、消息事件和卡片回调，缺了就问要不要扫码补上（要你是应用的所有者或管理员，确认页列出改动，按 `s` 跳过；应用有版本正在审核时会说明扫码会撤回它，默认不扫），再扫码授权用户身份，微信重新扫码登录，Telegram 重新验证 token 与 Start 绑定，Slack 重新验证两个 token 并私聊发口令，QQ 重新验证 AppID/AppSecret 与私聊验证码，邮箱选一个换密码；只有一个实例时不用写实例名，多个时会列出接了这个 IM 的实例让你选。要接入新的 IM 或给实例换一个飞书应用，运行 `vika setup` 修改实例（配置列表里的「飞书应用」）。新接入飞书后，机器人会在飞书里先发一条自我介绍；微信要你先给机器人发一句话，它才能主动找你（每次重新扫码后都一样）。`vika` 不带参数会列出本机所有实例和它们接入的 IM。

## 升级与回滚

```bash
vika upgrade <instance-id>
vika upgrade all        # 也滚动共享 Web，并清掉 manifest 里已删除的实例
```

`vika upgrade` 只是请求 `vika-updater`：updater 读下载地址上的 `latest`，下载这台机器平台的安装包，按 release 清单核对 SHA-256 和大小，解包后用安装包自带的 Node 跑冒烟检查（托管 Pi 版本、`vika --version`），全部通过才切换 `~/.vika/current`，旧版本留作 `~/.vika/rollback`。然后逐个重启目标实例并等 `/ready`；有实例起不来就用它原来的版本恢复。最后 updater 从新版本重启自己。用户机器上不再 `npm ci`、不跑测试、不需要 git，测试在打包时就跑过了。同一版本再次升级直接复用。

升级由独立的 updater 进程完成，所以在飞书里让机器人升级自己也能做完：机器人自己的进程被重启时，updater 不受影响。

回滚：`ln -sfn "$(readlink ~/.vika/rollback)" ~/.vika/current && vika restart`。

查看运行状态：

```bash
vika status
vika logs <instance-id>
curl http://127.0.0.1:3737/ready
```

只要还有一个 Connector 在运行，`/ready` 就返回 200；有 Connector 不可用时，body 的 `status` 是 `degraded`，`connectors.<id>` 带上 `error` 和下次重试时间 `retryAt`。

Vika 用自己的 pm2（安装包里的固定版本），状态放在 `~/.vika/pm2`，和你自己的 pm2 互不影响。

## 开机自启

```bash
vika autostart enable    # Linux：systemd 用户服务；macOS：LaunchAgent；都不需要 sudo
vika autostart disable
```

开机时执行的就是 `vika start`，PATH 取开启时的 shell，这样服务环境里也找得到 codex、claude。Linux 服务器上还要执行一次 `loginctl enable-linger`，否则退出登录后会停。

## 发布新版本（维护者）

发布由 OpenViking 仓库的 GitHub Actions（`.github/workflows/vika-release.yml`）完成，流程和规则见 OpenViking 的 [发版说明](../docs/repository/RELEASE_CN.md) 中「Vika 发版流程」一节。简单说：把 `vika/package.json` 的版本改好并合入，再推 `openviking-vika@<版本>` tag；CI 跑检查和测试、打四个平台的安装包，上传到 `https://ovrelease.tos-cn-beijing.volces.com/vika/releases/<版本>/`，最后才更新 `latest`。手动触发 workflow 会发一个 `<版本>-<commit>` 预览版，不动 `latest`。

本地打包只用来自测：

```bash
node scripts/build-release.mjs --all --download-base https://ovrelease.tos-cn-beijing.volces.com/vika
```

上传的 `install.sh` 里写死了下载地址，每个安装包也记着它，updater 据此找新版本。

## 多个 agent

在 `config.json` 的 `agents` 增加 agent。每个 agent 都要使用独立的 id、端口、dataDir、workspace，飞书实例还要独立的 profile，还要独立的 OV 配置；它们共享 release、Web 与 updater。`channel` 保留单 Connector 兼容行为；可选 `connectors` 数组允许一个 Core 同时启动 `lark`、`weixin`、`telegram`、`slack`、`qq` 和 `email`（邮箱至少搭配一个聊天 IM）。省略两者时走飞书。最简单的是再运行一次 `vika setup`；手改 manifest 后执行 `vika start <新实例>` 和 `vika restart web`，updater 每次请求都重读 manifest，不用重启。

自动召回按实例设置：在 `config.json` 的 `agents[]` 中填写 `"ovAutoRecall": true`，重启实例后生效，缺省为 `false`。开启时固定 `OPENVIKING_RECALL_PEER_SCOPE=actor`；不会关闭自动捕获或会话历史同步。运行中 OV 不可用时继续收发消息，新会话允许暂时跳过 OV 历史，后台同步会重试。
