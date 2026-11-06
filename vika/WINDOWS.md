# Windows native source preview

This branch adds native Windows support for the SuperAgent (`vika/`). It does
not port the separate OpenViking server's Python/native dependencies. Connect
to an existing OpenViking server if local server installation is unavailable.
No WSL, Git Bash, or Unix shell is required for the steps below.

Prerequisites: Windows 10/11, PowerShell 5.1 or later, Git, Node.js 24 LTS
(including npm). Install and log in to a supported agent runtime, or use the
bundled Pi runtime with provider credentials. Prefer native `.exe` binaries;
standard npm `.cmd` wrappers are resolved to their JavaScript entrypoints and
run with Node, without passing prompts through a command shell.

```powershell
git clone --branch ovsawin https://github.com/openviking-superagent/OpenViking.git
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
wizard. No Administrator permission is needed. Login startup uses the user's
Startup folder (Windows Script Host must be enabled), not a system service;
logging out may stop the processes.

PM2 is invoked through its Node entrypoint and uses a dedicated `.vika\pm2`
directory. Runtime termination uses `taskkill /T /F` on Windows, which forcefully
stops the selected process tree. Action API connections use Windows named pipes
and retain runtime authority checks; Unix socket permissions are not applied to
pipes. Windows provides the pipe's default access control, so this preview is
intended for a trusted single-user workstation.

The Lark CLI comes from its npm package's Windows installation. Slack's CLI is
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

```powershell
cd vika
npm ci --ignore-scripts
node --test packages/daemon/test/platform.test.mjs packages/daemon/test/windows-action-api.test.mjs packages/daemon/test/windows-process.test.mjs packages/daemon/test/windows-shell.test.mjs packages/cli/test/windows-autostart.test.mjs
```

The Windows CI job tests pipe transport, platform invocation, and autostart file
creation/removal. It does not log in to external services or claim to validate
interactive installation or real connector deliveries. Linux unit tests alone
are not proof that the full Windows workflow works.
