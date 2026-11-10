# Migrate to ov-superagent

OpenViking SuperAgent uses `ov-superagent` as its command and `~/.superagent` as its default home. The installer also writes `ovsa` and `vika` compatibility launchers.

| Interface | Canonical name |
| --- | --- |
| Installer | `https://ovrelease.tos-cn-beijing.volces.com/superagent/install.sh` (Windows: `install.ps1`) |
| Release storage | `/superagent/releases/<version>/`, configured with `OV_SUPERAGENT_DOWNLOAD_BASE` and `OV_SUPERAGENT_TOS_PREFIX` |
| Autostart | Linux `superagent.service`, macOS `com.superagent.agent`, Windows `superagent-autostart.vbs` |
| Runtime environment | `OV_SUPERAGENT_*`, such as `OV_SUPERAGENT_CONFIG`, `OV_SUPERAGENT_TEMP_DIR`, `OV_SUPERAGENT_LOG_LEVEL`, and `OV_SUPERAGENT_DSH_EFFORT` |
| PM2 processes | `superagent-<instance>`, `superagent-web`, `superagent-updater` |
| Default temporary directory | `/tmp/superagent-<instance>` when setup assigns a temporary directory for data under `/root`; explicit `tempDir` or `OV_SUPERAGENT_TEMP_DIR` wins |
| Default secrets file | `~/.superagent/secrets.env`; `OV_SUPERAGENT_SECRETS_FILE` overrides it |

The release workflow must publish the new prefix before the new installer URLs are usable. A documentation change does not deploy these files. Existing `/vika/` releases are not moved or deleted by this change. Formal releases use `openviking-superagent@<version>` tags and `superagent-<version>-<platform>` archives. The workflow retains the existing GitHub deployment environment `vika-release` so its approval rules and TOS credentials continue to apply.

Runtime entry points and deployment configuration loading accept the old `VIKA_*` and intermediate `OVSA_*` keys as aliases. An explicitly provided `OV_SUPERAGENT_*` key wins, including an empty value; otherwise `OVSA_*` wins over `VIKA_*`. New process environments use canonical keys. Update shell/service exports and manifest `env` keys to the canonical names. Provider credentials such as `OPENAI_API_KEY` and `DEEPSEEK_API_KEY` keep their names. Internal connector profiles and RPC identifiers are not renamed.

## Stop the old installation first

Back up the existing home before changing it. Use the currently installed command (`vika` or `ovsa`) to disable autostart and stop all instances **before installing the new release**. Inspect the old installation's PM2 list, stop/delete only its `vika-*` or `ovsa-*` entries, then save the PM2 list so a later reboot cannot resurrect them. Point `PM2_HOME` at that installation's `pm2` directory and use its bundled `current/bin/pm2` (Windows: `pm2.cmd`); do not operate on an unrelated global PM2 daemon.

For example, for an installation at `~/.ovsa` with one instance named `primary` and `vika-*` process names:

```sh
ovsa autostart disable
ovsa stop
PM2_HOME="$HOME/.ovsa/pm2" "$HOME/.ovsa/current/bin/pm2" list
# Verify these are this installation's processes before removing them.
PM2_HOME="$HOME/.ovsa/pm2" "$HOME/.ovsa/current/bin/pm2" delete vika-primary vika-web vika-updater
PM2_HOME="$HOME/.ovsa/pm2" "$HOME/.ovsa/current/bin/pm2" save
```

Adapt the command, home, namespace, and instance names to the existing installation. The new CLI refuses to start/restart a target while its historical PM2 name remains. It also refuses to register new autostart alongside a historical service file; `ov-superagent autostart disable` removes old registrations before enabling the new one.

## Move the home on macOS or Linux

Choose the home that contains your existing configuration, either `.vika` or `.ovsa`. Never merge two homes. If both exist and are not links to the same installation, stop and inspect them first.

```sh
legacyHome="$HOME/.ovsa"  # use "$HOME/.vika" for the earlier layout
# Only proceed if the destination does not exist.
test ! -e "$HOME/.superagent" && test ! -L "$HOME/.superagent" &&
  mv "$legacyHome" "$HOME/.superagent" &&
  ln -s "$HOME/.superagent" "$legacyHome"
```

Keep the compatibility link: manifests, credentials, and old releases can contain absolute paths into the previous home. Do not rewrite credentials or move individual instance directories separately. If an older `.vika` link already points to `.ovsa`, keep that chain intact.

Rerun the [installer](../install.md) after the new release has been published. Open a new terminal, run `ov-superagent status`, then `ov-superagent start`; re-enable autostart with `ov-superagent autostart enable` if desired. Existing configuration prevents first-time setup from replacing it.

## Move the home on Windows

After stopping the old services and removing their PM2 entries, use the same home as the old installation (`HOME` if set, otherwise `USERPROFILE`):

```powershell
$agentHome = if ($env:HOME) { $env:HOME } else { $env:USERPROFILE }
$oldHome = Join-Path $agentHome '.ovsa' # use '.vika' for the earlier layout
$newHome = Join-Path $agentHome '.superagent'
if (Test-Path -LiteralPath $newHome) { throw 'The new home already exists; do not merge installations.' }
Move-Item -LiteralPath $oldHome -Destination $newHome
New-Item -ItemType Junction -Path $oldHome -Target $newHome
```

Keep the junction for existing absolute paths. If creating it fails, restore the old directory before restarting services. Rerun the PowerShell installer, open a new terminal, check `ov-superagent status`, and start with `ov-superagent start`. Re-enable autostart if desired.

The installer and default configuration loader refuse an unmigrated legacy home instead of creating a second empty installation. Explicit configuration and data paths remain explicit overrides. Upgrading release files alone does not migrate the home, rename PM2 entries, or create new launchers; use the installation procedure for this namespace migration.
