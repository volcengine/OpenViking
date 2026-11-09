# OpenViking SuperAgent Features and Architecture

[Overview](../README.md) · [Quick start](../install.md)

Connect your local coding agent to Feishu / Lark, WeChat, Telegram, Slack, QQ (official bot or OneBot), and email events. Core maintains a shared Agent session and OpenViking space; independent Connector subprocesses handle message sources.

Vika makes Codex, TraeX, Claude, Pi, DeepSeek Harness (DSH), OpenCode, or Antigravity a long-term collaborator. OpenViking retains context so you need not re-explain projects, while code, credentials, and local tools remain on your machine.

## Usage

1. Run the daemon on a Mac or Linux server and connect trusted direct chats and groups.
2. Send tasks directly, without mentioning the bot every time: investigate an error, shorten a README and open a PR, or summarize a discussion.
3. Use threads for focused discussions. The agent can execute coding tasks, invoke subagents, and work in other authorized chats when needed.

By default it responds to questions, tasks, and corrections, and usually stays silent for acknowledgments, thanks, chatter, and conversations between people. Chats share a working context, allowing follow-ups across chats and days.

## Why use it

- **Chat is the entry point; the agent remains your coding agent.** Request code, research, documents, and collaboration without copying tasks to a terminal.
- **Context lives in OpenViking.** Project knowledge, memory, and task resources are retained alongside each agent's own workspace and memory configuration.
- **Your machine stays under your control.** The daemon connects trusted messages to a local agent without moving the workspace to the cloud.
- **Run multiple agents.** Each has a separate Feishu profile, workspace, data directory, and OV configuration, sharing Web and updater services.

## Architecture

Core starts Connector subprocesses from the manifest. Feishu receives through lark-cli long connections; WeChat long-polls Tencent's `openclaw-weixin` iLink HTTP protocol; Telegram long-polls Bot API `getUpdates`; Slack uses Socket Mode WebSocket; QQ uses official Bot API WebSocket for direct chats; OneBot is the WebSocket client of a OneBot v11 implementation that drives a QQ account. Each emits a unified `EVENT` envelope into one `AgentRuntime`. Failures and reconnects are isolated: an expired WeChat login does not stop Feishu or the runtime.

Outbound runtimes use the same Agent tools, such as `send_message` and `reply_message`. Codex/TraeX/Claude/Pi/DSH/OpenCode/Antigravity share an MCP adapter; Durable calls through its host process. Both forward to Core's local socket. Core validates authority, freshness, routing, and content, writes a single `outbox` table, then invokes the Connector. Daemon failure notices and slash-command responses use the same path. See [Connector protocol 0.2](connector-protocol.md).

All sources write `connector_events`, deduplicated by source instance and event ID. Chat/message/reply routing is optional: GitHub issues and calendar reminders can enter with `kind`, `subject`, title, and body without pretending to be chat messages. Built-in connectors are Feishu, WeChat, Telegram, Slack, QQ, OneBot, and IMAP/SMTP email; new sources require a Connector. Non-chat events notify the owner through `notify_owner`, with Core selecting the owner's direct chat on the primary `channel`, rather than the agent choosing a destination. Incoming chat replies stay on the originating IM. The primary channel handles unsolicited notifications and email approvals.

Connectors establish the owner during self-check: Feishu's lark-cli user account, WeChat's scanning account, Telegram's one-time Start binding, Slack's direct-chat passphrase sender, QQ's verified direct-chat account, or the friend who sent OneBot's private-chat code. Protocol 0.2 was a direct pre-release schema change: use a new data directory; the old `inbound` table is not migrated.

## Installation

Use the pinned command in [install.md](../install.md), then `vika setup` to connect channels. Node is bundled, and the UI supports `--lang zh|en`. The unversioned installer and `latest` endpoint are currently unavailable. See the [configuration reference](installation-reference.md) for dependencies, setup, upgrades, rollback, autostart, multiple agents, and releases.

## Chat channels and email

