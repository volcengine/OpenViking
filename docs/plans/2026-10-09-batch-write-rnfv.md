# Batch Write RNFV Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Migrate ordinary resource files in `POST /api/v1/content/batch-write` to file-scoped RNFV plans while preserving batch locks, grouped parent freshness, and memory/sidecar special paths.

**Architecture:** Each ordinary resource operation compiles a `root_is_file=True`, `vector_scope="self"` RNFV plan from the final inline bytes and a pre-hydrated exact-URI vector snapshot. The batch coordinator owns validation, locks, ordered formal commits, partial-write failure handling, batched direct-index dispatch, and direct-parent refresh aggregation. Memory and generated sidecars remain on their dedicated paths.

**Tech Stack:** Python, pytest/pytest-asyncio, OpenViking RNFV `ContextUpdatePlan`, AGFS exact path locks, QueueFS semantic and embedding queues.

---

### Task 1: Align batch mode semantics

**Files:**
- Modify: `openviking/storage/content_write.py`
- Test: `tests/server/test_content_batch_write.py`

**Step 1:** Add failing tests proving `create`, `replace`, and `upsert` overwrite existing files and create missing files, while a missing `append` creates its initial body.

**Step 2:** Run the focused tests and confirm the legacy strict mode checks fail.

**Step 3:** Normalize modes after locked `stat`: `create`/`replace`/`upsert` become `replace`; missing `append` becomes `replace`.

**Step 4:** Re-run the focused tests and commit the behavior change.

### Task 2: Add exact multi-URI vector inventory hydration

**Files:**
- Modify: `openviking/storage/viking_vector_index_backend.py`
- Modify: `openviking/storage/resource_diff.py`
- Test: `tests/storage/test_resource_diff_snapshot.py`

**Step 1:** Add failing tests for injecting an exact per-file V inventory into file-scoped RNFV snapshots without a per-file inventory read.

**Step 2:** Add an account-scoped, URI-chunked, strictly paginated multi-URI inventory reader returning records grouped by URI.

**Step 3:** Extend `build_rnfv_snapshot()` to accept the injected V inventory while preserving existing callers.

**Step 4:** Run focused storage tests and commit.

### Task 3: Centralize grouped file-parent refreshes

**Files:**
- Modify: `openviking/utils/summarizer.py`
- Modify: `openviking/storage/context_update_execution.py`
- Test: `tests/storage/test_context_update_plan.py`

**Step 1:** Add failing tests for same-parent coalescing, different-parent separation, and correct aggregate status collection.

**Step 2:** Add `Summarizer.refresh_file_parents()` and make `refresh_file_parent()` a one-file wrapper.

**Step 3:** Keep the single-file plan executor contract unchanged.

**Step 4:** Run focused tests and commit.

### Task 4: Execute ordinary resource batch operations through RNFV

**Files:**
- Modify: `openviking/storage/content_write.py`
- Modify: `openviking/storage/context_update_execution.py`
- Test: `tests/server/test_content_batch_write.py`
- Test: `tests/storage/test_content_write_processing_mode.py`

**Step 1:** Add failing batch tests for per-file RNFV scope, explicit F UPSERT when N equals V, grouped direct-index dispatch, and no sibling mutation.

**Step 2:** Add a private prepared-operation model and compile all ordinary resource plans before the first formal commit.

**Step 3:** Commit successful plan content actions under the existing batch lease, release it, then dispatch direct index actions and grouped parent refreshes.

**Step 4:** Preserve memory, generated-sidecar, wait, and partial-F-write failure behavior.

**Step 5:** Run focused server/storage regressions and commit.

### Task 5: Remove superseded batch plumbing and update the API contract

**Files:**
- Modify: `openviking/storage/content_write.py`
- Modify: `docs/en/api/12-content.md`
- Modify: `docs/zh/api/12-content.md`
- Test: `tests/server/test_content_batch_write.py`

**Step 1:** Delete the ordinary-resource-only legacy refresh/abstract bookkeeping that is made redundant by the plan executor.

**Step 2:** Document unified create/replace/upsert and missing-append behavior plus append retry limitations.

**Step 3:** Run the full targeted suite, lint, and diff checks; commit.
