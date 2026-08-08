# OpenViking — Embedding System & Graph DB (Population + Retrieval)

> A walkthrough of how OpenViking (OV) turns raw files into embeddings, how it
> materializes a hierarchical knowledge **graph**, and how that graph is queried.
> File paths are given so you can jump straight to the source.

---

## Part 1 — The Embedding System

### 1.1 The big idea

OV does **not** embed only flat documents. It builds a 3-level hierarchy per
directory and embeds *each level separately*, so that semantic search can match
at the right granularity (a whole topic vs. a single file).

The three levels (`openviking/core/context.py` → `ContextLevel`):

| Level | Enum value | What it represents | Canonical URI |
|-------|-----------|--------------------|---------------|
| **L0** | `ABSTRACT = 0` | A short *abstract* of a directory/topic | `{dir}/.abstract.md` |
| **L1** | `OVERVIEW = 1` | A longer *overview* of a directory | `{dir}/.overview.md` |
| **L2** | `DETAIL = 2`  | The actual leaf content (a file) | `{file_path}` |

Every embedded record carries a `level` and a `parent_uri`. Those two fields are
what turn a pile of vectors into a navigable tree (see Part 2).

### 1.2 The embedder abstraction

**The problem this solves.** OV supports a dozen embedding providers (OpenAI,
Voyage, Cohere, Gemini, …) plus a local model. Each has its own SDK, request
shape, and quirks. The rest of OV must *not* care which one is active — the
indexing pipeline and the retriever just want to say "turn this text into a
vector." So `base.py` defines one common interface, `EmbedderBase`, that every
provider implements. Swap providers in config and nothing downstream changes.
That interface is "the embedder abstraction."

```
   indexing pipeline / retriever
              │  embed(text)            ← they only ever call this
              ▼
        EmbedderBase  (the contract)
        ╱     │      ╲
   OpenAI   Voyage   local …            ← concrete providers, hidden behind it
```

#### What you get back: `EmbedResult`

Every `embed()` call returns one `EmbedResult` dataclass
(`base.py:95`). It holds up to two representations of the same text:

| Field | Type | What it is |
|-------|------|-----------|
| `dense_vector` | `List[float]` | The familiar "semantic" embedding — a fixed-length list of floats where closeness ≈ similar meaning. |
| `sparse_vector` | `Dict[str, float]` | A `term → weight` map (e.g. `{"retrieval": 0.8, "system": 0.4}`). Lexical / BM25-style — good at exact keyword matches dense vectors miss. |

Either may be `None`. Convenience properties (`is_dense`, `is_sparse`,
`is_hybrid`) just check which fields are populated. Holding both in one object is
what lets the vector store do **hybrid search** later (Part 1.6 / 2.3).

#### The one method everyone implements: `embed(text, is_query)`

This is the whole contract (`base.py:146`). The `is_query` flag is the one part
that trips people up:

- `is_query=False` → you're embedding a **document** being stored/indexed.
- `is_query=True` → you're embedding a **search query** at retrieval time.

The convenience wrappers `embed_query()` / `embed_document()` (`base.py:171`)
just call `embed()` with the flag pre-set so call sites read clearly.

> **Why `is_query` if both land in the same space?**
>
> Both *do* go into one shared vector space — that's exactly what makes
> "find the document nearest the query" a valid distance comparison. The flag
> isn't about *which space*; it's about *how the text enters it*.
>
> A query and the document that answers it rarely look alike — `"reset password"`
> vs. *"To recover access to your account, navigate to Settings → Security…"*.
> They share almost no words, so a naive model would place them far apart even
> though one perfectly answers the other. To fix this, most modern retrieval
> models are **asymmetric**: trained on (query, answer) pairs to pull the two
> together *despite* the wording gap, usually by prepending a hidden instruction:
>
> ```
> is_query=True   →  "query: reset password"
> is_query=False  →  "passage: To recover access to your account..."
> ```
>
> Think of it as **two on-ramps onto the same highway**: the highway (space) is
> shared so distances are comparable, but queries and passages enter from
> opposite sides and are deliberately routed to the *same neighborhood*. Pass the
> wrong flag and you take the wrong on-ramp — nothing crashes, but you land in the
> wrong neighborhood and retrieval quality silently degrades.
>
> Symmetric models (e.g. "find similar sentences") ignore the flag entirely. OV
> passes it unconditionally because it's *correct* for asymmetric models and
> *harmless* for symmetric ones. In OV: indexing embeds with `is_query=False`
> (Part 1.4, step 2); retrieval embeds the query with `is_query=True`
> (Part 2.3, step 1).

#### The three "flavors" (why there are sub-base-classes)

A provider might give you a dense model, a sparse model, or both. So `base.py`
splits `EmbedderBase` into three abstract subclasses that just declare *what a
correct `embed()` must return*:

