# Windows x64 downloadable preview

The **Windows x64 preview** job in **Vika Release** builds a separate, experimental ZIP.
It shares the POSIX workflow's pull-request, manual and `openviking-vika@*` tag
triggers; Windows output is always a preview artifact, including on a release tag.
Download the artifact from a successful run on the PR's Checks page or the
repository's Actions page. GitHub sign-in is required; artifacts expire after
14 days. This is not a public TOS release or an entry in the stable updater feed.
The package's `release.json` identifies the exact built commit (a PR run normally
builds GitHub's merge commit), Node version and ZIP SHA-256.
For a manual Windows-only test run, select the branch under **Vika Release → Run
workflow** and enable `windows_preview_only`. This skips the POSIX build and TOS
publisher. Leaving that option off preserves the existing manual POSIX publishing
behavior as well as building the Windows artifact.

The ZIP includes committed Vika source, Windows Node/npm, production dependencies,
PM2 and the pinned Windows lark-cli. It needs no system Node/npm/PM2 installation.
It does not install OpenViking, model runtimes or credentials. Pi requires Git
Bash; Durable uses native PowerShell. Slack still needs an explicit, separately
validated CLI. The preview does not restore fixes removed from the slim Windows
branch; pinned lark-cli/preflight compatibility still needs real-account testing.

## Install a local artifact

Use a normal, non-administrator Windows 10/11 x64 account. Extract the downloaded
Actions artifact to a directory first: it contains the **inner** Vika ZIP, its
`.sha256`, `release.json`, `install-preview.ps1` and these instructions. Keep the
ZIP and checksum together. In PowerShell 7 or Windows PowerShell 5.1, from that
directory:

```powershell
$archive = (Get-ChildItem -File -Filter 'vika-*-win32-x64.zip').FullName
if (@($archive).Count -ne 1) { throw 'Expected exactly one Vika ZIP' }
$destination = Join-Path $env:LOCALAPPDATA 'VikaPreview\test-1'
./install-preview.ps1 -File $archive -Destination $destination
```

Use a **new directory** for each installation. The installer verifies the checksum,
checks the extracted runtime, and moves it to the destination only on success.
An existing destination is rejected, including on repeated installation. It does
not write configuration, register a global `vika` command, change PATH, start
services or switch an existing `~/.vika/current` installation. The checksum
detects corruption; obtain both the package and installer from a trusted run.
If local execution policy blocks downloaded scripts, review/unblock them under
your organization's policy; administrator rights and a machine-wide policy change
are not required by this installer.

## Configure and test

Preserve any existing installation and configuration. Follow [windows.md](windows.md)
to create a separate manifest from `config.example.json` inside the installed
directory. Keep only the `primary` agent initially, use absolute Windows paths,
and configure an existing OpenViking service and authenticated connector/runtime.
The shared Web port is 3737: stop any conflicting test instance first.

From PowerShell, set the PM2 home you intend to use explicitly:

```powershell
$env:PM2_HOME = Join-Path $env:USERPROFILE '.vika\pm2'
$config = Join-Path $env:USERPROFILE '.vika\windows-preview.json'
& "$destination/vika-preview.ps1" -Action start -Config $config
& "$destination/vika-preview.ps1" -Action logs
# After testing:
& "$destination/vika-preview.ps1" -Action stop
```

This uses the package's Node and PM2 and manages only `vika-primary`/`vika-web`.
Windows PM2 is unmodified: a different `PM2_HOME` is **not** proof of process
isolation. Do not run alongside an existing instance with those names, reuse a
running PM2 daemon from another Node installation, or use `pm2 kill` on a shared
machine. Prefer a dedicated Windows test account/machine for first acceptance.
Do not use the release-oriented `vika start` for this preview.

The GitHub-hosted runner does not certify non-administrator acceptance. Test the
installation above from a normal user account on the target machine.

CI checks installation with PowerShell 7 and 5.1, relocation into a Chinese/space
path, bundled runtime loading without global Node/PM2, and selected Windows
regression tests. It does not authenticate to a model or connector. On your
machine, verify actual PIDs, the configured `/ready` response and connector state,
then one uniquely labelled message/reply with no duplicate delivery. Report the
commit, Windows/PowerShell versions, results and sanitized errors. No automatic
upgrade or autostart is provided. Roll back by stopping this preview and starting
the preserved installation with its original configuration.
