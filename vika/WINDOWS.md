# Windows native source preview

This branch adds native Windows support for the SuperAgent (`vika/`). It does
not port the separate OpenViking server's Python/native dependencies. Connect
to an existing OpenViking server if local server installation is unavailable.
No WSL, Git Bash, or Unix shell is required for the steps below.

Prerequisites: Windows 10/11 x64, PowerShell 5.1 or later, Git, Node.js 24 LTS
(including npm). Install and log in to a supported agent runtime, or use the
bundled Pi runtime with provider credentials. Prefer native `.exe` binaries;
Pi and Durable use their native PowerShell tools on Windows. Standard npm `.cmd` wrappers are resolved to their JavaScript entrypoints and
run with Node, without passing prompts through a command shell.

```powershell
git clone --branch vika-win https://github.com/openviking-superagent/OpenViking.git
cd OpenViking
powershell -NoProfile -ExecutionPolicy Bypass -File .\vika\scripts\install-windows.ps1
# Open a new terminal after installation:
vika setup
vika start
vika status
vika logs primary
vika stop
vika autostart enable
vika autostart disable
```

The installer copies source into `%USERPROFILE%\.vika\releases`, installs npm
dependencies there and creates a `current` directory junction. Existing
installations are refused; the installer never removes data/configuration or
replaces a running installation. `-SkipSetup` installs without the interactive
wizard. `-SkipPathUpdate` also leaves both user and process PATH untouched
(useful for isolated installations and CI). No Administrator permission is
needed. Login startup uses the user's
Startup folder (Windows Script Host must be enabled), not a system service;
logging out may stop the processes.

PM2 is invoked through its Node entrypoint and uses a dedicated `.vika\pm2`
directory and per-installation named pipes. Runtime termination uses `taskkill /T /F` on Windows, which forcefully
stops the selected process tree. Action API connections use Windows named pipes
and retain runtime authority checks; Unix socket permissions are not applied to
pipes. Media staging directories use a protected current-user-only Windows ACL; pre-existing shared directories are rejected without changing their permissions. Windows provides the pipe's default access control, so this preview is
intended for a trusted single-user workstation.

The installer explicitly ensures the Lark CLI from the pinned npm package is
present even when npm 12 blocks dependency postinstall scripts. It invokes that
package's checksum-verifying installer only if the `.exe` is missing, then
runs the actual `.exe --version` and verifies its version before creating the
`current` junction. Download, execution, or version errors stop installation;
it does not approve unrelated npm scripts. Slack's CLI is
not bundled for Windows: install the official `slack.exe` on PATH or configure
`SLACK_CLI_BIN`. Email accounts still require POSIX mode-0600 checks and are not supported in this
Windows preview; do not add the email connector. Other connector integrations
and runtimes need end-to-end
validation on Windows. Nonstandard `.cmd`/`.bat` wrappers are rejected with an
explicit error; configure a native executable or JavaScript entrypoint instead.

Automatic release upgrades are disabled on Windows because published Vika
artifacts are currently Unix-only. To update: `vika stop`, close other Vika
commands, remove only the `current` junction (not its target or configuration),
update your checkout, and run the installer again. Keep the previous release
for rollback. Do not run the Unix installer or `npm run setup` on Windows.

Validation from a checkout:

Run checkout validation in a normal, non-elevated PowerShell session. GitHub-hosted
Windows runners run as administrators with UAC disabled, so passing CI does not
replace validation under a non-elevated developer token. Investigate permission
failures in the fixture or product code instead of rerunning as administrator to
bypass them. See the [GitHub-hosted runners reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#administrative-privileges),
GitHub Docs.

Confirm the session is not elevated first:

```powershell
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$p = [Security.Principal.WindowsPrincipal]::new($id)
if ($p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run validation in a non-elevated PowerShell session.'
}
```

Then install dependencies and run the native test files:

```powershell
cd vika
npm ci --ignore-scripts
node --test packages/daemon/test/platform.test.mjs packages/daemon/test/windows-action-api.test.mjs packages/daemon/test/windows-process.test.mjs packages/daemon/test/windows-shell.test.mjs packages/cli/test/windows-autostart.test.mjs packages/cli/test/windows-lark-install.test.mjs packages/cli/test/windows-pm2.test.mjs packages/daemon/test/windows-acl.test.mjs packages/daemon/test/windows-coding-tools.test.mjs
```

The Windows CI job tests pipe transport, platform invocation, and autostart file
creation/removal. It also runs the real PowerShell installer in a disposable
Unicode home with npm lifecycle scripts deliberately disabled, checks the
installed Pi and Lark executables, tests both generated launchers, and verifies
that an existing installation is refused. The matrix covers both the bundled
npm and npm 12. It does not log in to external services or validate
interactive setup or real connector deliveries. Linux unit tests alone
are not proof that the full Windows workflow works.

Review scope and known differences: [feature parity](docs/windows-feature-parity.md) and [native audit](docs/windows-native-review.md). The feature-parity report includes a real-account Feishu + OpenViking acceptance checklist for Windows testers.


### Installed source preview: PM2 diagnostics

Use `vika status` for a bounded status check. `vika logs <instance-id>` follows
logs continuously; it is intended for an interactive terminal, not an unattended
agent command. For a bounded log snapshot, use the installed PM2 shim:

```powershell
pm2 logs vika-primary --nostream --lines 50
```

PM2 JSON can contain keys that differ only in case (for example `username` and
`USERNAME`). On PowerShell 7, parse it with `ConvertFrom-Json -AsHashtable`;
default `ConvertFrom-Json` creates case-insensitive properties and can reject
such output. For Windows PowerShell 5.1, which lacks `-AsHashtable`, use a
case-sensitive JSON parser such as Node's `JSON.parse`, or use `vika status`
when only application status is needed. Do not rewrite all environment keys
to uppercase: PM2 metadata and child-process environment are distinct fields,
and normalizing the launch environment does not guarantee unique JSON keys.
The source installer also places `pm2.cmd` in `~/.vika/bin`. It uses the same
Node executable as `vika.cmd`, the release's `scripts/pm2-windows.cjs` wrapper,
and Vika's private PM2_HOME (or an explicit PM2_HOME override). It does not add
all of node_modules/.bin to PATH. Open a new terminal after installation.

Do not invoke `current/node_modules/.bin/pm2.cmd` directly: it bypasses the
Windows per-home pipe adapter and may target a different PM2 daemon. Do not
use `pm2 kill` or broad application selectors to troubleshoot a single instance.
`pm2 jlist` can include environment credentials; never publish its raw output.
Existing installations need the updated installer-generated launchers as well
as updated source; a Git checkout change alone does not update installed files.
This compatibility entry is fork installation support, not part of the minimal
upstream Windows adapter PR.

### Review scope and remaining native gates

See [review resolution and acceptance handoff](docs/windows-review-resolution.md)
for C-1–C-3 and W-1–W-12 dispositions. The upstream minimal preview and this
installed fork have different startup, PM2, ACL and shell-tool contracts.

Windows has a finite command-line length. Durable host communication uses stdin,
but long Lark markdown passed to the CLI still needs native validation; argv-based
prompts in other runtimes can also exceed the limit. File attachments are an
alternative for large documents, not proof of long-text parity.

Action-pipe names are deterministic and use Windows default pipe access control.
Multi-user/RDP isolation, pipe squatting, orphan cleanup after leader crashes
and PID-reuse behavior remain unverified acceptance gates. The exited-child
guard only prevents taskkill for an already-exited leader. Do not deploy on an
untrusted shared workstation. Localhost Web binding is not per-user
authentication. Keep state/SQLite directories off OneDrive and network shares.

### OpenCode configuration isolation

On non-elevated Windows, the isolated OpenCode configuration uses directory junctions and copies individual non-OpenViking files. File copies refresh when the runtime launches; edits to source files during a running session require a restart. The source configuration is not rewritten. A junction or copy failure aborts preparation instead of silently omitting unrelated plugins. This does not require switching a Durable instance to OpenCode.
