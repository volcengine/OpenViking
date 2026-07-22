# Task Signature Experience Recall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `search_experience` return the exact Case identified by an optional `task_signature`, and use Situation search only when that Case file does not exist or no signature is supplied.

**Architecture:** Tau2 continues to build its existing structured `case_lookup`. The loader Skill exposes only the stable `task_signature` to the Agent, while the tool retains the structured lookup needed to locate and validate the Case file. Exact Case resolution reuses `MemoryStore`'s existing URI-candidate and content-validation rules; semantic search remains the fallback path.

**Tech Stack:** Python 3.10+, asyncio, Pydantic-free tool JSON schemas, pytest/pytest-asyncio, Ruff.

## Global Constraints

- `task_signature` is optional and must never be inferred by the Agent.
- A found exact Case is returned even when `Linked Experiences` is empty.
- Situation search runs only when no signature is supplied or the exact Case file does not exist.
- Experience content remains generic and does not store benchmark identity.
- Do not add fallback or backward-compatibility parameters beyond the specified behavior.

---

### Task 1: Exact Case resolution in `search_experience`

**Files:**
- Modify: `benchmark/tau2/train/rollout_executor_vikingbot.py:219-305`
- Test: `tests/session/train/test_rollout_executor_component.py:517-670`

**Interfaces:**
- Consumes: existing Tau2 `case_lookup: dict[str, Any]` from `_tau2_case_lookup(case)`.
- Produces: `_make_search_experience_tool(case_lookup: dict[str, Any] | None = None)` and `search_experience(situation, task_signature=None, limit=2)`.
- Produces response fields: `match_type`, optional `task_signature`, `situation`, and `candidates`.

- [ ] **Step 1: Write failing exact-hit and exact-empty tests**

Add async tests that construct `_make_search_experience_tool(case_lookup=lookup)`, pass the matching `task_signature`, and provide a fake client whose exact Case URI contains matching `Task Signature` and input fields. Assert that `client.search` is never called, `match_type == "exact_case"`, and the exact candidate is returned both with one linked Experience and with an empty `Linked Experiences` section.

```python
payload = json.loads(
    await tool.execute(
        None,
        situation="The user wants to cancel all upcoming reservations.",
        task_signature="tau2:airline:train:39",
    )
)
assert payload["match_type"] == "exact_case"
assert payload["task_signature"] == "tau2:airline:train:39"
assert payload["candidates"][0]["case_name"] == "tau2_airline_train_22"
assert fake_client.search_calls == []
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
pytest tests/session/train/test_rollout_executor_component.py -q --no-cov \
  -k 'search_experience and (exact_case or exact_empty)'
```

Expected: FAIL because `_make_search_experience_tool` does not accept `case_lookup` and the tool schema has no `task_signature`.

- [ ] **Step 3: Implement exact Case lookup and response metadata**

Change the tool factory and schema:

```python
def _make_search_experience_tool(case_lookup: dict[str, Any] | None = None):
    ...
    "task_signature": {
        "type": "string",
        "description": "Optional stable Case task_signature supplied by runtime context.",
    }
```

Add an async exact resolver that imports `MemoryStore`, normalizes the bound lookup, requires the supplied signature to equal the lookup signature, generates exact URI candidates with `_case_uri_candidates`, reads them, and validates content with `_case_matches_lookup`. Return the first validated Case item with score `1.0`; return `None` only when no candidate file exists.

When an exact item exists, call `_experience_search_summary` for that one item and return immediately with `match_type="exact_case"`. Do not inspect whether its Experience list is empty.

- [ ] **Step 4: Run exact tests and verify GREEN**

Run the focused command from Step 2.

Expected: PASS.

- [ ] **Step 5: Write failing fallback tests**

Add tests for:

```python
# Exact Case file absent: semantic search runs.
await tool.execute(None, situation="...", task_signature="tau2:airline:test:99")
assert fake_client.search_calls == [("...", cases_root, 2)]

# No task_signature: semantic search runs without attempting exact reads.
await tool.execute(None, situation="...")
assert fake_client.search_calls == [("...", cases_root, 2)]
```

Assert both responses use `match_type="semantic"`. For the missing exact file, assert `fallback_reason="task_signature_not_found"`; when no signature is supplied, omit `fallback_reason`.

- [ ] **Step 6: Run fallback tests and verify RED**

Run:

```bash
pytest tests/session/train/test_rollout_executor_component.py -q --no-cov \
  -k 'search_experience and (signature_not_found or without_signature)'
```

Expected: FAIL because semantic responses do not yet expose `match_type` or fallback metadata.

- [ ] **Step 7: Implement semantic fallback metadata**

