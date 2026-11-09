# OpenViking SuperAgent Configuration and Operations

[Overview](../README.md) · [Quick start](../install.md)

This reference covers running the daemon. For the product overview, read the [README](../README.md).

## One-command installation

Both platforms use the same [installation and setup guide](../install.md).

**macOS / Linux / WSL2:**

```bash
curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/vika/install.sh | sh
```

This installs the version in `vika/latest` (currently `0.5.0-preview`; until a stable release ships, `latest` follows previews). To pin a version, use `curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/vika/releases/<version>/install.sh | sh -s -- --version <version>`.

The installer detects Linux or macOS and x64 or arm64 (including WSL2), downloads the matching archive, verifies SHA-256, extracts it to `~/.vika/releases/<version>`, adds `~/.vika/bin` to your shell PATH, and enters `vika setup`. Only sh, curl, tar, and sha256sum (or shasum) are required. The package supplies pinned Node, PM2, lark-cli, and Pi without modifying existing Node, npm, PM2, or git installations. Before switching, it runs the new bundled Node; incompatible system libraries or CPU architecture leave the previous installation intact. musl systems such as Alpine are unsupported.

**Windows 10/11 x64 (preview), PowerShell 5.1 or 7:**

```powershell
irm https://ovrelease.tos-cn-beijing.volces.com/vika/install.ps1 | iex
```

The Windows installer downloads the latest Windows preview, verifies SHA-256, installs bundled Node, PM2 and chat/runtime dependencies, updates PATH, and opens the same `vika setup` on first interactive installation. No separate community wizard is needed. Vika uses `$env:HOME` when set, otherwise `$env:USERPROFILE`. Pi requires Git Bash; Durable uses native PowerShell. Slack requires a separately installed `SLACK_CLI_BIN`.

To pin a Windows preview version or skip setup:

```powershell
& ([scriptblock]::Create((irm https://ovrelease.tos-cn-beijing.volces.com/vika/install.ps1))) -Version '<version>' -NoSetup -Lang en
```

The one-line command also accepts `VIKA_VERSION` and `VIKA_DOWNLOAD_BASE`; the latter is the HTTPS Vika root, before `/windows-preview`. For offline artifacts and publishing details, see [Windows package maintenance](windows-preview.md).

Both the installer and CLI support English and Chinese. `vika --lang en` (or `zh`) persists the choice in `~/.vika/language`. To install in English, use `--lang en` on macOS / Linux or `-Lang en` on Windows. `VIKA_LANG` overrides the language for one invocation only. The default is Chinese when no preference is set.

Interactive download progress uses a sailing-boat animation, followed by a checkmark per completed step. Redirected output and `NO_COLOR` use plain text. If `~/.vika/config.json` does not exist, rerunning the installer returns to setup; with existing instances, it installs the release and asks you to run `vika restart`.

## Prerequisites

- Install and authenticate Codex, Claude Code, or TraeX, or use bundled Pi / Durable. Both bundled runtimes use `~/.pi/agent`; run `vika pi`, then `/login`, if needed.
- OpenCode is also supported: install **1.x, 1.15.7+**, and run `opencode auth login`. Vika uses ACP. OpenCode 2.x has an incompatible server contract and is unsupported by this release.
- Antigravity CLI is also supported: install `agy` and sign in. Vika uses ACP and tested 1.5.3.
- DeepSeek Harness (DSH) is also supported: install `dsh` **0.2.x**, version **0.2.2-rc.1** or later, on Node **24.9+**. It is not bundled. `dsh` is found on PATH or named by `DSH_BIN`; model routes and credentials live in the instance's own DSH settings document, not in `config.json`, and are managed with `vika dsh model`. `VIKA_DSH_EFFORT` sets the effort a new instance starts with and is read from the process environment only. See [DSH runtime](dsh-runtime.md).
- **OpenViking is required** for cross-session context. Setup lists detected connections, their sources, and check results, and always offers manual address/credential entry, a configuration-file path, redetection, and exit. Local or remote URLs, custom ports, hidden API key input, and optional Account / User / Agent fields are supported. Validation failures stay in the configuration screen; IM setup starts only after validation succeeds. First interactive installation enters this wizard directly.
- Feishu requires a phone-registered or enterprise account. Phone registration creates a free enterprise with you as administrator. **Feishu accounts signed in through Doubao are unsupported:** they can create and publish apps, but the platform refuses `tenant_access_token` with `code=21001`. Setup explains the failure and returns to the previous step; use another account or connect another channel first.

