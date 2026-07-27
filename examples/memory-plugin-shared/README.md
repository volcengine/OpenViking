# Memory Plugin Shared Library

This directory contains shared JavaScript modules that are vendored into the
Claude Code, Codex, OpenCode, and pi memory plugins by `sync.mjs`.

## Workspace Peers

`lib/workspace-peer.mjs` derives the default actor peer from the **stable
identity of the enclosing Git repository**, so the same clone resolves to one
peer across machines, checkouts, and linked worktrees instead of fragmenting
per absolute path.

Resolution order is:

1. **Explicit peer** (highest priority, overrides everything):
   `OPENVIKING_PEER_ID`, `actor_peer_id` / `peer_id` in `ovcli.conf`, or the
   harness-specific legacy peer config.
2. **Git remote canonical identity**: when the workspace is inside a Git repo
   with an `origin` remote, the peer is derived from the canonical remote URL.
   SSH and HTTPS forms of the same remote collapse together
   (`git@github.com:volcengine/OpenViking.git` ≡
   `https://github.com/volcengine/OpenViking.git`); credentials, query/fragment,
   `.git` suffix, and trailing slashes are stripped before the peer id is
   formed, so secrets never appear in it. The main checkout and every linked
   worktree of the same remote resolve to the same peer. The id has the shape
   `git-<host-owner-repo-slug>-<8-hex-hash>` (e.g.
   `git-github-com-volcengine-openviking-982dfa85`).
3. **Stable local-repo identity**: inside a Git repo with no usable remote
   (e.g. a freshly `git init`-ed repo, or a remote that points at a local
   path), the peer is derived from the Git common dir shared by the main
   checkout and all linked worktrees, so worktrees still stay together. The id
   has the shape `git-local-<12-hex-hash>`.
4. **Absolute path fallback**: outside any Git repo, the previous behavior is
   preserved — every non-letter-or-digit character in the absolute path becomes
   `-` (e.g. `/Users/x/Dev/OpenViking` becomes `-Users-x-Dev-OpenViking`).

When `workspacePeer` is `false` (or `OPENVIKING_WORKSPACE_PEER=0`), no peer is
derived and only an explicit peer (if any) is used.

> **Behavior change**: previously the default peer was always derived from the
> absolute workspace path. Workspaces inside a Git repo now get a stable
> remote-derived peer instead. If you relied on the path-derived peer (for
> example to keep memories pinned to one machine), set an explicit peer via
> `actor_peer_id` / `OPENVIKING_PEER_ID`, or set `workspacePeer=false` and
> provide your own peer.

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
