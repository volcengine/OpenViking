# Spec: One-Command Installer Profiles for Local-Embedding Setups

Status: draft / proposal
Companion docs: `docs/en/guides/11-local-embedding-for-codex.md`, `docs/en/guides/12-local-embedding-for-claude-code.md`

## Problem

Codex and Claude Code subscribers are the highest-volume "I want memory with zero extra API keys" audience, but getting them running today takes 6+ manual steps across three surfaces (pip, `ov.conf`, client plugin wiring), and the single most attractive configuration for Codex users — **local embedding + Codex-subscription VLM** — is not reachable through `openviking-server init` at all:

- `_wizard_cloud()` is the only flow that offers the `openai-codex` OAuth VLM, but it forces a cloud embedding API key first (`setup_wizard.py:948`).
- `_wizard_llamacpp()` offers Ollama / cloud-API-key / skip for VLM — no Codex option (`setup_wizard.py:864`).
- `_wizard_ollama()` hardcodes Ollama for VLM (`setup_wizard.py:689`).

Other friction points:

1. The wizard is interactive-only — no flags, so nothing (bootstrap script, plugin hook, agent-led install) can drive it unattended.
2. The wizard stops at `ov.conf`. Plugin wiring (`codex mcp add …`, `/plugin install …`) is a separate manual journey the user must discover in a different README.
3. The server must be started (and kept running) by hand; there is no service registration.
4. The GGUF path has exactly one preset, `bge-small-zh-v1.5-f16` (`setup_wizard.py:488`) — Chinese-optimized, wrong default for most English-speaking subscribers.
5. Users must already have a working Python 3.10+ / pip before anything else.

## Goal

One command from zero to working memory in the user's coding agent:

```bash
curl -fsSL https://openviking.ai/install.sh | sh -s -- --profile codex
```

or, with Python already present:

```bash
openviking-server init --profile codex --yes
```

## Non-goals

- Replacing the interactive wizard (it stays; profiles are a layer on top).
- Docker / k8s / remote deployments (covered by existing deployment guide).
- Windows service registration in v1 (doc the manual fallback; wheels-first install still applies).

## Design

### Profiles

A profile = deterministic bundle of {embedding choice, VLM choice, client wiring, service setup}. Proposed set:

| Profile | Embedding | VLM | Client wiring | Target user |
|---|---|---|---|---|
| `codex` | Ollama `qwen3-embedding:0.6b` (RAM-tiered) | `openai-codex` OAuth (import `~/.codex/auth.json`) | `codex mcp add openviking-memory …` | ChatGPT subscriber |
| `codex-min` | GGUF via llama.cpp (no daemon) | `openai-codex` OAuth | same | ChatGPT subscriber, no Ollama wanted |
| `claude-code` | Ollama (RAM-tiered) | Ollama VLM (RAM-tiered) | `/plugin` marketplace instructions or `claude plugin install` if CLI supports it | Claude subscriber, fully local |
| `claude-code-hybrid` | Ollama (RAM-tiered) | cloud key (prompted once, or `--vlm-api-key`) | same | Claude subscriber wanting better extraction |

Profiles reuse the existing preset tables (`EMBEDDING_PRESETS`, `VLM_PRESETS`, `_RAM_RECOMMENDATIONS`) and config builders (`_build_ollama_config`, `_build_local_config`) — a profile is essentially a pre-answered wizard run plus two new post-steps (wiring, service).

### Global-by-default: one daemon, one model, blind projects (Claude-first)

Installing OpenViking is a **machine-level** event, not a project-level one. The
default outcome of any profile is: one global daemon, one embedding model shared
by every project, and hard per-project data isolation — so that wherever Claude
is used from a terminal on that machine, OpenViking is already running in the
background with zero per-project setup. Claude Code users are the first target
for this default; Codex follows.

Three design consequences:

**1. One daemon, always up.** The server is already a global singleton — config
at `~/.openviking/ov.conf`, data under `storage.workspace` (default
`~/.openviking`). What's missing is that nothing keeps it alive. Service
registration (gap 5: launchd agent / systemd user unit) is therefore **promoted
from M3 polish to a core requirement of the `claude-code` profiles** — it is
what turns "the user must remember to start a server" into "OV is just there."
Belt-and-suspenders: the Claude Code plugin's `SessionStart` hook health-checks
`/health` and, if the daemon is down (fresh boot before service install, or
service disabled), spawns `openviking-server` detached and waits for readiness —
the openclaw plugin's `process-manager.ts` already implements this lazy-start
pattern and should be ported to the claude-code-memory-plugin.

*Idle footprint:* the always-on part is cheap. The daemon does nothing unless
a hook fires — embedding is event-driven through the queue, not continuous —
so steady state is one Python process + the local vector store (order of a
few hundred MB RAM, ~0% CPU). The embedding model itself lives in Ollama,
which unloads it after ~5 minutes idle by default, so its ~0.6–1 GB only
occupies RAM around actual capture/recall bursts. `doctor` should measure and
report the real numbers rather than us asserting them.

**2. Shared embedding model, isolated data.** Sharing the embedding model
across projects is safe and desirable: the model is stateless, so one Ollama
model resident in RAM serves N projects with no cross-contamination — isolation
never lives in the model, it lives in the store. And the store already has it:
every vector row carries `account_id` / `owner_user_id` / `owner_agent_id`, and
all searches are ANDed with tenant filters at the storage layer
(`_tenant_filter` / `_build_scope_filter`, see
`explaining/embedding-and-graph-system.md` §1.6, §2.3).

The gap is on the client side: the claude-code plugin currently uses a single
static agent identity for the whole machine (`agentId` defaults to
`"claude-code"`, `examples/claude-code-memory-plugin/src/memory-server.ts:92`),
so today **all projects land in one shared namespace**. Fix: **explicit
per-project registration** — the user, not a heuristic, declares the project
root.

- On the first session in an unregistered directory, the `SessionStart` hook
  injects a short notice: "This project isn't registered with OpenViking
  memory — run `/ov-init` to give it an isolated memory namespace."
- `/ov-init` (a plugin slash command) confirms the root with the user — "Is
  `<cwd>` the project root?" — and writes a marker file `.ovproject` there:

  ```json
  { "projectId": "a1b2c3d4e5", "name": "my-app", "createdAt": "…" }
  ```

  `projectId` is randomly generated and stored, so it is stable across moves
  and renames — which a derived path hash would not be. It is an opaque
  namespace label, nothing more: `.ovproject` contains **only identity
  metadata** — never memories, preferences, or embeddings. All actual data
  lives in `~/.openviking` on the user's machine. Writing the marker is O(1):
  one small file, **no repo scan, no indexing, no re-embedding** — `/ov-init`
  costs nothing regardless of repo size. Monorepo users simply run `/ov-init`
  inside the package folder they want as the memory boundary; nested markers
  are allowed and **nearest marker wins** (walking up from cwd), so a repo
  root and a sub-package can hold distinct namespaces.
- **Not committed (decided).** `/ov-init` adds `.ovproject` to the clone's
  `.git/info/exclude` — a per-clone ignore that is itself never committed —
  so the marker stays local to the user and invisible to the repo and
  teammates. Consequence: a fresh clone or a new machine re-runs `/ov-init`
  once (fine — memories are per-machine and don't travel with the repo
  anyway). Same applies to additional git worktrees.
- Resolution: the MCP server and hooks walk up from their `cwd` to the nearest
  `.ovproject` and compose `agentId = "cc-" + name + "-" + projectId`. No
  marker found → fall back to the current shared `"claude-code"` namespace
  (legacy behavior, zero breakage for existing users). The openclaw plugin's
  agent-prefix mechanism (`openclaw.plugin.json`) is the precedent for
  composed agent IDs.

