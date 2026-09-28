# OpenViking Memory Plugin for Codex and TraeCode CLI 2.0

Long-term semantic memory for [Codex](https://developers.openai.com/codex), powered by [OpenViking](https://github.com/volcengine/OpenViking).
TraeCode CLI 2.0 supports the same plugin format; use the shared installer's dedicated `--harness trae-cli` entry.

> **Requires an OpenViking server with `viking://~` home-alias support.** Recall targets the
> caller's own context space through `viking://~/memories` and `viking://~/skills`; the uid-less
> `viking://user/memories` shorthand is rejected by newer servers.

This is the Codex counterpart to [`claude-code-memory-plugin`](../claude-code-memory-plugin). It hooks Codex's lifecycle to:

- **Session-start profile injection** on `startup`, `clear`, and `resume`: load `profile.md` plus abstract-annotated indexes of `preferences/` and `entities/` through the shared CJK-aware profile builder, followed by an `<available-skills>` catalog of your own and account-shared OpenViking skills.
- **Auto-recall** relevant memories on every `UserPromptSubmit` and inject them via `hookSpecificOutput.additionalContext`
- **`viking://` notice on `PreToolUse` (`Bash`)**: a shell command that carries a `viking://` URI still runs, and the model is told that the URI is an OpenViking virtual path and which MCP tool reads it.
- **Incremental capture on `Stop`** (turn end): append the new user/assistant turns to a deterministic OpenViking session id `cx-<codex_session_id>`. When `pending_tokens` reaches `OPENVIKING_COMMIT_TOKEN_THRESHOLD`, commit while keeping a recent live tail.
- **Commit on `PreCompact`**: trigger OpenViking's memory extractor on the full pre-compact transcript before Codex summarizes it.
- **Commit on `SessionEnd`** (Codex ≥ 0.145): when a thread shuts down gracefully, catch up any turns `Stop` never sent and commit the OV session, so the extractor runs on the whole conversation the moment you leave.
- **Fallback sweep on `SessionStart` (source=startup|clear)**: commit state files that carry an end marker whose commit did not go through, or that have been idle past `OPENVIKING_CODEX_IDLE_TTL_MS`. `source=resume` never commits or sweeps; if the live OV session was already committed, it combines the profile block with the latest archive summary for continuity. See `DESIGN.md` for the full decision tree.

It also starts a local stdio MCP proxy that forwards to OpenViking's native `/mcp` endpoint with credentials resolved from env / `ovcli.conf`, so the model has direct access to the server's retrieval, memory, resource, skill (`add_skill`), watch, filesystem, and code-navigation tools.

## Quick Start

There are two install paths. **Pick one — don't mix them** (both surface the same `openviking-memory` plugin; enabling it from both would run the hooks twice). The **one-line installer (A)** is the recommended path for most users; the marketplace install (B) is useful when you already manage `~/.openviking/ovcli.conf` yourself.

### A. One-line installer — `curl | bash` (recommended)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness codex
```

For TraeCode CLI 2.0:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness trae-cli
```

Claude Code and Codex share this installer (drop `--harness codex` to pick interactively). It asks for your language (English/中文), the download source (GitHub, or a TOS mirror for GitHub-blocked regions — pass `--dist tos`; Codex on TOS installs from a TOS-hosted git repo and keeps remote updates), and your OpenViking credentials. It:

1. Checks `codex` and Node.js 18+ (the plugin itself wants Codex's bundled Node 22+ at runtime)
2. Sets up `~/.openviking/ovcli.conf` interactively
3. Registers the `openviking` marketplace — remote git by default (`codex plugin marketplace add https://github.com/volcengine/OpenViking.git`), or this checkout / a TOS archive in dev/archive mode — and enables `openviking-memory@openviking` with `features.plugin_hooks = true`
4. Keeps the checked-in stdio `.mcp.json` intact; `servers/mcp-proxy.mjs` reads your active `ovcli.conf` at runtime
5. Runs plugin-list and stdio MCP validation

After install:

```bash
codex             # first run: pick "Trust all and continue" at the hook review prompt
```

Startup stops on `6 hooks need review` — pick **Trust all and continue**. Every later update that touches a hook asks again, for however many changed. Choosing *Continue without trusting*, or skipping the prompt, leaves the hooks off: MCP tools still work, but recall and capture never fire. Two independent switches have to be on to get them back: `/hooks` (hook trust and on/off) and `/plugins` (the plugin's own enabled state). The same applies to TraeCode CLI 2.0, which runs this plugin under `trae-cli`.

### B. Codex marketplace install

This path uses the same checked-in stdio MCP proxy as the installer path. Authenticated and remote/cloud servers work when `~/.openviking/ovcli.conf` or the relevant `OPENVIKING_*` env vars are present in Codex's environment.

The repo ships a Codex marketplace catalog at `.agents/plugins/marketplace.json`, so you can install with Codex's native commands:

```bash
# 1. add the OpenViking marketplace (use volcengine/OpenViking once merged
#    upstream, or <your-fork>/OpenViking while testing a fork)
codex plugin marketplace add volcengine/OpenViking

# 2. install the plugin from that marketplace
#    (older Codex builds spell this `codex plugin install`)
codex plugin add openviking-memory@openviking
```

Then enable plugin hooks (if your Codex build doesn't already) by adding to `~/.codex/config.toml`:

```toml
[features]
hooks = true
# plugin_hooks = true  # for older Codex releases
```

Finally start Codex and trust the plugin hooks once:

```bash
codex            # then trust the hooks at the startup prompt, or via /hooks
```

> **Requirements & notes**
>
> - **Codex version**: this path relies on Codex injecting and inline-substituting `${PLUGIN_ROOT}` in plugin hook commands (current Codex does both). On an older Codex that doesn't substitute `${PLUGIN_ROOT}`, the hook script paths won't resolve — use path **A**.
> - **Catalog source**: the catalog entry (`.agents/plugins/marketplace.json`) uses a relative source (`./examples/codex-memory-plugin`). `codex plugin add` therefore installs the plugin from the same marketplace snapshot/ref that you added. This keeps fork, branch, tag, and upstream-main installs reproducible and testable without rewriting the catalog.

This path works out of the box against an unauthenticated local OpenViking at `http://127.0.0.1:1933`. For remote/cloud servers, create `~/.openviking/ovcli.conf` with `url`, `api_key`, and optional `account` / `user`; the proxy reads it when Codex starts.

### Manual setup

If you don't want the installer touching your rc, do these things yourself:

1. **Write `ovcli.conf` once** so hooks and MCP share the same connection:

   ```json
   {
     "url": "https://your-openviking-server.example.com",
     "api_key": "<your-api-key>",
     "account": "my-team",
     "user": "alice"
   }
   ```

   Or run the bundled interactive wizard: `node scripts/setup.mjs` (from the plugin directory).

2. **Add the plugin** via the remote marketplace (path B above), or via a local directory marketplace: `codex plugin marketplace add <checkout>/examples` reads `examples/.agents/plugins/marketplace.json` and yields the same `openviking-memory@openviking` id. `hooks/hooks.json` needs no rendering on modern Codex: it uses the native `${PLUGIN_ROOT}` token, which Codex injects into the hook env and substitutes inline.

## Configuration

Connection / identity source (applies to hooks, MCP, and `ov` commands run inside Codex):

1. **Default (auto)**: env-var credentials (`OPENVIKING_URL` / `OPENVIKING_BASE_URL`, `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN`, `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, `OPENVIKING_PEER_ID`) win when any is set; otherwise the active `ovcli.conf` is used: `OPENVIKING_CLI_CONFIG_FILE` or `~/.openviking/ovcli.conf`. With no credential env vars set, `ov config switch <name>` changes the active credentials for the CLI, hooks, MCP, and child `ov` commands together.
2. **Forced**: set `OPENVIKING_CREDENTIAL_SOURCE=cli` to force `ovcli.conf`, or `OPENVIKING_CREDENTIAL_SOURCE=env` to read env vars only, with neither config file.
3. **Fallback**: without credential env vars or an ovcli config, `ov.conf` is used (`server.url` / `server.root_api_key` plus legacy `codex.*` tuning); then `http://127.0.0.1:1933` unauthenticated.

The MCP proxy loads its connection through the same `loadConfig()` as the hooks, so the model tools and lifecycle hooks use the same target and key, including one set only in ovcli.conf's `plugin.codex` section. Codex passes the proxy only the variables `.mcp.json` lists, and that list covers every variable the connection reads.

Auth is sent as `Authorization: Bearer <api_key>` to both the REST API (used by hooks) and the `/mcp` endpoint (used by the model), and as nothing else — the hooks used to repeat the key as `X-API-Key`, which a gateway of your own can still add if it needs one. `account` and `user` go out as `X-OpenViking-Account` / `X-OpenViking-User` only in trusted mode; an `api_key` server reads both out of the key and ignores the headers.

By default the hooks derive the peer from git rather than from where the repository happens to sit: the normalized `origin` URL, else the repository root path. Outside a repository nothing is sent, and what is remembered there goes to your user-level space at `viking://user/<you>/memories`. In `/Users/x/Dev/OpenViking/examples/codex-memory-plugin` with origin `git@github.com:volcengine/OpenViking.git` the peer is `github.com-volcengine-openviking`, and it stays that from any subdirectory, worktree, machine or clone. Every clone of one repository therefore shares one project memory; a fork has a different origin and stays separate, and `gh pr checkout` of an external PR leaves `origin` alone, so reviewing one does not move the identity. Derivation is pure filesystem work — no `git` subprocess — so it also holds where `git` is missing from `PATH` or would refuse the repository over dubious ownership. Hooks pass the effective peer as `peer_id` for captured session messages and as `X-OpenViking-Actor-Peer` for retrieval and filesystem calls.

`OPENVIKING_PEER_SOURCE` (or `plugin.peerSource` / `plugin.codex.peerSource` in `ovcli.conf`, or `peer.source` in a workspace config file) picks the rule:

| Value | Meaning |
|---|---|
| `git` | Default. Same as `["{git_remote}", "{git_root}"]`: normalized origin, else repository root. Outside a repository nothing is sent. No prefix is added. |
| `cwd` | The previous behaviour, byte for byte — every non-letter-or-digit character becomes `-`, so `/Users/x/Dev/OpenViking` becomes `-Users-x-Dev-OpenViking`. |
| `none` | Send no peer at all; `OPENVIKING_WORKSPACE_PEER=0` and `codex.workspacePeer=false` still mean this. |
| a template | `"git-{git_remote}"`, `"team-{dir}"`, or a list tried in order; a template with an empty variable falls through to the next. |

The variables are `{git_remote}`, `{git_root}`, `{cwd}` and `{dir}` — see [Workspace Peers](../memory-plugin-shared/README.md#workspace-peers) for what each resolves to. `{git_root}` is empty outside a repository; `{cwd}` is never empty but sits in no default chain, so a bare path becomes a peer only when you ask for one; `{dir}` is the workspace root's directory name — the repository root, or the directory holding `.openviking/config.json` — and is empty when the directory is not a workspace.

To give a directory that is not a repository its own peer, create `.openviking/config.json` there holding `{"version": 1, "peer": {"id": "my-project"}}`.

Set `actor_peer_id` in `ovcli.conf` (or `OPENVIKING_PEER_ID` with `OPENVIKING_CREDENTIAL_SOURCE=env`) to pin an explicit peer instead of deriving one. The legacy `codex.peerId` / `codex.peer_id` fields in `ov.conf` still resolve as a fallback.

Upgrading from the path-derived peer needs no action: memories written under the old id stay reachable. With the default `peer_scope: "all"` the server's cross-peer sweep already covers them at no cost; with `actor` scope the hooks ask the old peer separately. There is no deadline, and `OPENVIKING_PEER_SOURCE=cwd` restores the old id outright.

Recall defaults to broad mode: global memory, the current workspace, and other workspace memories can all be recalled, with other workspaces ranked lower and rendered later. In this mode, the MCP proxy omits `X-OpenViking-Actor-Peer` so it can read any URI returned by broad recall for the authenticated user.

Set `OPENVIKING_RECALL_PEER_SCOPE=actor` or `codex.recallPeerScope="actor"` for isolation mode, which only sees global memory plus the configured peer. The MCP proxy requires `actor_peer_id` or `OPENVIKING_PEER_ID` in this mode and exits with a configuration error if neither is set. In deployments where one bot serves multiple people, such as zouk, vikingbot, or AstrBot, use isolation mode with an explicit actor peer so sessions cannot read another person's memories.

The checked-in `.mcp.json` contains only a stdio command. It never stores server URLs, bearer-token env mappings, or identity headers, so switching `ovcli.conf` changes the MCP target on the next Codex launch without cache rendering.

### Tuning the plugin

All plugin behavior is controlled by `OPENVIKING_*` environment variables. Connection and identity should normally live in `ovcli.conf`; tuning vars can be exported in your shell rc when you want every Codex launch to pick them up.

```sh
# ~/.zshrc — examples
export OPENVIKING_RECALL_LIMIT=10
export OPENVIKING_RECALL_COMPRESS=1
export OPENVIKING_RECALL_COMPRESS_MODEL=gpt-5.3-codex-spark
export OPENVIKING_RECALL_COMPRESS_THINKING=default
export OPENVIKING_RECALL_COMPRESS_BASE_URL=https://api.example.com/v1
export OPENVIKING_RECALL_TIMEOUT_MS=120000
export OPENVIKING_CAPTURE_ASSISTANT_TURNS=1
export OPENVIKING_AUTO_COMMIT_ON_COMPACT=1
export OPENVIKING_PROFILE_TOKEN_BUDGET=10000
export OPENVIKING_SKILL_CATALOG=1
export OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET=1200
export OPENVIKING_SESSION_START_MAX_BYTES=9500
export OPENVIKING_DEBUG=1
```

Full list: see the `Misc env vars` block in `scripts/config.mjs`. Tuning fields have `OPENVIKING_*` counterparts and env vars win for those tuning fields.

#### Private-gateway extra headers

Some private OpenViking deployments sit behind a gateway that requires custom headers on every request. The stdio MCP proxy reads those from `OPENVIKING_EXTRA_HEADERS`, a JSON object of scalar values (header name → header value) — see your deployment's gateway docs for the exact header names it expects.

```sh
export OPENVIKING_EXTRA_HEADERS='{"<header-name>":"<header-value>"}'
```

Reserved headers (`Content-Type`, `Accept`, `MCP-Protocol-Version`, `Mcp-Session-Id`, `Authorization`) are dropped with a stderr warning: the proxy owns those and letting an env var override them would break session negotiation or expose the wrong credential. Bad JSON is ignored with a warning rather than crashing the proxy. This env var is scoped to the stdio MCP proxy; hooks read credentials through the ovcli chain and do not consult it.

#### Input filters

Two knobs put an ordered list of regex rules in front of the text the plugin sends: `recallQueryFilters` / `OPENVIKING_RECALL_QUERY_FILTERS` shapes the prompt before it becomes a search query, and `captureFilters` / `OPENVIKING_CAPTURE_FILTERS` shapes every turn on the write path before it is stored.

Rules are sed-style strings applied in order to one piece of text: `s<d>pattern<d>replacement<d>[flags]` substitutes, `d<d>pattern<d>[flags]` drops the text on a match, and `k<d>pattern<d>[flags]` keeps it only on a match (chain them for AND). `<d>` is any punctuation delimiter — `/`, `|`, `#`, `:` — escaped with `\` inside the pattern; flags are `i`, `m`, `s`, `u`, `g`. A `user:` or `assistant:` prefix limits a rule to that role.

```sh
# strip a thinking-keyword prefix, and don't recall on slash / bash-mode prompts
export OPENVIKING_RECALL_QUERY_FILTERS='s/^\s*(ultrathink|think harder?)\s+//i,d|^\s*[/!]|'
# redact tokens before they are stored, and never store /clear or /compact turns
export OPENVIKING_CAPTURE_FILTERS='s/\b(sk|ghp|xoxb)_[A-Za-z0-9_-]+/[redacted]/g,user:d/^\s*\/(clear|compact)\b/'
```

The env vars are comma-separated lists, split before parsing, so a rule needing a literal comma — a bounded `{10,}` quantifier, say — belongs in the `ovcli.conf` array instead, where only trimming happens:

```json
{
  "plugin": {
    "codex": {
      "captureFilters": ["s/\\b(sk|ghp)_[A-Za-z0-9_-]{10,}/[redacted]/g"]
    }
  }
}
```

Rules run top to bottom and the first `d` that matches (or `k` that does not) ends the decision; text a substitution empties is not a drop, just too short to recall on. Filters run before `OPENVIKING_MIN_QUERY_LENGTH` and before the built-in ack / slash-command heuristics. A capture rule shapes what is sent, not what is already stored, and anything already in the pending queue carries the rules that were in effect when it was enqueued; adding a `d`/`k` rule mid-session also shortens the turn list the cursor counts, which reads as a transcript rewrite and replays from the last user turn. A rule that fails to compile is skipped, never fatal — `ov-memory-doctor` lists the active rules and reports the exact error for the ones it could not parse.

#### Workspace configuration files

A repository can carry its own plugin settings in `<repo-root>/.openviking/config.json`, which the team commits, and `<repo-root>/.openviking/config.local.json`, which stays private and gitignored. A third layer, this machine's entry under `~/.openviking/workspaces/`, outranks both, and all three outrank `ovcli.conf`.

```json
{
  "version": 1,
  "peer": { "source": "git" },
  "recall": { "peer_scope": "actor" },
  "bypass": { "session_patterns": ["**/fixtures/**"] }
}
```

`version: 1` is required; a file declaring another version is skipped with a warning. Schema v1 is `peer.source`, `peer.id`, `recall.enabled`, `recall.peer_scope`, `recall.dedup_turns`, `recall.max_items`, `recall.score_threshold`, `capture.enabled`, `capture.commit_token_threshold`, `bypass.session_patterns`, and `labels`. Lists union across layers, and a leading `"!reset"` drops what was inherited. Unknown keys are kept and ignored.

These files are trusted without a prompt, because a hook is non-interactive and an approval gate would mean one command per workspace. What is refused is structural: connection and credential keys (`url`, `api_key`, `account`, `user`, `extra_headers`, …) are stripped with a warning and `${VAR}` is never expanded in them. What a committed file switches off is announced by `$ov-memory-doctor` rather than blocked.

Keep `.gitignore` from ignoring all of `.openviking/`, or `config.json` can never be committed — narrow the rule to `.openviking/media/` and `.openviking/downloads/`. The doctor warns while the blanket rule is in place.

#### Legacy `codex` block in `ov.conf`

Earlier plugin versions configured tuning fields under a `codex` block in `~/.openviking/ov.conf`. That still works for backward compat — every env var above has a camelCase counterpart (`OPENVIKING_RECALL_LIMIT` → `codex.recallLimit`, etc.) — but **new deployments should prefer env vars**: this is the codex CLI's per-machine plugin tuning, and the server-side `ov.conf` is the wrong place for it. (It's read from `ov.conf`, not `ovcli.conf`, by historical accident in `scripts/config.mjs`.)

## Architecture

```
   ┌────────────────────────────────────────────────────────────────────────┐
   │                                 Codex                                  │
   └──┬─────────────────┬────────────────┬──────────────────┬───────────┬───┘
      │                 │                │                  │           │
 SessionStart      UserPromptSubmit    Stop             PreCompact   SessionEnd
 (startup|clear|resume) │              (per turn)           │      (graceful exit)
      │                 │                │                  │           │
 ┌────▼──────────┐ ┌────▼──────┐ ┌──────▼──────┐ ┌─────────▼──────┐ ┌───▼─────────┐
 │ session-start │ │ auto-     │ │ auto-       │ │ pre-compact-   │ │ session-    │
 │ -commit.mjs   │ │ recall.mjs│ │ capture.mjs │ │ capture.mjs    │ │ end.mjs     │
 │ (profile +    │ │ (search + │ │ (append +   │ │ (commit + reset│ │ (mark +     │
 │ fallback sweep│ │ compress) │ │ threshold)  │ │ ovSessionId)   │ │ catch-up +  │
 │ + resume      │ │           │ │             │ │                │ │ commit)     │
 │ archive)      │ │           │ │             │ │                │ │             │
 └────┬──────────┘ └────┬──────┘ └──────┬──────┘ └─────────┬──────┘ └───┬─────────┘
      │                 │                │                  │           │
      │             ┌───▼────────────────▼──────────────────▼───────────▼──┐
      └────────────►│                OpenViking REST API                   │
                    │ /api/v1/search/{recall,search}                       │
                    │ /api/v1/sessions [+/{id}/{messages,commit}]          │
                    │ /api/v1/content/read                                 │
                    └─────────────────┬───────────────────────────────────┘
                                      │
   Codex ◄── stdio MCP proxy ──► /mcp (find, search, read,
              (env/ovcli.conf)      remember, resources, add_skill,
                                  watches, filesystem)
```

The checked-in `.mcp.json` starts `servers/mcp-proxy.mjs` with `node`. The proxy keeps stdout protocol-clean, reads the same credential sources as the hooks, sends auth and identity headers to `/mcp`, caches the server `mcp-session-id`, and transparently reinitializes once if the server restarts.

For details on OpenViking's MCP endpoint, tools, and protocol, see the [MCP Integration Guide](../../docs/en/guides/06-mcp-integration.md). The tools list and per-tool semantics are documented there once, not duplicated here.

## How It Works

> See [`DESIGN.md`](./DESIGN.md) for the commit decision tree — it's the source of truth for *which* OpenViking session is sealed by *which* hook event.

### SessionStart profile injection and fallback sweep

Codex fires `SessionStart` with one of three `source` values: `startup` (fresh process / `/new` / zouk daemon spawn-without-sessionId), `resume` (`/resume` or short reconnect), and `clear` (`/clear` — the previous transcript is orphaned and a new session_id is created). `resume` never commits or sweeps; on `startup` and `clear` the hook runs the fallback sweep.

`hooks.json` registers `SessionStart` with `matcher: "clear|startup|resume"` so codex's dispatcher invokes the script on all three relevant sources. `session-start-commit.mjs` gates internally so only `startup` and `clear` sweep.

On all three sources, the hook uses the same shared `buildProfileBlock()` implementation as the Claude Code, OpenCode, and pi integrations. It reads the user's `profile.md` and adds URI plus abstract indexes for `preferences/` and `entities/`, with a CJK-aware token budget. The default budget is `10000`; set `OPENVIKING_PROFILE_TOKEN_BUDGET` or `plugin.codex.profileTokenBudget` to change it. Set `OPENVIKING_NO_AUTO_INJECT=1` or `plugin.codex.noAutoInject=true` to disable only this fixed profile/background injection, skill catalog included; per-prompt semantic recall remains controlled separately by `OPENVIKING_AUTO_RECALL`.

The same builder appends an `<available-skills>` block after `<user-profile>` and `<available-memories>`, inside the same `<openviking-context source="session-start">` envelope. One `GET /api/v1/skills?node_limit=200` call returns your own skills and the ones shared with the account under `viking://agent/skills`. Your own skills are listed first, and a shared skill with the same name as one of yours is left out. Each description is cut to about 40 tokens (CJK-aware), and envelope tags inside a description are escaped.

```text
<openviking-context source="session-start">
<user-profile uri="viking://user/default/memories/profile.md">...</user-profile>
<available-memories>...</available-memories>
<available-skills>
  OpenViking skills (stored in OpenViking, not local files). Before following one, read <dir>/<name>/SKILL.md with the OpenViking read tool.
  viking://user/default/skills/
    - pr-review — Review a pull request against the team checklist.
  viking://agent/skills/
    - deploy-runbook — Shared deployment runbook for the payments service.
</available-skills>
</openviking-context>
```

The catalog has its own budget, `OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET` or `plugin.codex.skillCatalogTokenBudget` (default `1200`, range `0`–`20000`), and never draws on the profile budget. Every entry keeps its description when that fits; otherwise the catalog lists names only, ending with `... +N more, search OpenViking skills to find the rest` if even the names do not all fit; when not even one name fits, the block shrinks to the single line `<available-skills>N OpenViking skills; search OpenViking skills to find them.</available-skills>`. Set `OPENVIKING_SKILL_CATALOG=0`, `plugin.codex.skillCatalog=false`, or the budget to `0` to leave the catalog out. With no skills, or against a server without `GET /api/v1/skills`, the block is omitted. The bundled `$openviking-skills` skill tells the model how to find a skill, create or replace one with MCP `add_skill`, install one from Git or a local folder, share one to `viking://agent/skills`, and run a one-time migration of local skills that the user asks for and approves skill by skill.

On `startup` or `clear`, the script walks every state file except the new session_id and, for each one that still holds a live `ovSessionId` or carries an end marker:

1. **`ended_retry`**: an `.ended.<timestamp>` marker is present, meaning `SessionEnd` fired but its commit never completed (server down, worker killed). Commit it now. A marker is swept even when the state has no live `ovSessionId`: `PreCompact` releases the id but leaves the cursor behind, so the catch-up under the lock is the only way the tail turns are ever sent, and it derives a live id by itself as soon as it has something to send.
2. **`idle_ttl`**: no marker, but the state has been idle for more than `OPENVIKING_CODEX_IDLE_TTL_MS` (default 30 min). This is the path for exits that never fire `SessionEnd` — signals, crashes, Codex older than 0.145, and app-server threads whose `SessionEnd` is deferred.
3. **Cursor retention in the same pass**: a state file with no live OV session is kept as a resume cursor until `OPENVIKING_CODEX_COMMITTED_TTL_MS` (default 30 days), or dropped after the idle TTL if it never captured a turn.

Each candidate is committed under its per-session lock with no waiting; a lock the sweep cannot take means a `SessionEnd` or `Stop` worker already owns that session, and the sweep logs the skip and moves on. Under the lock it first appends whatever the state's recorded `transcriptPath` still holds past the cursor, so a session whose own workers never ran is not archived without its tail turns; if part of that append fails it keeps the live session and the marker and leaves the commit to the next sweep. It also re-reads the `.ended` marker there: an `ended_retry` candidate whose marker is now gone (the thread was resumed) or newer than the snapshot (a later exit will commit it) falls back to the idle rule. A recorded `transcriptPath` that cannot be read is never mistaken for an empty transcript: the sweep logs `transcript_unreadable`, keeps the live session and the marker, and skips the commit. Commits preserve the transcript cursor for resume.

On any /commit failure (OV unreachable, non-2xx, timeout) we **preserve state** (keep `ovSessionId` set, and keep the `.ended` marker) so the next sweep can retry. `SessionEnd` and `PreCompact` apply the same unreadable-transcript guard as the sweep, so neither commits a session whose transcript it could not read.

On `resume`, the script skips commit/sweep. It still injects the profile block. If local state has no live `ovSessionId`, it also reads `/api/v1/sessions/{cx-session-id}/context` and combines the latest committed archive overview into the same `SessionStart` output. The archive block includes a `viking://~/sessions/{cx-session-id}/history/` URI and tells the model to use the OpenViking MCP `read`/`search` tools for exact prior commands, file paths, tool outputs, or messages. Set `OPENVIKING_RESUME_ARCHIVE_INJECT=0` to disable the archive half without disabling profile injection.

### Auto-recall (every UserPromptSubmit)

`auto-recall.mjs` adapts the Codex prompt/session payload and calls the shared
`buildRecallBlockDetailed()` pipeline. The shared core owns context search,
legacy `/recall`, raw-search fallback, ranking, injection budgets, digest selection,
compression caching and URI repair. Codex owns the `cx-<safe-session-id>` mapping,
model/profile selection, CLI execution and the hook deadline.

```json
{ "hookSpecificOutput": { "hookEventName": "UserPromptSubmit", "additionalContext": "<openviking-context>\n...\n</openviking-context>" } }
```

The shared core prefers a server digest and suppresses injection for
`no_relevant` / `NO_RELEVANT_MEMORY`. Local compressor failures retain bounded
retrieved context. Raw fallback uses session-aware search when a session exists,
then retries without the session if all targets are empty; unavailable search
can fall back to `find`. Explicit user targets retain the home-alias fallback.
Local compression receives bounded full leaf content before injection truncation.
Without local compression, `recallPreferAbstract` and the shared token budget
control the fallback (including URI and wrapper overhead).

The hook has an `OPENVIKING_RECALL_TIMEOUT_MS` deadline (default 120s); the bundled
hook allows 130s. Nested compressor calls disable automatic memory hooks and are
killed on timeout. A failed compressor is not restarted for a legacy-peer pass
within the same turn. Capture recognizes the shared `<openviking-context>` wrapper
and removes injected context from newly captured messages.

The compressor profile is recreated on every `SessionStart` and cached under `OPENVIKING_CODEX_STATE_DIR` so cross-session config changes are picked up but each `UserPromptSubmit` does not probe models. Default fallback order:

1. configured `OPENVIKING_RECALL_COMPRESS_MODEL` + `OPENVIKING_RECALL_COMPRESS_THINKING`
2. `gpt-5.3-codex-spark` with thinking `default`
3. `gpt-5.6-luna` with thinking `low`
4. off (deterministic digest, no `codex exec` compression)

Config knobs:

| Env var | Default | Meaning |
|---|---|---|
| `OPENVIKING_RECALL_LIMIT` | `10` | Legacy quota-scaling input; explicit values are converted to six coding quotas, not enforced as a final result cap. |
| `OPENVIKING_RECALL_COMPRESS` | `auto` | `server`: cloud rewrite, never launches `codex exec`; `client`: local only; `auto`: local when available, otherwise cloud; `off` / `0`: uncompressed. `1` aliases `auto`. |
| `OPENVIKING_RECALL_COMPRESS_MODEL` | unset | Custom first-choice compressor model. Set `off` to disable the local compressor (`auto` then uses cloud compression). |
| `OPENVIKING_RECALL_COMPRESS_THINKING` | unset | Custom `model_reasoning_effort`; `default` omits the Codex config override. Alias: `OPENVIKING_RECALL_COMPRESS_REASONING_EFFORT`. |
| `OPENVIKING_RECALL_COMPRESS_BASE_URL` | unset | Base URL for the nested compressor's provider. Use this when `--ignore-user-config` prevents the compressor from reading the main Codex provider configuration. |
| `OPENVIKING_RECALL_COMPRESS_MIN_INPUT_CHARS` | `1500` | Skip the nested compressor below this recalled-context size. Set `0` to compress every non-empty result. |
| `OPENVIKING_RECALL_COMPRESS_DETECT_ON_STARTUP` | `1` | Recreate/cache compressor profile in `SessionStart`. |
| `OPENVIKING_RECALL_COMPRESS_DETECT_TIMEOUT_MS` | `15000` | Per-candidate startup probe timeout. |
| `OPENVIKING_RECALL_COMPRESS_DETECT_TTL_MS` | `604800000` | Cache TTL used by `UserPromptSubmit` when reading the latest profile. |
| `OPENVIKING_RECALL_MAX_TOKENS` | `1600` | Token budget the server assembles the context block within, independent of the local compressor input limit. |
| `OPENVIKING_RECALL_DEDUP_TURNS` | `5` | Cross-turn cooldown: URIs served in the last N turns are skipped. |
| `OPENVIKING_RECALL_QUERY_EXPANSION` | `auto` | `auto` lets the server widen short prompts using session context; `off` disables it. |
| `OPENVIKING_RECALL_QUERY_FILTERS` | `""` | Comma-separated regex rules applied to the prompt before it becomes a query — see [Input filters](#input-filters). |

Recall now asks the server to assemble the context block in one request
(`POST /api/v1/search/search` with `mode="context"`), so budgeting, detail tiers
and cross-turn dedup are shared with every other harness. Deployments without
that endpoint fall back to `/api/v1/search/recall`, and that outcome is cached so
only the first turn pays for the probe. Server-owned Context defaults are omitted
unless explicitly configured, so the plugin follows the server instead of copying
values such as `limit=10` or `max_tokens=1600`. An explicit legacy `recallLimit`
is converted to per-category coding quotas, not a final result cap. Values
from 1 through 5 therefore produce an effective total quota of 6, one retrieval
slot for each coding domain. Eligible cache misses still use local `codex exec`
compression on top of whichever path answered.

The `mode="context"` request covers skills as well as memories, from both your own `skills/` and the account-shared `viking://agent/skills`, so a skill that fits the prompt can show up in the digest with its `viking://` URI.

Client-side knobs can also live in `~/.openviking/ovcli.conf` under
`plugin` (shared) or `plugin.codex` (this harness only), or in the workspace
layers; resolution order is env vars → the workspace layers → `plugin.codex` →
`plugin` → the legacy `codex` block in `ov.conf` → defaults.

### viking:// URI notice (PreToolUse on Bash)

`viking://` URIs are OpenViking virtual paths, so `cat viking://…` or `ls viking://…` cannot open them. `uri-guard.mjs` runs before every `Bash` call. When the command contains a `viking://` URI, it returns `hookSpecificOutput.additionalContext` without a `permissionDecision`: the command runs unchanged, and the model is told which OpenViking MCP tool reads the URI and to ignore the notice when the URI is intentional data (an `ov` CLI argument, an HTTP payload, a search pattern). A command without a `viking://` URI gets no output.

Nothing is denied: Codex edits files through `apply_patch`, whose input is a patch body rather than a path (Codex's `Edit` / `Write` matchers are aliases for it), so there is no path argument to guard.

> **Upgrading to 0.9.1**: `PreToolUse` is a newly registered hook event. Run `/hooks` in Codex after updating and approve it; until then shell commands run without the notice.

### Stop (turn end → `add_message`, threshold commit)

`auto-capture.mjs` derives one long-lived OpenViking session id per Codex `session_id` as `cx-<safe-session-id>` and incrementally appends every new user/assistant turn via `/api/v1/sessions/{id}/messages`. The `/messages` endpoint auto-creates the session on first append. Per-codex-session state lives at `~/.openviking/codex-plugin-state/<safe-session-id>.json`. Capture sanitizes obvious hook noise, metadata wrappers, and plugin-injected `<openviking-context ...>` blocks before append. Tool calls and results become dedicated `tool` parts and `tool_output` is reported verbatim — the server externalizes anything larger than `tool_output_externalization.threshold_chars` (default `20000`) and leaves a synopsis stub plus `tool_output_ref`, so the original stays readable via `/api/v1/sessions/{id}/tool-results`. `OPENVIKING_CAPTURE_TOOL_MAX_CHARS` (default `1000000`) is only a guard against pathological payloads. Configured `captureFilters` rules run last, just before the payload is sent — see [Input filters](#input-filters).

After a successful append, Stop reads the session meta and commits when `pending_tokens >= OPENVIKING_COMMIT_TOKEN_THRESHOLD` (default `20000`). Threshold commits pass `keep_recent_count=OPENVIKING_COMMIT_KEEP_RECENT_COUNT` (default `10`) so the newest turns remain live for continuity while older context is archived and extracted. `PreCompact` still commits everything before compaction.

### PreCompact (deterministic commit)

`pre-compact-capture.mjs`:

1. Catch-up append for any turns Stop hasn't captured yet (race-safe via `capturedTurnCount`)
2. Commit the long-lived OV session so the extractor runs against the full pre-compact transcript
3. Reset `ovSessionId` to `null` so the next `Stop` re-derives the same `cx-<safe-session-id>` and appends the post-compact half under that deterministic OV session id

### Session end

`SessionEnd` exists since Codex `rust-v0.145.0`. It fires when a thread shuts down gracefully — `/quit`, `/exit`, double Ctrl-C, EOF, and the end of a `codex exec` run — and at TUI exit every thread the process touched gets one, as a burst. `/new` on its own does not end the previous thread; its `SessionEnd` arrives when the process exits.

`session-end.mjs` catches up whatever turns the last `Stop` never sent, then commits the OV session so the extractor runs on the whole conversation. If any of those turns fail to land it keeps the live session and the marker instead of committing, so the sweep retries rather than archiving a conversation without its tail. Codex budgets the hook at 1s by default and clamps `timeout` in `hooks.json` to 3s, forces `async: true` hooks to run synchronously, and ignores their stdout — far too little for a commit. So the parent hook only writes an `.ended` marker next to the session state (lock-free, a millisecond) and detaches a worker that does the catch-up and the commit; Codex deliberately leaves cleanly detached helpers running after a hook exits.

`SessionEnd` does not fire on `SIGTERM`, `SIGHUP`, a closed terminal, `kill -9`, or a crash. When the TUI is attached to a `codex app-server` daemon, it is deferred to thread unload (30 min) or daemon shutdown. Those cases, and Codex older than 0.145 (and any TraeCode CLI build without it), are covered by the fallback sweep at the next `SessionStart`.

The `.ended.<timestamp>` marker and the per-session `.lock` directory live beside the state file. The timestamp it was written at is the marker's identity, and it is part of the filename: the `SessionEnd` parent hands it to its worker, which verifies the marker still matches before committing and returns untouched if it does not, and `Stop` / `PreCompact` / `resume` only clear markers older than their own start time. Because each removal unlinks the exact marker paths below its cutoff, a marker written while a removal is in flight is a different file and survives, so a late worker cannot erase a fresh exit's marker. `Date.now()` is only the starting point for that name: the marker is created exclusively and its timestamp bumped until that succeeds, so two exits within one millisecond cannot share a path. A bare `<id>.ended` written by an older build is still read back.

The lock serializes the four writers that persist the whole state object — the `Stop` worker, `PreCompact`, the `SessionEnd` worker, and the sweep — so none of them can clobber another's cursor or `ovSessionId`. The holder stamps an `owner` file inside the lock directory and releases only while it still owns it; a stale lock is taken over in place by claiming that `owner` file — an atomic rename aside followed by an exclusive create, so exactly one taker wins and the lock path is never momentarily absent. Its wait budget is `OPENVIKING_CODEX_LOCK_WAIT_MS` (default 120s for `SessionEnd`, 40s for `PreCompact`, which must answer inside a 60s hook budget); the sweep never waits.

> **Upgrading from 0.7.x**: `SessionEnd` is a newly registered hook event, and Codex has no trust record for it. Run `/hooks` in Codex after updating and approve it, otherwise it silently never runs and every session falls back to the sweep.

## Codex hook output schema

Codex's hook output schema differs from Claude Code's. Notably:

| Hook | Input field of interest | Output channel for context injection |
|------|------------------------|--------------------------------------|
| `SessionStart`   | `source` (`startup`/`resume`/`clear`), `session_id`, `cwd` | `hookSpecificOutput.additionalContext`; may also include `systemMessage` when an orphaned session was committed |
| `UserPromptSubmit` | `prompt`, `session_id`                     | `hookSpecificOutput.additionalContext` |
| `PreToolUse` (`Bash`) | `tool_name`, `tool_input.command`       | `hookSpecificOutput.additionalContext` with no `permissionDecision`, so the command still runs; no output when the command has no `viking://` URI |
| `Stop`           | `last_assistant_message`, `transcript_path`, `session_id` | `systemMessage` (only) |
| `PreCompact`     | `trigger` (`manual`/`auto`), `transcript_path`, `session_id` | `systemMessage` (only) |
| `SessionEnd`     | `session_id`, `transcript_path`, `cwd`, `reason` (constant `other`) | none — Codex ignores the output; the script prints `{}` for symmetry |

Unlike Claude Code, **Codex does not support `decision: "approve"`**; only `decision: "block"`. A no-op is `{}` (which is what these scripts emit when there's nothing to add).

## Troubleshooting

Start with the bundled doctor — it checks the install (marketplace, `config.toml` enablement, hook trust records, MCP wiring), the resolved config (which file won, API key shown masked), the connection (reachability, auth, `/mcp`) and the session state left by the hooks, and prints a fix for every finding:

```bash
node "$(ls -d ~/.codex/plugins/cache/openviking/openviking-memory/*/ | sort -V | tail -1)scripts/ov-memory-doctor.mjs"
```

Or invoke the `$ov-memory-doctor` skill in Codex, which runs the same script and walks the report. When the server runs on the same machine (loopback url) the report adds a Server health section — whether anything listens on the port, plugin-only keys in ov.conf that stop the server from starting, and `GET /ready`; everything else server-side (config validation, live embedding probe, native engine, disk) stays with `openviking-server doctor`.

## Testing

There is no `package.json` and no build step, so the suite runs straight through Node's own test runner:

```bash
cd examples/codex-memory-plugin
node --test scripts/*.test.mjs
```

CI runs the same files (`.github/workflows/pr.yml`), so a green local run is the same signal. They cover every hook end to end against a stubbed server — the deterministic `cx-<codex_session_id>` derivation, incremental append and idempotent re-runs, the PreCompact and SessionEnd commit paths with their `.ended.<ts>` markers and locks, the SessionStart sweep (idle TTL, cursor retention, `source=resume`), and recall assembly. The MCP proxy is shared code and its contract is tested once, in `examples/memory-plugin-shared/mcp-proxy-core.test.mjs`.

### Live checks

Two legs need a real server and real Codex auth, so they stay manual. Prerequisites: the `ov` CLI installed and reachable, Node.js 22+, and `~/.openviking/ovcli.conf` (or a per-tenant variant like `ovcli.conf.bob`) pointing at the OpenViking server you want to write to. The plugin sends `Authorization: Bearer <api_key>` from this file, and `X-OpenViking-Account` / `X-OpenViking-User` only in trusted mode.

**Memory extraction landed in the user namespace.** After a session commits, wait ~60 s for OV's extractor, then:

```bash
export OV_CONF=$HOME/.openviking/ovcli.conf.bob   # or whichever tenant
OPENVIKING_CONFIG_FILE=$OV_CONF ov ls viking://user/<your-user>/memories/
OPENVIKING_CONFIG_FILE=$OV_CONF ov read viking://user/<your-user>/memories/profile.md
```

Expect new entries describing the preferences the conversation stated, with timestamps from this run.

**Codex CLI smoke test** (requires codex auth):

```bash
codex plugin marketplace add /path/to/OpenViking-codex-marketplace   # if not already
codex                                                                 # interactive
# Have a brief conversation that mentions a clear preference,
# then /compact (manual PreCompact) to force a commit, then exit.
```

Then re-run the extraction check above.

## Plugin Structure

```
codex-memory-plugin/
├── .codex-plugin/
│   └── plugin.json              # Plugin manifest (hooks + mcp wiring)
├── hooks/
│   └── hooks.json               # SessionStart + UserPromptSubmit + PreToolUse + Stop
│                                  + SessionEnd + PreCompact (uses Codex's native
│                                  ${PLUGIN_ROOT} token; no rendering needed on modern Codex)
├── skills/
│   ├── openviking-memory/       # How to use the memory tools
│   ├── openviking-skills/       # Find, use, create (add_skill), share, and migrate OpenViking skills
│   ├── ov-experience-memory/
│   └── ov-memory-doctor/        # Install / config / connection / local-server troubleshooting
├── scripts/
│   ├── config.mjs               # Shared config loader (ovcli.conf + env)
│   ├── ov-memory-doctor.mjs     # Diagnostics script ($ov-memory-doctor skill)
│   ├── capture-utils.mjs        # Transcript text extraction, filtering, tool compression
│   ├── debug-log.mjs            # Structured JSONL logger
│   ├── recall-compressor-profile.mjs # Compressor profile detection/cache
│   ├── session-state.mjs        # Per-codex-session OV session state (+ .ended.<ts> / .lock sidecars)
│   ├── ov-session.mjs           # Shared OV HTTP + transcript catch-up helpers
│   ├── auto-recall.mjs          # UserPromptSubmit hook (REST /search/search)
│   ├── auto-capture.mjs         # Stop hook (append + threshold commit)
│   ├── session-start-commit.mjs # SessionStart hook (profile + fallback sweep + resume archive)
│   ├── session-end.mjs          # SessionEnd hook (mark + detached catch-up + commit)
│   ├── pre-compact-capture.mjs  # PreCompact hook
│   ├── uri-guard.mjs            # PreToolUse hook (viking:// notice on Bash)
│   └── *.test.mjs               # node --test suites (session-end, pre-compact, ...)
├── servers/
│   └── mcp-proxy.mjs            # stdio -> OpenViking /mcp bridge
├── setup-helper/
│   └── install.sh               # One-line installer
├── .mcp.json                    # stdio MCP wiring
├── DESIGN.md
└── README.md
```

No `src/` or `package.json`: there is no build step. Hook scripts and the MCP proxy are zero-dep `.mjs` files running on Codex's bundled Node 22 or a compatible system Node.

The Codex marketplace catalog that exposes this plugin for `codex plugin marketplace add` lives at the **repo root** in `.agents/plugins/marketplace.json` (Codex resolves a marketplace manifest from the source root, not from this subdirectory). The catalog points at `./examples/codex-memory-plugin` using a relative source, so the installed plugin follows the same marketplace snapshot/ref that the user added.

## Differences from the Claude Code Plugin

| Aspect | Claude Code Plugin | Codex Plugin |
|--------|--------------------|--------------|
| Plugin root env var | `CLAUDE_PLUGIN_ROOT` (expanded by CC) | `${PLUGIN_ROOT}` (injected into hook env + substituted inline by modern Codex; installer also renders it to absolute paths for older Codex) |
| `UserPromptSubmit` injection | `decision: "approve"` + `hookSpecificOutput.additionalContext` | `hookSpecificOutput.additionalContext` only — `approve` is not a Codex output |
| `Stop` decision | `decision: "approve"` no-op | `{}` no-op — only `block` is a valid Codex `decision` |
| Compaction hook | n/a (Claude Code does not expose one) | `PreCompact` — full-transcript commit before context loss |
| Config section | `claude_code` | `codex` |
| Default config file | `~/.openviking/ov.conf` | `~/.openviking/ovcli.conf`, falls back to `ov.conf` |
| MCP server | Local stdio proxy to OpenViking `/mcp` | Local stdio proxy to OpenViking `/mcp` |

## License

Apache-2.0 — same as [OpenViking](https://github.com/volcengine/OpenViking).


### Cloud recall compression

Set `OPENVIKING_RECALL_COMPRESS=server` to request `POST /api/v1/search/search`
with `mode: "context", rewrite: true`. This also disables local startup compressor
probes. A returned server digest is injected without a second local compression
pass; a server `no_relevant` result injects nothing. If rewrite is unavailable,
the hook preserves the existing raw-context / legacy retrieval fallback.

`auto` uses `rewrite: "auto"` when the Codex executable or its compressor profile
is unavailable (including a cached runtime failure). A first local failure still
uses the deterministic fallback for that turn; later turns use the server.
