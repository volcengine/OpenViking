# OpenViking SuperAgent

**Bring Your Own Machine. Run your own agent.**

Set up your machine (local or a cloud VM) with the software, projects, and tools you need. OpenViking SuperAgent (Vika) connects your agent to OpenViking and instant messaging: send a task in chat, let your agent work on your machine, and receive the results in the same conversation. OpenViking provides knowledge, memory, and context across sessions.

Vika supports runtimes such as Codex, Claude Code, and Pi, and connects to Feishu / Lark, WeChat, Telegram, Slack, QQ, and email. The CLI command is `vika`.

[Quick start](install.md) · [Windows source preview](docs/windows.md) · [Features and architecture](docs/usage-reference.md) · [Configuration example](config.example.json) · [Contributing](../CONTRIBUTING.md)

## Why Vika

- **Bring Your Own Machine.** Your agent works with your software and projects, connected to OV and IM through Vika.
- **Use your agent.** Connect an authenticated local coding agent or use the bundled Pi / Durable runtime. See [runtime setup](docs/installation-reference.md#prerequisites).
- **Keep context across sessions.** OpenViking stores session history for new sessions and runtime switches, and provides long-term memory and resource search. See [session context](docs/ov-session-context.md).
- **Connect multiple channels.** One instance can serve several chat channels; separate instances have their own workspaces, data directories, and OV configuration. See [multiple instances](docs/installation-reference.md#multiple-agents).

## Quick start

You need macOS or Linux, a configured model runtime, and **an OpenViking service (required; local or remote)**. Use WSL2 on Windows with this published release.

```bash
curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/vika/releases/0.4.0-24e73d5a3f7a/install.sh | sh -s -- --version 0.4.0-24e73d5a3f7a
```

This command installs a pinned release. The unversioned installer and `latest` endpoint are not currently available.

The package includes Node, PM2, chat CLIs, Pi, and Durable. On first installation, `vika setup` connects OV, selects a runtime and workspace, and authorizes your chat channels. You do not need to install lark-cli globally. For Codex or Claude Code, install and sign in to that CLI first.

Once configured, send the bot a task in a direct chat:

> Inspect the project in your workspace, explain how to start it, and check whether its tests pass.

See the [installation guide](install.md) for details.

## Everyday use

Send `/help` in a direct chat for commands, `/status` for status, `/model` and `/effort` to inspect or change model settings, and `/new` to start a new session. In your terminal:

```bash
vika setup           # Add or edit an instance
vika status
vika logs primary
vika upgrade primary
```

The console at `http://127.0.0.1:3737` shows message processing and tool activity.

**Connect trusted chats only.** The agent can operate on its configured workspace, and chats served by one instance share runtime context. Feishu setup asks whether to authorize searching and reading messages as your user; review the scope before granting access. See [message access](docs/usage-reference.md#message-access).

## Documentation

The release-process guide is currently in Chinese.

| Topic | Guide |
|---|---|
| Installation, model sign-in, first connection | [Installation](install.md) |
| Chat channels, email, attachments, and cards | [Feature reference](docs/usage-reference.md#chat-channels-and-email) |
| Permissions, sign-in, upgrades, rollback, and development | [Configuration and operations](docs/installation-reference.md) |
| Context archival and recovery | [OV session context](docs/ov-session-context.md) |
| Adding message sources | [Connector protocol](docs/connector-protocol.md) |
| Durable models and recovery | [Durable runtime](docs/pi-durable-runtime.md) |
| Reliability and acceptance checks | [Reliability limits](docs/usage-reference.md#reliability-limits) · [Feishu acceptance](docs/usage-reference.md#feishu-acceptance-checks) |

Features in the source tree may not yet be published. Check your installed release.
