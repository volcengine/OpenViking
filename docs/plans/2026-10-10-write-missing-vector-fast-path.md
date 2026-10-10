# Write Missing-Target Vector Fast Path Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Treat an explicit write whose locked F target is missing as authoritative new content, skipping V inventory and replacing the canonical L2 record with UPSERT semantics.

**Architecture:** The write adapters inject an empty V snapshot only for locked missing targets; existing targets keep full RNFV reads. Created-file intent travels through `FileRefreshRequest` and `SemanticMsg` to force semantic file vectorization to use UPSERT, so no execution-time MERGE read preserves discarded scalar state. Shared add-resources RNFV behavior remains unchanged.

**Tech Stack:** Python, pytest, RNFV `ContextUpdatePlan`, QueueFS semantic messages.

---

### Task 1: Lock the write-only V read boundary

- Test missing single write passes `vector_inventory={}`.
- Test existing single write leaves `vector_inventory=None` and reads V normally.
- Test mixed batch hydrates only existing target URIs.

### Task 2: Carry created-file UPSERT intent through the semantic queue

- Add a per-file vector action to `FileRefreshRequest` and `SemanticMsg`.
- Keep wire decoding backward compatible with missing/unknown fields.
- Forward the action through `SemanticProcessor` into `SemanticTreeExecutor`.
- Apply it only when no richer `SemanticPlan` action is present.

### Task 3: Verify scope and compatibility

- Verify vectors-only new files already use direct UPSERT.
- Verify semantic new files use UPSERT while modified files retain MERGE.
- Verify add-resources/reindex callers that omit the action retain current behavior.
- Run content-write, batch-write, semantic-message/executor, RNFV and lint regressions.
