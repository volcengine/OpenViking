# Agent Handoff — V1: Per-Project Memory Isolation for Claude Code

You are implementing V1 of OpenViking's "global daemon, isolated projects"
feature for Claude Code users. This brief is self-contained; the full design
rationale lives in `explaining/local-embedding-installer-spec.md` and
`explaining/v1-implementation-plan.md` — read both before writing code.

## Context (30 seconds)

OpenViking (OV) is a local memory/context engine: a Python server
(`openviking-server`) + local vector store under `~/.openviking`, with a
Claude Code plugin at `examples/claude-code-memory-plugin/` that captures
conversation memories (Stop hook), recalls them (UserPromptSubmit hook), and
exposes MCP tools (`src/memory-server.ts`).

**The bug/gap:** the plugin uses one static agent identity for the whole
machine (`agentId` defaults to `"claude-code"` —
`examples/claude-code-memory-plugin/src/memory-server.ts:92`), so every
project on a machine shares one memory namespace. The server already enforces
per-tenant isolation in the vector store (`account_id` / `owner_agent_id`
filters); the client just never selects a per-project identity.

**The fix (V1):** explicit per-project registration. The user runs `/ov-init`
in a project; it writes a tiny `.ovproject` marker; the plugin resolves the
nearest marker and scopes all captures/recalls to that project's namespace.

## Locked decisions — do NOT relitigate or redesign these

1. **Explicit registration only.** No path-hash or git-toplevel heuristics.
   The user declares the root via `/ov-init`. Monorepo users run it in the
   package folder they want; **nested markers are allowed, nearest marker
   (walking up from cwd) wins**.
2. **Marker file `.ovproject`** at the project root, JSON:
   `{ "projectId": "<10 hex chars, crypto-random>", "name": "<slug>", "createdAt": "<ISO>" }`.
   It is an opaque ID card ONLY — it must never contain memories, preferences,
   embeddings, or config. All data stays in `~/.openviking`.
3. **`agentId = "cc-" + slug(name) + "-" + projectId`** (slug: lowercase,
   letters/digits/dash).
4. **Resolution priority:** explicit `claude_code.agentId` in `ov.conf`
   (machine-wide override / escape hatch) → nearest `.ovproject` → legacy
   `"claude-code"`. **No marker = exactly today's behavior. Zero breakage for
   existing users is a hard requirement.**
5. **Not committed.** The init script adds `.ovproject` to the repo's
   `.git/info/exclude` (per-clone ignore, itself never committed). Never touch
   the team's `.gitignore`.
6. **Nothing is ever re-embedded, re-indexed, or scanned.** `/ov-init` is
   O(1): one file write + one exclude line + one registry append. After init,
   the new namespace simply starts empty; legacy memories stay untouched under
   the old namespace. The `/ov-init` user-facing output must state this
   explicitly so it doesn't read as data loss ("previously captured project
   memories won't appear here; user-scope memories still follow you; nothing
   was deleted or recomputed").
7. **Capture and recall must share one resolver.** The Stop-hook capture path
   and the recall path must call the same identity-resolution function, or
   memories get written to a namespace recall never searches. This must be
   covered by a test.

## Milestone 1 — Isolation (plugin only, do this first, ship as its own PR)

All work in `examples/claude-code-memory-plugin/`. **No Python/server changes
in this milestone.**

1. **Resolver module** — new `scripts/project-marker.mjs`:
   - `findNearestMarker(startDir)`: walk up to filesystem root, return first
     directory containing `.ovproject`, else null.
   - `readMarker(path)`: parse JSON; malformed/unreadable → treat as absent
     and log one warning to the existing debug log (see `scripts/config.mjs`
     for the debug-log pattern), never crash a hook.
   - `resolveAgentId({ cwd, ovConfOverride })` implementing the priority
     order in locked decision 4.
   - Mirror the same logic in `src/memory-server.ts` (TypeScript, resolved
     once at startup from `process.cwd()`). Keep the two implementations
     trivially small so duplication is acceptable, or share via the built
     bundle if the existing build setup allows it cleanly — follow whatever
     the current build in `package.json` supports; do not restructure the
     build.
2. **`/ov-init` command** — new `commands/ov-init.md` (Claude Code plugin
   slash command, markdown prompt). It must instruct Claude to:
   - State the current directory and ask the user to confirm it is the
     project root (mention: monorepo users should instead run this inside the
     package folder they want as the memory boundary).
   - Ask for an optional display name (default: directory basename).
   - Run `node ${CLAUDE_PLUGIN_ROOT}/scripts/project-init.mjs --path <root> --name <name>`.
   - Relay the script's output, including the "nothing was deleted or
     recomputed" note from locked decision 6.
3. **Init script** — new `scripts/project-init.mjs`:
   - Generate `projectId` via `crypto.randomBytes(5).toString("hex")`.
   - Write `.ovproject`; if inside a git repo, append `.ovproject` to
     `.git/info/exclude` (idempotently).
   - Append `{projectId, name, path, createdAt}` to
     `~/.openviking/projects.json` (create if missing).
   - Print the resulting `agentId`.
   - Idempotent: existing marker → print existing identity, exit 0; refuse to
     overwrite without `--force`.
