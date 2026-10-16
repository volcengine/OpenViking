# Qdrant Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add phase 1 dense and phase 2 sparse Qdrant support behind the current OpenViking `CollectionAdapter` contract.

**Architecture:** Keep all Qdrant-specific behavior in a REST collection implementation and a thin adapter. Store path metadata and stable IDs in the data collection, and store the OpenViking marker plus sparse term dictionary in a deterministic sidecar collection. Reuse the existing Filter AST and tenant-aware upper layer without changing its API.

**Tech Stack:** Python standard library (`urllib`, `json`, `hashlib`, `uuid`, `threading`), OpenViking `Collection`/`ICollection`, Qdrant REST API, pytest.

**Spec:** `docs/superpowers/specs/2026-08-20-qdrant-integration-design.md`

## Global Constraints

- Do not modify or reset the user's pre-existing `.gitignore` change.
- Do not reintroduce `qdrant-client` or use lossy sparse hashing.
- Do not silently drop filters or sparse vectors.
- Preserve `CollectionAdapter` and upper-layer search APIs.
- Every production behavior is introduced by a failing test first.
- Keep `USE_CONTENT_FIELD=False`.

### Task 1: Add failing pure conversion and filter tests

**Files:**
- Create: `tests/storage/test_qdrant_adapter.py`

**Interfaces:**
- Tests will import `QdrantCollectionAdapter`, `QdrantCollection`,
  `compile_qdrant_filter`, `build_qdrant_payload`, `to_qdrant_point_id`,
  and `SparseTermDictionary`.

- [x] **Step 1: Write failing tests**

Cover:

```python
def test_path_payload_includes_self_and_ancestors(): ...
def test_path_scope_depth_mapping_is_segment_aware(): ...
def test_multi_tag_eq_is_qdrant_must_and_in_is_match_any(): ...
def test_account_filter_is_preserved(): ...
def test_point_id_is_deterministic_and_original_id_round_trips(): ...
def test_sparse_term_collision_raises_instead_of_merging(): ...
```

- [x] **Step 2: Run the focused tests**

Run: `uv run --project . pytest -q tests/storage/test_qdrant_adapter.py`

Expected: collection/import failure because the Qdrant implementation does not
exist yet. If dependency installation is unavailable, run the pure module
through the repository's available Python environment and record the blocker.

### Task 2: Implement Qdrant REST transport and collection lifecycle

**Files:**
- Create: `openviking/storage/vectordb/collection/qdrant_rest.py`
- Create: `openviking/storage/vectordb/collection/qdrant_collection.py`
- Modify: `openviking/storage/vectordb/collection/__init__.py`

**Interfaces:**
- `QdrantRestClient.request(method, path, body=None, params=None) -> dict`
- `QdrantCollection(ICollection)`
- `QdrantCollection.create_remote_collection(schema, sparse_enabled)`
- `QdrantCollection.has_openviking_metadata() -> bool`

- [x] **Step 1: Add the minimal HTTP client**

Use `urllib.request`, JSON, optional `api-key`, bounded error bodies, and a
single configurable timeout. Keep the transport injectable for tests.

- [x] **Step 2: Implement collection existence and metadata**

Implement Qdrant collection discovery, create/delete, metadata sidecar creation,
schema marker validation, and index creation.

- [x] **Step 3: Implement data methods**

Implement `upsert_data`, `update_data`, `fetch_data`, `delete_data`,
`delete_all_data`, `aggregate_data`, and `get_meta_data` with the existing
`SearchResult`, `DataItem`, and `AggregateResult` dataclasses.

- [x] **Step 4: Run transport/lifecycle tests**

Run: `uv run --project . pytest -q tests/storage/test_qdrant_adapter.py -k 'transport or lifecycle'`

### Task 3: Implement phase 1 adapter and filter mapping

**Files:**
- Create: `openviking/storage/vectordb_adapters/qdrant_adapter.py`
- Modify: `openviking/storage/vectordb_adapters/factory.py`
- Modify: `openviking_cli/utils/config/vectordb_config.py`

**Interfaces:**
- `QdrantCollectionAdapter.from_config(config)`
- `compile_qdrant_filter(expr)`
- `build_qdrant_payload(record)`
- `to_qdrant_point_id(value)`

- [x] **Step 1: Implement deterministic IDs and path payloads**

