# Retrieval

OpenViking provides multiple retrieval methods, including simple vector similarity search, intelligent retrieval with session context, regex pattern matching, and file pattern matching.

## find vs search

| Aspect | find | search |
|--------|------|--------|
| Intent Analysis | No | Yes |
| Session Context | No | Yes |
| Query Expansion | No | Yes |
| Default Limit | 10 | 10 |
| Use Case | Simple queries | Conversational search |

## Retrieval Pipeline

The core retrieval pipeline is as follows:

```
Query → Intent Analysis (search only) → Vector Search (L0) → Rerank (L1) → Results
```

1. **Intent Analysis** (search only): Understand query intent, expand queries
2. **Vector Search**: Find candidates using embeddings
3. **Rerank**: Re-score using content for better accuracy
4. **Results**: Return top-k contexts

## API Reference

### find()

Basic vector similarity search without session context.

#### 1. API Implementation Introduction

The `find()` method performs pure vector similarity search for simple query scenarios. It uses hierarchical retrieval to search at the L0 summary level first, then matches in detail at L1/L2 levels.

**Processing Pipeline**:
1. Convert query text to vector
2. Perform global vector search within specified target URI
3. Use hierarchical retrieval strategy to recursively search relevant directories and files
4. Optional: Use rerank model to optimize result ordering
5. Return matched context list

**Code Entry Points**:
- `openviking_cli/client/sync_http.py:SyncHTTPClient.find()` - Python SDK entry (HTTP)
- `openviking/retrieve/hierarchical_retriever.py:HierarchicalRetriever.retrieve()` - Core retrieval implementation
- `openviking/server/routers/search.py:find()` - HTTP router
- `crates/ov_cli/src/commands/search.rs:find()` - Rust CLI command

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| query | str | No | "" | Search query string. Required unless `image_url` is provided |
| image_url | str | No | None | Image query as a `data:image/...;base64,...`, `http(s)://`, or `viking://` URI. Requires a multimodal embedding model |
| target_uri | str \| List[str] | No | "" | Limit search to specific URI prefix |
| context_type | str \| List[str] | No | None | Limit results to one or more `ContextType` values: `memory`, `resource`, or `skill` |
| tags | List[str] | No | None | Explicit retrieval tags in strict `k=v` form. Multiple tags are combined with AND; a result must contain every requested tag |
| node_limit | int | No | None | Maximum number of results |
| score_threshold | float | No | None | Minimum relevance score threshold |
| filter | Dict | No | None | Metadata filter |
| since | str | No | None | Lower time bound, accepts `2h` or ISO 8601 / `YYYY-MM-DD`. Timezone-less values are interpreted as UTC. CLI `--after` maps to this field |
| until | str | No | None | Upper time bound, accepts `30m` or ISO 8601 / `YYYY-MM-DD`. Timezone-less values are interpreted as UTC. CLI `--before` maps to this field |
| time_field | "updated_at" \| "created_at" | No | "updated_at" | Metadata time field used by `since` / `until` |
| level | str | No | None | Limit results to specific level(s), e.g., `0`, `1`, `2`, or `0,1,2`. CLI `--level`/`-L` maps to this field |
| include_provenance | bool | No | False | Include provenance/query-plan details in serialized result |
| read_content | bool | No | False | Read each final matched URI with the visible-content read semantics and inline the result as `content`. Individual read failures leave the hit unchanged. |
| telemetry | bool \| object | No | False | Attach telemetry data to response |

**Target resolution notes**:
- With empty `target_uri`, non-ROOT retrieval searches the current user root (`viking://user/{user}`) and shared `viking://resources`.
- To filter the current user's peer collection to one peer for filesystem and retrieval operations, send `X-OpenViking-Actor-Peer: <peer_id>` or construct the SDK/CLI client with `actor_peer_id`. See [Multi-Tenant: Peer Collection Filter](../concepts/11-multi-tenant.md#peer-restricted-view).
- Home-alias target URIs such as `viking://~/memories`, `viking://~/resources`, and `viking://~/skills` are expanded to the canonical path from the authenticated request identity. The uid-less spelling `viking://user/memories` (and the same shape for `resources`, `skills`, `peers`, `privacy`, `sessions`) is rejected with an error pointing at the `viking://~/...` form.

**Image search notes**:
- Image queries use the image vector as the query and search L2 resource leaf nodes in the target scope by default. Results are not limited to image files; multimodal embedding decides similarity between the query image and text/image resources.
- Text-only embedding models still index image summaries, but image query input is rejected.
- Existing image resources keep their existing vectors; image-vector recall applies to images vectorized after this capability is enabled or after a later reindex.

**FindResult Structure**

