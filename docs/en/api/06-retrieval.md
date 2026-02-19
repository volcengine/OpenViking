# Retrieval

OpenViking provides two search methods: `find` for simple semantic search and `search` for complex retrieval with session context.

## find vs search

| Aspect | find | search |
|--------|------|--------|
| Intent Analysis | No | Yes |
| Session Context | No | Yes |
| Query Expansion | No | Yes |
| Default Limit | 10 | 10 |
| Use Case | Simple queries | Conversational search |

## API Reference

### find()

Basic vector similarity search.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| query | str | Yes | - | Search query string |
| target_uri | str | No | "" | Limit search to specific URI prefix |
| limit | int | No | 10 | Maximum number of results |
| score_threshold | float | No | None | Minimum relevance score threshold |
| filter | Dict | No | None | Metadata filters |

**FindResult Structure**

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
    is_leaf: bool                    # Whether it's a leaf node
    abstract: str                    # L0 content
    category: str                    # Category
    score: float                     # Relevance score (0-1)
    match_reason: str                # Why this matched
    relations: List[RelatedContext]  # Related contexts
```

**Python SDK (Embedded / HTTP)**

```python
results = client.find("how to authenticate users")

for ctx in results.resources:
    print(f"URI: {ctx.uri}")
    print(f"Score: {ctx.score:.3f}")
    print(f"Type: {ctx.context_type}")
    print(f"Abstract: {ctx.abstract[:100]}...")
    print("---")
```

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

**CLI**

```bash
openviking find "how to authenticate users" [--uri viking://resources/] [--limit 10]
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "memories": [],
    "resources": [
      {
        "uri": "viking://resources/docs/auth/",
        "context_type": "resource",
        "is_leaf": false,
        "abstract": "Authentication guide covering OAuth 2.0...",
        "score": 0.92,
        "match_reason": "Semantic match on authentication"
      }
    ],
    "skills": [],
    "total": 1
  },
  "time": 0.1
}
```

**Example: Search with Target URI**

**Python SDK (Embedded / HTTP)**

```python
# Search only in resources
results = client.find(
    "authentication",
    target_uri="viking://resources/"
)

# Search only in user memories
results = client.find(
    "preferences",
    target_uri="viking://user/memories/"
)

# Search only in skills
results = client.find(
    "web search",
    target_uri="viking://skills/"
)

# Search in specific project
results = client.find(
    "API endpoints",
    target_uri="viking://resources/my-project/"
)
```

**HTTP API**

```bash
# Search only in resources
curl -X POST http://localhost:1933/api/v1/search/find \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "query": "authentication",
    "target_uri": "viking://resources/"
  }'

# Search with score threshold
curl -X POST http://localhost:1933/api/v1/search/find \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "query": "API endpoints",
    "target_uri": "viking://resources/my-project/",
    "score_threshold": 0.5,
    "limit": 5
  }'
```

---

### search()

Search with session context and intent analysis.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| query | str | Yes | - | Search query string |
| target_uri | str | No | "" | Limit search to specific URI prefix |
| session | Session | No | None | Session for context-aware search (SDK) |
| session_id | str | No | None | Session ID for context-aware search (HTTP) |
| limit | int | No | 10 | Maximum number of results |
| score_threshold | float | No | None | Minimum relevance score threshold |
| filter | Dict | No | None | Metadata filters |

**Python SDK (Embedded / HTTP)**

```python
from openviking.message import TextPart

# Create session with conversation context
session = client.session()
session.add_message("user", [
    TextPart(text="I'm building a login page with OAuth")
])
session.add_message("assistant", [
    TextPart(text="I can help you with OAuth implementation.")
])

# Search understands the conversation context
results = client.search(
    "best practices",
    session=session
)

for ctx in results.resources:
    print(f"Found: {ctx.uri}")
    print(f"Abstract: {ctx.abstract[:200]}...")
```

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
    "limit": 10
  }'
```

**CLI**

```bash
openviking search "best practices" [--session-id abc123] [--limit 10]
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "memories": [],
    "resources": [
      {
        "uri": "viking://resources/docs/oauth-best-practices/",
        "context_type": "resource",
        "is_leaf": false,
        "abstract": "OAuth 2.0 best practices for login pages...",
        "score": 0.95,
        "match_reason": "Context-aware match: OAuth login best practices"
      }
    ],
    "skills": [],
    "query_plan": {
      "expanded_queries": ["OAuth 2.0 best practices", "login page security"]
    },
    "total": 1
  },
  "time": 0.1
}
```

**Example: Search Without Session**

**Python SDK (Embedded / HTTP)**