- `DenseEmbedderBase` (`base.py:385`) — `embed()` returns dense only; also must
  implement `get_dimension()` (the vector length).
- `SparseEmbedderBase` (`base.py:416`) — `embed()` returns sparse only.
- `HybridEmbedderBase` (`base.py:445`) — `embed()` returns **both**.

`CompositeHybridEmbedder` (`base.py:488`) is the clever bit: instead of needing a
single model that does both, it **wraps one dense embedder + one sparse embedder
and presents them as a single hybrid one**. Its `embed_async()` fires both at
once with `asyncio.gather` (`base.py:523`) and stitches the two results into one
`EmbedResult`. This is how you mix, say, OpenAI dense + Volcengine sparse.

#### Sync vs async (and why async has guard rails)

Every method comes in a sync form and an `_async` form. OV's pipeline is async,
so it prefers `embed_async`. The default async impl just calls the sync `embed`
(`base.py:187`), so a provider only *has* to implement one — but real providers
override the async path to be genuinely non-blocking.

Two production concerns are baked into the async retry wrapper
`_run_with_async_retry` (`base.py:237`):

- **Concurrency cap.** A per-event-loop `asyncio.Semaphore` keyed by
  `max_concurrent` (default 10, `base.py:139`) limits how many embed calls hit
  the provider at once — so a big indexing job can't trigger rate-limit
  errors by firing thousands of requests simultaneously.
- **Retry/backoff.** Failed calls retry up to `max_retries` (default 3) via
  `retry_async`, with timing/wait telemetry recorded each attempt.

#### Picking which embedder is live

The active embedder is built from config —
`config.embedding.{hybrid|dense|sparse}` resolved through `get_embedder()`.
Providers live in `openviking/models/embedder/*_embedders.py`: OpenAI, Voyage,
Cohere, Gemini, Jina, MiniMax, DashScope, Volcengine, VikingDB,
LiteLLM (catch-all), and **local** (llama.cpp).

#### Shared helpers in `base.py`

- `truncate_and_normalize()` (`base.py:73`) — cut a dense vector to the target
  dimension and L2-normalize it, so the store's inner-product distance behaves
  like cosine similarity.
- `update_token_usage()` (`base.py:307`) — emits per-call token + latency metrics
  to `EmbeddingEventDataSource`. Wrapped in a `try/except` that swallows
  everything: **metrics are never allowed to break the embed path.**

### 1.3 What text actually gets embedded

Decided in `openviking/utils/embedding_utils.py`:

- **Directories** (`vectorize_directory_meta`): the `.abstract.md` content is
  embedded as L0, the `.overview.md` content as L1.
- **Files** (`vectorize_file`): strategy depends on `embedding.text_source`
  config and file type (`get_resource_content_type`):
  - Text files → embed **raw content** (default `content_only`), or the LLM
    summary if `summary_first` / `summary_only`.
  - Non-text (image/video/audio/unknown) → embed the **summary** instead.
  - Raw input is guarded by `_truncate_embedding_input()` using a CJK-aware token
    estimate against `embedding.max_input_tokens` (default 4096), appending a
    `...(truncated for embedding)` marker.

Each unit becomes a `Context` object (`openviking/core/context.py`) carrying
`uri`, `parent_uri`, `is_leaf`, `level`, `abstract`, timestamps, and ownership
(`account_id`, `owner_space`, user/agent). `context.set_vectorize(Vectorize(text=...))`
attaches the exact string to embed.

### 1.4 The async pipeline (queue-based, decoupled)

OV never embeds inline on the request path. It enqueues work:

```
write/index a resource
        │
        ▼
Context  ──EmbeddingMsgConverter.from_context──▶  EmbeddingMsg
        │
        ▼
EmbeddingQueue.enqueue()      (openviking/storage/queuefs/embedding_queue.py)
        │
        ▼  (background worker dequeues)
TextEmbeddingHandler.on_dequeue()   (openviking/storage/collection_schemas.py)
        │  1. circuit-breaker check (CircuitBreaker)
        │  2. embedder.embed(text, is_query=False)  → dense + sparse vectors
        │  3. dimension validation (must == config.embedding.dimension)
        │  4. compute deterministic id = md5(f"{account_id}:{seed_uri}")
        │  5. vikingdb.upsert(record)
        ▼
Vector DB  (the "context collection")
```

Robustness details in `TextEmbeddingHandler`:
- **Circuit breaker** opens after repeated failures; messages are re-enqueued
  (with `retry_after` sleep) instead of being lost.
- Errors are classified `permanent` vs `transient` (`classify_api_error`);
  transient → re-enqueue, permanent → drop + record failure.
- **Deterministic IDs**: `id = md5("{account_id}:{seed_uri}")` where `seed_uri`
  appends `/.abstract.md` (L0) or `/.overview.md` (L1) so each semantic layer of
  a URI maps to a stable row → upserts are idempotent.