When `read_content=true`, each successfully read hit additionally contains `content`. This is a full file read, so use `limit` to bound response size. `search(mode="context")` rejects `read_content` because context assembly already enforces its own token budget.

```python
class FindResult:
    memories: List[MatchedContext]   # Memory contexts
    resources: List[MatchedContext]  # Resource contexts
    skills: List[MatchedContext]     # Skill contexts
    query_plan: Optional[QueryPlan]  # Query plan (search only)
    query_results: Optional[List[QueryResult]]  # Detailed results
    total: int                       # Total count (auto-calculated)
```

**MatchedContext Structure**

```python
class MatchedContext:
    uri: str                         # Viking URI
    context_type: ContextType        # "resource", "memory", or "skill"
    level: int                       # Tier (0=L0, 1=L1, 2=L2)
    abstract: str                    # L0 content
    overview: Optional[str]          # L1 overview (optional for non-leaf nodes)
    category: str                    # Category
    score: float                     # Relevance score (0-1)
    match_reason: str                # Why this matched
```

#### 3. Usage Examples

**HTTP API**

```
POST /api/v1/search/find
```

```bash
curl -X POST http://localhost:1933/api/v1/search/find \
    -H "Content-Type: application/json" \
    -H "X-API-Key: your-key" \
    -d '{
        "query": "how to authenticate users",
        "limit": 10
    }'
```

**Search with Target URI and Time Filter**

```bash
curl -X POST http://localhost:1933/api/v1/search/find \
    -H "Content-Type: application/json" \
    -H "X-API-Key: your-key" \
    -d '{
        "query": "authentication",
        "target_uri": "viking://resources",
        "since": "7d",
        "time_field": "created_at"
    }'
```

**Search by Context Type**

```bash
curl -X POST http://localhost:1933/api/v1/search/find \
    -H "Content-Type: application/json" \
    -H "X-API-Key: your-key" \
    -d '{
        "query": "authentication",
        "context_type": ["memory", "resource"]
}'
```

**Image Search**

```bash
curl -X POST http://localhost:1933/api/v1/search/find \
    -H "Content-Type: application/json" \
    -H "X-API-Key: your-key" \
    -d '{
        "image_url": "viking://resources/images/cat.png",
        "limit": 10
    }'
```

**Search by Explicit Retrieval Tags**

```bash
curl -X POST http://localhost:1933/api/v1/search/find \
    -H "Content-Type: application/json" \
    -H "X-API-Key: your-key" \
    -d '{
        "query": "rollback runbook",
        "tags": ["env=prod", "team=search"]
    }'
```

Tags must use strict `k=v` strings. When multiple tags are provided, `find()` requires all of them; the example above only returns contexts whose explicit retrieval tags contain both `env=prod` and `team=search`.

**Python SDK**

```python
import openviking as ov
from openviking.retrieve import ContextType
from openviking_sdk import TextPart

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# Basic search
results = client.find(query="how to authenticate users")

# Search with filter and time range
recent_emails = client.find(
    query="invoice",
    target_uri="viking://resources/email",
    options={
        "since": "7d",
        "time_field": "created_at",
    },
)

# Search only memories and resources
typed_results = client.find(
    query="authentication",
    options={"context_type": [ContextType.MEMORY, ContextType.RESOURCE]},
)

# Search by local image, bytes, data URI, HTTP URL, or viking:// URI
image_results = client.find(query="", image="/path/to/photo.png")

# Search by explicit retrieval tags. Multiple tags are AND-ed.
tagged_results = client.find(
    query="rollback runbook",
    options={"tags": ["env=prod", "team=search"]},
)

# Iterate through results
for context in results.get("resources", []):
    print(f"URI: {context['uri']}")
    print(f"Score: {context.get('score', 0.0):.3f}")
    print(f"Type: {context.get('context_type')}")
    print(f"Abstract: {context.get('abstract', '')[:100]}...")
    print("---")
```

**Search with Target URI Limitation**

```python
# Search only in resources
results = client.find(
    query="authentication",
    target_uri="viking://resources",
)

# Search only in user memories
results = client.find(
    query="preferences",
    target_uri="viking://~/memories"
)

# Search only in current-user resources
results = client.find(
    query="private docs",
    target_uri="viking://~/resources"
)

# Search with the peer collection filtered to one peer
peer_client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="your-key",
    actor_peer_id="web-visitor-alice",
)
peer_results = peer_client.find(query="invoice follow-up")

# Search only in skills
results = client.find(
    query="web search",
    target_uri="viking://~/skills"
)

# Search in specific project
results = client.find(
    query="API endpoints",
    target_uri="viking://resources/my-project",
)
```

