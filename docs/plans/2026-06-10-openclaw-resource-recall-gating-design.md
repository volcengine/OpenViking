# OpenClaw Resource Recall Gating Design

Date: 2026-06-10

## Problem

When `recallResources` is enabled, the OpenClaw plugin searches both user memories and `viking://resources`, then applies the same recall score threshold to all returned items. The default threshold is low enough that shared resource entries such as transcripts or logs can be injected even when their semantic match is weak.

The ranking code also tokenizes recall queries with an ASCII-only expression, so Cyrillic query terms do not contribute lexical overlap ranking.

## Goals

- Keep the fix small and local to OpenClaw recall ranking.
- Require a stronger minimum score for `viking://resources` results than for user memories.
- Preserve existing behavior for user and agent memories.
- Support Unicode letters and numbers in recall query tokenization.
- Cover the behavior with focused unit tests.

## Non-Goals

- Do not change server-side retrieval or search scoring.
- Do not change the default `recallResources` setting.
- Do not add broad vague-query suppression in this patch, because it can reject useful short prompts.

## Design

Add resource-aware threshold helpers in `examples/openclaw-plugin/memory-ranking.ts`:

- `isResourceMemory(item)` returns true for URIs under `viking://resources/`.
- `RESOURCE_RECALL_SCORE_FLOOR` defines a minimum score for shared resources. The initial value is `0.56`, which blocks the observed `~0.53` noisy resource matches while keeping the change conservative.
- `recallScoreThresholdForItem(item, baseThreshold)` returns the max of the configured threshold and resource floor for shared resources, and the configured threshold for other memories.
- `passesRecallScoreThreshold(item, baseThreshold)` centralizes the threshold check.

Update recall flows to call `passesRecallScoreThreshold` before post-processing:

- Auto recall in `auto-recall.ts`.
- Explicit `memory_recall` in `index.ts`.

After the resource-aware filter, call `postProcessMemories` with `scoreThreshold: 0` so the threshold logic has a single owner.

Update query tokenization from ASCII-only tokens to Unicode-aware tokens:

```ts
/[\p{L}\p{N}][\p{L}\p{N}_-]+/giu
```

This lets Cyrillic query terms participate in lexical overlap ranking without changing the overall ranking model.

## Tests

Add or update unit tests to verify:

- `viking://resources/...` is detected as a resource memory.
- A resource item below `RESOURCE_RECALL_SCORE_FLOOR` fails the threshold even if it passes the base threshold.
- A user memory at the same score still passes the base threshold.
- Cyrillic query terms can move a matching item ahead of a higher-scored generic item.
- Auto recall skips a low-score resource result when resource recall is enabled.

## Risk

The main behavior change is that shared resources need a slightly stronger score to inject. This can reduce noisy context injection, but it may also suppress marginally relevant resources with scores between the base threshold and `0.56`. User and agent memories keep their existing threshold behavior.