4. **Wire resolution into all surfaces:**
   - MCP server: replace the static default at `src/memory-server.ts:92`.
   - Hooks: `scripts/auto-recall.mjs` and `scripts/auto-capture.mjs` resolve
     per invocation from the `cwd` field in the hook's stdin JSON, through
     the shared resolver (`scripts/config.mjs` is the shared config loader —
     extend it, don't fork it).
5. **SessionStart nudge** — in `scripts/bootstrap-runtime.mjs`: when no
   marker is found AND no `claude_code.agentId` override is set, emit a
   one-line `additionalContext` suggesting `/ov-init`. Keep it to one line;
   do not block or prompt.
6. **Tests** (vitest is already configured — see `vitest.config.ts` /
   `__tests__/`):
   - nested markers → nearest wins; no marker → legacy id; malformed marker →
     legacy id + warning; override beats marker.
   - init script: idempotency, `--force`, exclude-file append idempotency.
   - capture and recall resolve identical `agentId` for the same cwd (locked
     decision 7).
7. **Build + version:** rebuild the bundled server (see `package.json`
   scripts → output `servers/memory-server.js`), bump plugin version to 0.2.0
   in `package.json` and `.claude-plugin/plugin.json`.

**M1 acceptance (automatable in sandbox):** unit tests above pass; plus an
integration-style test that stubs the OV HTTP API (the plugin's client is
plain `fetch` against `baseUrl`) and asserts that with markers in two temp
dirs A and B, capture from A and recall from B use different
`X-OpenViking-Agent` headers / agent identities, and recall from A matches
A's capture identity.

## Milestone 2 — Daemon lifecycle (second PR)

1. **`openviking_cli/service.py`** — new subcommand
   `openviking-server service install|uninstall|status`:
   - macOS: launchd agent `~/Library/LaunchAgents/ai.openviking.server.plist`
     (`RunAtLoad` + `KeepAlive`).
   - Linux: systemd user unit
     `~/.config/systemd/user/openviking-server.service`
     (`Restart=on-failure`).
   - Windows: print manual instructions, exit 0.
   - Follow the CLI wiring conventions of the existing subcommands (see how
     `doctor` is registered).
2. **Lazy start** in `scripts/bootstrap-runtime.mjs`: `GET /health` with a
   short timeout; if down, spawn `openviking-server` detached and poll
   readiness (≤60s; the SessionStart hook timeout is 120s in
   `hooks/hooks.json`). Use a lockfile under `~/.openviking/` to prevent
   double-spawns from concurrent sessions. Port the pattern from
   `examples/openclaw-plugin/process-manager.ts`.
3. **`doctor` additions** (`openviking_cli/doctor.py`): service
   registered/running; report the active embedding model prominently; report
   daemon RSS/CPU at rest and note Ollama's `keep_alive` model-unload
   behavior.

**M2 verification note:** launchd/systemd registration cannot be fully
exercised in a remote sandbox. Required instead: unit tests for plist/unit
generation (golden-file compare), `service status` parsing, and lockfile
logic; plus a `docs/` note with the manual verification steps (reboot →
start claude → recall works).

## Milestone 3 — Installer profile (third PR)

1. `openviking-server init --profile claude-code|claude-code-hybrid --yes --dry-run`
   in `openviking_cli/setup_wizard.py` (or a new `openviking_cli/profiles.py`):
   a profile pre-answers the wizard using the existing `EMBEDDING_PRESETS`,
   `_RAM_RECOMMENDATIONS`, `_build_ollama_config`. `--yes` never prompts and
   fails hard with one actionable message; `--dry-run` prints planned actions
   and the resulting `ov.conf` without writing.
2. Post-steps: invoke `service install` (M2); print the `/plugin marketplace
   add` + `/plugin install openviking-memory` commands.
3. `scripts/install.sh`: ensure `uv` → `uv tool install openviking` →
   `openviking-server init --profile "$1" --yes`.
4. Docs: update `docs/en/guides/12-local-embedding-for-claude-code.md` and
   the plugin README with the `/ov-init` flow, monorepo guidance, and the
   machine-level "pick your embedding model once" warning.

## Guardrails

- One PR per milestone, in order; M1 must not depend on M2/M3.
- Do not rename or repurpose existing `ov.conf` keys; `claude_code.agentId`
  keeps working as before (it becomes the override).
- Do not add heavy dependencies to the plugin (it currently uses node stdlib
  + `@modelcontextprotocol/sdk`); the resolver and init script must be
  stdlib-only.
- Hook budgets are tight (`auto-recall` timeout is 8s): resolver work in the
  hook path must be a few `stat` calls walking up, nothing more.
- Match existing code style in each area (mjs scripts vs TS server vs Python
  CLI). Do not reformat unrelated code.
- If the OV server cannot run in your environment (Ollama models likely
  unavailable), do NOT fake end-to-end results — stub the HTTP layer in tests
  and say plainly in the PR description what was verified vs. what needs a
  local machine.
- Commit messages: conventional style used in this repo (see `git log`),
  e.g. `feat(claude-code-plugin): per-project memory namespaces via /ov-init`.

## Report back (per PR)

- What was implemented, file by file.
- Test results (paste the vitest/pytest output).
- Anything verified only by stub/unit test that needs manual confirmation on
  a real machine, as a checklist.
- Any spot where reality contradicted this brief (file moved, API differs) —
  flag it, don't silently improvise around a locked decision.
