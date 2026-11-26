# OpenViking SuperAgent on Termux

Native Termux arm64 uses the POSIX installer and reuses the JavaScript,
production dependencies and static resources in the `linux-arm64` release.
The installer uses existing native tools; it does not install Node or lark-cli.

## Requirements

Keep the installation on Termux's private filesystem, not Android shared storage.
Have these tools available on `PATH`:

- Native Android arm64 Node **22.19.0+**, with `node:sqlite`.
- A compatible native `lark-cli` (desktop releases pin version 1.0.99).
- `sh`, `bash`, `curl`, `tar`, `sha256sum` and `rg`.

The bundled Linux Node and connector binaries are not Android executables.
An explicit `LARK_CLI_BIN` can select a native lark-cli outside PATH.

## Install

```sh
curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/superagent/install.sh | sh
ov-superagent setup
ov-superagent start
ov-superagent status
```

The installer verifies the Linux arm64 archive, links the native Node and writes
Termux-compatible shell launchers. It checks the runtime and OpenViking before
activating the release. Native lark-cli and rg are selected from PATH; Slack
requires a compatible native CLI if used.

## Service

Install `termux-services`, then restart the Termux shell:

```sh
pkg install termux-services
ov-superagent autostart enable
ov-superagent autostart disable
```

The `ov-superagent` runit service supervises foreground PM2, sharing the CLI's
PM2 home and saved configuration paths. Device boot additionally requires
Termux:Boot. Android battery restrictions can still stop background processes.

## Updates

Run the installer again and then `ov-superagent restart`. Automatic release
upgrades are unavailable on Termux. Release rollback restores application code,
not system Node, native CLIs, credentials or data.