- An `EmbeddingTaskTracker` counts outstanding sub-tasks per `semantic_msg_id`
  so callers can await "indexing complete".

### 1.5 Where the abstracts/overviews come from

L0/L1 text isn't authored by hand — it's generated **bottom-up** by the
**Semantic** pipeline (`openviking/storage/queuefs/semantic_processor.py` +
`semantic_dag.py`):

1. Summarize each leaf file (LLM; image/video/audio summarizers for media).
2. Roll child summaries up into the directory's `.abstract.md` (L0) and
   `.overview.md` (L1).
3. Hand those off to the embedding queue (Part 1.4).

So there are **two cooperating queues**: a *semantic* queue (produces summaries
bottom-up, a DAG over the directory tree) and an *embedding* queue (vectorizes
the produced text). This is why OV ships `semantic_queue` + `embedding_queue`.

### 1.6 The vector store schema

Defined in `CollectionSchemas.context_collection`
(`openviking/storage/collection_schemas.py`). One unified collection holds all
levels. Notable fields:

- `id` (string PK), `uri` (path), `context_type` (`resource|memory|skill`),
- `vector` (dense, `Dim = config.embedding.dimension`) + `sparse_vector`,
- `level` (0/1/2), `parent_uri` (implicitly via URI + PathScope), `abstract`,
- `account_id`, `owner_user_id`, `owner_agent_id` (multi-tenant isolation),
- `created_at`, `updated_at`, `active_count` (for hotness/recency boosting).

Scalar indexes are built on `uri`, `level`, `context_type`, `account_id`,
ownership and timestamps so filtered vector search stays fast.

The engine itself (`openviking/storage/vectordb/`, C++ core with abi3 Python
bindings) supports dense+sparse **hybrid** search, scalar filtering, TTL, and
persistent multi-version snapshots — documented in
`openviking/storage/vectordb/README.md`.

---

## Part 2 — The "Graph DB": Population & Retrieval

### 2.1 What the graph actually is

OV has **no separate graph database** (no Neo4j/Nebula). The "graph" is
**materialized inside the vector store + filesystem** as two overlapping
structures:

1. **The hierarchy tree (primary edges).** Every vector row has a `uri` and a
   `level`. Parent→child edges are implied by URI prefixes: a row at
   `viking://.../topic/file.md` (L2) is a child of the directory
   `viking://.../topic` whose L0/L1 rows are `.../topic/.abstract.md` and
   `.../topic/.overview.md`. This is a **tree/DAG of contexts**.

2. **Relations (cross-cutting edges).** Arbitrary, typed cross-links between
   resources, stored per-directory in a sidecar `.relations.json` file
   (`openviking/storage/viking_fs.py`). Each entry is `{uri, reason}`. These are
   the "non-tree" edges that turn the tree into a genuine graph.

The in-memory helper `BuildingTree` (`openviking/core/building_tree.py`) is the
explicit tree abstraction used while building: it maintains `_uri_map`,
parent/children lookups, `get_path_to_root`, and `to_directory_structure`.

### 2.2 How the graph is populated

**Tree edges** are populated automatically as part of indexing:

- When a resource directory is indexed (`index_resource` in
  `embedding_utils.py`), OV reads `.abstract.md`/`.overview.md` and the files,
  builds `Context` objects with `parent_uri` set, and enqueues them.
- The embedding worker writes each as a row with its `level` and `uri`. The
  `parent_uri` relationship is preserved by the URI path itself + the `level`
  field, and queried later via `PathScope` (prefix + depth).
- The bottom-up Semantic DAG (`semantic_dag.py`, `semantic_processor.py`)
  guarantees every directory gets L0/L1 rows so the tree has interior nodes, not
  just leaves.

**Relation (cross) edges** are populated explicitly via the API:

- HTTP router: `openviking/server/routers/relations.py`
  - `POST /api/v1/relations/link` `{from_uri, to_uris, reason}`
  - `DELETE /api/v1/relations/link` `{from_uri, to_uri}`
  - `GET  /api/v1/relations?uri=...`
- Service layer: `openviking/service/relation_service.py` validates URIs and
  delegates to `VikingFS.link / unlink / relations`.
- Storage: `VikingFS` reads/writes `{dir}/.relations.json`
  (`_read_relation_table` / `_write_relation_table`). `link` appends
  `{uri, reason}` to the source's table; `unlink` removes it; `get_relations`
  returns the linked URIs.

So: **tree edges = emergent from indexing; relation edges = written on demand.**

### 2.3 How the graph is retrieved

Retrieval is **hierarchical graph traversal guided by vector similarity**,
implemented in `openviking/retrieve/hierarchical_retriever.py`
(`HierarchicalRetriever.retrieve`). It deliberately avoids a brute-force flat
search; instead it walks the tree from the most promising entry points downward.

