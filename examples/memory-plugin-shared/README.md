# Memory Plugin Shared Library

This directory contains shared JavaScript modules that are vendored into the
Claude Code, Codex, OpenCode, and pi memory plugins by `sync.mjs`.

> **Requires an OpenViking server with `viking://~` home-alias support.** Recall targets the
> caller's own context space through `viking://~/memories` and `viking://~/skills`; the uid-less
> `viking://user/memories` shorthand is rejected by newer servers.

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
   `git-github-com-volcengine-openviking-b44f5292`).
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

### Migrating from path-derived peers

Previously the default peer was always derived from the absolute workspace
path; a workspace inside a Git repo now gets a stable remote-derived peer
instead. **Actor-scoped recall** (`recallPeerScope: "actor"`) is the case most
at risk: isolation mode only recalls global memory plus the *current* peer, so
a peer change silently strands everything written under the old path-derived
peer. To avoid stranding existing data, pin the old peer explicitly **before**
upgrading and keep it pinned:

1. Compute the legacy path-derived peer for the workspace — every non-letter
   or non-digit character in the absolute path becomes `-`:
   ```sh
   # e.g. /Users/x/Dev/OpenViking -> -Users-x-Dev-OpenViking
   printf '%s' "$(pwd)" | LC_ALL=C tr -c 'A-Za-z0-9' '-'
   ```
2. Set it as the explicit peer (highest priority, overrides derivation):
   - env: `export OPENVIKING_PEER_ID=-Users-x-Dev-OpenViking`, or
   - `ovcli.conf`: `actor_peer_id = -Users-x-Dev-OpenViking` (harness legacy:
     `peer_id` / the harness-specific peer field).
3. Alternatively opt out of derivation entirely with `workspacePeer=false` (or
   `OPENVIKING_WORKSPACE_PEER=0`) and supply your own peer.

To consolidate under the new remote-derived peer instead, copy the memories
written under the old peer to the new one (the new peer id for a given remote
is produced by `deriveWorkspacePeerId` in `lib/workspace-peer.mjs`), verify
recall, then drop the override. Until verified, keep the explicit peer so
recall keeps hitting the old data.

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
