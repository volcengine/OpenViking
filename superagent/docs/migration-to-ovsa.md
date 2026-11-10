# Migrate an existing installation to ovsa

OpenViking SuperAgent now uses `ovsa` as its command and `~/.ovsa` as its default home. The installer also writes a `vika` compatibility launcher. `VIKA_*` environment variables, release tags, download URLs, and process names remain unchanged.

For a fresh installation, use the [installer](../install.md). If `~/.vika` exists but `~/.ovsa` does not, the new installer and default configuration loader stop instead of creating a second empty installation.

## macOS and Linux

Back up your old home directory first. Disable autostart and stop the old installation before moving it:

```sh
vika autostart disable
vika stop
# Only proceed if ~/.ovsa does not exist. Never merge two installation homes.
test ! -e "$HOME/.ovsa" && test ! -L "$HOME/.ovsa" &&
  mv "$HOME/.vika" "$HOME/.ovsa" &&
  ln -s "$HOME/.ovsa" "$HOME/.vika"
```

Keep the compatibility symlink: existing manifests, credentials, running process definitions, and old release launchers may contain absolute `.vika` paths. Do not rewrite credentials or move individual instance directories separately.

Rerun the installer from the [installation guide](../install.md) to install the new release and create both launchers. Open a new terminal, run `ovsa status`, then `ovsa start`; re-enable autostart with `ovsa autostart enable` if desired. Existing configuration prevents first-time setup from replacing it.

## Windows

Back up the old home first, then run `vika autostart disable` and `vika stop`. In PowerShell, use the same home that the existing installation uses (`HOME` if set, otherwise `USERPROFILE`):

```powershell
$agentHome = if ($env:HOME) { $env:HOME } else { $env:USERPROFILE }
$oldHome = Join-Path $agentHome '.vika'
$newHome = Join-Path $agentHome '.ovsa'
if (Test-Path -LiteralPath $newHome) { throw 'The new home already exists; do not merge installations.' }
Move-Item -LiteralPath $oldHome -Destination $newHome
New-Item -ItemType Junction -Path $oldHome -Target $newHome
```

Keep the junction for existing absolute paths. Rerun the PowerShell installer, open a new terminal, check `ovsa status`, and start with `ovsa start`. Re-enable autostart if desired. If creating the junction fails, restore the old directory before restarting services.

If both directories already exist, stop and inspect them before choosing which installation to keep. Explicit `VIKA_CONFIG`, `VIKA_SECRETS_FILE`, and configured data paths remain explicit overrides; migration does not rewrite them. Upgrading release files alone does not migrate the home or create the new launcher.