Detection checks the edited instance's `ovConfig`, `OPENVIKING_CLI_CONFIG_FILE`, `~/.vika/ov-connection.json`, `~/.openviking/ovcli.conf`, and `http://127.0.0.1:1933`. It does not scan other ports. Imported files remain unchanged. When saving the instance, manually entered or imported connections are saved under Vika with mode 0600 for future instances; already discoverable configuration files are not copied.

The macOS / Linux installer itself never prompts. Except for first interactive installation followed immediately by setup, it validates OV before switching releases. Existing instances use their own `ovConfig`. First installation with `--no-setup` or without a terminal tries the environment-specified file, saved Vika connection, OV CLI configuration, then the local default address. With no configuration, only dev-mode OV at the default local port is accepted. Failed validation prevents the switch. At daemon startup, rejected configuration or credentials cause exit; temporary unavailability records an activity entry and starts without history, with synchronization retried after recovery.

Editing an instance's OV connection preserves memory identity and recall settings in a new configuration copy. Saving updates the manifest; discarding removes the copy.

Codex, TraeX, Claude, Pi, OpenCode, and Antigravity need their respective model sign-in. Durable needs no separate login and can use the models available to Pi. DSH needs no sign-in step in Vika: setup checks `dsh --version` plus a keyless protocol probe instead, and its model routes and credentials belong to the instance's own DSH settings document, which `vika dsh model` reads and writes. Only the built-in `deepseek-official` route needs `DEEPSEEK_API_KEY`. See [config.example.json](../config.example.json) for all fields.

OpenCode setup checks `--version` and an ACP `initialize` handshake without creating a session or calling a model. It reuses OpenCode's model and authentication configuration. Override the model with `VIKA_OPENCODE_MODEL` or `/model provider/model`, and the executable with `OPENCODE_BIN`. The daemon injects session policy and the `personal_agent` MCP server through a temporary, daemon-owned `OPENCODE_CONFIG`; it does not edit `~/.config/opencode/opencode.json`. OpenCode serializes prompts within a session, so input arriving during a turn waits for the next turn.

Antigravity setup checks `--version`, an ACP `initialize` handshake that must identify Antigravity and offer an appended system prompt, and `agy models` to confirm the sign-in, without creating a session or calling a model. Override the executable with `AGY_BIN` and the model with `VIKA_AGY_MODEL` or `/model <id>`, using the suffixed ids `agy models` lists, such as `gemini-3.8-flash-low`; the effort is part of the id. The daemon passes session policy and the `personal_agent` MCP server over ACP and runs `agy` with `--dangerously-skip-permissions`. A prompt sent during a live turn would end that turn early, so input arriving during a turn waits for the next turn.

TraeX executables may be named `traex`, `traecli`, or `trae-cli`. Vika requires `app-server`; older versions offering only `acp serve` are unsupported. Setup checks those names in order, resolves and deduplicates symlinks, and requires both stdio `initialize` and `login status` to succeed. Probing creates no sessions or model calls. An explicit `TRAECLI_BIN` (or the legacy, higher-priority `TRAEX_BIN`) limits detection to that executable; failure does not silently select another. Setup saves the verified real path without modifying shell aliases.

For startup errors, run `vika logs <instance>`. Failed app-server handshakes include bounded, redacted stderr. Even for an online PM2 process, `vika start <instance>` verifies release ownership and readiness; failure does not restart or overwrite the process. If `vika` is not found, use `~/.vika/bin/vika`. The installer updates `.bash_profile` for macOS bash, `.bashrc` for Linux bash, and `.zshrc` for zsh; reload that file or reopen your terminal.