Extend `_format_search_experience_response` with keyword-only `match_type`, `task_signature=None`, and `fallback_reason=None`. Include optional fields only when non-empty. Keep `situation` separate from `task_signature`; pass only `situation` to `client.search`.

- [ ] **Step 8: Run all search tool tests**

Run:

```bash
pytest tests/session/train/test_rollout_executor_component.py -q --no-cov -k search_experience
```

Expected: PASS.

### Task 2: Supply `task_signature` through Tau2 Skill context

**Files:**
- Modify: `benchmark/tau2/train/rollout_executor_vikingbot.py:622-686, 962-981, 1134-1180, 1254-1296`
- Modify: `benchmark/tau2/train/experience_loader_template/SKILL.md`
- Test: `tests/session/train/test_rollout_executor_component.py:450-515, 850-890`

**Interfaces:**
- Consumes: `_tau2_case_lookup(case) -> dict[str, Any]`.
- Produces: `_configure_tools(..., case_lookup=None)` and `_prepare_experience_loader_skill(..., task_signature=None)`.
- The Skill tells the Agent to pass the exact runtime-provided signature when present and omit it otherwise.

- [ ] **Step 1: Write failing wiring and Skill-context tests**

Update the tool-registration test to pass a lookup and assert the registered `search_experience` schema contains optional `task_signature`. Update the Skill preparation test to pass `task_signature="tau2:airline:train:39"` and assert the generated Skill contains a runtime section with that exact value and the instruction not to infer it.

Add a second Skill preparation assertion with `task_signature=None` proving no runtime Case section is added.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
pytest tests/session/train/test_rollout_executor_component.py -q --no-cov \
  -k 'configure_tools or experience_loader_skill'
```

Expected: FAIL because lookup and signature are not wired into the tool or generated Skill.

- [ ] **Step 3: Implement wiring without automatic Experience injection**

Compute `case_lookup = _tau2_case_lookup(case)` once in `_execute_one_async`, pass it into both `_configure_tools` and `_run_agent`, and register `_make_search_experience_tool(case_lookup=case_lookup)`.

Remove `del case_lookup`. Pass `case_lookup.get("task_signature")` into `_prepare_experience_loader_skill`. Append this runtime-only section to the copied Skill when non-empty:

```markdown
## Runtime Case context

- `task_signature`: `tau2:airline:train:39`
- Pass this exact value to `search_experience`; do not infer or modify it.
```

Keep `experience_recall_enable=False`; the Agent must still explicitly call `search_experience` and `read_experience`.

- [ ] **Step 4: Update the static Skill tool contract**

Document:

```markdown
- `search_experience(situation, task_signature=None, limit=2)`
- Pass `task_signature` only when Runtime Case context provides it; otherwise omit it.
```

- [ ] **Step 5: Run component tests and verify GREEN**

Run:

```bash
pytest tests/session/train/test_rollout_executor_component.py -q --no-cov
```

Expected: PASS.

### Task 3: Verification and traceable behavior

**Files:**
- Modify if needed: `benchmark/tau2/train/rollout_executor_vikingbot.py`
- Test: `tests/session/train/test_rollout_executor_component.py`

**Interfaces:**
- Produces stable diagnostic response metadata without exposing Case URI or full Case input.

- [ ] **Step 1: Add assertions for data minimization and failure behavior**

Assert exact responses still omit `case_uri`, Case abstract, Case input, and internal lookup fields. Assert a client exception produces the existing `Error searching experience candidates: ...` response instead of a semantic result.

- [ ] **Step 2: Run the focused tests**

Run:

```bash
pytest tests/session/train/test_rollout_executor_component.py -q --no-cov -k search_experience
```

Expected: PASS.

- [ ] **Step 3: Run formatter and lint checks**

Run:

```bash
ruff format --check benchmark/tau2/train/rollout_executor_vikingbot.py \
  tests/session/train/test_rollout_executor_component.py
ruff check benchmark/tau2/train/rollout_executor_vikingbot.py \
  tests/session/train/test_rollout_executor_component.py
```

Expected: both commands exit 0.

- [ ] **Step 4: Run the relevant regression suite**

Run:

```bash
pytest tests/session/train/test_rollout_executor_component.py -q --no-cov
```

Expected: PASS with no warnings introduced by this change.

- [ ] **Step 5: Review final diff and commit implementation**

```bash
git diff --check
git status --short
git add benchmark/tau2/train/rollout_executor_vikingbot.py \
  benchmark/tau2/train/experience_loader_template/SKILL.md \
  tests/session/train/test_rollout_executor_component.py
git commit -m "benchmark/tau2: prefer exact case experience recall"
```

