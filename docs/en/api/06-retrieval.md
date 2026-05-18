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
| query | str | Yes | - | Search query string |
| target_uri | str \| List[str] | No | "" | Limit search to specific URI prefix |
| node_limit | int | No | None | Maximum number of results |
| score_threshold | float | No | None | Minimum relevance score threshold |
| filter | Dict | No | None | Metadata filter |
| since | str | No | None | Lower time bound, accepts `2h` or ISO 8601 / `YYYY-MM-DD`. Timezone-less values are interpreted as UTC. CLI `--after` maps to this field |
| until | str | No | None | Upper time bound, accepts `30m` or ISO 8601 / `YYYY-MM-DD`. Timezone-less values are interpreted as UTC. CLI `--before` maps to this field |
| time_field | "updated_at" \| "created_at" | No | "updated_at" | Metadata time field used by `since` / `until` |
| level | str | No | None | Limit results to specific level(s), e.g., `0`, `1`, `2`, or `0,1,2`. CLI `--level`/`-L` maps to this field |
| include_provenance | bool | No | False | Include provenance/query-plan details in serialized result |
| telemetry | bool \| object | No | False | Attach telemetry data to response |

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
    level: int                       # Tier (0=L0, 1=L1, 2=L2)
    abstract: str                    # L0 content
    overview: Optional[str]          # L1 overview (optional for non-leaf nodes)
    category: str                    # Category
    score: float                     # Relevance score (0-1)
    match_reason: str                # Why this matched
    relations: List[RelatedContext]  # Related contexts
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

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# Basic search
results = client.find("how to authenticate users")

# Search with filter and time range
recent_emails = client.find(
    "invoice",
    target_uri="viking://resources/email",
    since="7d",
    time_field="created_at",
)

# Iterate through results
for ctx in results.resources:
    print(f"URI: {ctx.uri}")
    print(f"Score: {ctx.score:.3f}")
    print(f"Type: {ctx.context_type}")
    print(f"Abstract: {ctx.abstract[:100]}...")
    print("---")
```

**Search with Target URI Limitation**

```python
# Search only in resources
results = client.find(
    "authentication",
    target_uri="viking://resources"
)

# Search only in user memories
results = client.find(
    "preferences",
    target_uri="viking://user/memories"
)

# Search only in skills
results = client.find(
    "web search",
    target_uri="viking://agent/skills"
)

# Search in specific project
results = client.find(
    "API endpoints",
    target_uri="viking://resources/my-project"
)
```

**CLI**

```bash
# Basic search
openviking find "how to authenticate users"

# Specify URI scope
openviking find "how to authenticate users" --uri "viking://resources"

# With time filter
openviking find "invoice" --after 7d

# With limit
openviking find "how to authenticate users" --limit 20

# Limit to specific level(s) (L0 only)
openviking find "how to authenticate users" --level 0

# Limit to specific level(s) (L1 and L2) using short option
openviking find "how to authenticate users" -L 1,2
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
                "relations": [],
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
                "relations": [],
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
| query | str | Yes | - | Search query string |
| target_uri | str \| List[str] | No | "" | Limit search to specific URI prefix |
| session | Session | No | None | Session for context-aware search (SDK) |
| session_id | str | No | None | Session ID for context-aware search (HTTP) |
| node_limit | int | No | None | Maximum number of results |
| score_threshold | float | No | None | Minimum relevance score threshold |
| filter | Dict | No | None | Metadata filter |
| since | str | No | None | Lower time bound, accepts `2h` or ISO 8601 / `YYYY-MM-DD`. Timezone-less values are interpreted as UTC. CLI `--after` maps to this field |
| until | str | No | None | Upper time bound, accepts `30m` or ISO 8601 / `YYYY-MM-DD`. Timezone-less values are interpreted as UTC. CLI `--before` maps to this field |
| time_field | "updated_at" \| "created_at" | No | "updated_at" | Metadata time field used by `since` / `until` |
| level | str | No | None | Limit results to specific level(s), e.g., `0`, `1`, `2`, or `0,1,2`. CLI `--level`/`-L` maps to this field |
| include_provenance | bool | No | False | Include provenance/query-plan details in serialized result |
| telemetry | bool \| object | No | False | Attach telemetry data to response |

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

**Python SDK**

