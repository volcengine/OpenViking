# Memory Overview Lock Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure each streaming memory update's exact-path batch lease covers the `.overview.md` file regenerated for every upserted or deleted memory directory.

**Architecture:** Extend `_operation_lock_paths` at the point where it still has the logical operation URI set. Derive one sibling `.overview.md` URI per directly mutated memory file, merge those URIs into the existing exact-lock set, then retain the current URI-to-path conversion, deduplication, sorting, lease acquisition, and propagation.

**Tech Stack:** Python 3.10+, pytest, Ruff, VikingFS/RagFS pathlock APIs.

## Global Constraints

- Use exact locks for `.overview.md`; do not replace file locks with directory tree locks.
- Add overview paths only for upsert and delete targets that cause `MemoryUpdater` to regenerate an overview.
- Preserve existing replacement and link endpoint lock coverage.
- Keep empty-directory recursive deletion behavior outside this focused change.

---

### Task 1: Cover derived overview files in the streaming update lease

**Files:**
- Modify: `tests/session/memory/test_streaming_memory_updater.py:406-445`
- Modify: `openviking/session/memory/streaming_memory_updater.py:1840-1864`

**Interfaces:**
- Consumes: `_operation_uri_set(operations: ResolvedOperations | None) -> set[str]` and `_uri_lock_paths(uris: set[str], viking_fs: Any | None, ctx: RequestContext) -> list[str]`.
- Produces: `_operation_lock_paths(operations: ResolvedOperations, viking_fs: Any | None, ctx: RequestContext) -> list[str]` with exact paths for directly mutated files and one `.overview.md` per directly affected parent directory.

- [x] **Step 1: Extend the existing regression expectation**

Update `test_operation_lock_paths_cover_deletes_replacements_and_link_endpoints` so its literal expected list contains:

```python
assert _operation_lock_paths(operations, fs, _ctx()) == [
    "/user/u/memories/notes/.overview.md",
    "/user/u/memories/notes/deleted.md",
    "/user/u/memories/notes/inherited_neighbor.md",
    "/user/u/memories/notes/neighbor.md",
    "/user/u/memories/notes/replacement.md",
    "/user/u/memories/notes/updated.md",
]
```

This catches the production bug where `_operation_lock_paths` omits the sidecar path. Because the fixture contains both an upsert and delete in the same directory, it also proves set-based deduplication produces one overview path while replacement and link endpoint paths remain covered.

- [x] **Step 2: Run the regression test and verify RED**

Run:

```bash
uv run pytest tests/session/memory/test_streaming_memory_updater.py::test_operation_lock_paths_cover_deletes_replacements_and_link_endpoints -q
```

Expected: FAIL because the actual list does not contain `/user/u/memories/notes/.overview.md`.

- [x] **Step 3: Add a distinct-directory regression test**

Add a test that builds one upsert under `notes/` and one delete under `events/2023/02/01/`, then asserts the literal result contains both memory paths and exactly these two derived paths:

```python
[
    "/user/u/memories/events/2023/02/01/.overview.md",
    "/user/u/memories/events/2023/02/01/deleted.md",
    "/user/u/memories/notes/.overview.md",
    "/user/u/memories/notes/updated.md",
]
```

- [x] **Step 4: Implement the minimal URI derivation**

In `_operation_lock_paths`, retain the directly mutated URI set separately and add each valid parent overview URI before collecting replacements and link endpoints:

```python
operation_uris = _operation_uri_set(operations)
uris = set(operation_uris)
for uri in operation_uris:
    normalized_uri = str(uri).rstrip("/")
    directory, separator, _ = normalized_uri.rpartition("/")
    if separator and directory:
        uris.add(f"{directory}/.overview.md")
```

Do not derive overview paths from `resolved_links`, `delete_replacements`, or inherited link endpoints because those URI categories do not directly drive the overview-generation loop in `MemoryUpdater.apply_operations`.

- [x] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
uv run pytest tests/session/memory/test_streaming_memory_updater.py::test_operation_lock_paths_cover_deletes_replacements_and_link_endpoints tests/session/memory/test_streaming_memory_updater.py::test_operation_lock_paths_cover_each_affected_overview_directory -q
```

Expected: 2 passed.

- [x] **Step 6: Run relevant regression suites and static checks**

Run:

```bash
uv run pytest tests/session/memory/test_streaming_memory_updater.py tests/session/memory/test_memory_updater.py -q
uv run ruff check openviking/session/memory/streaming_memory_updater.py tests/session/memory/test_streaming_memory_updater.py
uv run ruff format --check openviking/session/memory/streaming_memory_updater.py tests/session/memory/test_streaming_memory_updater.py
git diff --check
```

Expected: all tests and checks pass with no warnings introduced by the change.

- [x] **Step 7: Commit the implementation**

```bash
git add openviking/session/memory/streaming_memory_updater.py tests/session/memory/test_streaming_memory_updater.py docs/superpowers/plans/2026-08-10-memory-overview-lock-coverage.md
git commit -m "fix(memory): cover overview files in update leases"
```
