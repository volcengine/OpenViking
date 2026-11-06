# Native Windows feature coverage

Review baseline: `feat/vika` commit `40a26ec`. Reviewed Windows implementation:
`e26a9a62a529cec4c76b6cde99889b3486fecc2c` on `ovsawin`.
This review compares the Vika changes since the baseline and traces relevant
unchanged runtime, connector, configuration, and OpenViking client paths. It
is not a claim that every baseline feature has been exercised on Windows.
See [Windows installation instructions](../WINDOWS.md).

## Evidence and status definitions

[CI run 42199232146](https://github.com/openviking-superagent/OpenViking/actions/runs/42199232146)
completed successfully for the reviewed implementation: **25/25 native tests**,
**58/58 Feishu/OpenViking integration-contract tests**, CLI help, PowerShell
syntax checks, and the real source installer in an isolated Unicode Windows home.
The installer ran npm lifecycle scripts, checked Pi 1.4.0 and Lark CLI 1.0.94,
validated both launchers and the directory junction, and refused an existing
installation. These tests do not use live service credentials.

The native test step includes process invocation, named pipes, process-tree
termination, PowerShell quoting, startup files, isolated PM2 lifecycle, private
directory ACLs through Lark media/cache/file staging, and a Durable PowerShell
tool invocation. It does not authenticate to messaging or model providers.

- **Implemented / component-tested**: platform behavior is implemented and the
  relevant native test step passed; this does not establish a complete user flow.
- **Partial**: implementation exists, with an explicit dependency, reduced
  capability, or incomplete end-to-end validation.
- **Unverified**: baseline implementation remains, but Windows-specific business
  behavior has not been demonstrated.
- **Unsupported**: disabled or outside this port's scope.

Paths in the evidence column are relative to `vika/`.

## Feature matrix

| Feature | Windows status | Implementation and limits |
|---|---|---|
| Source installation and launchers | Implemented / installer smoke-tested | `scripts/install-windows.ps1`, `scripts/windows-launch.mjs`. Node 24, npm lifecycle installation, `current` junction and `vika.cmd`. `scripts/test-install-windows.ps1` exercises an isolated Unicode home, both launchers, installed Pi/Lark executables and refusal to overwrite an installation. No published Windows release artifact. |
| Interactive setup, Lark login and QR authorization | Partial; live flow unverified | `packages/cli/src/setup.mjs`, `lark-cli.mjs`, `qr.mjs`. Windows executable invocation and Explorer opening implemented. No real authorization or interactive wizard acceptance evidence. |
| PM2 start/stop/restart/status/logs | Implemented / component-tested | `packages/cli/src/control.mjs`, `scripts/pm2-windows.cjs`, `packages/updater/src/pm2.mjs`. Isolated pipes derived from PM2 home; adapter pinned to PM2 6.0.14. Release verification resolves junction/short-path aliases. Native fixture tests startup, readiness and graceful stop; full configured Vika lifecycle remains unverified. |
| Multiple agent instances | Partial | Existing configuration and per-instance action pipe paths retained. PM2 fixture is isolated; simultaneous real connector/runtime instances are not acceptance-tested. |
| Login autostart | Implemented / component-tested | `packages/cli/src/autostart.mjs` respects redirected `APPDATA`, writes Unicode Startup VBS. Tests create/remove files; actual logout/login is unverified. Requires Windows Script Host and user login; not a Windows service. |
| Release updater / automatic upgrades | Unsupported | `packages/cli/src/upgrade.mjs` and `control.mjs` reject Windows updater use. Manual stopped-installation update only. |
| Web activity console | Partial; business flow unverified | `packages/web/src/main.mjs` handles PM2 shutdown messages; existing HTTP/UI retained. No Windows browser or real configured-console acceptance test. |
| Action API and MCP transport | Implemented / component-tested | `packages/daemon/src/platform.mjs`, `actions/api.mjs`. Windows named pipes and runtime authority checks tested. Pipe DACL uses Windows defaults; not equivalent to a verified Unix 0600 isolation policy. Preview targets a trusted single-user workstation. |
| Executable discovery / npm shims / cancellation | Implemented / component-tested | `platform.mjs`, `process-utils.mjs`. PATH/PATHEXT normalization, direct Node execution of recognized npm shims, and `taskkill /T /F`. Nonstandard batch wrappers rejected. Cancellation is forceful. |
| Codex | Partial | `packages/daemon/src/codex.mjs` uses platform invocation; app-server/session/MCP logic retained. Actual Windows provider login, turns and recovery unverified. |
| TraeX | Partial | `packages/daemon/src/traex.mjs` inherits adapted Codex transport. Real Windows binary availability and app-server behavior unverified. |
| Claude | Partial | `packages/daemon/src/claude.mjs` uses platform invocation. Actual Windows provider sessions and MCP unverified. |
| OpenCode | Partial | `packages/daemon/src/opencode.mjs` uses platform invocation. Actual Windows ACP turns/recovery unverified. |
| Pi | Partial | `packages/daemon/src/pi-runtime.mjs`, `pi.mjs` add `--tools +powershell,-bash` on Windows, retaining extension tool selection. Argument selection tested; real Pi RPC plus PowerShell/MCP and restored-session tool selection unverified. |
| Durable | Partial; native shell component tested | `packages/daemon/src/coding-tools.mjs`, `durable-host.mjs` register read/write/edit/PowerShell on Windows. Native PowerShell invocation tested without using bash. Full model turn, durable restart/recovery and cancellation remain unverified. |
| Ark / Volc | Unverified; baseline limitations retained | `packages/daemon/src/volc.mjs` uses remote HTTP/SSE and an external Environment Worker. This does not port that worker to Windows. Baseline non-Lark connector restrictions in `agent-runtime.mjs` remain. |
| Feishu/Lark text, threads, reactions, history and document comments | Partial; real delivery unverified | `packages/daemon/src/lark-cli-bin.mjs`, `lark.mjs`. Uses npm-installed `lark-cli.exe`; connector and delivery rules retained. No authenticated long-connection/event-to-reply test. |
| Lark attachments, file staging and card image cache | Implemented local components; live uploads/downloads unverified | `private-temp.mjs`, `windows-acl.mjs`, `lark-media.mjs`, `lark-image-cache.mjs`, `actions/content.mjs`. Native tests cover ACL creation/refusal, media cleanup, image cache and outgoing file snapshots. No real Lark media API acceptance test. |
| Slack | Partial | `packages/daemon/src/slack/cli.mjs` expects external `slack.exe` on PATH or `SLACK_CLI_BIN`. Not bundled; real Socket Mode/read/write unverified. |
| Telegram | Unverified | Existing Node connector and worker IPC retained; no live Windows receiving/sending evidence. |
| Weixin | Unverified | Existing login/polling/media implementation retained; no live Windows acceptance evidence. |
| QQ | Unverified | Existing protocol and state implementation retained; no live Windows acceptance evidence. |
| Email | Unsupported | `packages/daemon/src/config.mjs` refuses Windows email account validation; setup hides email. Windows account-file ACL policy is not implemented. |
| Remote OpenViking configuration, authentication and user creation | Partial; real Windows service interaction unverified | `packages/cli/src/ov.mjs`, `ov-setup.mjs`; `packages/daemon/src/ov-connection.mjs`. Native paths and HTTP fetch retained. Mock protocol tests are not live service acceptance evidence. |
| OV session capture, archives, recall and timers | Unverified on Windows end-to-end | Existing `ov-session-context.mjs`, `ov-archive.mjs`, `ov-recall.mjs`, `ov-timers.mjs` retained. Restart, uncertain-write reconciliation and identity behavior have protocol tests, but no real Windows-to-OV validation in this workflow. |
| Runtime OV skills/MCP | Unverified | `packages/daemon/src/openviking.mjs` preserves `OPENVIKING_*`. This does not install or verify each runtime's external OV plugin/tool configuration. |
| Local OpenViking Python/native server | Outside this port's scope | This Windows change covers Vika. Connect to an existing OV server; no claim of a native local server installation. |
| Original POSIX development/release commands | Unsupported as a complete Windows workflow | Root Vika `npm run check` still uses POSIX shell loops; original setup/release helpers are not fully ported. Use the documented Windows installation/test path. |

## Resolved review findings and remaining boundaries

The original media-directory POSIX-mode rejection is replaced with a Windows
ACL implementation. Newly created dedicated directories receive a protected
current-user-only DACL; existing broad/shared directories are rejected without
changing their permissions. PowerShell 5.1 children rebuild their module search
path instead of inheriting incompatible PowerShell 7 module paths. Native tests
passed for these paths. A previously created preview temp directory or a manual
`VIKA_TEMP_DIR` may still be rejected if its ACL does not satisfy this policy.

Pi and Durable previously exposed bash by default. Both now select native
PowerShell tools on Windows. The old issue was primarily failure when invoking
the bash tool, not proof that runtime startup itself always failed. Existing
custom skills or commands can still require Unix tools; changing the runtime's
shell tool does not port those external assets.

Default configuration was checked for obvious path blockers: the launcher sets
HOME/PM2_HOME and normalizes PATH; daemon temp storage defaults to `dataDir/tmp`
and is first prepared through the private-directory helper; OV configuration
uses native path joins and HTTP; the selected Lark executable path matches the
pinned npm installer's Windows output. No additional deterministic blocker was
identified in that targeted static review. This does not replace execution.

## Evidence needed to accept Feishu + OpenViking

The native component tests establish useful infrastructure coverage. They do
not yet demonstrate the user's complete Feishu + remote OpenViking workflow.
Acceptance should record Windows, Node, runtime and Lark CLI versions and cover:

1. On the target workstation, finish interactive setup with an authorized
   Lark account and a reachable OV server; verify OV health and actual identity.
2. Start a configured daemon and one real runtime. Receive a Lark event, invoke
   the Core delivery tool and deliver text in the correct conversation/thread.
   Check that reconnect/restart does not duplicate the delivery.
3. Download a real attachment, send a file and send a card with a local image.
   Local ACL/cache tests alone cannot verify service upload or delivery semantics.
4. Observe the conversation captured under the expected OV identity, restart
   Vika and recover history, then demonstrate recall and `/new` archival. Exercise
   an OV outage/recovery to check uncertain writes are reconciled once.
5. For Pi or Durable, execute a native PowerShell command while Core tools remain
   available. For Pi, include a restored session. Verify stop/start and actual
   login autostart separately from creating the Startup file.

Use disposable fixtures and authorized test accounts. A successful path with
one runtime establishes that combination, not all runtimes/connectors or full
baseline parity. Email, automatic upgrades and the local OV server limitation
remain even after this acceptance path passes.