```python
# search can also be used without session
# It still performs intent analysis on the query
results = client.search(
    "how to implement OAuth 2.0 authorization code flow",
)

for ctx in results.resources:
    print(f"Found: {ctx.uri} (score: {ctx.score:.3f})")
```

**HTTP API**

```bash
curl -X POST http://localhost:1933/api/v1/search/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "query": "how to implement OAuth 2.0 authorization code flow"
  }'
```

---

### grep()

Search content by pattern (regex).

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI to search in |
| pattern | str | Yes | - | Search pattern (regex) |
| case_insensitive | bool | No | False | Ignore case |

**Python SDK (Embedded / HTTP)**

```python
results = client.grep(
    "viking://resources/",
    "authentication",
    case_insensitive=True
)

print(f"Found {results['count']} matches")
for match in results['matches']:
    print(f"  {match['uri']}:{match['line']}")
    print(f"    {match['content']}")
```

**HTTP API**

```
POST /api/v1/search/grep
```

```bash
curl -X POST http://localhost:1933/api/v1/search/grep \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://resources/",
    "pattern": "authentication",
    "case_insensitive": true
  }'
```

**CLI**

```bash
openviking grep viking://resources/ "authentication" [--ignore-case]
```

**Response**

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

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| pattern | str | Yes | - | Glob pattern (e.g., `**/*.md`) |
| uri | str | No | "viking://" | Starting URI |

**Python SDK (Embedded / HTTP)**

```python
# Find all markdown files
results = client.glob("**/*.md", "viking://resources/")
print(f"Found {results['count']} markdown files:")
for uri in results['matches']:
    print(f"  {uri}")

# Find all Python files
results = client.glob("**/*.py", "viking://resources/")
print(f"Found {results['count']} Python files")
```

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
    "uri": "viking://resources/"
  }'
```

**CLI**

```bash
openviking glob "**/*.md" [--uri viking://resources/]
```

**Response**

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

## Retrieval Pipeline

```
Query -> Intent Analysis -> Vector Search (L0) -> Rerank (L1) -> Results
```

1. **Intent Analysis** (search only): Understand query intent, expand queries
2. **Vector Search**: Find candidates using Embedding
3. **Rerank**: Re-score using content for accuracy
4. **Results**: Return top-k contexts

## Working with Results

### Read Content Progressively

**Python SDK (Embedded / HTTP)**

```python
results = client.find("authentication")

for ctx in results.resources:
    # Start with L0 (abstract) - already in ctx.abstract
    print(f"Abstract: {ctx.abstract}")

    if not ctx.is_leaf:
        # Get L1 (overview)
        overview = client.overview(ctx.uri)
        print(f"Overview: {overview[:500]}...")
    else:
        # Load L2 (content)
        content = client.read(ctx.uri)
        print(f"File content: {content}")
```

**HTTP API**

```bash
# Step 1: Search
curl -X POST http://localhost:1933/api/v1/search/find \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"query": "authentication"}'

# Step 2: Read overview for a directory result
curl -X GET "http://localhost:1933/api/v1/content/overview?uri=viking://resources/docs/auth/" \
  -H "X-API-Key: your-key"

# Step 3: Read full content for a file result
curl -X GET "http://localhost:1933/api/v1/content/read?uri=viking://resources/docs/auth.md" \
  -H "X-API-Key: your-key"
```

### Get Related Resources

**Python SDK (Embedded / HTTP)**

```python
results = client.find("OAuth implementation")

for ctx in results.resources:
    print(f"Found: {ctx.uri}")

    # Get related resources
    relations = client.relations(ctx.uri)
    for rel in relations:
        print(f"  Related: {rel['uri']} - {rel['reason']}")
```

**HTTP API**

```bash
# Get relations for a resource
curl -X GET "http://localhost:1933/api/v1/relations?uri=viking://resources/docs/auth/" \
  -H "X-API-Key: your-key"
```

## Best Practices

### Use Specific Queries

```python
# Good - specific query
results = client.find("OAuth 2.0 authorization code flow implementation")

# Less effective - too broad
results = client.find("auth")
```

### Scope Your Searches

```python
# Search in relevant scope for better results
results = client.find(
    "error handling",
    target_uri="viking://resources/my-project/"
)
```

### Use Session Context for Conversations

```python
# For conversational search, use session
from openviking.message import TextPart

session = client.session()
session.add_message("user", [
    TextPart(text="I'm building a login page")
])

# Search understands the context
results = client.search("best practices", session=session)
```

### Related Documentation

- [Resources](02-resources.md) - Resource management
- [Sessions](05-sessions.md) - Session context
- [Context Layers](../concepts/03-context-layers.md) - L0/L1/L2