Claude Code requires 2.1.259 or later and `--permission-prompts none`, checked during preflight. Daemon sessions are unattended: Pi extension dialogs and Codex/TraeX approval or input requests are explicitly cancelled or denied. Claude uses its official unattended settings and rejects unexpected control requests. To ask a person, the model should send a message or supported choice card, end its turn, and await a later answer. User extensions, providers, and other session configuration remain enabled.

Pi 1.4.0 reads RPC responses only after extension startup finishes. An extension asking for input during `session_start` therefore causes immediate startup refusal with instructions to disable that interaction. Runtime dialogs receive a normal cancellation.

Pi's exact version is pinned in `packages/daemon/package.json` and the lockfile and shipped in the release. `vika pi` opens that copy for `/login` and reuses existing Pi model credentials without upgrading global `pi`. An empty `PI_BIN` selects the bundled version; an explicit executable must match the version validated by this release or startup provides repair instructions.

The daemon registers `personal_agent` MCP only for its Pi session, with `direct` exposure so the model sees each tool and parameter. It does not edit `~/.pi/agent/mcp.json` or workspace `.pi/mcp.json`, or add the server to other sessions. A conflicting server name, disabled built-in MCP, or unloaded tools makes startup fail; there is no Bash/CLI fallback. Pi still loads other user MCP servers normally.