**TypeScript SDK**

```typescript
console.log(await client.find("authentication", { targetUri: "viking://resources/docs/" }));
```

**Go SDK**

```go
result, err := client.Find(ctx, "how to authenticate users", &openviking.FindOptions{
    TargetURI:   "viking://resources/docs",
    Limit:       10,
    ContextType: []string{"resource"},
})
if err != nil {
    return err
}
for _, item := range result.Resources {
    fmt.Println(item.URI, item.Score)
}
```

**CLI**

```bash
# Basic search
openviking find "how to authenticate users"

# Specify URI scope
openviking find "how to authenticate users" --uri "viking://resources"

# Limit to context types
openviking find "authentication" --context-type memory,resource

# With time filter
openviking find "invoice" --after 7d

# With limit
openviking find "how to authenticate users" --limit 20

# Limit to specific level(s) (L0 only)
openviking find "how to authenticate users" --level 0

# Limit to specific level(s) (L1 and L2) using short option
openviking find "how to authenticate users" -L 1,2

# Image queries use only --image; pass a local path, viking://, http(s)://, or data:image URI
openviking find --image ./query.png --uri "viking://resources/images" --limit 5

# Search by an image already stored in VikingFS
openviking find --image "viking://resources/images/cat.png" --uri "viking://resources/images" --limit 5

# Search by a public image URL
openviking find --image "https://example.com/images/cat.png" --uri "viking://resources/images" --limit 5

# Combine text and image
openviking find "red poster style" --image ./poster.png --uri "viking://resources/images"
```

**Response Example**

```json
{
    "status": "ok",
    "result": {
        "memories": [],
        "resources": [
            {
                "context_type": "resource",
                "uri": "viking://resources/01-overview/API_Overview/Documentation_Reading_P_2c6ae38b.md",
                "level": 2,
                "score": 0.12808319406977778,
                "category": "",
                "match_reason": "",
                "abstract": "This document is an API documentation reading plan that outlines the structure of subsequent API reference materials organized by functional module. Main sections or topics covered include resource management API, search API, file system operations, ses...",
                "overview": null
            },
            {
                "context_type": "resource",
                "uri": "viking://resources/01-overview/API_Overview/API_Endpoints/.abstract.md",
                "level": 0,
                "score": 0.12054087276495282,
                "category": "",
                "match_reason": "",
                "abstract": "This directory contains structured API reference documentation for the OpenViking platform, compiling detailed HTTP endpoint specifications for core and extended platform capabilities. It covers functional modules including system health checks, semanti...",
                "overview": null
            }
        ],
        "skills": [],
        "total": 2
    }
}
```

---

### search()

Intelligent retrieval with session context and intent analysis.

#### 1. API Implementation Introduction

The `search()` method adds session context understanding and intent analysis capability on top of `find()`. It better understands user query intent based on conversation history, performs query expansion, and provides more relevant search results.

**Processing Pipeline**:
1. Load session context (if session_id is provided)
2. Analyze query intent, understand actual needs combined with conversation history
3. Expand queries to improve recall rate
4. Execute same hierarchical retrieval pipeline as `find()`
5. Return search results with query plan

**Code Entry Points**:
- `openviking_cli/client/sync_http.py:SyncHTTPClient.search()` - Python SDK entry (HTTP)
- `openviking/retrieve/hierarchical_retriever.py:HierarchicalRetriever.retrieve()` - Core retrieval implementation
- `openviking/server/routers/search.py:search()` - HTTP router
- `crates/ov_cli/src/commands/search.rs:search()` - Rust CLI command

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| query | str | No | "" | Search query string. Required unless `image_url` is provided |
| image_url | str | No | None | Image query as a `data:image/...;base64,...`, `http(s)://`, or `viking://` URI. Requires a multimodal embedding model |
| target_uri | str \| List[str] | No | "" | Limit search to specific URI prefix |
| session | Session | No | None | Session for context-aware search (SDK) |
| session_id | str | No | None | Session ID for context-aware search (HTTP) |
| context_type | str \| List[str] | No | None | Limit results to one or more `ContextType` values: `memory`, `resource`, or `skill` |
| tags | List[str] | No | None | Explicit retrieval tags in strict `k=v` form. Multiple tags are combined with AND; a result must contain every requested tag |
| node_limit | int | No | None | Maximum number of results |
| score_threshold | float | No | None | Minimum relevance score threshold |
| filter | Dict | No | None | Metadata filter |
| since | str | No | None | Lower time bound, accepts `2h` or ISO 8601 / `YYYY-MM-DD`. Timezone-less values are interpreted as UTC. CLI `--after` maps to this field |
| until | str | No | None | Upper time bound, accepts `30m` or ISO 8601 / `YYYY-MM-DD`. Timezone-less values are interpreted as UTC. CLI `--before` maps to this field |
| time_field | "updated_at" \| "created_at" | No | "updated_at" | Metadata time field used by `since` / `until` |
| level | str | No | None | Limit results to specific level(s), e.g., `0`, `1`, `2`, or `0,1,2`. CLI `--level`/`-L` maps to this field |
| include_provenance | bool | No | False | Include provenance/query-plan details in serialized result |
| read_content | bool | No | False | Read each final matched URI with the visible-content read semantics and inline the result as `content`. Individual read failures leave the hit unchanged. Only supported by `mode="list"`. |
| telemetry | bool \| object | No | False | Attach telemetry data to response |