This maps cleanly onto OV's two memory scopes:

| Scope | URI | Visibility |
|---|---|---|
| User | `viking://user/memories` | Global across projects — preferences, identity, "how I like to work" |
| Agent | `viking://agent/memories` (agent = per-project ID) | Private to one project — codebase facts, decisions, project state |

Auto-recall queries both scopes, so a session in project A gets the user's
global preferences plus only A's project memories; project B's data is
invisible to it by construction (server-side tenant filter, not client-side
politeness).

**3. Embedding model choice becomes a machine-level decision.** One shared
collection has one vector dimension, so the embedding model is picked once per
machine, not per project — switching later means re-embedding everything (this
sharpens open question 3). Profiles should surface this at install time
("model X will be used for all projects on this machine") and `doctor` should
report the active model prominently.



### CLI surface

```
openviking-server init
  --profile codex|codex-min|claude-code|claude-code-hybrid
  --embedding <preset>        # override profile default (e.g. embeddinggemma:300m)
  --vlm <preset>              # override profile default
  --yes / -y                  # non-interactive: accept defaults, fail hard instead of prompting
  --no-wire                   # skip client plugin wiring
  --no-service                # skip service registration
  --dry-run                   # print planned actions + resulting ov.conf, change nothing
```

Rules for `--yes` mode: never prompt; if a required dependency is missing and not auto-installable (e.g. Codex CLI not signed in), exit non-zero with a single actionable message. Existing `ov.conf` is backed up via the existing `.bak` rotation, but `--yes` refuses to overwrite unless `--force` is passed (safer for scripted runs).

### Execution plan per profile (example: `codex`)

1. **Detect**: OS/arch, RAM (`_get_system_ram_gb`), Codex CLI auth (`~/.codex/auth.json` or `OPENVIKING_CODEX_BOOTSTRAP_PATH`), Ollama presence, existing `ov.conf`.
2. **Provision embedding**: ensure Ollama installed + running (`_ensure_ollama`), pull the RAM-tiered embedding model.
3. **Provision VLM auth**: import Codex auth into `~/.openviking/codex_auth.json` (`_ensure_codex_auth`); in `--yes` mode, import silently if the bootstrap file exists, else fail with "run `codex login` first".
4. **Write config**: Ollama embedding block + `openai-codex` VLM block (`gpt-5.4`, `https://chatgpt.com/backend-api/codex`), local binding.
5. **Wire client**: locate/build the codex-memory-plugin server bundle and run `codex mcp add openviking-memory -- node <path>`. Ship the built `memory-server.js` inside the wheel (new package data) so no Node build step happens on the user's machine — Node 22+ runtime is still required and should be preflighted.
6. **Service**: register launchd agent (macOS) / systemd user unit (Linux) running `openviking-server`; `--no-service` prints the manual command instead.
7. **Verify**: run `openviking-server doctor`, then hit `/health`, then one end-to-end embed round-trip; print a copy-pasteable smoke-test prompt for the user's agent.

`claude-code` differs at step 3 (no Codex auth; VLM is a second Ollama pull) and step 5 (Claude Code plugin install is user-driven via `/plugin` today — the installer prints the exact two commands, and upgrades to automated install if/when a headless `claude plugin install` path is available).

### Bootstrap script

Thin `install.sh` that: checks for `uv` (installs if missing) → `uv tool install openviking` (isolated env, solves the "no Python" problem) → executes `openviking-server init --profile "$1" --yes`. Windows gets a `install.ps1` twin later; until then the guides' manual path applies.

### Agent-led install path

This repo already maintains agent-oriented install docs (`docs/en/getting-started/04-setup-for-agent.md`, openclaw `INSTALL-AGENT.md`). Profiles make those dramatically simpler and more reliable: the agent doc collapses to "confirm the user's subscription type, then run `init --profile … --yes --dry-run`, show the plan, run it for real." Non-interactive mode is what makes the agent path safe — no TTY prompts for the agent to fumble.