Durable is bundled from [pi-durable](https://github.com/earendil-works/pi), without a separate CLI installation. It records conversations, model turns, and tool calls in `<dataDir>/durable/session.sqlite` before displaying them, and resumes an interrupted turn from the last checkpoint after restart. It shares Pi's built-in providers and `~/.pi/agent/models.json` (custom providers, including OpenAI-compatible Ark endpoints), `auth.json` (keys saved through `/login`), and the default model in `settings.json`. Built-in providers can also use `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, etc. in `~/.vika/secrets.env`, never in `config.json`. With no override, Pi's default model is used. Change it through `/model` and `/effort`, or manifest `env` keys `VIKA_DURABLE_MODEL` and `VIKA_DURABLE_EFFORT`. Like other runtimes, Durable sends messages through Agent tools; it also provides pi-durable's `bash`, `read`, `write`, and `edit` in the instance workspace. See [Durable runtime](pi-durable-runtime.md).

## Manual Feishu setup

QR-based app creation or selection in `vika setup` performs the following automatically. Do these yourself when writing `config.json` or manually entering App ID / App Secret; setup lists missing items it can inspect.

1. Create a bot in the Feishu developer console. Under event configuration, enable a long connection and subscribe to `im.message.receive_v1`; document-comment mentions also require `drive.notice.comment_add_v1`. Separately enable a long connection under callback configuration and add `card.action.trigger`. Message subscriptions alone receive card bodies, not button clicks.
2. Import [permission.json](../permission.json), publish the app, and complete permission review.
3. Add the bot to trusted chats and restrict app visibility. Receiving unmentioned group messages requires `im:message.group_msg`; messages from other bots also require `im:message.group_msg.include_bot:read`.
4. Configure the bot profile with lark-cli. Add user OAuth only when reading personal resources as the user.

Uploads require `im:resource`, sends require `im:message:send_as_bot`, card reading requires `im:message:readonly`, and answered-card updates require `im:message:update`. These scopes are in the permission list. Use lark-cli with `card.action.trigger` support; this implementation was validated against 1.0.94 commands and event schemas. A working CLI does not prove callbacks are enabled in the developer console. No public callback URL is needed: the CLI ACKs promptly and the model processes the answer asynchronously.

`permission.json` lists the full feature set, not minimum privileges. Restrict scopes and visibility for production use.

## Running from source

For Windows source development, see [Windows source development and validation](windows.md).

Source development requires Node.js 22.19.0 or later.

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking/vika
npm run setup          # Install locked dependencies, pinned lark-cli and Slack CLI, and check managed Pi
npm test && npm run check
mkdir -p ~/.vika && cp config.example.json ~/.vika/config.json
# Edit config.json: profile (Feishu), dataDir, workspace, model, ovConfig; ovAutoRecall defaults to false
npm run start:daemon
```

To test a local package through the same installation path as users:

```bash
node scripts/build-release.mjs                    # Packages committed code only; output in dist/
sh scripts/install.sh --file dist/vika-<version>-<platform>.tar.gz
vika restart
```

Configuration defaults to `~/.vika/config.json`. For a custom location, use the same absolute `VIKA_CONFIG` for every startup, deployment, and upgrade command.

### Attachment temporary directories

lark-cli path protection can affect attachment operations under `/root`. When setup detects a `dataDir` equal to or beneath `/root` without an explicit temporary-directory setting, it writes `"tempDir": "/tmp/vika-<instance>"`, for example `/tmp/vika-primary`. Each instance gets its own directory; databases, sessions, and workspaces stay in place. `TMPDIR` does not affect this default. Explicit `agents[].tempDir` settings are preserved.

Upgrades alone do not migrate existing instances. Edit and save the instance in setup to add the default, or add `tempDir` manually and run `vika restart primary`. Do not move the entire `dataDir` into `/tmp`.

Alternatively set `VIKA_TEMP_DIR=/tmp/vika-primary`, including through top-level `env`. This is a shared default; multiple instances should use their own higher-priority `agents[].tempDir`. Paths must be absolute. Without a setting, `<dataDir>/tmp` is used, regardless of `TMPDIR`. This directory also holds temporary outgoing file copies.

Upload and download directories are created and checked with mode 0700. Existing directories must belong to the daemon user, be inaccessible to other users, and not be symlinks. Use a dedicated directory, not `/tmp` itself. Uploads copy workspace files into private subdirectories under `<tempDir>/outbox` and pass relative paths to lark-cli. Image/file sends, replies, and owner notifications all use this path and remove copies after success or failure. Downloads remain under `<tempDir>/media` for MCP/tools and expire during maintenance according to `VIKA_RETENTION_MS` (90 days by default).

After restart, reread a previously failed image message and send and reply with a workspace image. `lark-cli --dry-run` checks local arguments only, not real uploads/downloads. Neither the server nor OV needs restarting, and the database need not be cleared.

### WeChat setup

For a separate WeChat instance, set `"channel": "weixin"`, `"connectors": ["weixin"]`, and a supported model such as Codex, TraeX, Claude, Pi, or DSH, with separate `port`, `dataDir`, `workspace`, and `ovConfig`. To share a session and OV space with Feishu, use `"channel": "lark"` and `"connectors": ["lark", "weixin"]`. WeChat-only instances must omit `profile`; providing it is rejected. See the [WeChat example](usage-reference.md#wechat-direct-messages).

```bash
vika login weixin weixin
VIKA_INSTANCE_ID=weixin npm run start:daemon
```

Login reads the instance manifest and displays a QR code. After phone confirmation, the token, bot account, scanning user, and polling cursor are saved in its private SQLite database. Core starts a WeChat Connector, calls `notifyStart`, polls `getupdates`, and wraps text, images, files, and existing voice transcriptions as `EVENT` inputs. Media is stored in `dataDir/weixin/media`. Unified Agent tools send queries and actions through `dataDir/actions.sock`; Core writes the outbox before the Connector calls `sendmessage` with `context_token` and an idempotency-derived `client_id`.

Only the scanning account can chat with the bot; other senders are ignored. One WeChat account can connect to only one bot at a time; scanning for another instance expires the old session. Send the bot a message after every login before it can send proactively; Vika otherwise returns an error. On expiry (`ret=-14`), only WeChat stops; Feishu continues. Core retries after 5 seconds, 15 seconds, 1 minute, then every 5 minutes. Rescan and the next retry uses the new credentials without restarting the instance. Supported: direct text, images, files, and existing WeChat voice transcription. Raw audio, video, and groups are unsupported.

### QQ setup

In a release with QQ support, select QQ in `vika setup`. Register a developer account at the [QQ platform](https://q.qq.com/#/), create a bot through the [QQ bot entry](https://q.qq.com/qqbot/openclaw/), obtain AppID / AppSecret, enable direct messages, and add it with your QQ account. Setup hides the secret and asks you to send a one-time `vika-bind …` code in a direct chat within three minutes. It saves only after verification. The host needs access to `https://api.bot.qq.com`, Tencent's WebSocket gateway, and attachment CDN; no public listener is required.

Owner direct chats support text, images, files, quoted replies, and slash commands. QQ can be the primary channel for notifications and numbered email approvals, subject to platform permissions and quotas. QQ-only instances omit `profile`; Codex, TraeX, Claude, and Pi are supported, Ark is not. Use a separate bot per instance. Credentials live in `<dataDir>/qq/state.sqlite3`, not `config.json`.

To reauthorize, run `vika login qq [instance]`. It stops the instance, verifies credentials and your direct chat, then restarts; cancellation or failure preserves the previous account. See [QQ direct messages](usage-reference.md#qq-direct-messages) for file limits, passive replies, and downtime guarantees. This feature appears only in releases that include it; editing a workspace does not update published `latest`.

### Telegram setup

In a release with Telegram support, select Telegram in setup. Use `/newbot` with official [@BotFather](https://t.me/BotFather), paste the token into hidden input, and open the generated link or scan its QR code. Click Start in the bot's direct chat within three minutes. Credentials are saved and the instance started only after binding succeeds. No public webhook or phone login is required; the host must reach `https://api.telegram.org`.

Only the bound owner's direct messages are processed. Text, images, files, quoted replies, primary-channel notifications, and numbered email approvals are supported. Telegram-only instances omit `profile`; mixed instances need it only for Feishu. Credentials and cursor live in `<dataDir>/telegram/state.sqlite3`, not `config.json`. See [Telegram direct messages](usage-reference.md#telegram-direct-messages) for limits.

```bash
vika login telegram [instance]
```

Reauthorization stops the instance, validates the new token and Start binding, and restarts it. Failure or cancellation preserves old credentials and attempts to restore the instance. Use a separate bot per instance with no competing poller. Existing webhooks cause refusal; use another bot or remove the webhook in the original service yourself. Codex, TraeX, Claude, and Pi are supported; Ark is not.

### Slack setup

Select Slack in setup. Unlike Feishu, Slack cannot create an app by QR code. Open the prefilled link, create and install an internal app in your workspace, or configure an existing app from the generated manifest as described under [Slack](usage-reference.md#slack). Paste its Bot User OAuth Token (`xoxb-`) and App-Level Token (`xapp-`, with `connections:write`) into hidden input, then send the one-time terminal passphrase to the bot in a Slack direct chat to bind the owner. Only successful verification saves credentials and starts the instance. Socket Mode needs access to `slack.com`, not a public endpoint. Each instance needs a separate app; credentials live in `<dataDir>/slack/state.sqlite3`.

Member queries use `conversations.members` and `users.info`. New-app templates include `channels:read`, `groups:read`, `mpim:read`, `im:read`, and `users:read`. Upgrading Vika does not grant scopes to existing apps: add the applicable conversation scopes under **OAuth & Permissions → Bot Token Scopes**, then **Reinstall to Workspace**. A `missing_scope` error explains these steps without interrupting messaging.

Conversation lists use `users.conversations`; history and threads use `conversations.history` / `conversations.replies`, through official Slack CLI 4.10.0. History needs the applicable `channels:history`, `groups:history`, `im:history`, or `mpim:history`, included in new-app templates. Only bot-authorized conversations are readable; personal Slack sign-in does not expand access. Releases bundle the CLI; source developers can run `node scripts/install-slack-cli.mjs`. Linux/macOS x64/arm64 binaries have pinned SHA-256 checks and retain the license. `SLACK_CLI_BIN` can override the absolute path with a compatible official CLI for development.

```bash
vika login slack [instance]
```

Use this after token changes, to rebind, or to switch apps/bots. Enter both tokens for the same target app and send the terminal passphrase in its direct chat. The selected instance pauses during verification and restarts afterward. The instance name is its configuration ID, not the bot display name. New scopes without a changed token do not require rebinding.

### OneBot setup

OneBot connects a personal QQ account through NapCat, LLOneBot or Lagrange.OneBot, unlike the official-bot QQ channel above. A platform may restrict an account driven by a third-party client; use one you can afford to lose. First add a WebSocket server with a token to the OneBot implementation (NapCat: `websocketServers`, `messagePostFormat: array`; optional `reportSelfMessage`), on loopback or a private network. Then select OneBot in setup, enter the `ws://` / `wss://` address and the token (hidden input), and send the one-time `vika-xxxxxxxx` code to the account in a private message from your own QQ, which must be its friend, within five minutes. Setup lists the account's groups with their numbers. Only a successful binding saves the account; credentials live in `<dataDir>/onebot/state.sqlite3`, not `config.json`.

Groups are connected by hand in `<dataDir>/onebot/config.json`, which the Connector rereads when it changes; without it only your private chat is connected. OneBot-only instances use `"channel": "onebot"`, `"connectors": ["onebot"]`, and omit `profile`; Codex, TraeX, Claude, Pi, DSH, and OpenCode are supported, Ark is not. Use one account per instance. See [OneBot Connector](onebot-connector.md) for the implementation settings, the policy file, when group messages reach the agent, and sharing the account with another bot program.

```bash
vika login onebot [instance]
```

Use this to change the address or token, or to rebind the owner. A failed or cancelled login keeps the previous account.

## Managing instances with setup

Once the relevant code is published, `vika setup` replaces manual configuration and developer-console steps. With existing instances, choose creation or editing. Editing lists current values for name, workspace, runtime, IM channels, primary channel, and email. Use arrow keys and Enter to edit a field; changed fields are marked, untouched fields stay unchanged, and the instance ID cannot be renamed. Saving shows changes, authorizes only newly connected channels, writes configuration, and restarts that instance. Discarding leaves everything unchanged. Add/remove mailboxes or change the primary channel here too.

To remove an instance, choose deletion and confirm (cancel is the default). Configuration and managed processes are removed, while workspace, history, credentials, and OV memory remain. Configuration is backed up with `.bak`. Deleting the default requires selecting another, automatically if only one remains. Removing the last instance also stops the console and updater and removes the active configuration, allowing fresh setup later.

The setup animation stays above the questions and yields the screen to QR codes. Terminals shorter than 30 lines show one full animation followed by a small banner. `VIKA_NO_ANIMATION=1` draws one frame. Language is selected first: Chinese locales skip the question; other locales such as `en_US` or `C` ask once, with Chinese preselected. The answer persists in `~/.vika/language` for setup, login, and status; change it with `vika --lang zh|en`.

During questions, Left or Esc returns to the previous step with prior answers as defaults. Right accepts the current value (in text fields, only before typing). Backtracked steps disappear from the screen. When editing a field, Left returns without changing it; from the field list, Left returns to the action menu. While waiting for a QR scan, Left returns to confirmation or the field list; temporary OV configuration for a new instance is cleaned up on cancellation. Once a scan has actually created or bound an app/account, going back is disabled. If no runtime is ready, open bundled Pi, use `/login`, then `/quit` to resume, or exit and authenticate another CLI.

1. The first instance is `primary`; later instances ask for an ID. Select and validate OV, then set display name, workspace, and an available authenticated runtime. Setup creates and validates instance-specific OV configuration before asking about channels; write or validation failure stops there. Choose Feishu, WeChat, Telegram, Slack, QQ, OneBot, and/or email; email cannot stand alone. With several chat channels, choose where proactive notifications and email approvals go. For Feishu, create an app by QR (default), select an app you own/administer, or manually enter and validate App ID / Secret. Choose whether to allow owner-identity message reads (default yes; see [message access](usage-reference.md#message-access)). Existing apps must already use long connections for events and callbacks; Vika does not switch them from webhooks. A competing receiver can steal randomly distributed long-connection messages. Existing-app QR confirmation lists missing scopes/events/callbacks, publishes a version, and withdraws any version already under review. OV cannot be skipped and is rechecked before scans or credential entry. Uncommitted OV configuration is cleaned up on cancellation/failure. Email setup validates IMAP and SMTP immediately and can add several accounts; Gmail, QQ, and 163/126/yeah.net have server presets.
2. Feishu uses two scans: app creation/selection with `permission.json` permissions and long-connection subscriptions, then owner user authorization for enabled user scopes. Publication may require administrator approval. Manual app credentials require only the second scan; Vika lists missing scopes, `im.message.receive_v1`, `drive.notice.comment_add_v1`, and `card.action.trigger`, but cannot read the message-event transport setting, which you must check yourself. Webhook card callbacks are rejected: immediately for manual credentials, or after confirmation for QR selection. `code=21001` similarly prevents configuration writes and returns to the previous screen with answers preserved; an app already created by QR remains in the platform and can be deleted there. Reusing a local instance's app prompts a warning. If app and entered bot names differ, setup offers the Feishu name by default, without renaming the platform app. WeChat uses one scan; Telegram uses token + Start; Slack uses two tokens + direct-chat passphrase; QQ uses AppID/Secret + direct-chat code; OneBot uses WebSocket address/token + private-chat code. Authorization starts after questions, and the manifest is written only after every channel succeeds. QR screens include backup links; Feishu's first link exceeds 1,000 characters and is shown with `l`. Oversized codes show a required terminal size and an image path, redraw after resizing, and delete the image after scanning. Use `i` in VS Code to open the image or `o` on a desktop to open the link. Colored terminals render black blocks with backgrounds and full quiet zones to avoid character gaps.
3. Setup writes `~/.vika/config.json`, starts the instance, restarts shared Web, ensures the updater is running, and sends a Feishu greeting. In a terminal, it waits up to three minutes for your reply (`s` skips). Failure shows a checklist covering long connections, events, publication, competing receivers, and logs. It then offers autostart and displays chat, console, and command instructions.

```bash
vika start|stop|restart [instance|web|updater]   # Uses installed release; does not fetch source
vika status
vika logs [instance]
vika login lark|weixin|email|telegram [instance] # Reauthorize after expiry or password changes
vika pi                                        # Open bundled Pi, e.g. /login
```

`vika login` reauthorizes already connected channels. Feishu first checks scopes, message/comment events, and callbacks, then offers an owner/admin QR scan to repair missing items (`s` skips). If a version is under review, it warns that scanning withdraws it and defaults to skipping. User authorization follows. WeChat rescans; Telegram rebinds token + Start; Slack verifies tokens + passphrase; QQ verifies credentials + direct-chat code; OneBot verifies the WebSocket address/token + private-chat code; email selects an account and replaces its password. A sole instance need not be named; otherwise choose among instances using the channel. Adding a channel or changing a Feishu app uses setup instead. A newly connected Feishu bot introduces itself; WeChat requires an incoming message after every scan before proactive delivery. Bare `vika` lists instances and channels.

## Upgrade and rollback

On Windows, `vika upgrade` and the automatic updater are not supported yet. Stop Vika with `vika stop`, rerun the PowerShell installer from [One-command installation](#one-command-installation), then run `vika restart`. The installer retains the previous release for [manual rollback](windows-preview.md#configure-and-test).

On macOS / Linux:

```bash
vika upgrade <instance-id>
vika upgrade all        # Also rolls shared Web and removes instances deleted from the manifest
```

The command asks `vika-updater` to read the release host's `latest`, download the platform archive, verify manifest SHA-256 and size, and smoke-test bundled Node, managed Pi, and `vika --version`. Only then does it switch `~/.vika/current`, retaining the previous version as `~/.vika/rollback`. It restarts target instances one at a time and waits for `/ready`; failed instances revert to their previous release. The updater restarts itself last. User machines do not run `npm ci` or tests and need no git; packaging already ran tests. Repeating an upgrade to the same version reuses it. This flow requires a published `latest` endpoint; the pinned preview above does not make that endpoint available.

The updater is a separate process, so it can finish even when the bot's own process restarts.

Rollback: `ln -sfn "$(readlink ~/.vika/rollback)" ~/.vika/current && vika restart`.

```bash
vika status
vika logs <instance-id>
curl http://127.0.0.1:3737/ready
```

`/ready` returns 200 while at least one Connector is running. If others are unavailable, the body reports `status: degraded` with `connectors.<id>.error` and `retryAt`.

Vika uses its bundled, pinned PM2 with state under `~/.vika/pm2`, independent of your own PM2.

## Autostart

```bash
vika autostart enable    # Linux user unit; macOS LaunchAgent; Windows Startup folder
vika autostart disable
```

Autostart executes `vika start` and captures the enabling shell's PATH so it can find codex and claude. Linux servers also need `loginctl enable-linger` to continue after logout. On Windows it is a `.vbs` in the current user's Startup folder: no service, no administrator, hidden at logon, logging to `~/.vika/logs/autostart.log`, and listed in Task Manager's Startup tab.

## Publishing releases

Maintainers publish through `.github/workflows/vika-release.yml`; see the Vika section of the [release guide](../../docs/repository/RELEASE_CN.md). Update `vika/package.json`, merge, and push `openviking-vika@<version>`. CI checks and tests, packages four platforms, uploads to `https://ovrelease.tos-cn-beijing.volces.com/vika/releases/<version>/`, then updates `latest`. A manual workflow creates a `<version>-<commit>` preview without changing `latest`.

Local packages are for testing:

```bash
node scripts/build-release.mjs --all --download-base https://ovrelease.tos-cn-beijing.volces.com/vika
```

Published `install.sh` and each archive record the download base used by the updater.

## Multiple agents

Add entries to `agents` in `config.json`, each with separate ID, port, dataDir, workspace, OV configuration, and (for Feishu) profile. They share release, Web, and updater. `channel` preserves single-Connector compatibility; optional `connectors` can start `lark`, `weixin`, `telegram`, `slack`, `qq`, `onebot`, and `email` together, with at least one chat IM for email. Omitting both selects Feishu. The easiest path is another `vika setup`; after manual edits, run `vika start <new-instance>` and `vika restart web`. The updater rereads the manifest per request and needs no restart.

Vika owns OpenViking memory, the same for every runtime. The daemon syncs the conversation into the instance's own OV user, naming each line's speaker in `from=` and attaching no peer; extraction keeps profile, entities and events, each event once. Preferences are not extracted: when the agent calls `openviking_remember`, the Core writes the preference into the directory of the person who sent that message, `memories/preferences/owner/` for the owner and `memories/preferences/<name>~<id>/` for anyone else (the id comes from the channel identity, so namesakes stay apart). Before forwarding direct chats, mentioned group messages, document comments, and non-chat events such as mail, the daemon attaches the owner's preferences (the owner's standing rules) and the sender's; set `agents[].ovAutoRecall` to `true` in `config.json` (default `false`, not in setup) to also run one semantic search per such line. OV outages do not stop messaging; new sessions can skip history temporarily while synchronization retries.

Runtimes started by Vika do not load their own OpenViking plugins: Codex and TraeX get launch overrides that disable the OV plugin, any MCP server running OV, and their own memory feature; Claude Code gets one `--settings` layer that disables the plugin and denies those servers; Pi turns extension discovery off and loads its built-in extensions and your other extensions by path; OpenCode reads a Vika-prepared config directory without the OV plugin and servers; Antigravity runs against a Vika-prepared `--gemini-dir` that links your sign-in and sessions and leaves out OV's plugins, skills, rules, hooks, and MCP servers; DSH gets a private `DSH_HOME` under `<dataDir>/dsh/home` and a Vika-generated `--patch` overlay, so it never reads your `~/.dsh` plugins, skills, or credentials. Should a plugin still load, the instance environment keeps it from writing or recalling, with its state in `<dataDir>/openviking/`. All of this applies only to processes Vika starts; Codex, Claude Code, Pi, DSH, OpenCode, and Antigravity you run yourself keep their behavior.