`search()` uses the same target resolution and explicit tag filtering rules as `find()`, including the peer collection filter selected by `X-OpenViking-Actor-Peer` or SDK `actor_peer_id`. When `image_url` is provided, `search()` uses direct image retrieval and skips session query planning.

#### 3. Usage Examples

**HTTP API**

```
POST /api/v1/search/search
```

```bash
curl -X POST http://localhost:1933/api/v1/search/search \
    -H "Content-Type: application/json" \
    -H "X-API-Key: your-key" \
    -d '{
        "query": "best practices",
        "session_id": "abc123",
        "context_type": "skill",
        "since": "2h",
        "time_field": "updated_at",
        "limit": 10
    }'
```

**Search without Session (Still Performs Intent Analysis)**

```bash
curl -X POST http://localhost:1933/api/v1/search/search \
    -H "Content-Type: application/json" \
    -H "X-API-Key: your-key" \
    -d '{
        "query": "how to implement OAuth 2.0 authorization code flow"
}'
```

**Image Search**

```bash
curl -X POST http://localhost:1933/api/v1/search/search \
    -H "Content-Type: application/json" \
    -H "X-API-Key: your-key" \
    -d '{
        "query": "similar poster",
        "image_url": "data:image/png;base64,...",
        "limit": 10
    }'
```

**Python SDK**

```python
import openviking as ov
from openviking.retrieve import ContextType

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# Create session with conversation context
session_info = client.create_session()
session = client.session(session_id=session_info["session_id"])
session.add_message(
    role="user",
    parts=[TextPart(text="I'm building a login page with OAuth")],
)
session.add_message(
    role="assistant",
    parts=[TextPart(text="I can help you with OAuth implementation.")],
)

# Search understands conversation context
results = client.search(
    query="best practices",
    session_id=session.session_id,
    options={
        "context_type": ContextType.SKILL,
        "since": "2h",
    },
)

for context in results.get("resources", []):
    print(f"Found: {context['uri']}")
    print(f"Abstract: {context.get('abstract', '')[:200]}...")
```

**Search without Session**

```python
# search can also be used without session
# It still performs intent analysis on the query
results = client.search(
    query="how to implement OAuth 2.0 authorization code flow"
)

for context in results.get("resources", []):
    print(f"Found: {context['uri']} (score: {context.get('score', 0.0):.3f})")
```

**Image Search**

```python
results = client.search(
    query="similar poster",
    image="/path/to/poster.png",
)
```

**TypeScript SDK**

```typescript
console.log(await client.search("authentication", { targetUri: "viking://resources/docs/" }));
```

**Go SDK**

```go
result, err := client.Search(ctx, "best practices", &openviking.SearchOptions{
    SessionID:   "abc123",
    ContextType: "skill",
    Limit:       10,
})
if err != nil {
    return err
}
fmt.Println(result.Total)
```

**CLI**

```bash
# Search with session ID
openviking search "best practices" --session-id abc123

# Limit to a context type
openviking search "best practices" --context-type skill

# Search with time filter
openviking search "watch vs scheduled" --after 2026-03-15 --before 2026-03-20

# Search without session (still performs intent analysis)
openviking search "how to implement OAuth 2.0 authorization code flow"

# Limit to specific level(s) (L0 only)
openviking search "best practices" --level 0

# Limit to specific level(s) (L1 and L2) using short option
openviking search "how to implement OAuth" -L 1,2

# Image queries also use --image; they use direct retrieval and skip session planning
openviking search "similar poster" --image ./poster.png --uri "viking://resources/images"
```

**Response Example**

