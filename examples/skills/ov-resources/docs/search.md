# Semantic Search (`ov find` / `ov search`)

OpenViking provides two semantic search commands for retrieving context from resources, memories, and skills.

## `ov find` — Pure Vector Similarity Search

Performs hierarchical vector similarity search without session context. Best for simple, direct queries.

```bash
# Basic search across all context
ov find "how to handle API rate limits"

# Search within specific URI scope
ov find "authentication flow" --uri "viking://resources/my-project"

# Limit results and set relevance threshold
ov find "error handling" --node-limit 5 --threshold 0.3

# Time-filtered search
ov find "invoice" --after 7d --time-field created_at

# Level-filtered search (L0 only)
ov find "overview" --level 0

# Multiple levels
ov find "details" -L 1,2
```

## `ov search` — Context-Aware Search with Intent Analysis

Adds session context understanding and intent analysis on top of `find()`. Better for conversational queries.

```bash
# Search with session context
ov search "best practices" --session-id abc123

# Search with time filter
ov search "watch vs scheduled" --after 2026-03-15 --before 2026-03-20

# Search without session (still performs intent analysis)
ov search "how to implement OAuth 2.0 authorization code flow"

# Level-filtered
ov search "best practices" --level 0
ov search "how to implement OAuth" -L 1,2
```

## Find vs Search

| Aspect | `find` | `search` |
|--------|--------|----------|
| Intent Analysis | No | Yes |
| Session Context | No | Yes |
| Query Expansion | No | Yes |
| Default Limit | 10 | 10 |
| Use Case | Simple queries | Conversational search |

## Common Parameters

| Parameter | Description |
|---|---|
| `--uri` | Limit search to specific URI prefix |
| `--node-limit` / `--limit` | Maximum number of results |
| `--threshold` / `--score-threshold` | Minimum relevance score (0-1) |
| `--after` | Lower time bound (`2h`, `7d`, ISO 8601) |
| `--before` | Upper time bound (`30m`, ISO 8601) |
| `--time-field` | `updated_at` (default) or `created_at` |
| `--level` / `-L` | Limit to levels: `0`, `1`, `2`, `0,1,2` |
| `--peer-id` | Stable interaction peer ID |
| `--session-id` | Session ID for context-aware search (`search` only) |

## Search Result Structure

Results are grouped by `context_type`:

```json
{
  "memories": [],
  "resources": [
    {
      "uri": "viking://resources/docs/auth.md",
      "context_type": "resource",
      "level": 2,
      "score": 0.95,
      "abstract": "OAuth 2.0 best practices...",
      "overview": "This guide covers...",
      "match_reason": "Context-aware match: OAuth login best practices",
      "relations": []
    }
  ],
  "skills": [],
  "total": 1,
  "query_plan": {
    "reasoning": "User is asking about OAuth implementation...",
    "queries": [...]
  }
}
```

`query_plan` is only present in `search` results.

## URI Scope Targets

```bash
# Search only resources
ov find "authentication" --uri "viking://resources"

# Search only memories
ov find "preferences" --uri "viking://user/memories"

# Search only skills
ov find "web search" --uri "viking://user/skills"

# Search specific project
ov find "API endpoints" --uri "viking://resources/my-project"
```

## Combining Search with Browse

```bash
# Step 1: Semantic search to find relevant directories
ov find "authentication" --uri "viking://resources/project-A"

# Step 2: Get overview for context
ov overview viking://resources/project-A/backend

# Step 3: Read specific content
ov read viking://resources/project-A/backend/auth.md
```
