# Memory Plugin Shared Library

This directory contains shared JavaScript modules that are vendored into the
Claude Code, Codex, OpenCode, and pi memory plugins by `sync.mjs`.

> **Requires an OpenViking server with `viking://~` home-alias support.** Recall targets the
> caller's own context space through `viking://~/memories` and `viking://~/skills`; the uid-less
> `viking://user/memories` shorthand is rejected by newer servers.

## Workspace Peers

`lib/workspace-peer.mjs` decides which peer a workspace writes its memories under; `lib/workspace-identity.mjs` derives the values it substitutes.

The peer used to be the working directory with every non-alphanumeric character replaced by `-`, so `/Users/x/Dev/OpenViking` became `-Users-x-Dev-OpenViking`. That made the identity an accident of where the repository sat: a clone on another machine, a rename, a git worktree, or simply working from a subdirectory each minted a separate, empty namespace. The default is now git's own identity, so one project keeps one memory wherever it is checked out. A directory that is not a repository sends no peer at all, and what is remembered there goes to your user-level space at `viking://user/<you>/memories` — an application that opens a fresh directory for every task would otherwise mint a fresh, empty peer for every task.

`peer.source` picks the rule. It is read from `OPENVIKING_PEER_SOURCE`, from `plugin.peerSource` / `plugin.<harness>.peerSource` in `ovcli.conf`, or from `peer.source` in a [workspace config file](#workspace-configuration):

- `git` — the default. Equivalent to the template list `["{git_remote}", "{git_root}"]`: the normalized `origin` URL, else the repository root path. Outside a repository nothing is sent. No prefix is added.
- `cwd` — the previous behaviour, byte for byte.
- `none` — send no peer at all. `OPENVIKING_WORKSPACE_PEER=0` still means this.
- A template such as `"git-{git_remote}"` or `"team-{dir}"`, or a list of templates tried in order. A template whose variables are empty falls through to the next one, so a half-substituted id is never sent.

The variables a template may use:

- `{git_remote}` — the normalized `origin` URL as `github.com-org-repo`; empty outside a git repository, or when there is no `origin`.
- `{git_root}` — the repository root path, legacy sanitation; empty outside a git repository. A marker file inside a repository still leaves this the repository's own root, so marking a subdirectory does not split the default peer.
- `{cwd}` — the working directory, legacy sanitation; never empty, and in no default chain, so a bare path becomes a peer only when you ask for one.
- `{dir}` — the workspace root's directory name: the repository root, or the directory holding `.openviking/config.json`; empty when the directory is not a workspace.
- `{harness}` — the name of the agent running, the same one the User-Agent carries; never empty. No preset uses it, so agents share one repository's memory unless a template such as `"{git_remote}-{harness}"` asks them not to. The MCP proxy takes no part in derivation, so a read path that goes only through it cannot resolve it.

To give a directory that is not a repository its own peer, create `.openviking/config.json` there holding `{"version": 1, "peer": {"id": "my-project"}}`.

So in `/Users/x/Dev/OpenViking/examples/codex-memory-plugin` with origin `git@github.com:volcengine/OpenViking.git`, the peer is `github.com-volcengine-openviking` — the same from any subdirectory, any worktree, any machine, any clone.

Every clone of one repository therefore shares one peer: project memory follows the project. A fork has a different origin, so it stays separate by default; reviewing an external PR through `gh pr checkout` leaves `origin` alone and does not move the identity. Worktrees converge through `commondir`, a submodule keeps its own identity, and `$HOME` and `/` are never workspace roots. The URL is normalized so ssh and https spellings of one repo agree, and an embedded token can never reach the peer id. The whole derivation is pure filesystem work — no `git` subprocess — so it also holds where `git` is absent from `PATH` or would refuse the repository over dubious ownership.

Resolution order is:

1. Explicit peer: `OPENVIKING_PEER_ID`, `peer.id` in a workspace layer, `actor_peer_id` / `peer_id` in `ovcli.conf`, or the harness-specific legacy peer config.
2. The peer derived by `peer.source`, when `workspacePeer` is not `false`.
3. No peer.

Migrating from the path-derived peer needs no action. The old id can always be recomputed locally, so recall still reaches memories written under it: with the default `peer_scope: "all"` the server's existing cross-peer sweep already covers them at zero cost, and with `peer_scope: "actor"` the plugin asks that peer separately. There is no deadline on this, and `peer.source: "cwd"` restores the old id outright.

## Recall Peer Scope

`lib/recall-core.mjs` defaults to the broad recall mode and does not send a
`peer_scope` field. In that mode, the server can recall global memory, the
current workspace, and other workspace memories; other workspaces are penalized
and rendered later.

When `recallPeerScope` is `actor`, the helper sends `peer_scope:"actor"`. This
is the isolation mode: recall only sees global memory plus the current
workspace. If an older server rejects that field with 400 or 422, `postRecall`
removes `peer_scope` and retries once.

For deployments where one bot serves multiple real people, such as zouk,
vikingbot, or AstrBot, configure an explicit actor peer and use the isolation
mode so one person's memories are not recalled into another person's session.

## Workspace Configuration

`lib/workspace-config.mjs` and `lib/workspace-registry.mjs` give a repository three configuration layers of its own: `<repo-root>/.openviking/config.json`, which the team commits, `<repo-root>/.openviking/config.local.json`, which stays private and gitignored, and a per-machine entry at `~/.openviking/workspaces/<slot>.json`. Precedence, highest first:

1. `OPENVIKING_*` environment variables
2. the machine registry, `~/.openviking/workspaces/<slot>.json`
3. `.openviking/config.local.json`
4. `.openviking/config.json`
5. `ovcli.conf` `plugin.<harness>`
6. `ovcli.conf` `plugin`
7. the harness block in `ov.conf` (legacy)
8. built-in defaults

Every file declares `version: 1`; one declaring another version is skipped with a warning. Schema v1 is `peer.source`, `peer.id`, `recall.enabled`, `recall.peer_scope`, `recall.dedup_turns`, `recall.max_items`, `recall.score_threshold`, `capture.enabled`, `capture.commit_token_threshold`, `bypass.session_patterns`, and `labels`. Lists union across layers, and a leading `"!reset"` clears what was inherited. Unknown keys are kept and ignored.

Workspace files are trusted without a prompt: a hook is non-interactive, and an approval gate would degrade into one command per workspace. What is refused instead is structural — connection and credential keys (`url`, `api_key`, `account`, `user`, `extra_headers`, …) are stripped with a warning and `${VAR}` is never expanded in these files. What a committed file switches off is announced in `ov-memory-doctor` rather than blocked.

`.gitignore` must not ignore all of `.openviking/`, or `config.json` can never be committed. Narrow the rule to `.openviking/media/` and `.openviking/downloads/`; doctor warns while it is still blanket.