```json
{
    "status": "ok",
    "result": {
        "memories": [],
        "resources": [
            {
                "context_type": "resource",
                "uri": "viking://resources/docs/oauth-best-practices",
                "level": 1,
                "score": 0.95,
                "category": "",
                "match_reason": "Context-aware match: OAuth login best practices",
                "abstract": "OAuth 2.0 best practices for login pages...",
                "overview": "This guide covers OAuth 2.0 best practices including secure token handling, redirect URI validation, and state parameter usage..."
            }
        ],
        "skills": [],
        "query_plan": {
            "reasoning": "User is asking about OAuth implementation best practices, expanding to related security topics",
            "queries": [
                {
                    "query": "OAuth 2.0 best practices",
                    "context_type": "resource",
                    "intent": "Find OAuth 2.0 implementation guidelines",
                    "priority": 3
                },
                {
                    "query": "login page security",
                    "context_type": "resource",
                    "intent": "Find login page security recommendations",
                    "priority": 2
                }
            ]
        },
        "total": 1
    }
}
```

---

### search(mode="context")

Assemble retrieval results into an injection-ready context block. `mode="list"` (the default) returns the ranked hit list and behaves exactly like the previous `search()`; `mode="context"` opens the assembly face: budgeting, tier degradation, cross-turn dedup and the optional LLM digest all happen server-side in one request.

#### 1. Implementation Overview

Injecting context every turn used to mean searching per type, reading each hit back, and stitching the block together client-side. With assembly on the server, a plugin sends one request and every harness shares one budgeting, degradation and dedup implementation.

