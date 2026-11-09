# Windows package maintenance

For installation and first-run setup, use the shared [installation guide](../install.md). This page covers Windows build artifacts, offline installation and validation.

The **Windows x64 preview** job in **Vika Release** builds a separate, experimental ZIP.
It shares the POSIX workflow's pull-request, manual and `openviking-vika@*` tag
triggers; Windows output is always a preview artifact, including on a release tag.
Manual and tag runs also publish it to the release bucket under
`vika/windows-preview/` (see [Download and install](#download-and-install));
PR runs only keep the GitHub Actions artifact (sign-in required, expires after
14 days). Either way it is not an entry in the stable updater feed: `vika/latest`
and `vika upgrade` stay macOS/Linux only.
The package's `release.json` identifies the exact built commit (a PR run normally
builds GitHub's merge commit), Node version and ZIP SHA-256.
For a manual Windows-only test run, select the branch under **Vika Release → Run
workflow** and enable `windows_preview_only`. This skips the POSIX build and POSIX TOS
publisher; the Windows preview is still published to TOS. Leaving that option off preserves the existing manual POSIX publishing
behavior as well as building the Windows artifact.

The ZIP includes committed Vika source, Windows Node/npm, production dependencies,
PM2 and the pinned Windows lark-cli. It needs no system Node/npm/PM2 installation.
It does not install OpenViking, model runtimes or credentials. Pi requires Git
Bash; Durable uses native PowerShell. Slack still needs an explicit, separately
validated CLI. The preview does not restore fixes removed from the slim Windows
branch; pinned lark-cli/preflight compatibility still needs real-account testing.

## Download and install

The newest preview is always at
`https://ovrelease.tos-cn-beijing.volces.com/vika/windows-preview/latest/`
(the build's version is in `latest/version`; every build also stays at
`vika/windows-preview/<version>/`). No GitHub account is needed.

Use a normal, non-administrator Windows 10/11 x64 account. Paste this into
PowerShell 7 or Windows PowerShell 5.1:

```powershell
irm https://ovrelease.tos-cn-beijing.volces.com/vika/install.ps1 | iex
```

The installer resolves `windows-preview/latest/version` once, downloads that
version's ZIP and required SHA-256, verifies it, and uses the same install,
self-check, PATH and first-run setup steps as the local installer. Download or
verification failure leaves the installed release unchanged. Temporary downloads
are removed on success and failure. The current terminal can run `vika` afterwards.

To pin a preview version or pass options without `iex`:

```powershell
& ([scriptblock]::Create((irm https://ovrelease.tos-cn-beijing.volces.com/vika/install.ps1))) -Version '<version>' -NoSetup -Lang en
```

`VIKA_VERSION` and `VIKA_DOWNLOAD_BASE` environment variables are also accepted
by the one-line command. The download base is the Vika root (before
`/windows-preview`), must use HTTPS, and defaults to the release bucket above.
The existing `install-preview.ps1 -File <zip>` offline entry remains available.

## Install a local artifact

Use a normal, non-administrator Windows 10/11 x64 account, in PowerShell 7 or
Windows PowerShell 5.1. Extract the downloaded Actions artifact to a directory
first: it contains the **inner** Vika ZIP, its `.sha256`, `release.json`,
`install-preview.ps1` and these instructions. Keep the ZIP and checksum together,
then from that directory:

```powershell
$archive = (Get-ChildItem -File -Filter 'vika-*-win32-x64.zip').FullName
if (@($archive).Count -ne 1) { throw 'Expected exactly one Vika ZIP' }
./install-preview.ps1 -File $archive
```

This takes the steps, and prints the words, of the macOS/Linux installer: verify,
unpack, self-check, install, then say what is next. It verifies the SHA-256
(a package without a `.sha256` beside it installs with a note instead of a
check), unpacks into a staging directory while rejecting unsafe entries, runs the
package's own runtime check, and only then installs. The layout is the POSIX one
under `~\.vika`: `releases\<version>`, the `current` link, the `rollback` link
to the version installed before, `bin\vika.cmd`, and `~\.vika\bin` on the PATH of
the terminal that ran the installer and of new ones. The release also carries `bin\pm2.cmd`, so `vika start`
runs its PM2 with its own Node. Installing the same version again changes nothing;
installing another one moves `current` and keeps the previous release.

`-Lang en` picks the language for this run and saves it in `~\.vika\language`, as
`vika --lang` does; `VIKA_LANG` overrides it for one run and `NO_COLOR` turns the
colours off. Colour and the unpacking spinner appear only on a terminal; piped
output stays plain lines.

Vika's home is `$env:HOME` when it is set, otherwise `%USERPROFILE%`: `vika`, the
installer and the release link all resolve it that way, so a machine that points
`HOME` elsewhere still gets one installation. `bin\vika.cmd` reads neither — it
finds the `.vika` directory it was written into — so `vika` keeps working if `HOME`
changes later; only a new install follows it.

With no manifest yet and a terminal to ask in, the installer goes straight into
`vika setup`, as the POSIX installer does. `~\.vika\bin` is on the PATH of the
terminal that ran it, so `vika setup` works in that same window; keep it open until
setup finishes. A `cmd.exe` window opened before the install does not see the new
PATH. `-NoSetup`, or a run with no terminal, stops after the install and prints
`next: run vika setup`.

The package does not carry OpenViking. When setup runs next it asks for the
connection itself, as install.sh leaves that to setup; otherwise the installer
runs the same `check-ov.mjs` probe, reports the result, and installs anyway,
because the manifest is then written by hand.

### An isolated copy

`-Destination` installs into exactly that new directory and changes nothing else:
no `current`, no launcher, no PATH entry, no saved language, no lock. Use it to
keep an existing installation and configuration untouched.

```powershell
$destination = Join-Path $env:LOCALAPPDATA 'VikaPreview\test-1'
./install-preview.ps1 -File $archive -Destination $destination
```

An existing destination is rejected, including on repeated installation. In
neither mode does the installer write configuration, start services, or switch an
existing `~\.vika\current` installation. The checksum detects corruption; obtain
both the package and installer from a trusted run. If local execution policy
blocks downloaded scripts, review/unblock them under your organization's policy;
administrator rights and a machine-wide policy change are not required.

## Configure and test

A first install is configured by `vika setup`, which the installer starts in the
same terminal: it picks the runtime, records an existing OpenViking service and
connects a channel. Afterwards start the release with `vika`, which runs that
release and, through its own `bin\pm2.cmd`, that release's PM2 and Node; no global
Node or PM2 is used. The shared Web port is 3737: stop any conflicting instance
first.

```powershell
vika start
vika status
vika logs primary
vika stop
```

An isolated copy is not under `~\.vika\current`, so `vika` cannot manage it: write
its manifest from `config.example.json` as [windows.md](windows.md) describes (only
the `primary` agent, absolute Windows paths, an existing OpenViking service), then
start it with the `vika-preview.ps1` inside the copy and an explicit `PM2_HOME`:

```powershell
$destination = Join-Path $env:LOCALAPPDATA 'VikaPreview\test-1'   # as installed above
$home = if ($env:HOME) { $env:HOME } else { $env:USERPROFILE }   # Vika's home
$env:PM2_HOME = Join-Path $home '.vika\pm2'
$config = Join-Path $destination 'config.json'
& "$destination\vika-preview.ps1" -Action start -Config $config
& "$destination\vika-preview.ps1" -Action logs
& "$destination\vika-preview.ps1" -Action stop
```

Both paths use the package's Node and PM2 and manage only `vika-primary`/`vika-web`.
Windows PM2 is unmodified: a different `PM2_HOME` is **not** proof of process
isolation. Do not run alongside an existing instance with those names, reuse a
running PM2 daemon from another Node installation, or use `pm2 kill` on a shared
machine. Prefer a dedicated Windows test account/machine for first acceptance.

The GitHub-hosted runner does not certify non-administrator acceptance. Test the
installation above from a normal user account on the target machine.

CI checks installation with PowerShell 7 and 5.1 in both modes, the default layout
with `current`, `rollback` and the launcher, the PATH the installer leaves in the
running terminal, relocation into a Chinese/space path, bundled runtime loading
without global Node/PM2, and selected Windows regression tests. It does not
authenticate to a model or connector. On your machine, verify
actual PIDs, the configured `/ready` response and connector state, then one
uniquely labelled message/reply with no duplicate delivery. Report the commit,
Windows/PowerShell versions, results and sanitized errors. No automatic upgrade is
provided: a default install keeps the version it replaced under
`~\.vika\releases` and names it in `~\.vika\rollback`, so rolling back means
pointing `current` at that release again and running `vika restart`; an isolated
copy is rolled back by stopping it and starting the preserved installation with its
own configuration. `vika autostart enable` writes a script into the current user's
own Startup folder — no service, no administrator, visible in Task Manager's
Startup tab — which runs `vika start` hidden at logon and logs to
`~\.vika\logs\autostart.log`; an isolated copy is not covered by it.
`vika start` needs `current` to be a directory link: on a
volume without junctions the installer writes a pointer file instead, which runs
`vika` but cannot be resolved as a release.


### Installer recovery and HOME overrides

Vika explicitly prefers `HOME` over `USERPROFILE`. This differs from Node's
Windows `os.homedir()`. Installation, setup, release lookup and login startup
must use that same Vika home. The login entry stays in Windows' `APPDATA`
Startup folder and captures the chosen HOME and PATH for its child process.
`VIKA_HOME` in the PowerShell fixture runner is an installer test override,
not a supported runtime configuration directory override.

Release versions must be safe single directory names. Existing release trees
are never overwritten: identical contents can be reused; different contents
or reparse points fail with the original tree preserved. Link replacement
prepares new links first and restores current/rollback on a caught failure.
The pair of renames is not crash-atomic. If recovery itself fails, the error
names the retained `.old-*` links; preserve them for recovery.

Run both `scripts/windows/test-install-preview.ps1` and
`scripts/windows/test-install-transactions.ps1` under PowerShell 7 and Windows
PowerShell 5.1. The latter injects preparation, current-switch, rollback-switch
and recovery failures without touching the real installation. Linux runs cover
file-pointer recovery only; Windows runs also exercise junctions. Real login
startup and authenticated model/IM operation still require a Windows machine.
