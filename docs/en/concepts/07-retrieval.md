# Retrieval Mechanism

OpenViking uses two-stage retrieval: intent analysis + hierarchical retrieval + rerank.

## Overview

```
Query → Intent Analysis → Hierarchical Retrieval → Rerank → Results
              ↓                    ↓                  ↓
         TypedQuery          Directory Recursion   Refined Scoring
```

## find() vs search()

| Feature | find() | search() |
|---------|--------|----------|
| Session context | Not needed | Required |
| Intent analysis | Not used | LLM analysis |
| Query count | Single query | 0-5 TypedQueries |
| Latency | Low | Higher |
| Use case | Simple queries | Complex tasks |

### Usage Examples

```python
# find(): Simple query
results = await client.find(
    "OAuth authentication",
    target_uri="viking://resources/"
)

# search(): Complex task (needs session context)
results = await client.search(
    "Help me create an RFC document",
    session_info=session
)
```

## Intent Analysis

IntentAnalyzer uses LLM to analyze query intent and generate 0-5 TypedQueries.

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

HierarchicalRetriever uses priority queue to recursively search directory structure.

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
| MEMORY | `viking://user/memories`, `viking://agent/memories` |
| RESOURCE | `viking://resources` |
| SKILL | `viking://agent/skills` |

### Recursive Search Algorithm

```python
while dir_queue:
    current_uri, parent_score = heapq.heappop(dir_queue)

    # Search children
    results = await search(parent_uri=current_uri)

    for r in results:
        # Score propagation
        final_score = 0.5 * embedding_score + 0.5 * parent_score

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
| `SCORE_PROPAGATION_ALPHA` | 0.5 | 50% embedding + 50% parent |
| `MAX_CONVERGENCE_ROUNDS` | 3 | Convergence detection rounds |
| `GLOBAL_SEARCH_TOPK` | 3 | Global search candidates |
| `MAX_RELATIONS` | 5 | Max relations per resource |

## Rerank Strategy

Rerank refines candidate results in THINKING mode.

### Trigger Conditions

- Rerank AK/SK configured
- Using THINKING mode (default for search())

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

| Backend | Model |
|---------|-------|
| Volcengine | doubao-seed-rerank |

## Retrieval Results

### MatchedContext

```python
@dataclass
class MatchedContext:
    uri: str                # Resource URI
    context_type: ContextType
    is_leaf: bool           # Whether file
    abstract: str           # L0 abstract
    score: float            # Final score
    relations: List[RelatedContext]  # Related contexts
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
