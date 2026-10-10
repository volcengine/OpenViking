# Content-write RNFV Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove duplicate inline file RNFV-plan assembly from single and batch writes without changing write, locking, or queue behavior.

**Architecture:** A private helper will create the immutable inline artifact, file-scoped RNFV snapshot, context update plan, authoritative content action, and prior L2 abstract from already-rendered bytes and locked target facts. Single write will pass its existing locked target state; batch write will pass its preloaded exact-URI inventory. Batch post-commit orchestration will also remove a legacy return-type branch that is unreachable because `_refresh_batch()` always returns `_BatchRefreshOutcome`.

**Tech Stack:** Python async I/O, OpenViking RNFV, ContextUpdatePlan, pytest.

---

### Task 1: Lock the shared helper contract in tests

**Files:**
- Modify: `tests/storage/test_content_write_processing_mode.py`
- Modify: `tests/server/test_content_batch_write.py`

**Step 1: Write failing tests**

Verify the single-write and batch adapters both preserve the same file-scoped invariants: `root_is_file=True`, self-only V inventory, empty V when F is missing, and an authoritative formal UPSERT action even when the RNFV diff is otherwise unchanged.

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest --no-cov -q tests/storage/test_content_write_processing_mode.py tests/server/test_content_batch_write.py -k inline`

Expected: FAIL because no shared helper is observable.

**Step 3: Implement the smallest shared helper**

Add a private helper in `ContentWriteCoordinator` that consumes locked/preloaded facts and returns `_PreparedBatchResource`; adapt both call sites without adding storage reads or changing lock ownership.

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest --no-cov -q tests/storage/test_content_write_processing_mode.py tests/server/test_content_batch_write.py -k inline`

Expected: PASS.

### Task 2: Remove unreachable batch refresh result handling

**Files:**
- Modify: `openviking/storage/content_write.py`
- Test: `tests/server/test_content_batch_write.py`

**Step 1: Keep the existing batch refresh status tests green**

Run: `.venv/bin/pytest --no-cov -q tests/server/test_content_batch_write.py`

**Step 2: Simplify only the impossible dict branch**

Because `_refresh_batch()` returns `_BatchRefreshOutcome` on every path, use one typed local outcome and derive `queue_status` directly from it.

**Step 3: Run focused regressions and static checks**

Run: `.venv/bin/pytest --no-cov -q tests/storage/test_content_write_processing_mode.py tests/server/test_content_batch_write.py tests/storage/test_context_update_plan.py`

Run: `ruff check openviking/storage/content_write.py && ruff format --check openviking/storage/content_write.py && git diff --check`

**Step 4: Commit**

Run: `git add openviking/storage/content_write.py tests/... docs/plans/... && PRE_COMMIT_ALLOW_NO_CONFIG=1 git commit -m "refactor(content): share inline rnfv planning"`
