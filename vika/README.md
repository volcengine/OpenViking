# OpenViking SuperAgent

**Bring Your Own Machine. Run your own agent.**

Set up your machine (local or a cloud VM) with the software, projects, and tools you need. OpenViking SuperAgent (Vika) connects your agent to OpenViking and instant messaging: send a task in chat, let your agent work on your machine, and receive the results in the same conversation. OpenViking provides knowledge, memory, and context across sessions.

Vika supports runtimes such as Codex, Claude Code, DeepSeek Harness, and Pi, and connects to Feishu / Lark, WeChat, Telegram, Slack, QQ (an official bot, or a personal account through OneBot), and email. The CLI command is `vika`.

[Quick start](install.md) · [Features and architecture](docs/usage-reference.md) · [Configuration example](config.example.json) · [Contributing](../CONTRIBUTING.md)

## Why Vika

- **Bring Your Own Machine.** Your agent works with your software and projects, connected to OV and IM through Vika.
- **Use your agent.** Connect an authenticated local coding agent or use the bundled Pi / Durable runtime. See [runtime setup](docs/installation-reference.md#prerequisites).
- **Keep context across sessions.** OpenViking stores session history for new sessions and runtime switches, and provides long-term memory and resource search. See [session context](docs/ov-session-context.md).
- **Connect multiple channels.** One instance can serve several chat channels; separate instances have their own workspaces, data directories, and OV configuration. See [multiple instances](docs/installation-reference.md#multiple-agents).

## Quick start

You need a configured model runtime and **an OpenViking service (required; local or remote)**. Choose the command for your system; both installers open the same `vika setup` on first interactive installation.

**macOS / Linux / WSL2 (x64 or arm64):**

```bash
curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/vika/install.sh | sh
```

This installs the latest release (currently `0.5.0-preview`). To pin a version, use `curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/vika/releases/<version>/install.sh | sh -s -- --version <version>`.

**Windows 10/11 (x64, preview), in PowerShell 5.1 or 7:**

```powershell
irm https://ovrelease.tos-cn-beijing.volces.com/vika/install.ps1 | iex
```

The package includes Node, PM2, chat CLIs, Pi, and Durable. On first installation, `vika setup` connects OV, selects a runtime and workspace, and authorizes your chat channels. You do not need to install lark-cli globally. For Codex or Claude Code, install and sign in to that CLI first. DeepSeek Harness is not bundled either: install `dsh` **0.2.x** (0.2.2-rc.1 or later) on Node 24.9+, then manage its model routes with `vika dsh model`.

Once configured, send the bot a task in a direct chat:

> Inspect the project in your workspace, explain how to start it, and check whether its tests pass.

See the [installation guide](install.md) for details.

## Everyday use

Send `/help` in a direct chat for commands, `/status` for status, `/model` and `/effort` to inspect or change model settings, and `/new` to start a new session. In your terminal:

```bash
vika setup           # Add or edit an instance
vika status
vika logs primary
```

For upgrades, see the [platform-specific instructions](docs/installation-reference.md#upgrade-and-rollback).

The console at `http://127.0.0.1:3737` shows message processing and tool activity.

**Connect trusted chats only.** The agent can operate on its configured workspace, and chats served by one instance share runtime context. Feishu setup asks whether to authorize searching and reading messages as your user; review the scope before granting access. See [message access](docs/usage-reference.md#message-access).

## Documentation

The release-process guide is currently in Chinese.

| Topic | Guide |
|---|---|
| Installation, model sign-in, first connection | [Installation](install.md) |
| Chat channels, email, attachments, and cards | [Feature reference](docs/usage-reference.md#chat-channels-and-email) |
| QQ groups through a personal account (OneBot) | [OneBot Connector](docs/onebot-connector.md) |
| Permissions, sign-in, upgrades, rollback, and development | [Configuration and operations](docs/installation-reference.md) |
| Context archival and recovery | [OV session context](docs/ov-session-context.md) |
| Adding message sources | [Connector protocol](docs/connector-protocol.md) |
| Durable models and recovery | [Durable runtime](docs/pi-durable-runtime.md) |
| DSH model routes, credentials, and limits | [DSH runtime](docs/dsh-runtime.md) |
| Reliability and acceptance checks | [Reliability limits](docs/usage-reference.md#reliability-limits) · [Feishu acceptance](docs/usage-reference.md#feishu-acceptance-checks) |

Features in the source tree may not yet be published. Check your installed release.