**Pipeline**:
1. **L1 query understanding**: optional bounded intent expansion from the session's recent messages (at most 3 queries, timeout fuse, falls back to the original query)
2. **L0 retrieval**: bucketed per `quotas`, or a single whole-scope search when quotas are off
3. **L2 assembly**: tier filling inside the token budget (everyone at their category's default tier first, then leftover budget deepens in score order); an oversized tier falls back instead of being truncated
4. **L3 rewrite**: optional digest with URI citations (timeout fuse; on failure the unrewritten `rendered` is still returned; an exact `NO_RELEVANT_MEMORY` result is reported as `stats.rewrite="no_relevant"` so Coding Agent clients inject nothing instead of falling back to `rendered`)

**Code entry points**:
- `openviking/server/routers/search.py:_search_context()` - HTTP route branch
- `openviking/retrieve/context_assembler/pipeline.py:assemble_context()` - assembly orchestration
- `openviking/retrieve/context_assembler/budget.py:plan_entries()` - budgeting and tier filling
- `openviking/retrieve/context_assembler/tiers.py` - overview extraction per source type

#### 2. Parameters

**L0 retrieval domain**: `query`, `image_url`, `context_type`, `limit`, `score_threshold`, `filter`, `tags`, `since`/`until` behave as in list mode. `limit` applies only to quota-free retrieval. Once `purpose` or explicit `quotas` enables bucketed retrieval, the per-category quotas are the only candidate ceilings. `target_uri` is not supported in context mode yet (returns 400); `level` is ignored because `detail` governs tiers.

**L1 query understanding**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `session_id` | str | None | Required to enable query expansion and server-side dedup |
| `query_expansion` | `off` \| `auto` | `auto` | Bounded session-aware expansion; falls back to the original query without a session or on failure |

**L2 assembly**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | 10 | Candidate ceiling for quota-free retrieval only; ignored when `purpose` or `quotas` enables bucketed retrieval |
| `max_tokens` | int | 1600 | The single budget parameter, estimated with a CJK-aware heuristic (codepoint ≥ 0x3000 counts 1.5 tok/char, otherwise chars/4) |
| `quotas` | object | None | Absolute per-bucket limits; keys are `events`/`entities`/`preferences`/`experiences`/`resources`/`skills`. Explicit quotas ignore `limit` |
| `purpose` | `chat` \| `coding` | None | Enables six-domain bucket sampling with the absolute preset quotas below. Applies only when `quotas` is not given |
| `detail` | `abstract` \| `overview` \| `full` \| object | None | Requests one starting/maximum tier for every entry. Entries whose requested tier is unavailable or does not fit step down instead of being truncated. Omitted, each category takes its default tier (below). Also accepts a per-category object such as `{"events":"overview","preferences":"abstract"}`; categories left out keep their default. `"auto"` is a deprecated spelling and behaves as if omitted |
| `dedup_turns` | int | 0 | Cooldown window in turns; needs `session_id`. Ledger lives at `{session_uri}/.recall_log.json` |
| `exclude_uris` | string[] | [] | Stateless dedup fallback, up to 200 entries, unioned with `dedup_turns` |
| `peer_scope` | `actor` \| `all` | `all` | `actor` excludes other peers while keeping global, self-owned and current-actor content |
| `other_peer_penalty` | number \| object | per-category defaults | Score penalty applied to other-peer hits |

**L3 rewrite**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `rewrite` | bool \| `auto` | `false` | Server-side digest rewrite; `auto` engages only when a query_planner model is configured |
| `rewrite_max_bullets` | int | 6 | Digest bullet ceiling (1–20) |

**Tier rules**

- **Purpose presets**: `chat` uses `events:3, entities:3, preferences:1, experiences:1, resources:1, skills:1`; `coding` uses `events:1, entities:2, preferences:1, experiences:1, resources:3, skills:2`. These are absolute per-category ceilings, not weights. Results are deduplicated and globally sorted after gathering, but are not truncated by a second global `limit`
- **Default tier per category**: with `detail` omitted, each category lands on the tier below. Only `events` reads a file; every other category costs no read

  | Category | Default tier | Leftover budget may reach | Why |
  |----------|--------------|---------------------------|-----|
  | `events` | overview | full | The one memory type whose body is long enough for `# Summary` extraction to be a real compression |
  | `entities` / `preferences` / `experiences` | abstract | abstract | Short bodies, and the writer stores the whole body in the abstract scalar, so abstract already is the complete file |
  | `resources` / `skills` | abstract | abstract | The 256-char abstract from semantic processing; bodies can be large or carry credentials, so deepening is opt-in |
  | `memories` | abstract | abstract | Built-in memory types outside the four named ones — `cases`, `patterns`, `tools`, `trajectories`, skill-usage memories. Only quota-free retrieval reaches them; they own no bucket, so `quotas` cannot name them, but `detail` and `other_peer_penalty` can |
  | Directory hits | overview | overview | A directory has no abstract, so it reads the `.overview.md` sidecar; a full tier is meaningless for a subtree |

- **Floor**: every result carries at least its `uri`. When a memory abstract is unavailable or busts the per-entry cap, the entry falls back to overview: the memory writer stores the whole body in that scalar, so for memory categories overview sits *below* abstract on the content ladder and the substitute discloses less. A `resources` or `skills` abstract is the short generated summary instead, so the same substitution would read a body the caller never asked for — those two degrade to a bare `uri` rather than deepen
- **Explicit `detail`**: sets that tier as both the requested start and ceiling; entries that do not fit still step down a tier rather than being truncated. The memory overview substitute above is the one case where the served `detail` can outrank the pin, and only because it carries less content than the pinned tier would
- **Overview by source type**: memory files use the leading `# Summary` section, code files use class and function signatures (reusing `code_outline`), long documents use the heading tree plus first paragraph
- **Per-entry cap**: `max_tokens ÷ candidate_count × 2`, applied to every tier except the bare `uri`; a tier exceeding it falls back to the previous tier rather than being truncated. If budget is still left over, one final deepening pass ignores the cap and is bounded only by `max_tokens`

#### 3. Examples

**HTTP API**

```bash
# Basic context assembly
curl -X POST http://localhost:1933/api/v1/search/search \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENVIKING_API_KEY" \
  -d '{"query":"what changed on this branch","mode":"context","max_tokens":1600}'

# Session-aware: query expansion plus cross-turn dedup
curl -X POST http://localhost:1933/api/v1/search/search \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENVIKING_API_KEY" \
  -d '{
    "query":"continue that refactor",
    "mode":"context",
    "session_id":"cc-1a2b3c",
    "query_expansion":"auto",
    "dedup_turns":5,
    "purpose":"coding",
    "max_tokens":3000
  }'

# With the server-side digest rewrite
curl -X POST http://localhost:1933/api/v1/search/search \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENVIKING_API_KEY" \
  -d '{"query":"tier design","mode":"context","max_tokens":3000,"rewrite":true}'
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "entries": [
      {
        "uri": "viking://user/default/memories/events/2026/07/14/tier_design.md",
        "category": "events",
        "score": 0.45,
        "detail": "full",
        "text": "# Summary\nTiers now take a per-category default\n...",
        "origin": "self"
      },
      {
        "uri": "viking://user/default/memories/entities/software/openviking_fs.md",
        "category": "entities",
        "score": 0.43,
        "detail": "abstract",
        "text": "OpenViking FS storage layer...",
        "origin": "self"
      }
    ],
    "rendered": "<memory uri=\"viking://user/default/memories/events/2026/07/14/tier_design.md\" type=\"events\" score=\"0.45\" detail=\"full\">\n# Summary\n...\n</memory>",
    "digest": "",
    "stats": {
      "candidates": 13,
      "returned": 13,
      "dropped": 0,
      "deduped": 0,
      "max_tokens": 3000,
      "used_tokens": 2510,
      "per_entry_cap": 462,
      "detail": null,
      "tier_counts": {"full": 4, "overview": 2, "abstract": 7},
      "fill": {"floor_tokens": 1890, "overview_upgrades": 0, "full_upgrades": 4, "spare_upgrades": 0},
      "query_expansion": "used",
      "rewrite": "off",
      "rewrite_usage": null,
      "excluded": 0,
      "dedup": {"turns": 5, "status": "ok", "cooled": 2, "turn": 34}
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `entries[].uri` | string | Entry URI, always present at every tier, expandable with the MCP `read` tool |
| `entries[].category` | string | `events`/`entities`/`preferences`/`experiences`/`resources`/`skills`, or `memories` for a built-in memory type outside those four |
| `entries[].detail` | string | Tier actually served: `full`, `overview`, `abstract` or `uri` |
| `entries[].text` | string | Body for that tier; empty at the `uri` tier |
| `rendered` | string | Flat XML context block, ready to inject; empty when rewrite reports `no_relevant` |
| `digest` | string | Digest when the rewrite succeeded, empty string on failure or when the compressor reports no relevant memory |
| `stats` | object | Budget usage, tier distribution, expansion and rewrite status (`off`, `ok`, `no_relevant`, `failed` or `timeout`), dedup ledger state; carries `retrieval_errors` when a retrieval scope failed, so a broken index is distinguishable from having no relevant memories |

When `stats.rewrite` is `no_relevant`, the response keeps `entries` for
inspection but returns both `digest` and `rendered` as empty strings. This makes
the successful empty result safe for clients that predate the explicit status.
Nothing was served that turn, so those URIs also stay out of the `dedup_turns`
ledger and remain available to the later turn they are relevant to.

**Validation rules**

- Any context-only parameter sent explicitly under `mode="list"` → 400
- `target_uri` under `mode="context"` → 400
- Unknown `quotas` key → 400
- Fields ignored in context mode (`level`, and `limit` when `purpose` or explicit quotas are active) are reported in `stats.ignored`

---

### grep()

Search content by pattern (regex).

#### 1. API Implementation Introduction

The `grep()` method performs regex pattern matching search in the file system, used to find files and content lines containing specific patterns. Unlike semantic search, grep is exact pattern matching.

**Processing Pipeline**:
1. Traverse file system starting from specified URI
2. Perform regex matching on each file content
3. Collect matching lines and position information
4. Return matching results list

**Code Entry Points**:
- `openviking_cli/client/sync_http.py:SyncHTTPClient.grep()` - Python SDK entry (HTTP)
- `openviking/server/routers/search.py:grep()` - HTTP router
- `crates/ov_cli/src/commands/search.rs:grep()` - Rust CLI command

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI to search in |
| pattern | str | Yes | - | Search pattern (regex) |
| case_insensitive | bool | No | False | Ignore case |
| exclude_uri | str | No | None | URI prefix to exclude from search |
| node_limit | int | No | 256 | Maximum number of results. Omitted requests default to 256; pass a larger integer when you need more results |
| level_limit | int | No | Python SDK: 5; HTTP API / CLI / Go SDK: 10 | Maximum directory depth to traverse. The Go SDK currently uses the HTTP API default. |

#### 3. Usage Examples

**HTTP API**

```
POST /api/v1/search/grep
```

```bash
curl -X POST http://localhost:1933/api/v1/search/grep \
    -H "Content-Type: application/json" \
    -H "X-API-Key: your-key" \
    -d '{
        "uri": "viking://resources",
        "pattern": "authentication",
        "case_insensitive": true
    }'
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

