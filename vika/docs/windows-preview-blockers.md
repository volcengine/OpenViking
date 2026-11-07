# Native Windows preview: installation status and blockers

Status measured on a non-elevated Windows 11 workstation (10.0.22631, Windows
PowerShell 5.1.22621.6133, Node.js v24.19.0, npm 12.0.2) at `vika-win`
commit `8bcde778bc2dcdcf3d6ef756561706a509fef65c` (PR #2 merged as `355c3e3`,
PR #4 merged as this commit).

## What works at this baseline

- The documented manual update flow works: stop, remove only the `current`
  junction, update the checkout, rerun
  `scripts/install-windows.ps1 -SkipSetup -SkipPathUpdate`. The previous release
  stays under `releases\` for rollback. On Windows PowerShell 5.1,
  `Remove-Item current` fails with `NullReferenceException`; use
  `[IO.Directory]::Delete($current)` to remove only the junction.
- Under npm 12 the installer now recovers the blocked Lark CLI by itself:
  `npm ci` still reports
  `@larksuite/cli@1.0.94 (postinstall: node scripts/install.js)` blocked, then the
  installer runs the pinned package's checksum-verifying installer only for the
  missing `.exe`, executes `lark-cli.exe --version` and verifies `1.0.94` before
  creating `current` and the launchers. No manual `install.js` was run for the
  install measured here.
- Bundled Pi and Durable remain `1.4.0` and `check-pi.mjs` passes; the daemon
  runs the release's Pi, not one on PATH. A managed-Pi `--thinking low` turn
  answered through the user-local model configuration.
- `~/.openviking/ovcli.conf` was detected and the OV server on
  `127.0.0.1:1933` was reachable.
- Checkout validation at this baseline: elevation guard passed; 9 native test
  files 31/31 pass; `windows-lark-install.test.mjs` alone 6/6 pass; the isolated
  Unicode-home installer smoke with npm lifecycle scripts disabled passed and
  left the original user `%USERPROFILE%\.vika` byte-identical (release list,
  `current` junction and launcher hashes unchanged).
- `-SkipPathUpdate` left user and process PATH untouched; the installer created
  no configuration and no PM2 processes.

## Open blockers

1. **Single-command install still does not exist on `vika-win`.** There is no
   published Windows artifact and no `install.sh` equivalent;
   `install-windows.ps1` copies the current checkout, there is no
   `--version`-style install, and automatic upgrades are disabled. PR #5
   (`windows-release-preview`, head `8f94da6`) adds Windows ZIP packaging and
   installer sources with bundled Node, SHA256 and per-file verification,
   bundled Node/Pi/Durable/Lark checks, no PATH change and setup hand-off, but
   it is still a draft: no built release artifact, no public one-line URL, and
   real packaging, offline install and clean-machine acceptance are pending. It
   should not be described as a working `irm <url> | iex` flow yet.

2. **Model guidance exists; the boundary is detection scope, not a missing
   step.** Source already offers, when no runtime can run, "use the built-in Pi
   and log in to a model now" (opens the bundled Pi, `/login`, `/quit` returns to
   setup), and the runtime notes tell users to run `vika pi` + `/login` or edit
   `~/.pi/agent/models.json`. Measured at this baseline:
   - Only `~/.vika/secrets.env` with a provider key and no `~/.pi/agent` config:
     Pi availability is false (`no model yet: run vika pi and /login, or edit
     ~/.pi/agent/models.json`); Durable is available.
   - Only `~/.pi/agent/models.json`: both Pi and Durable are available.
   Reason: Pi availability is probed with the bundled `pi --list-models` using
   the inherited environment plus Pi config, while keys from
   `~/.vika/secrets.env` are injected only into the Durable probe
   (`DURABLE_PROVIDER_KEYS` in `packages/cli/src/runtimes.mjs`). The Pi list is a
   model-catalog check, not credential validation: a `models.json` entry with a
   placeholder key still produced a non-empty list.
   Reproduction: with no Pi config and no provider keys in the inherited
   environment, write one provider key to `~/.vika/secrets.env` and run
   `vika setup`; Pi reports no model yet and offers login, while Durable may be
   offered. So "just provide a secrets file" does not guarantee Pi, and a clean
   machine cannot finish with only Feishu App ID / App Secret.

## Acceptance criteria for the Windows release

- One command, e.g. `irm <release-url>/install.ps1 | iex`, installing a versioned
  artifact built from a commit (the Windows counterpart of the TOS Unix
  `install.sh -s -- --version ...` flow).
- Bundle the Node runtime, `node_modules` (Pi, Durable, Lark CLI binary), verify
  the bundled Pi and Lark CLI versions after extraction, and leave PATH
  untouched; no `npm ci` on the user side and no Administrator rights.
- Detect `~/.openviking/ovcli.conf` automatically, then run setup and keep the
  manual App ID / App Secret entry path working.
- Keep model bootstrap user-local: document the Pi login/model config step or a
  user-supplied secrets file; never embed provider credentials in the release
  artifact, repository, logs, or diagnostics.
- Align the credential sources for runtime detection: if a secrets file is meant
  to cover Pi as well, inject it into the Pi probe too, and make the setup note
  say explicitly that Pi needs `~/.pi/agent` or inherited provider keys.
