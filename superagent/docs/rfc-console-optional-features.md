# RFC: Console optional features

- Status: Proposed
- Date: 2026-11-14
- Related implementation: PR #7699 (isolated GitHub login in setup)

## Problem

Optional capabilities do not all belong in the first-run setup wizard. Setup should get an instance to a working state; asking about every integration makes that path longer and mixes one-time onboarding with capabilities an operator may want to add, remove, or repair later.

GitHub is a useful first example. The account is shared by OVSA instances rather than tied to a chat connector, and it may be needed only for development tasks. It needs a visible place to connect, inspect, reauthorize, and disconnect without rerunning instance setup.

## Proposal

Add an **Optional features** page to the OVSA console. It lists machine-wide capabilities independently of instance onboarding. Each row shows its purpose, availability, current state, and one primary action.

Initial entries:

| Feature | States | Primary actions |
| --- | --- | --- |
| GitHub | Not connected, connecting, connected, attention required | Connect, reauthorize, disconnect |
| Future machine-wide integration | Not installed, disabled, enabled, attention required | Install/enable, configure, disable |

The list is data-driven so a new optional feature does not require redesigning the page. Only implemented features are rendered; this RFC does not propose showing placeholders for unbuilt integrations.

For GitHub, **Connect** starts gh's OAuth device flow. The console displays the verification URL and user code and polls the local operation until it succeeds, expires, or is cancelled. A connected row shows the GitHub login, not the token. Reauthorization uses the same flow. Disconnect removes only OVSA's isolated credentials and manifest entries, never the host gh or Git configuration.

First-run setup remains limited to required instance configuration. Once the console flow ships, remove the GitHub question from setup. Existing isolated accounts continue working and appear as connected in the console; no credential migration is needed.

## Local API

Add local-only Core endpoints, authenticated the same way as other console mutations:

- `GET /api/features`: implemented features, availability, state, and non-secret account metadata.
- `POST /api/features/github/connect`: start one device-flow attempt and return an operation ID plus the device verification fields.
- `GET /api/features/github/operations/<id>`: return pending/succeeded/failed/expired state without exposing gh output that might contain sensitive data.
- `DELETE /api/features/github/operations/<id>`: cancel the attempt and terminate its process tree.
- `DELETE /api/features/github`: disconnect OVSA's account after confirmation.

The daemon owns gh subprocesses. The browser never supplies a token and never reads `hosts.yml`. Concurrent connect attempts for GitHub are rejected or replace an explicitly cancelled attempt. Operation state is bounded and expires.

## Configuration and credential boundary

Use the same isolation as PR #7699:

- gh configuration: `~/.ov-superagent/github/gh`
- Git global configuration: `~/.ov-superagent/github/gitconfig`
- manifest environment: `GH_CONFIG_DIR`, `GIT_CONFIG_GLOBAL`, and `GIT_CONFIG_NOSYSTEM=1`
- no `GH_TOKEN`, `GITHUB_TOKEN`, or other credential value in the manifest, browser response, or worker environment

The private Git configuration uses `gh auth git-credential` for GitHub HTTPS. Workers inherit only these paths and behavior flags. Host/system Git configuration, inherited GitHub tokens, and injected Git credential overrides stay outside the OVSA account boundary. POSIX directories/files use 0700/0600; Windows relies on the profile ACL.

Changing feature configuration updates the manifest atomically and restarts affected OVSA processes through the existing control path. A failed update leaves the previous working configuration active.

## Console behavior

The page explains consequences next to the action rather than putting every detail in the feature list. For GitHub, the connect dialog says that OVSA uses a separate account and does not alter the machine's existing gh/Git configuration.

Device authorization must work when the console is opened from another machine: show a selectable URL and code, not a localhost-only link or a QR code as the sole option. Cancellation and expiry return the row to its previous state. Errors are actionable, such as installing gh, checking network access, or retrying authorization.

## Testing and rollout

1. Land the shared isolated gh/Git implementation and its unit tests.
2. Add Core operation lifecycle tests: success, cancellation, expiry, concurrent attempts, process failure, manifest conflict, and restart failure.
3. Add console tests for state rendering, device-code display, polling, and disconnect confirmation.
4. Validate device authorization on Linux and Windows, including a host already logged into a different GitHub account and a host with inherited `GH_TOKEN` / `GITHUB_TOKEN`.
5. Move the entry point from setup to the console and verify existing installations discover their account without reauthorization.

## Non-goals

- A general plug-in marketplace or arbitrary script installation from the console.
- Repository- or instance-specific GitHub accounts.
- Importing the host's existing gh credentials.
- Putting GitHub credentials in OpenViking memory or browser storage.
