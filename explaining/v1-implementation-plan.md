# V1 Implementation Plan — Global OpenViking for Claude Code

Status: draft
Parent spec: `explaining/local-embedding-installer-spec.md`
Target: Claude Code subscribers, macOS + Linux. Codex, Windows, and migration
tooling are explicitly out of V1.

## What V1 delivers

A Claude Code user goes from zero to isolated per-project memory like this:

```bash
curl -fsSL https://openviking.ai/install.sh | sh -s -- --profile claude-code
# … one-time: /plugin install openviking-memory (printed by the installer)
cd ~/code/my-app
claude
> /ov-init          # "Is ~/code/my-app the project root?" → yes
```

From then on: one global daemon (launchd/systemd user service) shares one
embedding model across every project on the machine; each `/ov-init`-ed
project has a private memory namespace; projects never see each other's data;
user-scope memories (preferences) follow the user everywhere. If the user
never runs `/ov-init`, everything behaves exactly as today (shared
`"claude-code"` namespace) — zero breakage.

## Design decisions locked for V1

1. **Explicit registration, not heuristics.** The user confirms the project
   root via `/ov-init`. Monorepo users run it inside the package folder they
   want as the boundary. Nested `.ovproject` markers are allowed; nearest
   marker (walking up from cwd) wins.
2. **Stable random `projectId`** stored in `.ovproject` — an opaque namespace
   label (no memories, preferences, or embeddings in the marker; all data
   stays in `~/.openviking`). Survives moves and renames; never derived from
   the path. Not committed to the repo (kept local via `.git/info/exclude`),
   so fresh clones/worktrees re-run `/ov-init` once.
3. **Identity resolution priority:** `claude_code.agentId` in `ov.conf`
   (explicit machine-wide override, escape hatch that disables per-project
   isolation) → nearest `.ovproject` → legacy `"claude-code"`.
4. **Isolation is server-enforced.** The plugin only *selects* the namespace
   (`agentId`); blindness between projects comes from the existing tenant
   filters in the vector store (`_tenant_filter`, see
   `embedding-and-graph-system.md` §2.3), not client politeness.

---

## Milestone 1 — Per-project isolation (plugin only)

All changes in `examples/claude-code-memory-plugin`. No server changes.
Estimated size: 2–4 days.

