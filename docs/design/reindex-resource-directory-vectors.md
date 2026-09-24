# Resource directory vector reindexing

The behavior contract is [Issue #5332](https://github.com/volcengine/OpenViking/issues/5332). This report describes the current implementation.

## Flow and ownership

`ReindexExecutor` owns the resource vector traversal shared by resource, user-namespace, and global-namespace reindex. In `semantic_and_vectors` mode, semantic regeneration finishes first. The vector traversal deduplicates directory and file URIs, processes resource directories in bounded batches, then runs the existing file batches. Memory and skill traversal use their own paths.

| Obligation | Authority and interface | Evidence |
|---|---|---|
| Directory selection, marker fallback, L0 before L1 | `ReindexExecutor._reindex_resource_vectors_from_entries` | Mixed-directory contract test |
| Per-task directory limit and ordered result accounting | `ReindexExecutor._run_ordered_counter_batches` and `ReindexConfig.directory_vectorization_concurrency` | Concurrency, cap, and config tests |
| Vector message fields and embedding completion tracking | `ReindexExecutor._upsert_context` | Existing enqueue tests and mixed-directory contract test |
| Cancellation and unexpected failure cleanup | `ReindexExecutor._run_ordered_counter_batches` | Cancellation test |

Each directory processor returns its own counters. The batch runner merges them in traversal order, so completion order does not change warning order or counts. Known L0 and L1 enqueue failures are counted independently; unexpected errors cancel and drain active batch tasks before propagation. Successful vector enqueues remain queued. The file stage begins only after all directory batches finish.

The setting defaults to eight active directories and is capped at 64. It is independent of the file admission limit. This limits concurrent directory work within one reindex task; it does not change the embedding worker's concurrency or bound the sum across separate reindex tasks.

## Validation

The owner-level tests cover directory overlap and cap, marker selection, ordered warnings and counts, and cancellation. The existing file-stage test protects its behavior after the shared batch runner change. Config tests cover the new default, invalid values, and independent settings. A directory-heavy benchmark compares serial admission with the default; its results and backend are reported in the PR.

The change does not alter semantic regeneration, persistent records, resource import, memory/skill reindex, or search ranking. Concurrent directories have no cross-directory enqueue ordering guarantee.
