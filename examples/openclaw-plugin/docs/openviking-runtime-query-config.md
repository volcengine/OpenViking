# OpenViking Runtime Query Config

Runtime query config lets operators tune recall and search behavior without restarting OpenClaw. It is intended for live debugging and rollout control around recall limit, candidate count, score threshold, target resource types, and ranking weights.

This document describes the runtime query config capability ported from the broader #2613 work into the split OpenViking plugin PR series.

## Configuration Layers

Effective query config is resolved per request with this priority:

```text
explicit tool request arguments
  > session runtime config
  > peer runtime config
  > explicitly configured static plugin value
  > plugin field default
```

The higher layer wins field by field. Object fields such as ranking weights are shallow-merged. Array fields such as `resourceTypes` replace the lower layer. `recallLimit` is the exception at the bottom: when no layer sets it explicitly, the plugin omits quotas and uses the OpenViking server default.

## Scopes

Runtime config has two persistent scopes:

| Scope | Meaning |
| --- | --- |
| `peer` | Applies to the current OpenClaw peer/assistant identity across sessions. |
| `session` | Applies only to the current session identity. |

For command compatibility, `--scope claw` is accepted as an alias for `peer`.

## Supported Fields

| Field | Purpose |
| --- | --- |
| `recallLimit` | Optional result target for auto-recall and `memory_recall`; explicit values use the standard coding quota mapping. |
| `candidateLimit` | Legacy candidate width used only by an explicit `memory_recall.targetUri` compatibility request. |
| `candidateMultiplier` | Legacy multiplier used only to derive that targeted compatibility candidate width. |
| `scoreThreshold` | Minimum score sent to server context search for auto-recall and `memory_recall`. |
| `maxInjectedChars` | Injection budget converted to server `max_tokens` at four characters per token for both recall paths. |
| `recallPreferAbstract` | When true, pin server-assembled recall to abstract detail; false leaves the server detail default unchanged. |
| `resourceTypes` | Default semantic recall target types: `user`, `agent`, `resource`. |
| `targetUri` | Exact `viking://` scope for `ov_search` and `memory_forget`. An explicit `memory_recall.targetUri` also selects the legacy targeted recall path. |
| `ovSearchLimit` | Default result count for `ov_search`. |
| `rankingWeights` | Legacy ranking weights used only by an explicit `memory_recall.targetUri` request. |
| `categoryWeights` | Legacy category weights used only by an explicit `memory_recall.targetUri` request. |
| `resourceTypeWeights` | Legacy resource-type weights used only by an explicit `memory_recall.targetUri` request. |

Automatic recall and the default `memory_recall` path both use one session-aware context/coding search. When no tool, runtime, or static `recallLimit` is set, the request omits quotas and uses the server default. Explicit values follow the same coding quota mapping as the other memory-plugin harnesses. `memory_recall` returns the server's token-budgeted `digest` or `rendered` context directly. The explicit `targetUri` compatibility path keeps the previous targeted `/find`, local ranking, and read behavior.

Session history is not a semantic recall target. Use `ov_archive_search` and `ov_archive_expand` for session archive recovery.

## Slash Command

### Get

```text
/ov-query-config get --scope session
/ov-query-config get --scope peer
```

The response includes the effective config and per-field sources, which are useful for understanding why a value is active.

### Set

```text
/ov-query-config set --scope peer \
  --recallLimit 6 \
  --candidateLimit 40 \
  --scoreThreshold 0.15 \
  --resourceTypes user,agent,resource
```

```text
/ov-query-config set --scope session \
  --ovSearchLimit 8 \
  --recallPreferAbstract true
```

### Weights

```text
/ov-query-config set --scope peer \
  --weight baseScore=1,leaf=0.12,lexicalOverlapMax=0.2 \
  --categoryWeight preference=0.2,event=0.1 \
  --resourceTypeWeight user=0.15,resource=0.1
```

### Unset

```text
/ov-query-config unset recallLimit scoreThreshold rankingWeights --scope session
```

### Reset

```text
/ov-query-config reset --scope session
/ov-query-config reset --scope peer
```

## Persistence

Runtime query config can be persisted by configuring `runtimeQueryConfigPath`.

```json
{
  "plugins": {
    "entries": {
      "openviking": {
        "config": {
          "runtimeQueryConfigPath": "/path/to/runtime-query-config.json"
        }
      }
    }
  }
}
```

If the file is missing, the store starts empty. If the file is malformed, the plugin keeps the last known-good in-memory config and reports a warning instead of breaking recall.

## Interaction With Tools

`memory_recall` and `ov_search` use runtime config as defaults. Explicit arguments on the current tool call still win.

Examples:

- If peer scope sets `ovSearchLimit=8`, `ov_search` defaults to 8 results.
- If a specific `ov_search` call passes `limit=3`, that request returns 3 results and does not mutate runtime config.
- If session scope sets `scoreThreshold`, it overrides peer/static threshold only for that session.

## Verification

The split implementation is covered by unit tests for:

- normalization and persistence,
- layer precedence,
- empty patch rejection,
- `peer` and `claw` scope compatibility,
- `memory_recall` and `ov_search` runtime defaulting,
- explicit request arguments overriding runtime defaults.
