# Memory Link Lock Stabilization

## Problem

`StreamingMemoryUpdater` acquires one exact-path batch lease before calling
`MemoryUpdater.apply_operations`. The lock set is computed from the resolved
operations, including the links and backlinks present on
`delete_file_contents`.

When a deleted memory is replaced, `MemoryUpdater` later rereads the persisted
deleted file and inherits its links onto the replacement and neighboring
memories. The persisted file may contain link endpoints that were absent from
the resolved operation object. Those neighbors are therefore outside the
original lease, and RagFS rejects their writes with `pathlock lease ref ...
does not cover the requested operation`.

Lock expansion cannot safely happen inside relation inheritance. Upserts have
already been written by that point, so releasing the original lease there
would expose a partially applied update. Waiting for additional locks while
holding the original lease could also produce a cross-owner circular wait.

## Options

1. Stabilize an exact-path lock set before applying any mutation. Acquire the
   initial operation locks, reread persisted replacement sources, calculate the
   complete relation endpoint set, and reacquire the complete batch if it grew.
   Revalidate after reacquisition and retry if the source relationships changed.
2. Dynamically acquire additional exact locks during relation inheritance with
   the original lease as `owner_lease_ref`. This avoids self-conflict, but two
   owners can hold disjoint initial locks and wait for each other's expansion.
   It also occurs after earlier writes, so failure cannot restore atomicity.
3. Acquire a tree lock for the entire peer memory subtree. This covers every
   possible relationship endpoint, but serializes otherwise independent memory
   updates and would significantly reduce Locomo commit concurrency.

Use option 1. It preserves exact-lock concurrency and makes the correctness
boundary explicit: no memory mutation starts until one lease covers every path
known from authoritative persisted relationship data.

## Design

Add a focused asynchronous helper in `streaming_memory_updater.py` that returns
the stable lease used by `MemoryUpdater`:

```python
async def _acquire_stable_operation_lease(
    operations: ResolvedOperations,
    viking_fs: Any,
    ctx: RequestContext,
) -> Any | None:
    """Acquire one lease covering authoritative relation endpoints."""
```

The helper performs these steps:

1. Compute the initial exact paths with `_operation_lock_paths` and acquire them
   as one batch.
2. While holding that lease, reread every URI in `delete_replacements`. Parse
   each persisted file and union all link and backlink endpoints into the
   required URI set.
3. If the required lock set is unchanged, return the current lease.
4. If it grew, release the current lease and acquire the full required set in
   one all-or-nothing batch. Do not wait for expanded paths while retaining the
   narrower lease.
5. Reread the replacement sources after reacquisition and recompute the set. If
   the set grew again, release and repeat. Limit stabilization to three total
   batch acquisitions; failure raises before `MemoryUpdater.apply_operations`
   starts.

The authoritative reread is used only to discover relation endpoints. It does
not replace the resolved operation objects or change the memory content chosen
by extraction. Existing URI conversion, deduplication, deterministic sorting,
overview coverage, and the 300-second batch acquisition timeout remain intact.

`StreamingMemoryUpdater._apply_operations` calls the helper inside its existing
process-local `_apply_lock`, constructs `MemoryUpdater` with the returned lease,
and releases that final lease in its existing `finally` block. No lock lifecycle
logic is added to `_inherit_deleted_link_relations`.

## Concurrency and Consistency

Releasing a narrow lease before acquiring the complete set creates an unlocked
window. The post-acquisition reread closes that TOCTOU gap: mutations proceed
only when the relation endpoint set observed under the final lease is a subset
of that lease's coverage. If another process changes a replacement source in
the window, the changed endpoint set triggers another full-batch acquisition.

Each acquisition requests the complete known set at once. RagFS normalizes and
sorts batch requests deterministically and applies all-or-nothing rollback on
conflict. The updater never waits for an expanded path while retaining a
narrower lease, eliminating the staged A-then-B/B-then-A circular-wait pattern.

The three-acquisition limit prevents an adversarially changing relationship
graph from keeping a commit in an unbounded stabilization loop. On exhaustion,
the helper releases its current lease and the update fails before writing any
memory file. The helper also releases any lease it still owns before propagating
an unexpected discovery or parsing exception.

## Error Handling

- A missing persisted deleted file contributes no additional endpoints; the
  existing delete/update path remains responsible for reporting its result.
- Malformed persisted memory content fails lock preparation and aborts the
  update before mutation rather than silently applying with incomplete locks.
- Failure to acquire or release a lease propagates through the existing commit
  error path.
- Stabilization exhaustion raises an explicit error that includes the number of
  acquisitions and does not call `MemoryUpdater.apply_operations`.

## Verification

Add regression coverage for these observable behaviors:

- a persisted replacement source containing a neighbor absent from
  `delete_file_contents` causes the final lease to include that neighbor before
  any write;
- when the first authoritative reread expands the set, the narrow lease is
  released before the complete batch acquisition;
- a source that changes during release and reacquisition is reread and causes a
  bounded second stabilization acquisition;
- exhaustion raises before any memory write;
- operations without replacement inheritance retain one acquisition and the
  existing lease propagation behavior.

Run the focused streaming memory updater tests, the memory updater regression
tests, Ruff checks for touched files, and `git diff --check`.

## Follow-up: Post-group Link Remapping

Grouped streaming updates have a second link-write path in
`_apply_post_group_links`. All grouped requests have completed before this
method runs, so both inputs that determine the final link endpoints are already
stable: the request's resolved links and the combined result's
`delete_replacements` map.

The current ordering acquires exact locks for the original link endpoints and
then remaps those endpoints. A case-normalizing replacement such as
`entities/person/andrew.md` to `entities/person/Andrew.md` can therefore make
`write_stored_links` write a path outside the lease.

Three approaches were considered:

1. Remap links first, then acquire exact locks for the remapped endpoints.
   This is the smallest fix and covers every later write because validation
   only filters links; it never changes or adds endpoints.
2. Lock the union of original and remapped endpoints. This is correct but holds
   locks that the post-remap validation and write path never use.
3. Acquire the original range and release/reacquire if remapping expands it.
   This adds an unnecessary unlocked window and retry lifecycle even though the
   complete remapped range is already computable.

Use option 1. `_apply_post_group_links` will merge and remap links before
calling `_uri_lock_paths`. It will then acquire one exact-path batch lease,
filter the remapped links, and write only endpoints already covered by that
lease. No dynamic expansion or tree lock is needed on this path.

Add a regression test whose original links use pre-replacement URIs and whose
combined result maps them to differently cased replacement URIs. The test must
observe that the acquired batch contains the replacement paths and that all
link writes complete under that lease. The test must fail against the old
ordering by surfacing the same lease-coverage rejection seen in Locomo.