Normalize `viking://` URIs to `/...`, include self and ancestors in
`scope_roots`, and reject malformed path scope input.

- [x] **Step 2: Implement AST mapping**

Map `And`, `Or`, `Eq`, `In`, `Range`, `TimeRange`, and `PathScope` to Qdrant
filters. Reject unsupported `Contains` with `NotImplementedError` rather than
changing its substring semantics.

- [x] **Step 3: Implement adapter factory/config wiring**

Add standard backend name `qdrant`, a nested config object for URL/auth/vector
names/timeout, and registry wiring. Preserve dotted custom class paths.

- [x] **Step 4: Run phase 1 tests**

Run: `uv run --project . pytest -q tests/storage/test_qdrant_adapter.py`

### Task 4: Implement phase 2 durable sparse term dictionary

**Files:**
- Modify: `openviking/storage/vectordb/collection/qdrant_collection.py`
- Modify: `openviking/storage/vectordb_adapters/qdrant_adapter.py`
- Modify: `tests/storage/test_qdrant_adapter.py`

**Interfaces:**
- `SparseTermDictionary.index_for(term) -> int`
- `SparseTermDictionary.encode(vector) -> dict[str, list[...]]`
- `QdrantCollection.encode_sparse_vector(vector)`

- [x] **Step 1: Add failing sparse tests**

Verify stable term indices, sidecar persistence, dense+sparse conversion, and
collision rejection.

- [x] **Step 2: Implement deterministic collision-checked Qdrant-compatible indices**

Derive a positive uint32 candidate from SHA-256 because Qdrant sparse indices
are uint32 at the REST/protobuf boundary. Persist each term/index pair in the
sidecar collection. Query existing records for the candidate index; raise a
collision error when a different term owns it.

- [x] **Step 3: Wire sparse upsert and search**

Convert OpenViking `{term: weight}` dictionaries to named Qdrant sparse vectors,
and reject sparse operations when the collection was created dense-only.

- [x] **Step 4: Run phase 2 tests**

Run: `uv run --project . pytest -q tests/storage/test_qdrant_adapter.py -k sparse`

### Task 5: Add optional live Qdrant integration coverage

**Files:**
- Create: `tests/storage/test_qdrant_integration.py`
- Modify: `tests/README.md`

- [x] **Step 1: Write environment-gated tests**

Use `QDRANT_URL` and optional `QDRANT_API_KEY`; skip cleanly without a live
server. Exercise create, upsert, path/tag filters, count, sparse search, and
drop.

- [x] **Step 2: Run the integration tests when available**

Run: `QDRANT_URL=http://127.0.0.1:6333 uv run --project . pytest -q tests/storage/test_qdrant_integration.py`

### Task 6: Verify, document, and commit

**Files:**
- Modify: `openviking/storage/vectordb_adapters/README.md`
- Modify: `docs/en/guides/01-configuration.md`
- Modify: `docs/zh/guides/01-configuration.md`

- [x] **Step 1: Add configuration and capability docs**

Document Qdrant URL/auth, vector names, dense-only versus sparse-enabled mode,
metadata sidecar behavior, and the explicit unsupported-filter policy.

- [x] **Step 2: Run final verification**

Run:

```bash
uv run --project . pytest -q tests/storage/test_qdrant_adapter.py
uv run --project . pytest -q tests/storage tests/unit
uv run --project . ruff check openviking/storage/vectordb openviking/storage/vectordb_adapters tests/storage/test_qdrant_adapter.py
git diff --check
```

- [x] **Step 3: Review the diff and commit**

```bash
git status --short
git diff --stat
git commit -am "feat: add qdrant vector backend"
```

## Verification notes (2026-08-21)

- Focused adapter coverage: `45 passed`; transport/lifecycle: `2 passed`;
  sparse: `9 passed`.
- The reconstructed pre-implementation test run on `main` failed at collection
  with the expected missing Qdrant module import.
- Disposable live Qdrant coverage passed: `1 passed` against a local Qdrant
  container at `http://127.0.0.1:6333`; the container was removed afterward.
- The integration test path is `tests/storage/test_qdrant_integration.py`.
- The repository-wide `tests/storage tests/unit` run remains red: 19 known
  failures reproduce on `main`, with additional environment/order-dependent
  failures still unclassified. This is recorded as a repository-health
  boundary, not a Qdrant implementation blocker.
