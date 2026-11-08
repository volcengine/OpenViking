# Windows native source preview

Vika can run natively on a trusted, single-user Windows workstation. This does
not port the separate OpenViking server: connect to an existing OV service.
The published Unix release and automatic release updater do not support Windows.

## Install and run

Requirements: Windows 10/11 x64, PowerShell 5.1+, Git, Node.js 24 LTS with npm,
and a configured model provider or supported agent runtime. Administrator rights,
WSL and Git Bash are not required. Pi and Durable use native PowerShell tools.
Standard npm `.cmd` shims resolve to their JavaScript entrypoints; other `.cmd`,
`.bat`, `.ps1` and `.vbs` direct runtime entries require an explicit interpreter.

From the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\vika\packages\daemon\windows\install-windows.ps1
# Open a new terminal after installation:
vika setup
vika start
vika status
vika logs primary --lines 50 --no-follow
vika stop
```

The installer copies source into `%USERPROFILE%\.vika\releases`, installs npm
dependencies, checks the bundled Pi and pinned Lark CLI, then creates a `current`
directory junction and launchers in `.vika\bin`. The Lark bootstrap handles npm
12 blocking dependency postinstall scripts without approving unrelated scripts.
It fails on download, executable or version errors. `-SkipSetup` omits the wizard;
`-SkipPathUpdate` leaves user and process PATH unchanged. Existing installations
are refused rather than overwritten.

`vika autostart enable` / `disable` manages login startup through the user's
Startup folder. Windows Script Host must be enabled; this is not a system service
and logging out may stop the processes. Slack requires an installed `slack.exe`
or `SLACK_CLI_BIN`. Email's POSIX permission checks are not supported in this
preview. Other connectors and runtimes require their own Windows validation.

## Update and troubleshoot

Stop the designated instance, close other Vika commands, remove only the `current`
junction (not its target or configuration), update the checkout, and rerun the
installer. Keep the previous release for rollback. Updating a checkout alone
does not update the running installation. Do not use the Unix installer or
`npm run setup` on Windows.

Use `vika status` for a bounded status check. `vika logs primary` follows logs;
use `--no-follow` for unattended queries. The installed `pm2.cmd` uses Vika's
private PM2 home and per-home named pipes. Do not invoke
`current/node_modules/.bin/pm2.cmd` directly or use broad PM2 selectors to repair
one instance. `pm2 jlist` may contain credentials; do not publish raw output.
PowerShell 7 needs `ConvertFrom-Json -AsHashtable` for PM2's mixed-case keys;
PowerShell 5.1 users can use Node's `JSON.parse` or `vika status`.

## Platform boundaries

- Runtime cleanup uses `taskkill /T /F` for the selected process tree.
- Action IPC uses Windows named pipes with authority checks and Windows default
  pipe access control. Multi-user/RDP isolation and pipe squatting are not
  certified. Localhost Web binding is not per-user authentication.
- Media staging requires a private current-user Windows ACL. Existing shared
  directories are rejected without changing their permissions.
- Structured file tools reject invalid Windows paths. Arbitrary PowerShell
  scripts are not a filesystem sandbox and can access NTFS alternate streams.
- OpenCode config isolation uses directory junctions and individual file copies.
  Copies refresh at runtime launch; restart to pick up later file edits.
  Original configuration is preserved, and copy/link failures abort preparation.
- Keep SQLite/state directories off network shares and OneDrive. Long CLI
  arguments remain subject to Windows command-line limits. Hard-kill UI recovery
  and complete connector/runtime parity are not established by this preview.

## Development checks

Windows components live in `packages/daemon/windows/`; shared adapters remain
in the normal runtime modules. From `vika/`, install test dependencies with
`npm ci --ignore-scripts`, then run the relevant `node --test` files listed in
[the Windows workflow](../../.github/workflows/vika-windows.yml). Keep native
process, named-pipe, ACL and runtime tests: Linux results cannot certify Windows
behavior. Run real-machine checks without elevation; hosted Windows CI does not
replace a normal user's installation, startup and connector checks.
