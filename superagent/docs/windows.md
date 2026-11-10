# Windows source development and validation

For a normal installation, use the Windows one-line command in the shared
[installation guide](../install.md). For offline packages and release validation,
see [Windows package maintenance](windows-preview.md). The instructions below
are for developers running a source checkout.

This is a source-only preview for a trusted, single-user Windows 10/11 x64
workstation. It does not install the separate OpenViking server; configure an
existing OpenViking service. Native Windows and macOS validation of this
refactoring is still pending. Earlier Windows acceptance results for the full
fork do not certify this version.

## Prerequisites and startup

Install Node.js 24 LTS (minimum 22.19.0), npm, PM2 and Git yourself.
Ensure `node`, `npm` and `pm2` are on PATH. Durable uses the native `powershell`
tool: `pwsh` (PowerShell 7) first, then `powershell` (Windows PowerShell 5.1)
if the executable cannot start. At least one must be on the service PATH.
Durable does not require Git Bash. Pi still requires Git Bash; this change
does not replace the shell of other runtimes. Configure and
log in to the model runtime you intend to use. No administrator rights are
required by Vika, but Windows policy may restrict junctions or process management.

From the checkout's `vika` directory, in PowerShell:

```powershell
npm ci --ignore-scripts
node scripts/install-lark-cli.mjs
npm run check:pi
npm run check:durable
$env:HOME = $env:USERPROFILE
$env:PM2_HOME = Join-Path $env:USERPROFILE '.vika\pm2'
$env:VIKA_CONFIG = Join-Path $env:USERPROFILE '.vika\windows-preview.json'
```

Create that manifest using [config.example.json](../config.example.json), keeping
only the `primary` instance initially. Replace all sample paths with absolute
Windows paths, set its OpenViking configuration, runtime and authenticated
connector profile. JSON paths can use forward slashes, such as `C:/Users/name/work`.
Keep configuration and data local, outside OneDrive or network shares. Do not
replace an existing working installation's configuration while evaluating this preview.

Launch from the same terminal after configuring the manifest:

```powershell
pm2 start ecosystem.config.cjs --only vika-primary,vika-web
pm2 describe vika-primary
pm2 logs vika-primary --lines 50 --nostream
pm2 stop vika-primary vika-web
```

PM2 is the user-installed, unmodified program. Vika does not patch its Windows
pipes or promise isolation between multiple PM2 homes. Use only the named apps;
never stop unrelated PM2 applications. These commands start the source checkout.
The release-oriented `vika start` expects an installed `current` release and is
not the source startup procedure. Updating a checkout requires restarting these
apps; it does not update a separate installed release.

## Supported scope and limits

- The adapter handles npm Node shims, environment key casing, process-tree
  termination, action named pipes and OpenCode/Antigravity directory junctions/file copies.
  File copies refresh when the runtime starts. Runtime and connector parity
  still requires real-machine testing.
- Codex sends developer instructions through `thread/start` and `thread/resume`
  RPC. Claude uses its documented `--append-system-prompt-file` option; install
  a Claude version supporting that option. Pi/OpenCode/Antigravity already use files or RPC.
  TraeX's corresponding RPC support is unverified, so its instructions still
  use argv and remain subject to the Windows command-line limit. Antigravity
  has not been run on a real Windows machine; its token and sessions are
  reached through a directory junction.
- Pipe names contain a random per-process component and a user/instance digest.
  An occupied pipe must fail startup; authority tokens guard action calls.
  This is **not pipe-owner SID verification**. Multi-user/RDP isolation is not
  established, and localhost Web access is not per-user authentication.
- Vika does not provision or verify Windows ACLs. Use private user-owned data,
  workspace and temporary directories. No filesystem sandbox is claimed.
- Upgrade/updater and stable Windows release builds are disabled. Autostart is
  available at the user level: `vika autostart enable` writes a script into the
  current user's own Startup folder, which the shell runs at logon. It is not a
  service, needs no administrator, and shows up in Task Manager's Startup tab.
- Email is enabled. On Windows the email account file's mode is not checked
  (Windows reports 0666 regardless of ACLs); keep account files in a private,
  user-owned directory.
  The separate preview Action provides a ZIP and local installer, but no stable
  release and no system-wide service.
  Slack has no Windows bundled/default
  executable; it requires an explicit `SLACK_CLI_BIN` and separate validation.

## Migration and validation

This replaces the full Windows fork's installation approach. That fork's
installer, its `.vika/bin` launchers and its patched `pm2.cmd` are no longer
provided; the preview package's installer adds only a `vika.cmd` shim that runs
this release's CLI, and the release's own `bin\pm2.cmd`, which runs that release's
Node on its bundled pm2, so `vika start` uses no system Node or PM2. Keep the old
checkout/commit and configuration for rollback; do not copy this tree over a
running installation. The previous logs options and unrelated lock, logging,
delivery, interruption and PM2 behavior fixes were split out for separate review.

From `vika`, run `npm test` and `npm run check` in your usual terminal. The syntax
check runs JavaScript through Node on every platform; POSIX shell scripts are
checked with `sh -n` on POSIX hosts. Platform isolation and POSIX configuration snapshots
run in the normal suite; macOS configuration selection on Linux is not a macOS
runtime test. On Windows, test normal-user startup/stop, descendant cleanup,
occupied pipes, long instructions and each connector/runtime you actually use.

Report issues with the commit, Windows/Node/npm/PM2/PowerShell versions, selected
runtime and connector, reproduction steps and sanitized error text. Do not post
raw configuration, credentials, `pm2 jlist` output or conversation contents.

### Durable native shell acceptance

From `vika`, run `node --test packages/daemon/test/durable-coding-tools.test.mjs`.
The Windows cases execute the native shell with an invalid Bash path and also
force the Windows PowerShell fallback. On other systems these cases are skipped.
For real acceptance, start a fresh Durable session with Git Bash unavailable to
its process, confirm that `powershell` (not `bash`) is registered, and check
`$PSVersionTable.PSVersion`, Chinese/space paths, native exit codes, timeout and
cancellation of a harmless child process. Repeat with only Windows PowerShell
available. Verify Lark and OV after a normal restart. Do not remove Git or alter
the global PATH to run these checks; use a dedicated test process environment.
