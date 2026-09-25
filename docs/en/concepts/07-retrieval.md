# Retrieval Mechanism

OpenViking retrieves context through vector search and directory traversal. `search()` can analyze query intent first; a configured reranker can refine candidate ordering.

## Overview

```text
find: query → QUICK vector retrieval → results
search: query + optional session → optional intent analysis → retrieve each query → merge
                                        ├─ No reranker / image query: QUICK
                                        └─ Text query with reranker: THINKING hierarchy traversal
```

## find() vs search()

| Feature | find() | search() |
|---------|--------|----------|
| Session context | Not used | Optional; used when `session_id` is supplied |
| Intent analysis | Not used | Uses an LLM when session content exists and intent analysis is enabled |
| Query count | Single query | zero or more TypedQueries |
| Retrieval path | QUICK; no recursion, rerank, or hotness weighting | Depends on reranker configuration and query type |
| Latency | Usually lower | Depends on intent analysis, query count, and retrieval path |
| Use case | Simple queries | Complex tasks |

For `search`, `limit` applies to each planned query. Merged results may exceed it, and the current implementation does not guarantee deduplication across queries.

### Usage Examples

These examples use a configured synchronous Python SDK client named `client`.

```python
# find(): Simple query
results = client.find(
    query="OAuth authentication",
    target_uri="viking://resources/",
)

# search(): Complex task (needs session context)
session_info = client.create_session()
client.add_message(
    session_id=session_info["session_id"], role="user",
    content="We are designing the OAuth login flow for this project.",
)
results = client.search(
    query="Help me create an RFC document",
    session_id=session_info["session_id"],
)
```

## Intent Analysis

When `retrieval.enable_intent=true` and the session contains a summary or messages, IntentAnalyzer uses an LLM to analyze query intent and generate zero or more TypedQueries. The model used for this stage is separately configurable via the [`query_planner`](../guides/01-configuration.md#query-planner) config, falling back to `vlm` when unset.

### Input

- Session compression summary
- Last 5 messages
- Current query

### Output

```python
@dataclass
class TypedQuery:
    query: str              # Rewritten query
    context_type: ContextType  # MEMORY/RESOURCE/SKILL
    intent: str             # Query purpose
    priority: int           # 1-5 priority
```

### Query Styles

| Type | Style | Example |
|------|-------|---------|
| **skill** | Verb-first | "Create RFC document", "Extract PDF tables" |
| **resource** | Noun phrase | "RFC document template", "API usage guide" |
| **memory** | "User's XX" | "User's code style preferences" |

### Special Cases

- **0 queries**: Chitchat, greetings that don't need retrieval
- **Multiple queries**: Complex tasks may need skill + resource + memory

## Hierarchical Retrieval

The following describes the THINKING path. QUICK performs vector retrieval without directory recursion. THINKING uses a priority queue to expand directories and calls the reranker when scoring candidates.

### Flow

```
Step 1: Determine root directories by context_type
        ↓
Step 2: Global vector search to locate starting directories
        ↓
Step 3: Merge starting points + Rerank scoring
        ↓
Step 4: Recursive search (priority queue)
        ↓
Step 5: Convert to MatchedContext
```

### Root Directory Mapping

| context_type | Root Directories |
|--------------|------------------|
| MEMORY | Memories in the current user's space; with `actor_peer_id`, limited to user memories and that peer's memories |
| RESOURCE | `viking://resources` and resources in the current user's space; with `actor_peer_id`, includes that peer's resources |
| SKILL | `viking://~/skills` and `viking://agent/skills` |

An explicit `target_uri` takes precedence over these type defaults. Without a target, the public API searches the current user's space and account-shared resources; explicitly include `viking://agent/skills` to search shared skills. Permissions and peer visibility still apply.

### Recursive Search Algorithm

This sketch omits implementation details for queue priorities, deduplication, and stopping conditions.

```python
while dir_queue:
    current_uri, parent_score = heapq.heappop(dir_queue)

    # Search children
    results = await search(parent_uri=current_uri)

    for r in results:
        # Score propagation
        final_score = score_propagation_alpha * embedding_score + (1 - score_propagation_alpha) * parent_score

        if final_score > threshold:
            collected.append(r)

            if not r.is_leaf:  # Directory continues recursion
                heapq.heappush(dir_queue, (r.uri, final_score))

    # Convergence detection
    if topk_unchanged_for_3_rounds:
        break
```

### Key Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| `retrieval.score_propagation_alpha` | 1.0 | Child-score weight in the propagation blend; `1.0` uses only the child's own score and ignores the parent score |
| `MAX_CONVERGENCE_ROUNDS` | 3 | Convergence detection rounds |
| `GLOBAL_SEARCH_TOPK` | 10 | Lower bound on global candidate count; actual count is at least the query `limit` |

## Rerank Strategy

Rerank refines candidate results in THINKING mode.

### Trigger Conditions

- A reranking model and its credentials are configured
- Text `search()` selects THINKING when a reranker is configured; `find()` and image queries use QUICK
- If rerank returns an invalid result or the API call fails, retrieval falls back to vector scores

### Scoring Method

```python
if rerank_client and mode == THINKING:
    scores = rerank_client.rerank_batch(query, documents)
else:
    scores = [r["_score"] for r in results]  # Vector scores
```

### Usage Points

1. **Starting point evaluation**: Evaluate global search candidate directories
2. **Recursive search**: Evaluate children at each level

### Backend Support

Supported provider settings include `vikingdb` (Volcengine), `cohere`, `openai` (compatible endpoints), `litellm`, and `jev` (default model for `vikingdb`: `doubao-seed-rerank`). Model names and authentication depend on the provider; see the [Configuration Guide](../guides/01-configuration.md).

## Retrieval Results

The following excerpts show server-internal types. For HTTP/SDK response fields, see the [Retrieval API](../api/06-retrieval.md).

### MatchedContext

```python
@dataclass
class MatchedContext:
    uri: str                # Resource URI
    context_type: ContextType
    is_leaf: bool           # Whether file
    abstract: str           # L0 abstract
    score: float            # Final score
```

### FindResult

```python
@dataclass
class FindResult:
    memories: List[MatchedContext]
    resources: List[MatchedContext]
    skills: List[MatchedContext]
    query_plan: Optional[QueryPlan]      # Present for search()
    query_results: Optional[List[QueryResult]]
    total: int
```

## Related Documents

- [Architecture Overview](./01-architecture.md) - System architecture
- [Storage Architecture](./05-storage.md) - Vector index
- [Context Layers](./03-context-layers.md) - L0/L1/L2 model
- [Context Types](./02-context-types.md) - Three context types
