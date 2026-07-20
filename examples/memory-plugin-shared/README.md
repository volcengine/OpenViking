# Memory Plugin Shared Library

This directory contains shared JavaScript modules that are vendored into the
Claude Code, Codex, OpenCode, and pi memory plugins by `sync.mjs`.

## Workspace Peers

`lib/workspace-peer.mjs` derives the default actor peer from the current
workspace path. The rule matches Claude's project-directory naming: every
character outside `A-Z`, `a-z`, and `0-9` becomes `-`; paths are not normalized,
folded, or trimmed. For example, `/Users/x/Dev/OpenViking` becomes
`-Users-x-Dev-OpenViking`.

Resolution order is:

1. Explicit peer: `OPENVIKING_PEER_ID`, `actor_peer_id` / `peer_id` in
   `ovcli.conf`, or the harness-specific legacy peer config.
2. Workspace-derived peer when `workspacePeer` is not `false`.
3. No peer.

Set `OPENVIKING_WORKSPACE_PEER=0` or the harness config `workspacePeer=false`
to disable workspace-derived peers.

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
