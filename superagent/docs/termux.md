<!-- Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and the OpenViking contributors. -->
<!-- SPDX-License-Identifier: AGPL-3.0 -->
# Native Termux (Android/Bionic)

Termux **arm64** uses the same POSIX installer and release layout as desktop
systems, but a dedicated `termux-arm64` archive. Linux/glibc archives cannot run
natively on Android/Bionic. This is Termux support, not a generic Android app
or PRoot distribution installation.

The installer keeps **OS/ABI** separate from **installation environment**:

| Userspace running the installer | OS | Environment / archive |
|---|---|---|
| Native Termux with Android Bionic | `android` | `termux` / `termux-arm64` |
| A PRoot APK's Linux/glibc guest | `linux` | POSIX / `linux-<arch>` |
| Linux/musl guest | `linux` | Unsupported by the existing Linux release |

The shell's libc mappings in `/proc` determine Android versus Linux, not the
shared Android kernel or inherited `TERMUX_VERSION` / `PREFIX`. Linux guest
mappings take precedence if host Bionic mappings are also visible. If `/proc`
cannot be read, detection stops rather than guessing. This does not add
general APK/PRoot service support: run the installer **inside** the
guest, keep its HOME/PATH/loader environment separate from the host, and use
the guest/app's process supervision rather than assuming Termux runit. Other
native Android APK environments are rejected until explicitly supported.

The archive contains production JS dependencies, PM2, the pinned managed
runtimes and a native lark-cli. CI builds lark-cli for Android from a reviewed,
immutable Go source commit, preserving its MIT license and build provenance.
It uses the pinned Android NDK with cgo (API 24) so DNS uses Android libc,
not a desktop Go resolver or an undocumented Termux toolchain patch.
The desktop npm dependency remains mandatory and unchanged. **No npm, Go, git
or compiler is needed on the device**, and package download hooks are not run
there. Global Pi and lark-cli installations are not modified.

## Prepare Termux APT and dependencies

Use a current official Termux installation from F-Droid or the Termux GitHub
releases. Work inside Termux private storage (`$HOME`), **not `/sdcard`**:
executables, symlinks and POSIX permissions must work.

1. Run `termux-change-repo`. Select the **Main** repository and a reachable
   official Termux mirror. The default official Main source is
   `https://packages.termux.dev/apt/termux-main` (`stable main`); mirrors listed
   by `termux-change-repo` are preferable to copying an arbitrary URL.
2. On a fresh installation, refresh and upgrade Termux packages, then install
   the runtime prerequisites:

   ```sh
   pkg update
   pkg upgrade
   pkg install nodejs-lts bash curl tar coreutils termux-exec
   ```

   `nodejs` may be used **instead of** `nodejs-lts`, not alongside it. Do not
   install Linux Node via nvm, add Debian/Ubuntu APT sources, or use a glibc
   distribution's binaries. Root and X11 repositories are not required.
   Review APT upgrades and back up other applications before upgrading an
   existing environment; the SuperAgent installer never runs `pkg` for you.
3. Restart the Termux shell after installing loader packages, then verify:

   ```sh
   "$PREFIX/bin/node" --version
   "$PREFIX/bin/node" -e 'new(require("node:sqlite").DatabaseSync)(":memory:").close()'
   ```

   Native Termux Node **>=22.19.0** with `node:sqlite` is required. If the
   repository supplies an older Node, fix/update the Main mirror rather than
   substituting a desktop binary. Keep the Termux loader environment intact.

Prepare a reachable OpenViking server and model credentials, as in the
[shared setup guide](../install.md). A remote OV server is fine. Optional
DeepSeek Harness is installed separately and needs Node 24.9+.

## Install a prebuilt archive

Once a release contains `termux-arm64`, use the same command as desktop POSIX:

```sh
curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/superagent/install.sh | sh
```

Until that release is published, download the Termux artifact from a trusted
PR workflow run. It contains the `.tar.gz` and `.sha256` files. From the matching
source revision, install the archive locally:

```sh
sh scripts/install.sh --file /absolute/path/superagent-<version>-termux-arm64.tar.gz
```

The same installer verifies the checksum/platform, checks native Node, Pi,
Durable and the bundled lark-cli, and follows the normal OV/setup checks before
activation. It installs under `~/.ov-superagent/releases`, links the APT-owned
Node into the release, and switches `current`. No desktop Node is extracted,
and no npm dependency is made optional. `--no-setup` still requires working OV
configuration; it is not a validation bypass.

Use `ov-superagent setup`, `start`, `stop`, `status` and `logs` as on desktop.
The installer adds `~/.ov-superagent/bin` to the shell PATH. An explicit
`LARK_CLI_BIN` overrides the bundled native CLI. Slack has no bundled Termux
artifact; provide a compatible native `SLACK_CLI_BIN` if using that connector.

## runit

For optional unattended startup:

```sh
pkg install termux-services
# Restart the Termux shell so its supervisor starts, then:
ov-superagent stop
ov-superagent autostart enable
sv status "$PREFIX/var/service/ov-superagent"
# Later:
ov-superagent autostart disable
```

runit supervises foreground PM2; PM2 supervises instances and Web. Both runit
and the CLI default to `~/.ov-superagent/pm2`. An explicit absolute `PM2_HOME`
is honored by both; retain that override for subsequent CLI commands and
never select an unrelated PM2 installation. Startup transfers a leftover
background PM2 in that home to foreground supervision; other homes are not
addressed.

`SVDIR` selects another service root. Disable persists the `down` marker even
if the supervisor is off; it stops a running service without deleting runit's
supervision directory. Enable removes `down` and signals a running supervisor.

The script captures PATH, Termux loader variables, native CLI overrides and
`OV_SUPERAGENT_CONFIG` / `OV_SUPERAGENT_SECRETS_FILE` paths, **not API key
values**. Keep credentials in the runtime's credential files or private
`~/.ov-superagent/secrets.env`, never `config.json`. Re-enable autostart after
changing captured paths. `stop` controls apps; `autostart disable` persistently
stops supervision. Reboot also needs Termux:Boot configured to start
termux-services; Android battery/background limits still apply.

## Updates and rollback

Automatic updater service/commands remain disabled for this initial Termux
release path. Use the **same POSIX installer** with a newer Termux archive,
then `ov-superagent restart`; it retains the previous code release via
`~/.ov-superagent/rollback`. Disable runit and stop apps before changing links
for a manual rollback, then start or re-enable the service. Back up data first:
code rollback does not reverse configuration, database or credential changes.
APT owns Node; restoring application code does **not** roll back Node/APT.

Legacy `~/.vika`, `~/.ovsa` and `~/.superagent` data is not migrated. Back up
those directories and stop their services/processes before a fresh setup;
the shared installer preserves legacy data and disables legacy autostart.

## Build and validation boundaries

Release CI stages the unchanged npm graph on Linux, cross-builds native
lark-cli and publishes a Termux archive alongside desktop POSIX archives.
PR CI uploads a device-test artifact only; it does not publish releases.
Updating the pinned lark npm version requires updating its reviewed Termux
source commit too; a mismatch fails the build instead of silently omitting it.
The build also pins the NDK revision and rejects archives containing desktop
Node/Slack or a non-ARM64 ELF lark-cli. DNS-dependent HTTPS must be tested with
the actual CI-produced binary; a Termux-patched native Go build is not equivalent.

The existing POSIX installer tests cover activation, checksum/platform failure,
OV refusal and rollback; Termux runs those same tests. Platform-independent
runit state/configuration tests still run on Windows. Only the real POSIX
PM2 process-lifecycle test is skipped there. Native device validation must
also exercise the produced artifact, runtime/tool calls, connector readiness,
service restart and cleanup; host-side cross-compilation alone is not proof.
