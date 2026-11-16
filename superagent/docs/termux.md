<!-- Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and the OpenViking contributors. -->
<!-- SPDX-License-Identifier: AGPL-3.0 -->
# Native Termux (Android/Bionic)

This source installation uses Termux's Node and a native lark-cli. It does not
use glibc Linux artifacts, impersonate Linux, require PRoot, or change global
Pi installations. The managed Pi and Durable versions are pinned to **1.5.0**.
Both runtime checks must pass before the installer activates the release.

## Install

Use Termux private storage, not `/sdcard`: execution, symlinks and POSIX file
modes are required. Install Node **>=22.19.0** with `node:sqlite`, npm, bash,
git and tar from Termux. Optional DeepSeek Harness needs Node **24.9+**.
Prepare a reachable OpenViking server (a remote server is fine).

Build/install a native lark-cli separately; it must implement the CLI/API used
by SuperAgent. The desktop dependency is pinned to **1.0.99**. A different
native build needs connector testing; `--version` alone does not establish API
compatibility. The source installer currently requires this executable even
when the first configured channel is not Lark.

From the checkout's `superagent/` directory:

```sh
export LARK_CLI_BIN="$HOME/.local/bin/lark-cli"
node scripts/install-termux.mjs
export PATH="$HOME/.ov-superagent/bin:$PATH"
ov-superagent setup
ov-superagent status
```

Setup starts the configured instance. Later, use `ov-superagent start` after
stopping it. The installer itself starts no services and changes no shell
profiles. It stages application files under `~/.ov-superagent/releases`, runs
`npm ci --omit=dev --ignore-scripts`, checks Pi and Durable, and only then
creates `~/.ov-superagent/current` and `~/.ov-superagent/bin/ov-superagent`.
Failed checks clean the staged release without activating it. Existing or
concurrently created installations are never overwritten.

The launcher remembers the native lark-cli path; an explicit `LARK_CLI_BIN`
overrides it. The unsupported npm lark wrapper is optional so npm can skip it
on Android. No Slack CLI or desktop Node binary is downloaded; configure a
native `SLACK_CLI_BIN` separately if needed.

## runit

Install `termux-services` and restart the Termux shell to start its supervisor.
Stop a manually started instance before enabling foreground supervision:

```sh
ov-superagent stop
ov-superagent autostart enable
sv status "$PREFIX/var/service/ov-superagent"
# Later:
ov-superagent autostart disable
```

runit supervises foreground PM2, which supervises the instances and Web. Both
the service and CLI use `~/.ov-superagent/pm2` by default. An explicit
absolute `PM2_HOME` is honored by both; use the same override for subsequent CLI calls
and never point it at an unrelated PM2 installation. On service startup, any
leftover background PM2 in that selected home is stopped before foreground
PM2 starts. Other PM2 homes are not touched.

`SVDIR` selects another service root. Disabling writes the persistent `down`
marker and stops a running supervisor; it also works while termux-services is
off. It does not remove runit's supervision directory. Re-enabling removes
`down` and signals a running supervisor.

The service captures PATH, Termux loader variables, native chat CLI paths, and
`OV_SUPERAGENT_CONFIG` / `OV_SUPERAGENT_SECRETS_FILE` paths, **not model API key
values**. Keep provider credentials in their runtime credential files or the
private `~/.ov-superagent/secrets.env` file, not in `config.json`. Re-enable
autostart after changing captured paths. `ov-superagent stop` controls apps;
`ov-superagent autostart disable` stops supervision persistently.

Device reboot additionally requires Termux:Boot configured to start
termux-services. Android battery/background-process restrictions still apply;
a runit service does not bypass them.

## Manual updates and rollback

Android has no compatible published release artifact, so automatic release
upgrades are disabled. To update from a new source checkout:

1. Disable autostart if enabled, then stop SuperAgent.
2. Back up `~/.ov-superagent/current` and `~/.ov-superagent/bin/ov-superagent`
   under distinct names; keep the old release directory.
3. Run the new checkout's Termux installer and start SuperAgent. If using
   runit, re-enable autostart to regenerate its script and native paths.
4. Verify readiness and a real message round trip before removing backups.

To roll back, disable supervision and stop apps, restore both saved entries,
then start or re-enable the service. Configuration and data stay in place;
code rollback does **not** reverse data migrations. Back up data separately
before upgrading. Legacy `~/.vika`, `~/.ovsa` and `~/.superagent` installations
are not migrated: disable their old services and stop their processes before
creating a fresh `~/.ov-superagent` installation.

## Validation scope

Cross-platform tests cover failure cleanup, missing-entrypoint rejection,
configuration/PM2 paths, environment policy, and runit enable/disable state.
Only real POSIX shell, symlink and process-lifecycle tests are skipped on
Windows. The POSIX tests execute the installed CLI launcher, refuse a raced
installation, and exercise foreground PM2 takeover and SIGTERM cleanup with a
non-default configuration and PM2 home.

Native Android end-to-end validation is still required for this rebased
implementation: install, setup, Pi and Durable turns/tool calls, a real
connector round trip, restart/session recovery, and runit stop/start. Do not
infer these from npm installation, desktop tests, or a `--version` response.
