# RFV Reindex Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 resource/skill reindex 接入 RNFV 的状态、`ContextUpdatePlan` 和队列执行主链，统一 `recursive`，增加 `force`，默认按 L0/L1/L2 内容指纹和标量变化增量收敛。

**Architecture:** reindex 使用 RFV（Request、Formal、Vector）快照，不构造 parser artifact 或 N。RFV resolver 输出既有 `ContentState`/`IndexState`，复用 `SemanticPlan`、`IndexSlot` 和 `DirectIndexAction`；`force` 只在 resolver 中把目标 level 解析为 `STALE/MISSING`，不进入 Plan。第一阶段迁移 resource/skill；memory、模型迁移和旧队列消息 fencing 暂不改。

**Tech Stack:** Python、asyncio、FastAPI/Pydantic、VikingFS、VikingDB、pytest、Python/Go/TypeScript/Rust SDK。

---

### Task 1: Reindex API and scope contract

**Files:**
- Modify: `openviking/server/routers/content.py`
- Modify: `openviking/service/core.py`
- Modify: `openviking/service/reindex_executor.py`
- Test: `tests/server/test_admin_rebuild_api.py`

**Steps:**
1. Write failing tests for `force=false/true` propagation and consistent `recursive` propagation in both modes.
2. Run focused tests and confirm the expected missing-argument failures.
3. Add `force: bool = False`; retain `recursive: bool = True`.
4. Remove the standalone `prune_orphans/dry_run` contract; complete RFV snapshots clean confirmed V orphans during normal reindex.
5. Run focused tests.

### Task 2: RFV snapshot and state resolution

**Files:**
- Create: `openviking/storage/resource_rfv.py`
- Modify: `openviking/storage/resource_diff.py`
- Modify: `openviking/storage/context_update_plan.py`
- Test: `tests/storage/test_resource_rfv.py`
- Test: `tests/storage/test_context_update_plan.py`

**Steps:**
1. Write failing tests for one F scan, one V inventory, scope validation, completeness, L0/L1/L2 MD5, scalar-only changes, force and recursive.
2. Implement immutable RFV snapshots using shared `RequestIntent` and vector snapshot types.
3. Implement RFV resolution: `COMPLETE`, `STALE`, `MISSING`, `ORPHAN`, duplicate and level conflict.
4. Add per-level index state/source state to `ResourceDiffEntry` without changing existing RNFV behavior.
5. Compile RFV state with the existing Plan schema and no content actions.
6. Run RFV and RNFV regression tests.

### Task 3: Vectors-only execution through DirectIndexAction

**Files:**
- Modify: `openviking/utils/resource_processor.py`
- Modify: `openviking/utils/embedding_utils.py`
- Test: `tests/storage/test_context_update_plan.py`
- Test: `tests/unit/test_vectorize_file_strategy.py`

**Steps:**
1. Write failing tests for L0/L1/L2 UPSERT, MD5 propagation and source-unreadable fail-closed behavior.
2. Extend existing direct-action execution for directory L0/L1 using `vectorize_directory_meta`.
3. Write normalized visible-body MD5 into L0/L1 records.
4. Keep L2 file-byte MD5 semantics.
5. Run focused tests.

### Task 4: Semantic and vector work through one SemanticPlan

**Files:**
- Modify: `openviking/storage/context_update_plan.py`
- Modify: `openviking/storage/queuefs/semantic_executor.py`
- Modify: `openviking/utils/resource_processor.py`
- Test: `tests/storage/test_semantic_executor_incremental.py`
- Test: `tests/storage/test_context_update_plan.py`

**Steps:**
1. Write failing tests showing one RFV plan drives file generation, directory aggregation and L0/L1/L2 vector MERGEs.
2. Reuse `SemanticTreeEntry.repair` and existing `IndexSlot(UPSERT)` for stale/missing levels.
3. Make directory emission honor repair state while preserving normal incremental no-op behavior.
4. Ensure the reindex semantic path no longer performs a second manual vector scan.
5. Run semantic-plan regression tests.

### Task 5: Resource/skill executor integration

**Files:**
- Modify: `openviking/service/reindex_executor.py`
- Test: `tests/server/test_admin_rebuild_api.py`
- Test: `tests/server/test_api_skills.py`

**Steps:**
1. Write failing end-to-end service tests for resource/skill, both modes, force, recursive, tags clear/replace/append, orphan cleanup and failures.
2. Route resource/skill reindex through the RFV snapshot/resolver/Plan path.
3. Preserve authorization, exact/tree locks, task tracking, wait behavior, counters and warnings.
4. Keep memory on the legacy path.
5. Delete legacy resource/skill vector loops only after all callers have moved.

### Task 6: SDKs, CLI and documentation

**Files:**
- Modify Python/Go/TypeScript SDK reindex options and tests.
- Modify Rust `ov reindex` arguments and tests.
- Modify English/Chinese content API documentation.

**Steps:**
1. Add failing serialization tests for `force`.
2. Implement omission/default behavior consistently.
3. Document `recursive`, `force`, modes, skip semantics and the current resource/skill scope.
4. Run all SDK/CLI focused tests.

### Task 7: Verification

1. Run RFV/RNFV storage tests.
2. Run reindex server tests and resource/skill API tests.
3. Run Python/Go/TypeScript/Rust focused suites.
4. Run lint/type checks on changed files.
5. Request independent code review and address all Critical/Important findings.