results = client.grep(
    uri="viking://resources",
    pattern="authentication",
    case_insensitive=True,
    node_limit=1024,
)

print(f"Found {results['count']} matches")
for match in results['matches']:
    print(f"  {match['uri']}:{match['line']}")
    print(f"    {match['content']}")
```

**TypeScript SDK**

```typescript
console.log(await client.grep("viking://resources/docs/", "authentication"));
```

**Go SDK**

```go
nodeLimit := 1024
result, err := client.Grep(ctx, "viking://resources", "authentication", &openviking.GrepOptions{
    CaseInsensitive: true,
    NodeLimit:       &nodeLimit,
})
if err != nil {
    return err
}
fmt.Println(result["count"])
```

**CLI**

```bash
# Basic search
openviking grep "authentication" --uri viking://resources

# Ignore case
openviking grep "authentication" --uri viking://resources --ignore-case

# Specify depth limit
openviking grep "TODO" --uri viking://resources --level-limit 3
```

**Response Example**

```json
{
    "status": "ok",
    "result": {
        "matches": [
            {
                "uri": "viking://resources/docs/auth.md",
                "line": 15,
                "content": "User authentication is handled by..."
            }
        ],
        "count": 1
    },
    "time": 0.1
}
```

---

### glob()

Match files by glob pattern.

#### 1. API Implementation Introduction

The `glob()` method uses file wildcard pattern matching URIs, similar to Unix shell glob functionality. Used to find files and directories by name patterns.

**Supported Pattern Syntax**:
- `*` matches any character (except path separator)
- `**` recursively matches any directory
- `?` matches single character
- `[]` matches character range

**Code Entry Points**:
- `sdk/python/openviking_sdk/client.py:SyncHTTPClient.glob()` - Python SDK entry (HTTP)
- `openviking/server/routers/search.py:glob()` - HTTP router
- `crates/ov_cli/src/commands/search.rs:glob()` - Rust CLI command

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| pattern | str | Yes | - | Glob pattern (e.g., `**/*.md`) |
| uri | str | No | "viking://" | Starting URI |
| node_limit | int | No | 256 | Maximum number of matches to return. Omitted requests default to 256; pass a larger integer when you need more results |

#### 3. Usage Examples

**HTTP API**

```
POST /api/v1/search/glob
```

```bash
curl -X POST http://localhost:1933/api/v1/search/glob \
    -H "Content-Type: application/json" \
    -H "X-API-Key: your-key" \
    -d '{
        "pattern": "**/*.md",
        "uri": "viking://resources"
    }'
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# Find all markdown files (defaults to returning at most 256 matches)
results = client.glob(pattern="**/*.md", uri="viking://resources")
print(f"Found {results['count']} markdown files:")
for uri in results['matches']:
    print(f"  {uri}")

