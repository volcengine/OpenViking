# Global Agent Evolution Switch Design

## Scope

Agent Evolution is controlled by one deployment-level switch for the entire
OpenViking server instance. All accounts and users served by the same process
share the same effective value.

The switch controls whether session commits may generate or update these memory
types:

- `cases`
- `trajectories`
- `experiences`

Disabling the switch does not delete existing files and does not prevent
existing experiences from being searched or read.

## Configuration

Add the switch directly to `ServerConfig`:

```json
{
  "server": {
    "agent_evolution": {
      "enabled": false
    }
  }
}
```

The default is `false`.

`server.user_config_defaults` remains responsible only for existing per-user
defaults such as add targets. Agent Evolution is no longer part of active
`UserConfig` resolution.

This setting belongs to the HTTP server deployment surface. A directly
constructed `SessionService` retains its enabled default for internal service
callers that do not load `ServerConfig`.

## Commit Behavior

`SessionService` passes the deployment-level Agent Evolution configuration into
each `Session`. A commit applies the global value after validating the
session-level `memory_policy`:

- When enabled, the session policy remains authoritative. A session may still
  exclude `cases`, `trajectories`, or `experiences`.
- When disabled, the commit removes all three Agent Evolution memory types from
  the effective policy. A session cannot enable them through `memory_policy`.

Phase 1 stores the effective boolean and skip reason in archive metadata.
Asynchronous Phase 2 reads that snapshot, so normal queue processing and direct
recovery of that archive use the value accepted at commit time.

When a later commit rolls earlier failed archives into one recovery batch, the
entire batch uses the triggering archive's snapshot. OpenViking keeps one
extraction policy per batch instead of splitting the merged conversation across
different Agent Evolution settings. Therefore, changing the deployment setting
before a later recovery commit can affect replayed messages from earlier failed
archives.

Archives created before the snapshot field existed preserve the historical
enabled behavior during recovery.

## Removed User-Level Surface

Remove Agent Evolution from the active per-user configuration and resolution
logic. Retain a deprecated parse-only schema field so existing
`user_config.json` files remain loadable; its value is ignored.

Remove the user-level management surfaces introduced by the current branch:

- `GET /api/v1/user-settings/memory`
- `PATCH /api/v1/user-settings/memory`
- Python SDK memory-setting methods
- `ov user-settings memory`
- `ov user-settings set-memory`
- Agent Evolution fields accepted during account or user creation

Existing stored `agent_evolution` fields are ignored after upgrade. They do not
override the deployment-level setting.

## Compatibility

- Existing experiences remain readable and searchable.
- Directly constructed services preserve their enabled default because they do
  not have the HTTP server configuration surface.
- Existing user config files containing `agent_evolution` continue to parse,
  preventing an upgrade from breaking users that already wrote the branch-era
  configuration.
- Session-level `memory_policy` remains supported as an allow-list below the
  global switch.
- Queue payload structure remains unchanged; the commit-time decision stays in
  archive metadata.

## Verification

Tests cover:

- Default global value is disabled.
- Explicit global enablement produces Agent Evolution memory when the session
  policy permits it.
- Global disablement cannot be bypassed by session `memory_policy`.
- Different users in one server receive the same effective value.
- Existing user config with the deprecated field loads but does not affect the
  result.
- Phase 2 uses the archived commit-time value.
- Existing experiences remain readable while production is disabled.