The traversal primitives live in
`openviking/storage/viking_vector_index_backend.py`:

- `search_global_roots_in_tenant()` — vector search across all levels
  (`In("level", [0,1,2])`) within the tenant scope. Finds promising *entry
  points* anywhere in the graph.
- `search_children_in_tenant(parent_uri, ...)` — vector search restricted to the
  **direct children** of a node via `PathScope("uri", parent_uri, depth=1)`.
  This is the "expand this node's edges" operation.
- All searches are wrapped by `_build_scope_filter` / `_tenant_filter`, which AND
  in `account_id` + visible-root `PathScope`s for **multi-tenant isolation**
  (root role bypasses).

The algorithm (best-first search over the tree):

```
1. Embed the query once (dense + sparse), is_query=True.

2. Pick starting points:
     - root URIs for the requested context_type
       (_get_root_uris_for_type → memories / resources / skills), plus
     - global vector hits (search_global_roots_in_tenant).
   Level-2 global hits are seeded directly as terminal candidates;
   non-L2 hits become directory entry points.

3. Best-first expansion (a max-heap keyed by score) — _recursive_search:
     pop highest-scoring node
       → search_children_in_tenant(node)           # expand edges (depth=1)
       → (optional) rerank children with RerankClient (THINKING mode)
       → score-propagate to children:
             final = α·child_score + (1−α)·parent_score   (score_propagation_alpha)
       → keep children passing the threshold
       → push directory children (level 0/1) back on the heap;
         level-2 files are terminal hits (not expanded)
     repeat until the top-k set stabilizes
     (MAX_CONVERGENCE_ROUNDS = 3) or the queue drains.

4. Convert to results — _convert_to_matched_contexts:
     - optional hotness/recency boost:
         final = (1−hotness_alpha)·semantic + hotness_alpha·hotness_score(active_count, updated_at)
     - attach related contexts: get_viking_fs().get_relations(uri)
       → read their L0 abstracts (capped at MAX_RELATIONS = 5)
     - rebuild user-facing URI with the right level suffix
       (.abstract.md / .overview.md).
   Re-sort by blended score, return top-k.
```

Key tuning knobs (from `RetrievalConfig` / `RerankConfig`):

- `score_propagation_alpha` — how much a child trusts its own score vs. its
  parent's (controls how "directory relevance" flows down).
- `hotness_alpha` — weight of recency/usage vs. pure semantic similarity.
- `threshold` / `score_gte` — prune weak branches.
- `mode = THINKING|QUICK` — THINKING enables the cross-encoder rerank pass at
  every expansion; QUICK uses raw vector scores only.
- `GLOBAL_SEARCH_TOPK = 10`, `DIRECTORY_DOMINANCE_RATIO = 1.2`,
  `MAX_CONVERGENCE_ROUNDS = 3`.

### 2.4 Why this design

- **Hybrid vectors** (dense + sparse) give both semantic and lexical recall in
  one store.
- **Hierarchical traversal** means a query first lands on the right *topic*
  (L0/L1) and only then drills into *files* (L2) — far cheaper and more precise
  than flat top-k over every leaf, and it naturally returns context at the right
  altitude.
- **Score propagation** lets a strongly-matching directory lift its children,
  and a weak directory prune entire subtrees early.
- **Relations** overlay associative recall ("things linked to this") on top of
  the hierarchy, and are surfaced alongside every hit.
- Everything is **multi-tenant filtered** at the storage layer, so the same
  collection safely serves many accounts/agents.

---

## Quick file map

| Concern | File |
|--------|------|
| Embedder interfaces | `openviking/models/embedder/base.py` |
| Provider impls | `openviking/models/embedder/*_embedders.py` |
| What text to embed / indexing entry | `openviking/utils/embedding_utils.py` |
| Levels & Context model | `openviking/core/context.py` |
| Embedding queue worker | `openviking/storage/collection_schemas.py` (`TextEmbeddingHandler`) |
| Queues | `openviking/storage/queuefs/{embedding,semantic}_queue.py` |
| Bottom-up summary DAG | `openviking/storage/queuefs/semantic_{processor,dag}.py` |
| Vector store schema | `openviking/storage/collection_schemas.py` (`CollectionSchemas`) |
| Vector engine docs | `openviking/storage/vectordb/README.md` |
| Tree abstraction | `openviking/core/building_tree.py` |
| Graph traversal queries | `openviking/storage/viking_vector_index_backend.py` |
| Hierarchical retrieval | `openviking/retrieve/hierarchical_retriever.py` |
| Relations (cross edges) | `openviking/service/relation_service.py`, `openviking/server/routers/relations.py`, `openviking/storage/viking_fs.py` |