```python
import openviking as ov
from openviking.message import TextPart

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# Create session with conversation context
session = client.session()
session.add_message("user", [
    TextPart(text="I'm building a login page with OAuth")
])
session.add_message("assistant", [
    TextPart(text="I can help you with OAuth implementation.")
])

# Search understands conversation context
results = client.search(
    "best practices",
    session=session,
    since="2h"
)

for ctx in results.resources:
    print(f"Found: {ctx.uri}")
    print(f"Abstract: {ctx.abstract[:200]}...")
```

**Search without Session**

```python
# search can also be used without session
# It still performs intent analysis on the query
results = client.search(
    "how to implement OAuth 2.0 authorization code flow"
)

for ctx in results.resources:
    print(f"Found: {ctx.uri} (score: {ctx.score:.3f})")
```

**CLI**

```bash
# Search with session ID
openviking search "best practices" --session-id abc123

# Search with time filter
openviking search "watch vs scheduled" --after 2026-03-15 --before 2026-03-20

# Search without session (still performs intent analysis)
openviking search "how to implement OAuth 2.0 authorization code flow"

# Limit to specific level(s) (L0 only)
openviking search "best practices" --level 0

# Limit to specific level(s) (L1 and L2) using short option
openviking search "how to implement OAuth" -L 1,2
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
                "relations": [],
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
| node_limit | int | No | None | Maximum number of results |
| level_limit | int | No | 5 | Maximum directory depth to traverse |

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
    "viking://resources",
    "authentication",
    case_insensitive=True
)

print(f"Found {results['count']} matches")
for match in results['matches']:
    print(f"  {match['uri']}:{match['line']}")
    print(f"    {match['content']}")
```

**CLI**

```bash
# Basic search
openviking grep viking://resources "authentication"

# Ignore case
openviking grep viking://resources "authentication" --ignore-case

# Specify depth limit
openviking grep viking://resources "TODO" --level-limit 3
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
- `openviking_cli/client/sync_http.py:SyncHTTPClient.glob()` - Python SDK entry (HTTP)
- `openviking/server/routers/search.py:glob()` - HTTP router
- `crates/ov_cli/src/commands/search.rs:glob()` - Rust CLI command

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| pattern | str | Yes | - | Glob pattern (e.g., `**/*.md`) |
| uri | str | No | "viking://" | Starting URI |
| node_limit | int | No | None | Maximum number of matches to return |

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

# Find all markdown files
results = client.glob("**/*.md", "viking://resources")
print(f"Found {results['count']} markdown files:")
for uri in results['matches']:
    print(f"  {uri}")

# Find all Python files
results = client.glob("**/*.py", "viking://resources")
print(f"Found {results['count']} Python files")
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

results = client.find("authentication")

for ctx in results.resources:
    # Start with L0 (abstract) - already in ctx.abstract
    print(f"Abstract: {ctx.abstract}")

    if ctx.level < 2:
        # Get L1 (overview) for directories
        overview = client.overview(ctx.uri)
        print(f"Overview: {overview[:500]}...")
    else:
        # Load L2 (content) for files
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

# Step 2: Read overview for directory result
curl -X GET "http://localhost:1933/api/v1/content/overview?uri=viking://resources/docs/auth" \
    -H "X-API-Key: your-key"

# Step 3: Read full content for file result
curl -X GET "http://localhost:1933/api/v1/content/read?uri=viking://resources/docs/auth.md" \
    -H "X-API-Key: your-key"
```

### Get Related Resources

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

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
# Get relations for resource
curl -X GET "http://localhost:1933/api/v1/relations?uri=viking://resources/docs/auth" \
    -H "X-API-Key: your-key"
```

## Best Practices

### Use Specific Queries

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# Good - specific query
results = client.find("OAuth 2.0 authorization code flow implementation")

# Less effective - too broad
results = client.find("auth")
```

### Scope Your Searches

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# Search in relevant scope for better results
results = client.find(
    "error handling",
    target_uri="viking://resources/my-project"
)
```

### Use Session Context for Conversations

```python
import openviking as ov
from openviking.message import TextPart

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# For conversational search, use session
session = client.session()
session.add_message("user", [
    TextPart(text="I'm building a login page")
])

# Search understands context
results = client.search("best practices", session=session)
```

## Related Documentation

- [Resources](02-resources.md) - Resource management
- [Sessions](05-sessions.md) - Session context
- [Context Layers](../concepts/03-context-layers.md) - L0/L1/L2
