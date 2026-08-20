# Memory Link Lock Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure a streaming memory update acquires one stable exact-path lease covering relationship endpoints discovered from persisted replacement sources before any mutation begins.

**Architecture:** Add a pre-apply lease helper that acquires the current operation paths, rereads persisted deleted memories under that lease, and releases/reacquires the complete batch whenever new endpoints appear. The helper revalidates after every reacquisition and aborts after three acquisitions, while `MemoryUpdater.apply_operations` continues to receive and release only the final stable lease.

**Tech Stack:** Python 3.10+, asyncio, pytest, Ruff, VikingFS/RagFS PathLock APIs.

## Global Constraints

- Never wait for newly discovered paths while retaining a narrower lease.
- Do not call `MemoryUpdater.apply_operations` until the lock set is stable.
- Preserve exact locks, deterministic path sorting, overview lock coverage, and the existing 300-second acquisition timeout.
- Treat persisted memory content as authoritative only for link endpoint discovery; do not replace extraction output.
- Limit stabilization to three total batch acquisitions and release the current lease before propagating failure.

---

### Task 1: Stabilize authoritative relationship lock coverage before mutation

**Files:**
- Modify: `tests/session/memory/test_streaming_memory_updater.py:43-110,385-470`
- Modify: `openviking/session/memory/streaming_memory_updater.py:450-490,1840-1890`

**Interfaces:**
- Consumes: `_operation_lock_paths(operations, viking_fs, ctx) -> list[str]`, `_uri_lock_paths(uris, viking_fs, ctx) -> list[str]`, `MemoryFileUtils.read(content, uri=uri) -> MemoryFile`, and `ResolvedOperations.delete_replacements`.
- Produces: `_acquire_stable_operation_lease(operations, viking_fs, ctx) -> Any | None`, returning a lease whose requested path set covers every persisted replacement-source link and backlink endpoint observed under that lease, or raising before mutation after three expanding observations.

- [x] **Step 1: Write all failing stabilization tests**

Extend `RecordingPathlockClient` so each acquisition returns a distinct lease while preserving the first lease value expected by existing tests:

```python
lease_number = len([event for event in self.events if event[0] == "acquire"]) + 1
lease_ref = "memory-batch-lease" if lease_number == 1 else f"memory-batch-lease-{lease_number}"
lease = {"lease_ref": lease_ref}
```

Add a `ChangingReadPathlockedInMemoryVikingFS` fake for TOCTOU cases:

```python
class ChangingReadPathlockedInMemoryVikingFS(PathlockedInMemoryVikingFS):
    def __init__(self, files, changing_uri, changing_contents):
        super().__init__(files)
        self.changing_uri = changing_uri
        self.changing_contents = list(changing_contents)
        self.changing_read_count = 0

    async def read_file(self, uri: str, ctx=None):
        if uri == self.changing_uri and self.changing_read_count < len(self.changing_contents):
            content = self.changing_contents[self.changing_read_count]
            self.changing_read_count += 1
            self.events.append(("read", uri, self._async_agfs.active_lease))
            return content
        return await super().read_file(uri, ctx=ctx)
```

Add `test_streaming_memory_updater_reacquires_for_persisted_delete_links_before_writes`. Build a persisted deleted `MemoryFile` whose `links` contains a neighbor absent from the `delete_file_contents` fixture, then call `_apply_operations` and assert:

```python
assert neighbor_path not in first_acquire[1]
assert neighbor_path in second_acquire[1]
assert events.index(first_release) < events.index(second_acquire)
assert all(event[2] == {"lease_ref": "memory-batch-lease-2"} for event in write_events)
assert events.index(second_acquire) < min(events.index(event) for event in write_events)
```

This test catches removal of authoritative persisted-link discovery, failure to release before expansion, and writes performed under the narrow lease.

Add `test_streaming_memory_updater_revalidates_changed_persisted_links`. Return persisted content containing neighbor A on the first read and content containing both A and B on subsequent reads. Assert exactly three acquisitions, both neighbor paths in the third batch, and every write event uses `{"lease_ref": "memory-batch-lease-3"}`.

Add `test_streaming_memory_updater_aborts_after_three_expanding_lock_acquisitions`. Return three successive persisted contents that add neighbors A, B, and C. Assert:

```python
with pytest.raises(RuntimeError, match="after 3 acquisitions"):
    await updater._apply_operations(
        operations=operations,
        request=request,
        messages=messages,
    )

assert len([event for event in fs.events if event[0] == "acquire"]) == 3
assert len([event for event in fs.events if event[0] == "release"]) == 3
assert fs.writes == []
```

- [x] **Step 2: Run the new tests and verify RED**

Run:

```bash
uv run pytest tests/session/memory/test_streaming_memory_updater.py -k 'reacquires_for_persisted_delete_links_before_writes or revalidates_changed_persisted_links or aborts_after_three_expanding_lock_acquisitions' -q
```

Expected: all three tests FAIL because the current implementation acquires only once and never discovers persisted endpoints before mutation.

- [x] **Step 3: Implement persisted endpoint discovery and stable acquisition**

Add a constant and two module-level helpers near the existing lock-path helpers:

```python
_MEMORY_APPLY_LOCK_MAX_ACQUISITIONS = 3


async def _persisted_replacement_relation_uris(
    operations: ResolvedOperations,
    viking_fs: Any,
    ctx: RequestContext,
) -> set[str]:
    uris: set[str] = set()
    for deleted_uri in dict(operations.delete_replacements or {}):
        try:
            content = await viking_fs.read_file(deleted_uri, ctx=ctx)
        except (FileNotFoundError, NotFoundError):
            continue
        if not content:
            continue
        memory_file = MemoryFileUtils.read(content, uri=deleted_uri)
        for link in list(memory_file.links or []) + list(memory_file.backlinks or []):
            from_uri = link.get("from_uri") if isinstance(link, dict) else link.from_uri
            to_uri = link.get("to_uri") if isinstance(link, dict) else link.to_uri
            if from_uri:
                uris.add(str(from_uri))
            if to_uri:
                uris.add(str(to_uri))
    return uris
```

Implement `_acquire_stable_operation_lease` with a monotonic `required_paths` set:

```python
async def _acquire_stable_operation_lease(
    operations: ResolvedOperations,
    viking_fs: Any | None,
    ctx: RequestContext,
) -> Any | None:
    lock_paths = _operation_lock_paths(operations, viking_fs, ctx)
    if not lock_paths:
        return None

    required_paths = set(lock_paths)
    for acquisition in range(1, _MEMORY_APPLY_LOCK_MAX_ACQUISITIONS + 1):
        lease = await viking_fs._async_agfs.pathlock_acquire_exact_batch(
            sorted(required_paths),
            timeout_secs=_MEMORY_APPLY_LOCK_TIMEOUT_SECONDS,
        )
        try:
            relation_uris = await _persisted_replacement_relation_uris(
                operations,
                viking_fs,
                ctx,
            )
            expanded_paths = required_paths | set(
                _uri_lock_paths(relation_uris, viking_fs, ctx)
            )
        except BaseException:
            await viking_fs._async_agfs.pathlock_release(lease)
            raise

        if expanded_paths == required_paths:
            return lease

        await viking_fs._async_agfs.pathlock_release(lease)
        required_paths = expanded_paths
        if acquisition == _MEMORY_APPLY_LOCK_MAX_ACQUISITIONS:
            raise RuntimeError(
                "Unable to stabilize memory apply lock coverage after "
                f"{_MEMORY_APPLY_LOCK_MAX_ACQUISITIONS} acquisitions"
            )

    raise AssertionError("unreachable")
```

Import `NotFoundError` from `openviking_cli.exceptions`. Catch only
`FileNotFoundError` and `NotFoundError`; parsing and permission failures must
abort before mutation.

Change `_apply_operations` from its direct `pathlock_acquire_exact_batch` call to:

```python
lease = await _acquire_stable_operation_lease(
    operations,
    viking_fs,
    request.ctx,
)
```

Retain the existing final release around `MemoryUpdater.apply_operations`.

- [x] **Step 4: Run the new tests and verify GREEN**

Run:

```bash
uv run pytest tests/session/memory/test_streaming_memory_updater.py -k 'reacquires_for_persisted_delete_links_before_writes or revalidates_changed_persisted_links or aborts_after_three_expanding_lock_acquisitions' -q
```

Expected: 3 passed.

- [x] **Step 5: Run the existing lease regression test**

Run:

```bash
uv run pytest tests/session/memory/test_streaming_memory_updater.py::test_streaming_memory_updater_holds_batch_pathlock_across_apply -q
```

Expected: 1 passed and exactly one acquisition remains for an operation without replacement inheritance.

- [x] **Step 6: Run focused regressions and static checks**

Run:

```bash
uv run pytest tests/session/memory/test_streaming_memory_updater.py tests/session/memory/test_memory_updater.py -q
uv run ruff check openviking/session/memory/streaming_memory_updater.py tests/session/memory/test_streaming_memory_updater.py
uv run ruff format --check openviking/session/memory/streaming_memory_updater.py tests/session/memory/test_streaming_memory_updater.py
git diff --check
```

Expected: all commands exit 0 without new warnings.

- [x] **Step 7: Commit the implementation**

```bash
git add openviking/session/memory/streaming_memory_updater.py tests/session/memory/test_streaming_memory_updater.py docs/superpowers/plans/2026-08-11-memory-link-lock-stabilization.md
git commit -m "fix(memory): stabilize link update lock coverage"
```

---

### Task 2: Lock remapped post-group link endpoints

**Files:**
- Modify: `tests/session/memory/test_streaming_memory_updater.py`
- Modify: `openviking/session/memory/streaming_memory_updater.py:231-272`

**Behavior:** `_apply_post_group_links` must acquire its exact-path batch lease
from the endpoints produced by `remap_stored_links`, not the original request
endpoints. `filter_valid_links` may reduce that set but cannot introduce an
endpoint outside it.

- [x] **Step 1: Add the failing regression test**

Create a grouped-link scenario with original lower-case endpoint URIs and
`result.operations.delete_replacements` mapping them to differently cased
replacement URIs. Use the pathlock-aware VikingFS fake so a write outside the
active lease raises the real coverage-style error. Assert the replacement
paths are acquired and the remapped endpoint files are updated.

- [x] **Step 2: Verify RED**

Run only the new test and confirm it fails because the old implementation locks
the original paths before remapping.

- [x] **Step 3: Implement the ordering fix**

Move `remap_stored_links` before `_uri_lock_paths` in
`_apply_post_group_links`. Keep merge, filtering, result accounting, timeout,
and lease release behavior unchanged.

- [x] **Step 4: Verify GREEN and focused regressions**

Run the new test, the existing post-group link tests, the focused streaming
memory updater suite, Ruff checks for the touched Python files, and
`git diff --check`.