| # | Task | Where |
|---|------|-------|
| 1.1 | Shared resolver module: `findNearestMarker(startDir)` (walk up to filesystem root), `readMarker` (tolerate malformed JSON → treat as absent, log once), `composeAgentId(name, projectId)` → `cc-<slug(name)>-<projectId>` | new `scripts/project-marker.mjs` + mirrored logic in `src/memory-server.ts` |
| 1.2 | `/ov-init` slash command: prompt-driven — confirm `cwd` is the intended root (monorepo note in the prompt), ask for an optional display name, then run the init script | new `commands/ov-init.md` |
| 1.3 | Init script: generate `projectId` (10 hex chars, `crypto.randomBytes`), write `.ovproject`, add `.ovproject` to `.git/info/exclude` if inside a git repo (per-clone ignore, never committed, doesn't touch the team's `.gitignore`), append `{projectId, name, path, createdAt}` to `~/.openviking/projects.json` registry, print the resulting `agentId`. Idempotent: existing marker → print it and exit 0 (no overwrite without `--force`). O(1) always: one file write, no repo scan, no indexing | new `scripts/project-init.mjs` |
| 1.4 | Wire resolution into both surfaces: MCP server resolves once at startup from `process.cwd()` (`memory-server.ts:92` replaces the static default); hooks resolve per-invocation from the `cwd` field in hook stdin JSON (`auto-recall.mjs`, `auto-capture.mjs` via `config.mjs`) | `src/memory-server.ts`, `scripts/config.mjs` |
| 1.5 | Unregistered-project notice: `bootstrap-runtime.mjs` (SessionStart) emits one-line `additionalContext` suggesting `/ov-init` when no marker is found and no `agentId` override is set | `scripts/bootstrap-runtime.mjs` |
| 1.6 | Verify the server auto-creates an agent namespace on first write with a novel `agentId` (openclaw's agent-prefix flow implies it does; confirm and note in the plugin README) | manual check + README |
| 1.7 | Tests (vitest): nested markers → nearest wins; no marker → legacy id; malformed marker → legacy id + warning; init idempotency; override precedence | `__tests__/` (new) |
| 1.8 | Rebuild `servers/memory-server.js`, bump plugin to 0.2.0 | `package.json`, `.claude-plugin/plugin.json` |

**Acceptance:** with two registered projects A and B — capture a memory in A;
`auto-recall` in B does not surface it; recall in A does; a user-scope memory
is visible in both; an unregistered third project still sees the legacy shared
namespace and gets the `/ov-init` nudge.

## Milestone 2 — Daemon always up

Estimated size: 2–3 days.

| # | Task | Where |
|---|------|-------|
| 2.1 | `openviking-server service install\|uninstall\|status`: macOS launchd agent (`~/Library/LaunchAgents/ai.openviking.server.plist`, `RunAtLoad` + `KeepAlive`), Linux systemd user unit (`~/.config/systemd/user/openviking-server.service`, `Restart=on-failure`). Windows: print manual instructions, exit 0 | new `openviking_cli/service.py` |
| 2.2 | Lazy-start fallback in the SessionStart hook: `GET /health` with a short timeout; if down, spawn `openviking-server` detached and poll readiness (≤60s; hook timeout is already 120s). Use a lockfile in `~/.openviking/` so concurrent sessions don't double-spawn. Port the pattern from `examples/openclaw-plugin/process-manager.ts` | `scripts/bootstrap-runtime.mjs` |
| 2.3 | `doctor` additions: service registered/running, and report the active embedding model prominently ("model X serves all projects on this machine") | `openviking_cli/doctor.py` |
| 2.4 | Measure and report idle footprint: daemon RSS + CPU at rest, Ollama model residency (confirm the default ~5 min `keep_alive` unload; expose `keep_alive` tuning in `ov.conf` if users want faster unload on RAM-tight laptops). Surface in `doctor` output | `openviking_cli/doctor.py`, docs |

**Acceptance:** reboot the machine, `cd` into a registered project, start
`claude`, ask something that triggers recall — it works with no manual server
start.

## Milestone 3 — One-command install (claude-code profile)

Estimated size: 3–5 days.

| # | Task | Where |
|---|------|-------|
| 3.1 | Profile plumbing: `openviking-server init --profile claude-code|claude-code-hybrid --yes --dry-run`. A profile pre-answers the wizard (Ollama RAM-tiered embedding + VLM via existing `EMBEDDING_PRESETS` / `_RAM_RECOMMENDATIONS` / `_build_ollama_config`); `--yes` never prompts, fails hard with one actionable message; `--dry-run` prints planned actions + resulting `ov.conf` | `openviking_cli/setup_wizard.py` or new `openviking_cli/profiles.py` |
| 3.2 | Profile post-steps: run `service install` (M2), then print the exact `/plugin marketplace add …` + `/plugin install openviking-memory` commands (automated install deferred until the CLI supports headless plugin install) | same |
| 3.3 | `install.sh`: ensure `uv` (install if missing) → `uv tool install openviking` → `openviking-server init --profile "$1" --yes` | `scripts/install.sh` + hosting |
| 3.4 | Docs: update `docs/en/guides/12-local-embedding-for-claude-code.md` and the plugin README with the `/ov-init` flow, the machine-level "pick your model once" warning, and the monorepo guidance | docs |

**Acceptance:** on a clean macOS or Linux machine with Claude Code installed:
`install.sh --profile claude-code`, one plugin install, `/ov-init`, and the
M1/M2 acceptance scenarios pass end to end.

---

## Explicitly deferred (post-V1)

- **Codex profile installer** (spec gaps 1, 3 — the English GGUF preset is not
  blocking V1 because the claude-code profile defaults to Ollama).
- **Migration/split tool** for data accumulated in the legacy shared
  `"claude-code"` namespace (spec open question 6). V1 stance: legacy data
  stays where it is and remains reachable in unregistered dirs; registered
  projects start fresh.
- **Windows** service + installer.
- **Automated Claude Code plugin install** when a headless path exists.
- **Re-embedding tooling** for model changes (spec open question 3).

## Risks / watch items

- **Hook cwd vs MCP-server cwd drift.** The MCP server resolves identity once
  at startup; hooks resolve per call. A session that wanders across project
  boundaries could disagree. V1 stance: a Claude Code session belongs to one
  project; document it. If it bites, add re-resolution on the server side per
  request.
- **Auto-capture attribution.** The Stop-hook capture path must use the same
  resolver as recall, or memories land in the wrong namespace — covered by
  routing both through the shared module (task 1.1/1.4), guarded by a test.
- **`.ovproject` in VCS — decided: not committed.** The init script adds it to
  `.git/info/exclude` (per-clone, itself never committed), keeping the marker
  local to the user and invisible to teammates. Cost: fresh clones, new
  machines, and additional git worktrees re-run `/ov-init` once — acceptable,
  since memories are per-machine and never travel with the repo.
- **Empty namespace after `/ov-init`** in a project with prior legacy
  memories: the new namespace starts *empty* — old memories were saved under
  the shared legacy namespace name and are simply not matched by the new
  filter. To be explicit: **nothing is recomputed, re-indexed, or re-embedded
  — ever.** `/ov-init` writes one small file; memories are conversation-derived,
  not a repo index, so there is no expensive "re-initialization" on any
  hardware. The only user-visible effect is that previously captured
  project memories stop appearing in recall (user-scope memories still
  follow the user). The `/ov-init` output must say this explicitly so it
  doesn't read as data loss.