# Find all Python files with a higher explicit cap
results = client.glob(
    pattern="**/*.py",
    uri="viking://resources",
    node_limit=1024,
)
print(f"Found {results['count']} Python files")
```

**TypeScript SDK**

```typescript
console.log(await client.glob("**/*.md", "viking://resources/docs/"));
```

**Go SDK**

```go
result, err := client.Glob(ctx, "**/*.md", "viking://resources", &openviking.GlobOptions{
    NodeLimit: openviking.Int(1024),
})
if err != nil {
    return err
}
fmt.Println(result["count"])
```

**CLI**

```bash
# Find all markdown files
openviking glob "**/*.md" --uri viking://resources

# Find all Python files
openviking glob "**/*.py"
```

**Response Example**

```json
{
    "status": "ok",
    "result": {
        "matches": [
            "viking://resources/docs/api.md",
            "viking://resources/docs/guide.md"
        ],
        "count": 2
    },
    "time": 0.1
}
```

---

## Working with Results

### Read Content Progressively

Retrieval results usually only contain L0 summaries, you can progressively load more detailed content as needed.

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

results = client.find(query="authentication")

for context in results.get("resources", []):
    # Start with L0 (abstract) - already in context["abstract"]
    print(f"Abstract: {context.get('abstract', '')}")

    if context.get("level", 0) < 2:
        # Get L1 (overview) for directories
        overview = client.overview(uri=context["uri"])
        print(f"Overview: {overview[:500]}...")
    else:
        # Load L2 (content) for files
        content = client.read(uri=context["uri"])
        print(f"File content: {content}")
```

**HTTP API**

```bash
# Step 1: Search
curl -X POST http://localhost:1933/api/v1/search/find \
    -H "Content-Type: application/json" \
    -H "X-API-Key: your-key" \
    -d '{"query": "authentication"}'

# Step 2: Read overview for directory result
curl -X GET "http://localhost:1933/api/v1/content/overview?uri=viking://resources/docs/auth" \
    -H "X-API-Key: your-key"

# Step 3: Read full content for file result
curl -X GET "http://localhost:1933/api/v1/content/read?uri=viking://resources/docs/auth.md" \
    -H "X-API-Key: your-key"
```

## Best Practices

### Use Specific Queries

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# Good - specific query
results = client.find(query="OAuth 2.0 authorization code flow implementation")

# Less effective - too broad
results = client.find(query="auth")
```

### Scope Your Searches

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# Search in relevant scope for better results
results = client.find(
    query="error handling",
    target_uri="viking://resources/my-project",
)
```

### Use Session Context for Conversations

```python
import openviking as ov
from openviking_sdk import TextPart

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# For conversational search, use session
session_info = client.create_session()
session = client.session(session_id=session_info["session_id"])
session.add_message(
    role="user",
    parts=[TextPart(text="I'm building a login page")],
)

# Search understands context
results = client.search(
    query="best practices",
    session_id=session.session_id,
)
```

## Related Documentation

- [Resources](02-resources.md) - Resource management
- [Sessions](05-sessions.md) - Session context
- [Context Layers](../concepts/03-context-layers.md) - L0/L1/L2
