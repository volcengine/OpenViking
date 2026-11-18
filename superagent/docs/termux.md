# Termux: phase-one runtime support

This is an initial **native Termux / Android arm64** runtime path, not a new
release platform. It reuses the existing `linux-arm64` archive's JavaScript,
production npm dependencies and static resources. It does **not** run the
archive's glibc Node or Linux connector binaries on Android.

## Prerequisites

Use a current Termux installation and keep the installation on Termux's private
filesystem, not Android shared storage. Install native tools:

```sh
pkg install nodejs-lts bash curl tar coreutils ripgrep
```

`node` must be a native Android build, version **22.19.0 or newer**, with
`node:sqlite` available. A recent native `nodejs` package also works.
Install a compatible native `lark-cli` separately and put it on `PATH`.
The desktop npm wrapper is not an Android binary; this phase does not build or
download a native replacement. The desktop release pins lark-cli 1.0.99; use a
compatible native version and validate your login and connector operations.
A working `--version` command is not proof of authenticated connector readiness.

```sh
command -v node lark-cli rg sh
node -p 'process.platform + " " + process.arch + " " + process.version'
lark-cli --version
```

## Install and run

Invoke this branch's installer explicitly with Termux's `sh`:

```sh
sh superagent/scripts/install.sh --file /path/to/superagent-<version>-linux-arm64.tar.gz
```

Keep the matching `.sha256` file beside a local archive. The existing remote
installer mode also selects the `linux-arm64` archive on native Termux; there is
no `termux-arm64` download in this phase. Use an installer containing this
change, not an older installer distributed with a previous release.

**The archive must also contain this runtime change.** Applying only the new
installer to an older release does not backport its JavaScript. Until a release
includes this PR, published archives alone are not a supported installation of
phase one; local validation must overlay the changed runtime files explicitly.
No dedicated Termux archive is built by this PR.

The installer verifies the archive as before, substitutes a symlink to the
native Node found on `PATH`, and writes shell entrypoints with an absolute
Termux `sh` shebang. It checks the runtime and OpenViking connection before
activation. It does not install npm dependencies or change your system Node
or lark-cli. Native Android is distinguished from a glibc PRoot guest using the
installer shell's libc mappings; inherited Termux environment variables do not
turn a Linux guest into a native Termux installation.

After installation:

```sh
~/.ov-superagent/bin/ov-superagent setup
~/.ov-superagent/bin/ov-superagent start
~/.ov-superagent/bin/ov-superagent status
```

On Termux, runtime defaults select native `lark-cli` and `rg` from `PATH`, not
the Linux binaries. `LARK_CLI_BIN` and the existing CLI configuration overrides
still take precedence. Slack requires a compatible native `slack` on `PATH` or
an explicit configured executable; the bundled Linux Slack is not supported.
Loader variables needed by Termux subprocesses are preserved without widening
the connector credential boundary. Browser links use `termux-open-url`.

## Deliberate limits

- No new CI matrix, NDK build, native lark-cli cross-compilation, artifact
  manifest or Termux-specific installer package.
- No Pi/Durable version upgrade; the existing dependency baseline is retained.
- No runit, Termux:Boot or login-service integration; start and stop manually.
- Automatic release upgrades are disabled for this runtime. Upgrade manually
  with a newer compatible release and this installer, then restart.
- Existing release activation/rollback links are reused. They roll back code,
  **not** APT-owned Node, native lark-cli, credentials or data. Keep those native
  dependencies compatible yourself.
- Android may suspend or kill background processes. This is not a promise of
  persistent Android background execution or support for every native addon.

## Phase two

A separate change can add CI-built `termux-arm64` artifacts, a pinned native
lark-cli toolchain, actual-device artifact validation and the corresponding
publishing/install policy. Service management is separate from this minimal
runtime support.
