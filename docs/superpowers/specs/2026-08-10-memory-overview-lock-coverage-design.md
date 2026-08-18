# Memory Overview Lock Coverage

## Problem

`StreamingMemoryUpdater` currently acquires one exact-path batch lease for the
memory files, replacement targets, and link endpoints touched by an update. It
passes that lease into `MemoryUpdater`, which later reuses it to write each
affected directory's derived `.overview.md` file.

The overview path is not part of the original lock batch. Rust pathlock coverage
validation therefore rejects the write with `does not cover the requested
operation`, leaving the memory file updated but its overview stale or missing.

## Options

1. Add each affected `.overview.md` path to the original exact-path batch. This
   preserves one atomic lease acquisition, serializes updates that share an
   overview, and avoids locking unrelated descendants.
2. Acquire a separate exact lock inside `generate_overview`. This holds the
   overview lock for less time, but introduces nested acquisition and permits
   memory files to change before overview generation obtains its lock.
3. Replace the exact file locks with directory tree locks. This covers every
   derived operation but unnecessarily serializes all mutations below a memory
   directory.

## Design

Use option 1. Extend `_operation_lock_paths` so every upserted or deleted memory
URI contributes both its own exact path and its parent directory's
`.overview.md` exact path. Continue adding replacement and link endpoint paths
as today; those files do not independently trigger overview generation, so they
do not contribute additional overview paths unless they are also an upsert or
delete target.

Normalize directory URIs by removing trailing slashes before appending
`.overview.md`, convert all URIs through `VikingFS._uri_to_path`, and retain the
existing set-based deduplication and sorted result. Multiple operations in one
directory therefore add only one overview lock.

The resulting batch lease is acquired before any memory mutation and remains
held until `MemoryUpdater.apply_operations` finishes. The existing
`lease_ref=self._transaction_handle` propagation then becomes valid for the
overview write without changes to `generate_overview`.

This change intentionally uses an exact lock for `.overview.md`, not a tree lock
for its directory. It fixes overview write coverage while preserving concurrency
for unrelated files. Recursive removal of an empty memory directory requires a
tree lock and is outside this focused change; its current best-effort behavior
remains unchanged.

## Error Handling

Lock acquisition retains the existing 300-second timeout and all-or-nothing
batch semantics. If the overview path conflicts with another updater, the whole
memory update waits or fails before mutating storage rather than updating memory
and discovering invalid coverage at overview write time.

## Verification

Add regression assertions covering:

- one upsert includes its memory file and sibling `.overview.md` paths;
- multiple operations in the same directory deduplicate the overview path;
- operations in distinct directories include one overview path per directory;
- delete targets contribute their overview paths;
- replacement and link endpoint coverage remains unchanged.

Run the focused streaming-memory-updater tests, the memory-updater overview
tests, formatting/lint checks for touched Python files, and the broader session
memory test suite if focused tests pass.
