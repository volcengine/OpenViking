# Install OpenViking SuperAgent

Install Vika and verify that it can receive and answer messages. See the [README](README.md) for an overview and the [configuration reference](docs/installation-reference.md) for advanced setup. The CLI command is `vika`.

## Prerequisites

- **System:** macOS or Linux, x64 / arm64. On Windows, use WSL2 for this release. musl / Alpine is not supported. You need sh, curl, tar, and sha256sum (or shasum); Node, npm, PM2, and lark-cli need not be installed beforehand.
- **OpenViking:** a reachable OV service is required, either local or remote. Have its address and required credentials ready, or import an existing configuration during setup. There is no skip option.
- **Runtime:** choose one below and prepare its model authentication or API configuration.
- **Chat account:** Feishu requires a phone-registered or enterprise account; signing in to Feishu through a Doubao account is not supported for this integration. For other channels, prepare the bot or app credentials requested by setup.

| Runtime | Preparation |
|---|---|
| Codex | Install Codex CLI and sign in |
| Claude Code | Install version 2.1.259 or later and sign in |
| TraeX | Install and sign in to a version supporting `app-server` |
| Pi / Durable | Bundled; reuses model configuration from `~/.pi/agent`. Without existing configuration, run `vika pi` after installation and use `/login` |
| OpenCode | Install and sign in to **1.x**, version 1.15.7 or later; this release does not support 2.x |

See [runtime configuration](docs/installation-reference.md#prerequisites) for models, version detection, and environment variables. Do not put API keys in `config.json` or commit them to a repository.

## Install

```bash
curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/vika/releases/0.4.0-24e73d5a3f7a/install.sh | sh -s -- --version 0.4.0-24e73d5a3f7a
```

This installs the specified release, not a floating latest version. The unversioned `vika/install.sh` and `vika/latest` endpoints are not currently available. Omitting `--version` makes the installer look up `vika/latest`, so keep the version argument with this command.

The installer verifies the downloaded archive and installs it under `~/.vika/releases/`. A first installation in an interactive terminal opens the setup wizard. If your shell cannot find `vika`, reopen the terminal or use `~/.vika/bin/vika`.

```bash
vika setup
```

Follow the wizard:

1. **Connect OV.** Select a detected configuration or enter the local / remote address and credentials. Continue after validation succeeds.
2. **Configure the agent.** Set its name and workspace, then choose an available runtime. The first instance ID is `primary`.
3. **Connect chat.** Select and authorize a channel. With multiple IM channels, choose the primary one for proactive notifications. Email requires an IM channel alongside it.
4. **Verify delivery.** Saving starts the service. The Feishu bot sends a greeting; reply to confirm that incoming messages work too.

For Feishu, scan a QR code to create or select an app, or enter its App ID and App Secret manually. An existing app must use a long connection and must not also be monitored by another bot service. Setup asks whether to allow reading messages as your user; review [message access](docs/usage-reference.md#message-access) before authorizing.

Other channel setup: [WeChat](docs/installation-reference.md#wechat-setup) · [QQ](docs/installation-reference.md#qq-setup) · [Telegram](docs/installation-reference.md#telegram-setup) · [Slack](docs/installation-reference.md#slack-setup).

## Verify and manage

```bash
vika status
vika logs primary
vika restart primary
```

Open `http://127.0.0.1:3737` to view activity. Send a specific task in a direct chat and verify both the reply and the workspace result. An online process alone does not prove that messaging works.

- Add or edit instances and channels, or remove an instance: `vika setup`.
- Renew expired authentication: `vika login <channel> [instance]`.
- Upgrade to a published release: `vika upgrade primary`.
- Enable startup at login: `vika autostart enable`. On Linux, use `loginctl enable-linger` if the service must continue after logout.
- Change the interface language: `vika --lang zh` or `vika --lang en`.

Configuration defaults to `~/.vika/config.json`. For a custom location, set the same absolute `VIKA_CONFIG` path for related commands. Editing a source checkout does not update an installed service.

## Troubleshooting

| Symptom | Check |
|---|---|
| OV validation fails | Address, connectivity, and credentials; private services must be reachable from the machine running Vika |
| No runtime is available | CLI authentication, or configure the bundled model with `vika pi` |
| Feishu receives no messages | Long connection, message events, app publication / approval, and competing bot services |
| Messages work but tools fail with permission errors | App permissions and user authorization; see [manual Feishu setup](docs/installation-reference.md#manual-feishu-setup) |
| Startup or attachment operations fail | `vika logs primary`; see [attachment temporary directories](docs/installation-reference.md#attachment-temporary-directories) |

The release-process guide below is currently in Chinese.

[Source development](docs/installation-reference.md#running-from-source) · [Upgrade and rollback](docs/installation-reference.md#upgrade-and-rollback) · [Multiple instances and memory](docs/installation-reference.md#multiple-agents) · [Release process](../docs/repository/RELEASE_CN.md)
