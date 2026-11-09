# Install OpenViking SuperAgent

Install Vika and verify that it can receive and answer messages. See the [README](README.md) for an overview and the [configuration reference](docs/installation-reference.md) for advanced setup. The CLI command is `vika`.

## Prerequisites

- **System:** macOS / Linux / WSL2 (x64 or arm64), or Windows 10/11 (x64, preview). macOS / Linux needs sh, curl, tar, and sha256sum (or shasum); musl / Alpine is unsupported. Native Windows needs PowerShell 5.1 or 7 and a normal user account, without administrator rights. Node, npm, PM2, and lark-cli are bundled.
- **Windows runtimes:** Durable uses native PowerShell; Pi also requires Git Bash. Slack needs a separately installed CLI configured with `SLACK_CLI_BIN`.
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
| Antigravity | Install the `agy` CLI and sign in; tested with 1.5.3 |
| DeepSeek Harness | Install `dsh` **0.2.x**, 0.2.2-rc.1 or later, on Node 24.9+; model routes and credentials are managed with `vika dsh model` |

See [runtime configuration](docs/installation-reference.md#prerequisites) for models, version detection, and environment variables. Do not put API keys in `config.json` or commit them to a repository.

DeepSeek Harness has no separate sign-in. Its model routes and credentials belong to the instance's own DSH settings document, `<dataDir>/dsh/home/profiles/sdk/cordis.patch.yml`, with the key values in `<dataDir>/dsh/home/.credentials.yaml` beside it; `vika dsh model` adds, removes and imports routes, repairs an unreadable settings document, and stores or removes keys, all through DSH's own services, and the isolated DSH home never reads routes or credentials from your `~/.dsh`. Only the built-in `deepseek-official` route needs `DEEPSEEK_API_KEY`. `dsh` is found on PATH or named by `DSH_BIN`: like every other `*_BIN`, a bare command name also goes through PATH and a path is used as-is, and the value may come from the manifest `env` or from the shell (setup reads the manifest copy too). `VIKA_DSH_EFFORT` sets the effort a new instance starts with and is read from the process environment only. See [DSH runtime](docs/dsh-runtime.md).

## Install

**macOS / Linux / WSL2:**

```bash
curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/vika/install.sh | sh
```

This installs the version in `vika/latest` (currently `0.5.0-preview`). To pin a version, use `curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/vika/releases/<version>/install.sh | sh -s -- --version <version>`.

**Windows (PowerShell):**

```powershell
irm https://ovrelease.tos-cn-beijing.volces.com/vika/install.ps1 | iex
```

This downloads the latest Windows x64 preview. No separate ZIP or setup wrapper is needed. See [installer options](docs/installation-reference.md#one-command-installation) to pin a Windows version.

Both installers verify the downloaded archive, install it under `~/.vika/releases/`, and open `vika setup` on a first interactive installation. Follow the same setup steps below on either platform. If your shell cannot find `vika`, reopen the terminal. On macOS / Linux you can also use `~/.vika/bin/vika`; on Windows the launcher is `bin\vika.cmd` under the Vika home (`$env:HOME` when set, otherwise `$env:USERPROFILE`).

```bash
vika setup
```

Follow the wizard:

1. **Connect OV.** Select a detected configuration or enter the local / remote address and credentials. Continue after validation succeeds.
2. **Configure the agent.** Set its name and workspace, then choose an available runtime. The first instance ID is `primary`.
3. **Connect chat.** Select and authorize a channel. With multiple IM channels, choose the primary one for proactive notifications. Email requires an IM channel alongside it.
4. **Verify delivery.** Saving starts the service. The Feishu bot sends a greeting; reply to confirm that incoming messages work too.

For Feishu, scan a QR code to create or select an app, or enter its App ID and App Secret manually. An existing app must use a long connection and must not also be monitored by another bot service. Setup asks whether to allow reading messages as your user; review [message access](docs/usage-reference.md#message-access) before authorizing.

Other channel setup: [WeChat](docs/installation-reference.md#wechat-setup) · [QQ](docs/installation-reference.md#qq-setup) · [Telegram](docs/installation-reference.md#telegram-setup) · [Slack](docs/installation-reference.md#slack-setup) · [QQ over OneBot](docs/installation-reference.md#onebot-setup).

## Verify and manage

```bash
vika status
vika logs primary
vika restart primary
```

Open `http://127.0.0.1:3737` to view activity. Send a specific task in a direct chat and verify both the reply and the workspace result. An online process alone does not prove that messaging works.

- Add or edit instances and channels, or remove an instance: `vika setup`.
- Renew expired authentication: `vika login <channel> [instance]`.
- Upgrade on macOS / Linux: `vika upgrade primary`. On Windows, stop Vika, rerun the PowerShell installer, then run `vika restart`; automatic upgrades are not supported yet. See [upgrade and rollback](docs/installation-reference.md#upgrade-and-rollback).
- Enable startup at login: `vika autostart enable`. On Linux, use `loginctl enable-linger` if the service must continue after logout; on Windows it adds a per-user Startup-folder entry, with no service or administrator.
- Change the interface language: `vika --lang zh` or `vika --lang en`.

Configuration defaults to `~/.vika/config.json`. For a custom location, set the same absolute `VIKA_CONFIG` path for related commands. Editing a source checkout does not update an installed service.

## Troubleshooting

| Symptom | Check |
|---|---|
| OV validation fails | Address, connectivity, and credentials; private services must be reachable from the machine running Vika |
| No runtime is available | CLI authentication, or configure the bundled model with `vika pi` |
| Antigravity is not offered | `agy models` works for this user, and `AGY_BIN` points at the Antigravity CLI |
| DeepSeek Harness is not offered, or a route is missing from `/model` | `dsh --version` is 0.2.x on Node 24.9+ and `DSH_BIN` points at it; `vika dsh model list` names unready routes and why |
| Feishu receives no messages | Long connection, message events, app publication / approval, and competing bot services |
| Messages work but tools fail with permission errors | App permissions and user authorization; see [manual Feishu setup](docs/installation-reference.md#manual-feishu-setup) |
| Startup or attachment operations fail | `vika logs primary`; see [attachment temporary directories](docs/installation-reference.md#attachment-temporary-directories) |

The release-process guide below is currently in Chinese.

[Source development](docs/installation-reference.md#running-from-source) · [Upgrade and rollback](docs/installation-reference.md#upgrade-and-rollback) · [Multiple instances and memory](docs/installation-reference.md#multiple-agents) · [Release process](../docs/repository/RELEASE_CN.md)
