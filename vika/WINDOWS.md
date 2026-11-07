# Windows source preview

This PR adds platform adapters for a Windows x64 source checkout. It does not
ship a Windows installer, release package, CI certification, or automatic
upgrade. Real-machine acceptance is separate. Linux/macOS retain their existing
installation and process behavior.

Install Node.js 24 LTS, Git for Windows (including Git Bash), and PM2 yourself.
Git Bash is required by the bundled Pi/Durable coding tools and POSIX command
examples in agent prompts. Install/login to other desired agent runtimes too.
Use an existing OpenViking server; this preview does not port its native server.
Keep the checkout and data under your own Windows account, with private NTFS
permissions. Windows skips Unix mode-bit checks; it does not create/verify ACLs.

From PowerShell in the checkout's `vika` directory:

```powershell
$env:HOME = $env:USERPROFILE
npm ci --ignore-scripts --no-audit --no-fund
node scripts/install-lark-cli.mjs
node packages/daemon/scripts/check-pi.mjs
node packages/cli/bin/vika.mjs setup
```

The existing Lark download helper selects the Windows ZIP from the platform map
and checks it against the pinned npm package's checksums. It does not depend on
npm postinstall being enabled. Windows includes `tar.exe` for ZIP extraction.

Setup saves your configuration before attempting managed-release startup. That
startup can report failure in a source checkout; after confirming configuration
was saved, start only your configured instance and Web using your own PM2:

```powershell
# Replace primary if setup used a different instance ID.
pm2 start ecosystem.config.cjs --only vika-primary,vika-web
pm2 logs vika-primary
pm2 stop vika-primary
pm2 stop vika-web
```

Do not start the updater. PM2 ownership, installation, autostart and isolation
are prerequisites managed by the user: Windows PM2 versions may share global
named pipes even with different PM2_HOME directories. Do not assume this preview
isolates an existing PM2 installation. Run from Git Bash when executing the
agent prompt's POSIX shell command examples; PowerShell uses different quoting.

Standard npm Node shims and native executables are supported. Arbitrary batch
wrappers are rejected. Use explicit runtime executable paths when necessary.
Automatic upgrades, Vika-managed autostart, and email account permissions are
not supported on Windows here. Slack CLI provisioning is external; configure
`SLACK_CLI_BIN` if needed. The action API uses Windows named pipes and retains
runtime authority checks. Actual connector delivery, multimedia, permissions,
and user-account behavior must still be validated on the target machine.
