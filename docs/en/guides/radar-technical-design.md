# GitHub PR Radar Technical Design

Radar collects merged GitHub PRs during source Watch execution and publishes reports grouped by merge-time windows. For setup and commands, see the [user guide](radar-usage.md).

## Scope

- GitHub repositories only; collect merged PRs rather than open or unmerged closed PRs.
- Execute Radar before the source HEAD deduplication check, including when HEAD is unchanged.
- Keep collection progress, a persistent PR queue, and pending publication records within Radar.
- Use existing Watch scheduling. Radar errors are logged without changing the source Watch's business result.
- Keep public authentication, account caches, and source resource moves unchanged.

## Storage and identity

The service derives the report directory from the Watch owner and Watch ID:

```text
viking://user/<user_id>/resources/radar/<watch_id>/
├── latest.md
├── reports/<report_id>.md
├── .radar-signals.jsonl
└── .radar-state.json
```

The Watch ID is distinct from the initial import task ID. Storage context provides account isolation.

Radar uses the owner's ordinary `USER` context with `bypass_acl=False`. It does not accept a custom report destination or add shared-directory permissions, ownership markers, or source-move hooks.

Watches for the same repository have separate report directories. Moving source resources does not move reports when the Watch ID stays the same. Deleting a Watch does not delete its historical reports.

## Collection and progress

Radar calls `gh api graphql`, with credentials managed entirely by `gh`.

The GraphQL search uses `repo:`, `is:pr`, `is:merged`, and a `merged:` time range. Each page contains at most 100 PRs. Ranges exceeding the 1,000-result search limit are split at second boundaries. An overflowing single-second range fails without advancing progress.

Each run permits at most 32 requests, with a 30-second timeout per request. `max_prs_per_run` limits newly collected PRs per run. Incomplete ranges are persisted; page cursors are not reused across triggers.

The collector stores PR numbers, titles, URLs, authors, labels, descriptions, state, draft flags, and creation, update, closure, and merge times. It does not fetch comments or diffs by default.

Two watermarks serve different purposes:

- Collection progress identifies fully scanned time ranges whose facts have been saved.
- Report progress identifies windows whose publication has completed, or empty windows that have been closed.

Queries overlap previous ranges to accommodate indexing delay. PRs are deduplicated by repository and number; later metadata edits are not new report events. Distinct PRs merged in the same second remain distinct.

Facts are saved before collection progress advances. A capped or interrupted run retains its queue so later triggers can complete collection. Truncated search results never count as a complete range.

## Report windows

The Watch creation time `T0` anchors windows:

- Initial backfill covers `[T0 - backfill_days, T0]`. A nonempty backfill can publish as soon as collection completes.
- Subsequent windows cover `(T0, T0 + interval]`, then successive intervals.
- `merged_at` determines which window owns each PR.
- A due window waits until collection is complete.
- Empty windows advance report progress without generating a file or calling the model.
- Overdue windows are processed in order rather than combined under an inaccurate interval.

The Watch interval controls collection frequency. `digest_interval_minutes` independently controls report windows and is required when Radar is enabled. Neither a manual trigger nor a queue threshold closes a window early.

## Summaries and reports

The pipeline first builds deterministic PR facts, then optionally asks a model for per-PR Chinese summaries and topic overviews.

PR numbers, titles, URLs, authors, states, and times come from collected facts. Model-returned numbers must belong to the input batch, and highlighted PRs must belong to their reported topic.

Model inputs are batched. Failed batches retain their facts; topic-generation failures retain available per-PR summaries. If summaries are disabled or entirely fail, Radar can publish a factual template.

Reports identify their repository, window, generation time, PR count, generation mode, and source links. Model interpretations are not presented as verified source-code analysis.

## Publication and recovery

Publication spans several files:

1. Save a pending record containing a fixed report ID, full Markdown content, window information, and the intended final queue and state.
2. Write the historical report and `latest.md`.
3. Save the final queue and progress, then clear the pending record.

If publication fails after the pending record is saved, the next trigger resumes with the same content and ID without calling the model again. It completes recovery before a later trigger resumes collection, preventing saved recovery data from overwriting newly collected facts.

State JSON and queue JSONL are encoded as UTF-8 bytes, written to same-directory `.tmp` files, and published through the existing AGFS `mv` operation. Writes and replacement preserve ordinary user authorization. Errors propagate; loading uses only the formal files.

For the local filesystem, staging prevents an interrupted temporary write from truncating the formal checkpoint. Individual file replacement is not a multi-file transaction or a guarantee of power-loss durability. The publication record coordinates recovery across files.

State read or parse failures are errors, not permission to silently create a new Watch state.

## Configuration example

These are example test intervals, not default scheduling values:

```json
{
  "processing_mode": "vectors_only",
  "watch_interval": 1,
  "args": {
    "pr_radar": {
      "enabled": true,
      "backfill_days": 7,
      "digest_interval_minutes": 30,
      "max_prs_per_run": 50,
      "summary_enabled": true,
      "summary_batch_bytes": 60000
    }
  }
}
```

`vectors_only` controls source processing; Radar summaries have their own `summary_enabled` switch. Creation validates the repository, types, ranges, Watch interval, and unsupported fields before background execution.

## Validation

The Radar and Watch regression suite passed 89 tests, including simulated short writes and replacement failures. These tests establish behavior under controlled faults, not real backend power-loss validation.

The recorded macOS run used a one-minute Watch interval, a 30-minute report interval, and a one-day backfill. It published 48 merged PRs with 48 model summaries and 12 topic overviews in `pr_summaries` mode. The latest and historical report matched, and the first subsequent empty window advanced progress without replacing the report.

The full Chinese design and local validation details are available in the [Chinese document](../../zh/guides/radar-technical-design.md).