## Code changes required (by gap)

| # | Gap | Change | Where |
|---|---|---|---|
| 1 | No local-embed + Codex VLM path | Add `OpenAI Codex` to the VLM options of `_wizard_llamacpp` and `_wizard_ollama`, reusing `_ensure_codex_auth` and the `_DEFAULT_CODEX_MODEL`/`_DEFAULT_CODEX_BASE_URL` constants | `openviking_cli/setup_wizard.py` |
| 2 | Interactive-only | Introduce a `Profile` dataclass + `run_init(args)` branching; prompts become the fallback when a value isn't supplied by profile/flag | `openviking_cli/setup_wizard.py` (or new `openviking_cli/profiles.py`) |
| 3 | Single Chinese GGUF preset | Add an English/multilingual GGUF preset (e.g. `bge-small-en-v1.5` or an embeddinggemma GGUF) to `LOCAL_DENSE_MODEL_SPECS` + `LOCAL_GGUF_PRESETS`; make the English one the default for non-zh locales | `openviking/models/embedder/local_embedders.py`, `setup_wizard.py` |
| 4 | No plugin wiring | New `_wire_codex()` / `_wire_claude_code()` post-steps; ship built `memory-server.js` as package data | new module + `pyproject.toml` package data |
| 5 | No service management | `openviking-server service install|uninstall|status` (launchd/systemd user units) | new `openviking_cli/service.py` |
| 6 | Python bootstrap | `install.sh` (uv-based) hosted at a stable URL | repo `scripts/` + website |
| 7 | Single machine-wide agent ID → projects share one namespace | `/ov-init` command writes a `.ovproject` marker (stable random `projectId`); MCP server and hooks resolve the nearest marker upward from cwd; no marker → legacy shared namespace | `examples/claude-code-memory-plugin` (new command + `src/memory-server.ts` + hook scripts) |
| 8 | Daemon not guaranteed alive when a session starts | `SessionStart` health-check + detached lazy start (port `process-manager.ts` from openclaw plugin) | `examples/claude-code-memory-plugin` |

## Phasing

Claude users first: the global-daemon + per-project-isolation default ships on
the `claude-code` profiles before the Codex profile gets its installer polish.

- **M1 (unblocks the docs + isolation):** gap 1 + gap 3, plus gap 7
  (per-project `agentId`) — the isolation fix is client-side only, small, and
  every day it waits, users accumulate cross-project data in one namespace
  that is painful to migrate later.
- **M2 (Claude installer core):** gap 2 (profiles + `--yes` + `--dry-run`),
  gap 5 (service registration — required by `claude-code` profiles, not
  polish), and gap 8 (lazy start). Codex side of gap 4 lands here too.
- **M3 (polish):** gap 6, Windows story, and automated Claude Code plugin
  install when the CLI supports it.

## Open questions

1. Ship the Codex MCP server inside the Python wheel (Node source as package data) vs. publish it to npm and `npx` it? npm publish avoids bundling Node artifacts in a Python package but adds a registry dependency at install time.
2. Should `claude-code` profile default to hybrid instead of fully-local? Fully-local extraction quality on `qwen3.5:2b` may generate poor memories on 8 GB machines — needs a quality benchmark before choosing the default.
3. Does re-embedding tooling exist for users who later change embedding models? If not, profiles should record the chosen model prominently and the docs' "pick once" warning stays load-bearing.
4. Locale detection for GGUF default (zh vs en preset): env `LANG` sniffing, or just always ask/require a flag?
5. ~~Should `.ovproject` be committed to the repo or gitignored?~~ **Decided: not committed.** Kept local via `.git/info/exclude` (see design section). Revisit only if a shared-server story ever lands.
6. Migration for existing users: everyone on the current plugin has all projects under the shared `"claude-code"` agent. Ship a one-time split tool, or leave old data in the shared namespace (still recalled via user scope?) and only isolate going forward?