Channel setup and supported behavior follow. [Attachments and cards](#attachments-and-cards) are described separately.

### WeChat direct messages

Add a WeChat instance to `config.json`. `channel` is the compatibility field; `connectors` selects processes started by Core. `profile` belongs to Feishu's lark-cli and must be omitted for WeChat-only instances. `ovConfig` is required; `ovAutoRecall` defaults to `false`.

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

Codex, TraeX, Claude, Pi, DSH, OpenCode, and Antigravity can be used. To share the session and OV space between Feishu and WeChat:

```json
{
  "channel": "lark",
  "connectors": ["lark", "weixin"],
  "model": "pi"
}
```

Run `vika login weixin`, choosing an instance if needed, or name it explicitly. Add WeChat to an existing instance through setup. Without `connectors`, the legacy single `channel` behavior remains.

For source development:

```bash
npm install
vika login weixin weixin
VIKA_INSTANCE_ID=weixin npm run start:daemon
```

Scan the terminal QR code or open its backup link, then confirm on your phone. Credentials are stored in `dataDir/weixin/state.sqlite3`. Send the new bot contact text, images, or files. **Send it a message after every scan before expecting proactive messages**, including email notifications or approvals. One WeChat account can connect to only one bot; scanning another instance disconnects the previous one. Existing WeChat voice transcription enters as text. Only the scanning account can chat with the bot; other people and groups are unsupported.

Inbound media is downloaded from Tencent CDN, AES-decrypted, and stored in `dataDir/weixin/media`, with a 20 MiB per-file limit for downloads and outgoing workspace files and cleanup under the daemon retention policy. Raw audio/ASR, video, groups, quoted replies, and reactions are not implemented. The protocol is pinned to `Tencent/openclaw-weixin` 2.4.9; see its [notice](../packages/daemon/src/weixin/NOTICE.md) and [MIT license](../packages/daemon/src/weixin/LICENSE.openclaw-weixin).

### QQ direct messages

QQ uses official Bot API v2 and WebSocket, not a personal-account protocol, and needs no public webhook. In a release with QQ support:

1. Register at the [QQ developer platform](https://q.qq.com/#/) if needed, then create a separate bot at the [QQ bot entry](https://q.qq.com/qqbot/openclaw/), obtain AppID / AppSecret, and add it with your QQ account. These credentials work with Vika; OpenClaw need not be running.
2. Select QQ in setup, enter credentials (secret input is hidden), and ensure the bot is online with direct-message permission.
3. Send the complete `vika-bind …` code shown in the terminal to the bot within three minutes. Only the matching direct message saves the account; another early sender does not become owner.
4. Send tasks after startup. Use `vika login qq [instance]` to rebind or change keys; the instance pauses during verification, resumes afterward, and retains old credentials on failure.

Only the bound owner's direct chats are processed. Text, images, files, quoted replies, and slash commands are supported. Text is plain, up to 4,000 characters. Files/downloads are limited to 20 MiB, with multipart upload from 5 MB; sending generic files depends on bot permissions. Groups, personal-account interception, audio, video, interactive buttons, and history search are unsupported. Only Tencent's MIT Node SDK is used for WebSocket, not a separate QR credential SDK.

QQ can be the primary channel for notifications and numbered email approvals, subject to proactive-message permissions and quota. Passive reply counters persist locally, with at most four replies and a maximum 60-minute window measured from message creation. The official page also mentions five minutes in a field description; earlier server rejection is returned as an error. Quota exhaustion, permission errors, expiry, and uncertain results do not trigger automatic proactive fallback or retries. See [QQ sending rules](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_messages.post.html).

A QQ-only instance uses `"channel": "qq"`, `"connectors": ["qq"]`, and no `profile`; mixed instances are supported. Codex, TraeX, Claude, Pi, DSH, OpenCode, and Antigravity are available. AppSecret, owner OpenID, and reply routes live in `<dataDir>/qq/state.sqlite3` (0600, parent 0700), not manifest/logs. Media lives in `<dataDir>/qq/media` with retention cleanup. Bind one local receiver and one instance per bot. WebSocket Resume is attempted within the same process; complete recovery of messages during process downtime is not guaranteed. Common ACK/outbox behavior is unchanged.

### Telegram direct messages

The official [Telegram Bot API](https://core.telegram.org/bots/api) uses long polling without public webhooks. Vika must reach `api.telegram.org` over HTTPS, and you need Telegram on your phone or desktop.

1. Send `/newbot` to official [@BotFather](https://t.me/BotFather), create a separate bot, and copy its token.
2. Select Telegram in setup. It can be primary or share an instance with Feishu, WeChat, and email.
3. Paste the token into hidden input, open the generated link or QR code, and click **Start** in the bot's direct chat within three minutes.
4. After startup, send tasks. `vika login telegram [instance]` changes tokens or binding, pausing the instance during verification and retaining old credentials until success.

Only the account completing the current random binding link can trigger the agent; other accounts, groups, and old binding commands are ignored. Ordinary bot direct chats are not end-to-end encrypted. Use one bot per instance with no competing poller. Existing webhooks cause refusal rather than automatic deletion of another service's configuration.

Text, images, files, quoted replies, primary notifications, and numbered email approvals are supported. Plain text is limited to 4,096 characters. Workspace files retain Core's **20 MiB** limit; JPEG/PNG files up to **10 MB** are sent as photos, larger ones as files. Incoming media downloads on demand, limited to **20 MB** by the hosted Bot API. Groups, voice transcription, video, button cards, reactions, and chat-history search are unsupported.

A Telegram-only instance uses `"channel": "telegram"`, `"connectors": ["telegram"]`, and no `profile`. A mixed example is `"connectors": ["lark", "telegram", "email"]`, with `profile` required for Feishu. Codex, TraeX, Claude, Pi, DSH, OpenCode, and Antigravity are supported.

Tokens, owner/chat IDs, receive cursors, and reply routes live in `<dataDir>/telegram/state.sqlite3` (0600, parent 0700), not manifest/logs. Media lives in `<dataDir>/telegram/media` and expires under retention. Telegram retains pending updates for at most 24 hours. Reception uses protocol 0.2 stdout delivery/cursors without an additional Core durable ACK guarantee. Sending retries only explicit `429 retry_after` responses within limits; uncertain network outcomes are not automatically resent.

### Slack

Use an internal app in your workspace with [Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode), requiring HTTPS/WSS access to `slack.com` but no public callback. Use one app per instance: Slack distributes events randomly among an app's connections, so competing receivers lose messages. Vika stops with an explanation when it detects this.

1. Select Slack in setup.
2. Open the terminal link and create an app with its prefilled configuration. Alternatively use **Create New App → From a manifest** at [api.slack.com/apps](https://api.slack.com/apps) with `<dataDir>/slack/app-manifest.json`. Workspace approval may be required. For an existing app, enable Socket Mode and App Home messaging, add manifest subscriptions/scopes, and reinstall. Token Rotation is unsupported; stop any previous receiver.
3. **Install to Workspace** and copy the Bot User OAuth Token (`xoxb-`) from OAuth & Permissions. Create an App-Level Token (`xapp-`) with `connections:write` under Basic Information. Paste both into hidden terminal input.
4. Send the one-time `vika-xxxxxxxx` passphrase to the bot in a direct chat within five minutes; its sender becomes owner. `vika login slack [instance]` rebinds tokens or owner, pausing the instance during validation; messages received during this pause are replayed afterward.

Anyone in the workspace can directly message the bot. Invite it to channels with `/invite @bot`; mentioning it is not required, and the agent decides whether to answer. Slack Connect inputs carry `external=true` and follow external-group privacy boundaries. Other apps' messages carry `sender=bot` and `bots_since_human=N`. The agent decides whether another response helps. A fuse blocks further replies after 20 bot-message responses in a channel/thread with no intervening human message, notifying the owner once. Human input resets it. Set `VIKA_BOT_REPLY_FUSE` to change the threshold, or `0` to disable it. Feishu uses the same mechanism.

Slack has no quoted replies: replying to a channel message starts/continues its thread; thread input stays there. Markdown converts to Slack syntax, with a 12,000-character limit and longer content sent as files. `@channel` and `@here` are suppressed. Text, files (workspace limit 20 MiB; incoming files downloaded on demand), mentions, reactions, and retraction are supported; button cards and search are not. Email approval uses numbered messages such as `发送 N` (send N). Slack intercepts slash commands, so prefix Vika commands with a space, e.g. ` /new`.

`list_chat_members` pages through channel/direct-chat members and returns IDs, display names, and known bot/self/deactivated flags. Names can be used in `mention_names`; use IDs for duplicates. Page size defaults to 100, maximum 200, with `next_cursor`. Unavailable profiles retain their IDs and an explicit unknown marker rather than being classified as human. New-app templates include these permissions; existing apps need [additional authorization](installation-reference.md#slack-setup). Missing scopes affect this query only.

Read-only queries use `Agent → Core → Slack Connector → official Slack CLI`. `list_chats` lists joined group conversations, `list_chat_messages` reads newest first, and `list_thread_messages` reads oldest first including the root. Members and profiles use the CLI too. Each call returns one page; continue with `next_cursor` when `has_more`, even if name filtering emptied the current page. History/threads allow at most 50 messages per page and bot identity only, including authorized pre-binding and bot messages. Full returned `message_id` / `thread_id` values work for replies, thread reads, and downloads. History is context only, never requeued or valid as `decision_id`. Mixed instances can specify `source: "slack"`.

Official [Slack CLI](https://github.com/slackapi/slack-cli) 4.10.0 is bundled at `bin/slack` or installed by `npm run setup`. The Connector uses `slack api` for allowlisted read-only calls and manages temporary configuration, token environment, and timeouts. Core does not execute the CLI; the agent does not receive the bot token. Socket Mode reception and Web API sending, reactions, retraction, and file transfer remain in the Connector.

Slack-only instances use `"channel": "slack"`, `"connectors": ["slack"]`, and no `profile`; Codex, TraeX, Claude, Pi, DSH, OpenCode, and Antigravity are supported. Tokens, owner, routes, display-name cache, and pending messages live in `<dataDir>/slack/state.sqlite3` (0600, parent 0700), not manifest/logs. Messages persist before ACK, surviving Connector restarts. Slack retries only briefly during total daemon downtime, so longer outages can lose messages. Slack provides no idempotency key; uncertain sends are not automatically retried.

### QQ over OneBot

The `onebot` Connector speaks through an ordinary QQ account driven by a OneBot v11 implementation (written against the NapCat source and tested against a simulated OneBot server, not a live QQ login; LLOneBot and Lagrange.OneBot differ in a few features). Unlike the official QQ bot above, it can join groups and read history. QQ may restrict an account driven by a third-party client: use one you can afford to lose. Vika is the client of the implementation's WebSocket server and needs no public address.

1. Add a WebSocket server with a token to the implementation (NapCat: `websocketServers`, `messagePostFormat: array`), reachable only from your machine or private network.
2. Select OneBot in setup and enter the address and the access token (hidden input; required).
3. Send the one-time `vika-xxxxxxxx` code to the account in a private message from your own QQ (a friend of the account) within five minutes; you become owner. Setup lists the account's groups. `vika login onebot [instance]` changes the address, token or owner and keeps the old account on failure.
4. List groups in `<dataDir>/onebot/config.json`, as `"ambient"` (the agent follows the conversation and decides when to speak) or `"mention"` (woken only by an @mention, a quote-reply to its message, or a line containing one of `names`). The file is reread when it changes.

Only listed groups and your private chat are connected (`"private": "friends"` connects every friend's private chat). Other messages are dropped before they are stored, and the agent cannot send to other chats. Group lines are held and delivered in batches: an addressed line at once, unaddressed lines in an `ambient` group after `settleMs` (default 3 s) of quiet, at most 45 s later, so a busy group wakes the model once per batch instead of once per line. If another bot program shares the account, list its command prefixes in `commandPrefixes` and make that program answer only its own commands. What it, or you by hand, sends from the account reaches the agent as context in a group and is not shown in a private chat. See [OneBot Connector](onebot-connector.md) for the policy keys, the timing rules and the implementation differences.

Text is plain, up to 4,000 characters. Files are up to 20 MiB; images are sent as images. Quoted replies, @mentions by name in groups, reactions on group messages (NapCat and LLOneBot), retraction of the agent's own messages, attachment downloads (a forwarded chat as a text transcript), `list_chats` for connected groups, and the newest page of `list_chat_messages` are supported. Threads, cards, search, member lists, and @全体成员 are not. Messages that arrive while Vika is disconnected are not delivered later; the agent can read them with `list_chat_messages`.

A OneBot-only instance uses `"channel": "onebot"`, `"connectors": ["onebot"]`, and no `profile`; Codex, TraeX, Claude, Pi, DSH, and OpenCode are supported. It can be the primary channel. The address, token, owner, routes, and lines waiting for delivery live in `<dataDir>/onebot/state.sqlite3` (0600, parent 0700), not manifest/logs; media lives in `<dataDir>/onebot/media` with retention cleanup. Every member of a connected group can talk to an agent that runs commands in its workspace: connect only trusted groups and run the instance isolated. OneBot has no idempotency key: a failed send, whether or not it reached the server, is not retried automatically.

### Email

The email Connector watches IMAP read-only: it does not mark messages read, move, or delete them. SMTP sends replies. Each configured mailbox runs as a separate Connector subprocess, such as `email-gmail` or `email-qq`; bad passwords or rate limits back off independently. The agent decides whether incoming mail merits an owner notification on the primary IM. It **cannot send email directly**: whether asked or acting on its own judgment, it drafts a reply and Core presents sender account, recipient, subject, and body for owner approval.

- With Feishu as primary, edit the draft body in a card and click Send or cancel.
- With WeChat, Telegram, Slack, QQ, or OneBot, use numbered messages: `发送 3` sends, `不发 3` discards, and `修改 3 新的正文` replaces draft 3 with the supplied body. These are literal Chinese command forms; an edited draft is shown again and still needs explicit send approval. Asking the agent to redraft invalidates the old number. Core handles exact-format owner direct messages without invoking the agent.

Before delivery to the agent, each email is stored in the instance's private OV area, `viking://~/resources/emails/<email>/`. `body.md` contains subject, addresses, time, attachment list, and full body. Attachment text is under `attachments/<number>-<filename>/full/`: OV parses PDF, Word, Excel, PPT, HTML, and text into Markdown; textual forwarded mail and calendar invitations remain as text. Images, archives, and inline body images are not stored. Because one OV file has one vector based on roughly its first 4,096 tokens, long bodies/attachments also get sibling `chunks/` for recall. The agent always reads the full text, starting at the matching position when a chunk is recalled. `vectors_only` avoids LLM summaries. The agent initially receives addresses, storage location, attachment list, and the first 500 characters; `read_email` reads full body or attachments. During OV outages, the first 6,000 body characters are delivered directly and attachments are unavailable.

Only the owner can approve, once, within three days. Initial startup watches only subsequently arriving mail. List/subscription mail is marked `kind=email.list`; automatic replies, most bounces, and the bot's own replies are skipped. Automated alerts such as security or task-failure notifications still reach the agent.

Select email in setup for new or existing instances and add one or more accounts. Gmail, QQ, and 163/126/yeah.net have presets; other servers are manual. IMAP and SMTP are tested before saving. The edit menu adds accounts, changes passwords, or removes accounts; `vika login email` is quicker for password changes. Replies use the receiving mailbox, shown on the draft.

Account files are `~/.vika/email/<instance>/<account>.json` (0600), referenced by the instance:

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

Account IDs start with `email-` and use lowercase letters, digits, and hyphens. Handwritten account files must have mode 0600 and must not be placed in `env`:

```json
{
  "address": "you@gmail.com",
  "password": "<app-password>",
  "imap": { "host": "imap.gmail.com", "port": 993 },
  "smtp": { "host": "smtp.gmail.com", "port": 465 }
}
```

Optional `imap.mailbox` selects one folder, default `INBOX`, e.g. `"imap": { "host": "imap.example.com", "port": 993, "mailbox": "Projects" }`. Case and spaces are preserved (only INBOX is case-insensitive), including non-ASCII names. A newly watched folder starts with future mail rather than importing existing messages. Returning to a previously watched folder resumes its cursor. Folder identities are independent; old INBOX mail and archives remain readable/replyable.

For several folders, replace `imap.mailbox` with `imap.mailboxes`, e.g. `["INBOX", "Projects", "Projects/Alerts"]`, up to 100; the two settings are mutually exclusive. Single folders use server-supported IDLE. Multiple folders are read-only polled with sequential EXAMINE on one connection, waiting 60 seconds after each round by default; `imap.pollIntervalSeconds` accepts 15–3600. IDLE watches only the selected folder, not every folder through one INBOX subscription.

Each folder tracks UIDVALIDITY and UID separately. Permission failures or deletion do not block other folders; reconnect resumes cursors with backoff. Renumbering resets only that folder and follows the new-mail-only rule. Each round processes at most the latest 50 pending messages per folder and skips excess (the SEARCH response itself is not bounded). Attachments can lengthen rounds. Copies/moves can notify again because the destination has a new IMAP identity; Message-ID is not globally deduplicated.

To discover new folders, use `imap.discovery`, mutually exclusive with `mailbox` / `mailboxes`. This is the account file's `imap` object:

```json
{
  "host": "imap.example.com",
  "port": 993,
  "pollIntervalSeconds": 60,
  "discovery": {
    "roots": ["INBOX", "Projects"],
    "exclude": ["Projects/Trash"],
    "refreshIntervalSeconds": 300
  }
}
```

`LIST "" "*"` finds selectable folders; server hierarchy delimiters determine roots and descendants, so `Projects` does not match `ProjectsOld`. Empty `roots` means all selectable folders; exclusions include descendants. Explicitly exclude provider-specific Trash/Sent names; purposes are not guessed. Discovery always polls and refreshes every 300 seconds by default (15–86400 allowed, also affected by processing/poll time). More than 100 matches rejects that refresh; narrow the scope. Failure retains the last successful list. Deleted folders retain cursors for reappearance; newly discovered folders notify only future mail. Logs record counts in `email_mailboxes_discovered` and failures in `email_mailbox_discovery_failed`, never silently falling back to INBOX.

Protocol references: [RFC 9051](https://www.rfc-editor.org/rfc/rfc9051.html) for mailbox, UIDVALIDITY, UID, and LIST; [RFC 2177](https://www.rfc-editor.org/rfc/rfc2177.html) for IDLE; [RFC 3501 §5.1.3](https://www.rfc-editor.org/rfc/rfc3501.html#section-5.1.3) for IMAP4rev1 non-ASCII names. NOTIFY / OBJECTID are not implemented.

`password` is a per-account third-party app password shared by IMAP and SMTP. Gmail requires two-step verification and an app password without spaces; QQ/163 call it an authorization code and require enabling IMAP/SMTP. SMTP uses implicit TLS on 465 and STARTTLS elsewhere, overridable with `smtp.secure`. Optional `username` defaults to `address`; `name` sets the reply sender name. OAuth-only providers such as Outlook are not supported here. Self-hosted Ark lacks Agent tools and cannot use email.

## Attachments and cards

`send_message` / `reply_message` accept exactly one of `markdown`, `file_path`, `file_key`, `card`, or `choices`. WeChat, Telegram, Slack, QQ, and OneBot support only `markdown` and `file_path`. Files are workspace-relative and at most 20 MiB; images are sent as images. Inline Card 1.0/2.0 is limited to 30 KiB. Thread files/cards use replies with `reply_in_thread: true`.

`choices` sends a single-choice button question and returns immediately. After a click, the daemon validates answerer, expiry, and duplicate submissions, delivers a new input, and best-effort updates the card. Question state and thread routes persist in SQLite. Ordinary cards also enter the agent, showing `[card]` when text is unavailable. See [message formats](../packages/daemon/agent/lark-message-formats.md). Outbound schemas are runtime-independent; only Agent-tool cards register callbacks.

Enable long-connection `card.action.trigger` in Feishu callback configuration before deployment; see [manual setup](installation-reference.md#manual-feishu-setup).

## Interaction rules

- In a connected IM direct chat, `/primary` selects that IM as primary; `/primary status` shows the primary and current IM. Only its bound owner can use these, never groups. In Slack, prefix a space. Changes persist immediately without restart or task interruption. New notifications/approvals move to the new IM; pending approvals remain on the original one. Selecting the current IM is a no-op. Unavailable connections, unbound owners, or save failures preserve the old setting. WeChat warns that proactive delivery needs recent context and may require another incoming message. An already open setup wizard refuses to overwrite a concurrent command change; restart setup.
- All connected chats can request workspace writes, so connect only trusted chats.
- Chats share one global runtime/session. Inputs interleave with `chat=` identifying their origins.
- Group batching lasts 400 ms from the first pending message; later arrivals do not reset it. Chat/thread queues expire independently. Active Codex/TraeX/Claude/Pi/Durable/DSH turns receive immediate steering; OpenCode, Antigravity, and Ark follow-ups wait for the next turn.
- Questions, tasks, corrections, and meaningful follow-ups usually receive replies. Acknowledgments, thanks, chatter, status broadcasts, conversations between people, and already answered questions usually do not.
- Images, files, audio, and video enter as restricted resource references. Codex/TraeX/Claude/Pi/Durable/DSH/OpenCode/Antigravity use `get_message_media` with image/file keys (WeChat/Telegram/QQ/OneBot use message ID; Slack uses message and file IDs). Images are supplied through tool results, not embedded in the inbound prompt. Self-hosted Ark needs equivalent Worker-local tools because it does not inherit daemon Agent tools. Unknown message types do not trigger replies.
- Direct-chat slash commands are handled by the daemon without waiting for the model: `/new`, `/help`, `/status`, `/runtime` (alias `/run`), `/model`, `/effort`, and `/primary`. `/new` starts a clean session; Ark must first create the remote session successfully before committing the local epoch. Runtime switching supports Codex, TraeX, Claude, Pi, Durable, DSH, OpenCode, Antigravity, and Ark; selecting the same runtime preserves the session. Successful switches save both manifest and SQLite state and survive restart; setup reconciles desired configuration with actual runtime state. Cross-runtime switching retires the native session, restores the target's last model, and attaches recent raw OV history to the first real input; older raw messages are available through archive queries. `/model` affects only the current runtime (e.g. Claude `sonnet`, Antigravity `gemini-3.8-flash-low`, or Pi/Durable/DSH/OpenCode `provider/id`), without implicit runtime or session switching. Claude's CLI supplies names, version IDs, and efforts. Model/effort changes apply next turn, preserving the current turn and context. Effort candidates are validated against the model; unavailable capabilities are not guessed. When changing model, retain a supported effort, otherwise use its declared default, then supported medium or first option, and report the adjustment. Remove effort overrides for models explicitly lacking support. Unknown capabilities preserve settings and request retry. Unavailable saved models fall back to the runtime default, but discovery failure is not proof a model is absent; fallback is saved only after successful switching. Ark model/effort are configured remotely. `/status` reports main-model sampling and duration, showing idle while it waits for tools/subagents. Prompt/code updates require a published release via `vika upgrade <instance-id>`.
- After ten minutes without successful outbound delivery, subsequent trusted input metadata gets `idle=<seconds>s`; no reaction is sent automatically. For genuinely long work, Codex/TraeX/Claude/Pi/Durable/DSH/OpenCode/Antigravity may add temporary `OnIt` via `react_to_message`. Ark's Worker/CLI path supports final reactions only. Short tasks and ignored group messages create no extra noise; silence does not consume the idle window. Core tracks exact `reaction_id` values in memory per chat/thread and removes them on the next successful final text/reaction or when the turn ends, fails, or is abandoned. No polling/database scan is used. Deletion failure or SIGKILL may leave a reaction behind; this is best-effort UX. `VIKA_IDLE_REACTION_AFTER_MS=0` disables the hint.
- `agents[].role` chooses `colleague` (AI coworker collaborating in work groups), `assistant` (owner-focused tasks, reminders, and notifications), `qa` (verified product answers with privacy and topic boundaries), or an absolute Markdown path. Setup can copy a built-in role as a custom starting point. Built-ins update with releases; custom files do not. Omission uses default rules only. Owner/product knowledge belongs in OV, not the role. Restarting after role edits starts a new session.
- Natural-writing guidance defaults on; `agents[].naturalWriting: false` disables it.
- Setup's instance **Other settings** toggles staged replies, natural writing, explicit silence, OV recall, and meeting notes. Save/restart applies changes. New instances default to natural writing and explicit silence; the first Feishu colleague/assistant also enables meeting notes. The confirmation screen lists defaults. These fields may also be edited in `config.json`.
- `agents[].meetingNotes` stores enabled smart notes and transcripts from your Feishu meetings in private OV `viking://~/resources/meetings/<meeting_id>/`: `note.md`, `transcript.md`, and long-text recall `chunks/`. The agent reads full text with `read_meeting_note`. At startup and every 15 minutes it queries recent meetings as the owner, initially looking back 30 days, and checks new/waiting meetings. Notes generally appear 1–4 minutes after meetings and are archived about 15–20 minutes later; after two hours without notes, polling stops for that meeting. No Feishu events or extra app configuration are needed; ingestion does not wake the agent or send messages. Minutes without read permission are not stored; linked documents are read on demand, not copied. Enable this on only one instance per machine to avoid duplicate ingestion. The first Feishu colleague/assistant defaults on, QA off; disable the old instance before moving it. State is `<dataDir>/meeting-notes/meetings.sqlite3`, with waiting/pending/stored/none/skipped/failed per meeting.
- Explicit silence defaults **on**, except Ark defaults off. Set `agents[].explicitSilence: false` and restart to disable. When enabled, each input requires a delivery or `stay_silent`, recorded in SQLite `decisions`; undecided inputs are offered once more with `undecided=1`. When disabled, prompt/tools/policyHash match the feature-free behavior. Changing the setting changes policyHash and prevents native thread resume. Upgrading an older default-off version without an explicit field enables it and starts a new session on the first turn; specify false to retain the old behavior. Ark lacks Core tools; enabling this while running/switching to Ark logs `explicit_silence_not_applied`.
- Staged replies default **off**; enable `agents[].stagedReplies`. Immediate answers still use one message. Real investigation starts with `OnIt` (a short message on WeChat/Telegram), followed only by verified intermediate findings and a self-contained final answer. A necessary question is that turn's final response; elapsed time is never consent. Intermediate `send_message`/`reply_message` calls use `interim: true`, text only, in the originating chat/thread. They neither complete the decision nor remove `OnIt`. Disabled tools omit `interim` and use one final delivery per decision. Changing this setting changes policyHash. Ark lacks support and logs `staged_replies_not_applied`.
- Anyone can request research, documents, and similar work. lark-cli acts only as bot or owner, so actions in another person's own account (their calendar/tasks/meetings) are unavailable; the agent should explain and supply details they can add themselves.
- Replies quoting an agent message carry `reply_to=me` and a `quote=` excerpt to relate delayed answers to the original question.
- Feishu document-comment mentions and replies to comments the bot joined wake it. A document is `chat=doc:<type>:<token>` and each comment is a thread. The bot reads the comment, referenced passage, prior replies, and title as bot; read failures still wake the model with an explanation. `reply_message` answers in plain text only. Resolved/whole-document comments cannot accept replies, so a new whole-document comment mentions the asker. Everyone with document access can see it; group privacy rules apply. The API has no idempotency key, so uncertain replies are not retried. Only comments on documents shared with the bot are received.
- Failed model calls produce a programmatic quoted failure notice with the actual error instead of silently losing the message.
- Core cancels a turn after three ineffective repeated actions on the same input since the last effective action (delivery, decision, timer creation/deletion). Duplicate silence, sends, or identical timers count despite wording changes; queries neither count nor reset the counter. Codex/TraeX/Claude/Pi/Durable/DSH/OpenCode/Antigravity cancel in place without replacing process/session. No failure notice is sent for the first cancellation; pending input continues, and undecided chat/non-chat inputs are offered once more, with `undecided=1` only when explicit silence is enabled. A second cancellation for that input stops replay and sends a failure notice. `decisions` records final deliveries even with explicit silence disabled for this check, without changing prompt/tools. The next turn receives an explanation. A second cancellation within ten minutes, or a turn still active 30 seconds after cancellation, creates a new native session with recent history while preserving cognition epoch. Activity records the cancellation. Repeated file reads/commands are not detected; Ark is excluded because it bypasses Core tools.

Codex/TraeX/Claude/Pi/DSH/OpenCode/Antigravity MCP and Durable sends, replies, reactions, and retractions all pass through Core authority, freshness, idempotent outbox, and delivery accounting. Policy forbids bypassing them with lark-cli. The fixed bot-profile CLI remains available for non-message documents/calendar work; auth/config/admin operations stay forbidden. Self-hosted Ark is an exception: its Environment Worker cannot reach the local socket, so Worker bash invokes fixed lark-cli without Core freshness/outbox. Tools can deliver the entire reply; `end_turn` need not also return `agent.message`.

## Feishu prerequisites

Create the app under a phone-registered Feishu account (a free enterprise is created with you as administrator) or an enterprise account. Doubao-signed-in Feishu can create/publish apps but cannot obtain `tenant_access_token` (`code=21001`, access-control rejection). Setup explains and does not save configuration.

[permission.json](../permission.json) contains the complete feature permission list. Import it through the developer console's permission-management bulk-enable screen; see [official scope guidance](https://open.feishu.cn/document/server-docs/application-scope/introduction?lang=zh-CN). It covers documents, messages, tasks, meetings, and more, not minimum privileges. Restrict scopes and app visibility for production. Review-required permissions take effect only after creating/publishing an app version and administrator approval.

The basic messaging path requires:

1. A long-connection subscription to `im.message.receive_v1`.
2. `im:message.p2p_msg` or its corresponding read-only permission.
3. `im:message.group_msg` for unmentioned group messages; group-at alone does not receive them. Bot-authored messages additionally need `im:message.group_msg.include_bot:read` (or `im:message.group_at_msg.include_bot:readonly` for at-only). Setup requests these when creating the app.
4. `im:message:send_as_bot`.
5. `im:chat:read` for the bot's group list.
6. Group membership and appropriately restricted app visibility.

### Message access

Setup asks whether to search/read Feishu messages as your user (`agents[].larkMessageAccess`).

- **Allow** (default `owner`): the second scan authorizes `search:message`, `im:message:readonly`, `im:message.group_msg:get_as_user`, and `im:message.p2p_msg:get_as_user`. `search_messages` uses your identity; history can use `as: "user"`, including your direct chats and groups the bot has not joined.
- **Deny** (`bot`): neither scan requests those four user scopes; the app requests bot `search:message` instead. Search/history return only the bot's joined groups and its own direct chats.

Feishu retains previous app grants. Disabling user scopes later blocks history but may leave user-identity search available. The choice therefore follows the app and is asked only for creation/replacement. To restrict a previously allowed app to bot-only conversations, create a new bot. Setup warns if you choose denial for an app previously authorized to read messages.

Document comments additionally require long-connection `drive.notice.comment_add_v1` (included by setup) and sharing the document with the bot. `docs:document.comment:*` scopes are in `permission.json`.

After upgrades, setup checks every Feishu instance for scopes, message/comment events, card callbacks, and owner authorization. Missing items are shown in the action menu and Feishu app settings. Choose QR repair and save; an owner/admin scan adds missing app configuration, another scan refreshes user authorization if needed, then the instance restarts.

As of lark-cli 1.0.86, no CLI command imports/publishes developer-console scope manifests: `lark-cli application` manages slash commands; `lark-cli apps` manages Miaoda apps. Do not use `auth login` to repair bot scopes; import, publish, and approve in the developer console. User scopes additionally require user OAuth.

The selected local lark-cli profile must exist with bot identity `ready`; user OAuth is needed only for user scopes. The chosen coding CLI (such as `codex` or `traecli`) must be authenticated. The daemon pins its startup profile and does not follow later default-profile changes.

## Local development validation

```bash
cd vika
mkdir -p ~/.vika
cp config.example.json ~/.vika/config.json
npm test
npm run check
npm run start:daemon   # 127.0.0.1:3738
npm run start:web      # 127.0.0.1:3737
```

Use a small, separate agent workspace so test coding agents cannot modify the daemon itself. Installed releases live under `~/.vika/releases/`, managed by Vika's PM2. Persistent manifests outside releases independently configure each workspace.

## Operations

```bash
vika status
vika logs <instance-id>
curl http://127.0.0.1:3737/ready
vika upgrade <instance-id>
vika upgrade all
```

`vika-updater` listens on a local Unix socket with mode 0600, not TCP, and never reads the agent's source checkout. It accepts only manifest IDs or `all`, rereading the manifest each time; requests cannot specify versions, paths, download URLs, or shell commands. It installs only the published `latest`, verifies SHA-256 and bundled-Node smoke checks, atomically switches `current`, rolls target daemons from the same ecosystem configuration (`all` also rolls shared Web), then restarts itself. Same-version requests reuse the release. It shares the installer's deployment lock. A published `latest` endpoint is required; see the current pinned-install limitation in [install.md](../install.md).

### Multiple-agent deployment

OV is required. Before IM onboarding, setup lists detected connections and always offers Volcengine managed OV (API key from the OV console's User Management → API Key), manual URL/credentials, and file import, including remote/custom ports. API-key services derive identity from the key; manually changing the address does not reuse old Account/User fields. Editing an instance opens its settings directly; select OpenViking to edit and save to apply. Backing out/discarding preserves old configuration. The installer does not prompt: first interactive installation enters setup; other installs require OV before switching. Rejected startup credentials cause exit; temporary outages allow history-free startup and later sync.

Each instance requires separate `ovConfig`. Vika owns OV memory for every runtime: only the daemon's session sync writes the conversation, each line naming its speaker in `from=`, with no peers; extraction keeps profile, entities, and events, each event once. Preferences are written only by `openviking_remember`, into the sender's own directory (`memories/preferences/owner/` for the owner, `<name>~<id>/` for anyone else); the owner's preferences are the agent's standing rules. Before forwarding direct chats, mentioned group messages, document comments, and non-chat events such as mail, the daemon attaches the owner's preferences and the sender's (about three seconds; failures/timeouts skip them). Boolean `agents[].ovAutoRecall` (default `false`) adds one semantic search on the line; results follow it with `who=` naming the owner, the sender, or another person. Extraction never changes the agent's own name or role: those come from configuration, and when someone else asks it to change how it behaves for everyone, or how it treats another person, it asks the owner first. Runtimes Vika starts do not load their own OpenViking plugins. Restart to apply changes.

Core provides `openviking_*` tools with identical names, schemas, and behavior for every local runtime (Codex/TraeX/Claude/Pi/Durable/DSH/OpenCode/Antigravity) through the same ActionService: `openviking_find` / `openviking_search` (retrieval; a person's preference says whose it is), `openviking_read` / `openviking_list` (read and browse `viking://`), `openviking_add_resource` (asynchronously import a URL or workspace file someone asked to keep, by default into the private `viking://~/resources/inbox`, never into the daemon's mail, meeting-note, timer, memory, or session trees), and `openviking_remember` (when someone states how they want to be treated, asks that something about them stay private, or changes or withdraws such a wish, store, replace, or remove that preference by topic; the Core decides whose it is from the message's real sender, the model gives only topic and words). Ark (Volc) has no Core tools and is the only exception.

New-instance connection lists include environment-selected, saved Vika, and OV CLI configurations, not other instances' configurations; editing additionally shows that instance's current connection. New instances create their own OV file and user identity. Request-identified dev/Trusted services use `<connection-user>.<instance-id>` or just the ID without a connection user. API-key services identify by key: if already used by another instance or CLI, explicitly choose shared memory or another key. An admin key can create a new user without being saved. Backtracking reuses newly created users; setup completion/cancellation/error deletes unused created users, reporting cleanup failures for administrators. Instance configuration has no peer, and the daemon attaches none. An existing default-local connection avoids repeated anonymous probing. Trusted mode requires Account and User and cannot proceed until identity checks pass.

Runtime OV failures do not exit the daemon or proactively notify chats. New native sessions can skip history; the local sync ledger remains for retry. Existing sessions do not suddenly receive recovered history; a later new session can load it. Archive-integrity errors still block incomplete handoff rather than replaying SQLite history.

Each agent is an independent daemon, not another agent's subagent. The manifest is the sole configuration source; see [config.example.json](../config.example.json). Top-level fields are `version`, `defaultInstance`, `env`, and `agents`. Instances provide `id`, `displayName`, `port`, `dataDir`, `workspace`, `ovConfig`, `model`, and `profile` when using Feishu. `channel` defaults to `lark`; connector-specific examples above describe other channels.

Shared `env` accepts only listed keys and string JSON values, including numeric/boolean settings (empty strings are allowed). Process environment values take precedence. Identity, channel, profile, port, directories, OV/model, and Web/updater control fields do not belong in `env`; unknown/reserved keys are discarded. Feishu `profile` fixes both `VIKA_LARK_PROFILE` and `LARKSUITE_CLI_PROFILE`; non-Feishu agent children do not inherit either.

Unknown manifest fields at any level are ignored and reported in `config_fields_ignored`, status, and setup, allowing older configuration to start. Known fields remain strict: relative paths, invalid IDs/ports, duplicate IDs/profiles/ports/directories/OV files, and nested dataDir/workspace pairs are rejected. The ecosystem creates `vika-<id>` apps, Web routes, and updater allowlists with fixed per-instance settings.

`npm run start:daemon` uses `defaultInstance`; `VIKA_INSTANCE_ID=<id>` selects another for foreground development. After topology changes, save the manifest and run `vika upgrade all`, or `vika start <new-instance>` plus `vika restart web`. After all new apps are healthy, `upgrade all` removes obsolete apps from the same managed release without deleting data or touching unrelated prefix-matching processes. To reuse an old port immediately after an ID rename, verify executable ownership and stop/delete that exact old app; there is no alias/migration mechanism. Ordinary code upgrades use `vika upgrade <id>`.

Setup deletion defaults to cancellation. It removes the instance configuration and managed PM2 app, saves the process list, and refreshes Web, retaining workspaces, history, credentials, OV memory, and platform bots. Choose a new default if needed; the sole remaining one is automatic. Deleting the last instance removes Web/updater, backs up config to `.bak`, and removes the active file. Deletion is blocked during deployment/upgrades; process-removal failure retains configuration for retry.

Maintainers must publish code before `vika upgrade <instance-id>`. The updater can finish a self-upgrade after its caller exits, but the current CLI/model connection may be lost. Verify through `/status` or local health afterward.

## Activity page

Timers complete through `finish_timer_occurrence` with `occurrence_id` and optional `delivery: { tool, input }`, reusing `send_message`, `reply_message`, or `notify_owner`. Core marks completion only after confirmed sending. If no message is needed, omit delivery and provide `reason`. Ordinary messages can ask the owner or report progress without completing the occurrence. Failed/uncertain/dry-run sends do not complete it. Completion ends this occurrence's checks, not future timer triggers, and the activity ledger retains results/reasons.

Timers and occurrence checks default to the instance's OV at `viking://~/resources/vika/<agents[].id>/timers-state.json`. OV expands `~` using authenticated identity; separate OV users isolate instances, while intentional user sharing still separates IDs. State loads on startup and writes only on changes, awaiting background processing before the next queued mutation. `VIKA_TIMER_STORAGE=sqlite`, also available in manifest `env`, selects legacy local storage. Other data stays local; no automatic migration occurs. During OV outages, messaging continues while timer loading retries; timer operations report unavailability and never overwrite remote state with an empty snapshot or silently fall back.

Exhausted occurrence checks expire seven days after their last saved check time in either backend, even without active timers. Removed occurrences reject completion calls. OV still indexes the state file; `vectors_only` does not disable indexing.

Run a given instance under a given OV user on only one machine. Stop the old machine before moving the same identity/ID to another; concurrent writers are unsupported.

Timers do not guarantee exactly-once final delivery: sending and saving OV completion are not one transaction, so a successful send with unsaved completion can be delivered again after checks or migration.

OV writes rely on successful write responses. Failed/timed-out writes retry the identical state within the serial queue, at most three times; failure is reported without read-back to infer success. Subsequent mutation/execution must first reload OV successfully rather than overwrite it from stale memory. Scheduling keeps checking and resumes after recovery.

`cancel_timer` removes the timer and all occurrence checks, stopping future triggers and heartbeat reminders. It can also clear checks for a naturally ended timer. One-shot or count-exhausted timers remove the schedule but retain the final occurrence check. Cancellation does not interrupt work already handed to the model.

Open [http://127.0.0.1:3737/](http://127.0.0.1:3737/) for received messages, model turns (reasoning/tools), delivery decisions, and outcomes. It has no login and listens locally only. For remote servers, use `ssh -L 3737:127.0.0.1:3737 <server>`.

- The current cognitive session opens by default. `/new` creates a new cognitive session; `/runtime` changes native session but preserves cognition. Same-runtime `/model` and `/effort` preserve both and apply next turn. The top bar selects history or the complete ledger.
- A lower timeline separates turns, messages, assistant, delivery, commands, and system tracks; details and playhead synchronize.
- Failures remain red at their original time; there is no separate action queue.
- Historical views do not live-follow; return to the current session or latest position explicitly.
- The local page refreshes every four seconds, using no external resources or cloud service.

Activity events persist in the same SQLite ledger and are not deleted by ordinary state cleanup. Listener records close on process takeover or normal shutdown, retaining historical starts while only the current listener appears active. Web APIs return redacted source/session references, cognitive epochs, and clipped bodies, not Feishu chat/message IDs, native session IDs, idempotency keys, or raw events.

## Runtime boundaries

The daemon and slash commands control models only through `AgentRuntime.start`, `submit`, `snapshot`, `transition`, and `stop`. `runtime-catalog` centralizes aliases, efforts, and capabilities; `runtime-adapter` normalizes transports; `runtime-input` creates authenticated envelopes. Adapters retain protocol differences: Codex JSON-RPC, Claude stream-json, Pi RPC JSONL, Durable host JSONL, DSH bridge JSON-RPC (LF-framed), OpenCode and Antigravity ACP nd-JSON, and Ark Sessions/SSE. Self-hosted Ark output/tool execution belongs to Worker/runtime. `submit` with `accepted: false` leaves the batch with the caller; ownership transfers only on true.

### Runtime tools

`packages/daemon/src/agent-clis.mjs` registers bundled lark-cli and official Slack CLI (`slack`). Codex/TraeX/Claude/Pi/Durable/DSH/OpenCode/Antigravity children prepend their directories to PATH, and prompts list actual command paths. Releases and source `npm run setup` need no global installation or manual PATH changes. `LARK_CLI_BIN` / `SLACK_CLI_BIN` affect both discovery and prompt paths; renamed/spaced paths are shell-escaped and relative paths resolve from the Vika package root. Messaging, profile, and credential boundaries remain unchanged; the Slack Connector retains its token.

Registering another pinned bundled CLI requires its name, binary path, and version-check arguments to reuse local-runtime discovery and release smoke checks. `node packages/daemon/scripts/check-agent-clis.mjs` checks sh/bash discovery/execution with temporary HOME, separate workspace, and clean PATH; release builds run it automatically.

Pi is an exact-version npm dependency, not global `pi` by default. Its session extension registers `personal_agent` MCP with `direct` exposure without editing user MCP configuration; failed loading prevents startup. Local runtimes share conversation policy rather than a Pi-specific Bash/CLI tutorial. See [installation](../install.md) for authentication.

Durable is also pinned and bundled. Its host journals conversations, model turns, and tool calls in `<dataDir>/durable/session.sqlite`, resuming checkpoints after interruption. It reuses `~/.pi/agent` configuration and provider API-key environment variables. The host registers Agent tools directly through `actions.sock`, alongside pi-durable `bash`, `read`, `write`, and `edit`. See [Durable runtime](pi-durable-runtime.md) for model precedence and recovery.

Agent tools have one definition/implementation in Core `ActionService`. Codex/TraeX/Claude/Pi/DSH/OpenCode/Antigravity use stdio `actions/mcp.mjs`; Durable uses `actions/client.mjs`; both forward to `<dataDir>/actions.sock` with `PERSONAL_AGENT_SOCKET` / `PERSONAL_AGENT_AUTHORITY`. Self-hosted Ark's official Environment Worker instead invokes fixed lark-cli through bash because it is outside the daemon process tree; only this Worker path can invoke Environment CLIs repeatedly within one native turn. The daemon observes `agent.tool_use`, `user.tool_result`, and final status. The cloud sandbox strict JSON Tool Call bridge was removed. Synchronizing CLI credentials to a cloud sandbox is not a supported or recommended replacement. Ark's CLI path uses stable idempotency keys but bypasses daemon authority/freshness/outbox and has different reliability limits.

## Reliability limits

- Messages first enter SQLite as DONE, then the resident runtime. Codex/TraeX use `turn/start` / `turn/steer`; Claude uses persistent stream-json input; Durable journals before model invocation; Ark uses session events/SSE and event-history/event-ID gap recovery. Ark persists its `user.message` fence and restores accepted remote turns without duplicate POST after daemon restart. If acceptance is uncertain, the old session is quarantined, a new one cloned, and epoch/authority atomically rotated before the queue resumes, preventing late output from crossing turns.
- The 400 ms group window batches transport only; it creates no chat-specific native session/history/memory. A crash in the window can leave persisted messages undelivered to the runtime. This version adds no durable replay/delivery ledger for that gap.
- Slash commands and subsequent input share an ingress barrier, but commands do not wait for the model. Read-only commands reply concurrently; session changes interrupt the old turn, prepare a new session, atomically CAS epoch/session/authority/model in SQLite, then perform non-failing local activation. Failed preparation/CAS preserves cognition. Cross-runtime switches preflight the destination before handoff. Model/effort changes save settings without interrupting or replacing sessions, applying next turn: Codex/TraeX per-turn parameters, Pi `set_model` / `set_thinking_level` RPC, Claude process replacement resuming the same session, DSH `vika/session/model/select` against the running session. Input is delivered only after successful application. Repeating current settings is a no-op.
- SQLite retains active runtime/session, idempotency ledger, and OV sync outbox. Cross-runtime context comes only from recent raw OV session messages, with older raw archives queried on demand. Older OV services, including managed services, may generate Working Memory summaries; Vika neither changes that setting nor uses those summaries. Native sessions retain native compaction. OV failures allow new sessions without history, never replaying the local journal. See [OV session context](ov-session-context.md).
- Input is one line, `[chat=… msg=… event=… time=… from=…] body`. Full chat/message/event IDs serve as tool arguments; display-name/thread representations may be shortened. `msg=` identifies the reply target and `event=` the decision. Non-chat inputs may lack `msg=` and still use `event=` for notifications. New sessions receive prior cross-chat history separated by `--- history above, new messages below ---`.
- Local runtimes use Core Agent tools for messaging.
- Raw Feishu events are not stored wholesale, avoiding header tokens; bodies are retained for context.
- Activity retains redacted short summaries of input, turns, steering, outputs, and delivery. Old entries fold in the UI rather than disappearing with business-table cleanup.
- There is no total-turn watchdog; a long-running Codex/TraeX/Claude turn can remain active indefinitely.

Subscriptions use raw events, not compact mode. Rich-text post images become image-key resource references; Markdown image syntax is not parsed.

## Feishu acceptance checks

1. A clear direct-chat task should execute and receive a reply.
2. A direct-chat acknowledgment such as “Received, thanks” should remain unanswered.
3. An unresolved group question without a mention may receive a reply when the agent adds value.
4. Chatter or conversations between people should remain unanswered.
5. While Codex/TraeX is idle, send a task followed by a correction in the same chat/thread within 400 ms. Both should share `turn/start`, and the reply should follow the correction.
6. While Codex/TraeX/Claude is thinking, send a correction; it should steer into the same turn.
7. In a direct chat, request a test message to another joined group. The agent should resolve its name, send through the dedicated tool, and confirm in the original chat.
