# 检索

OpenViking 提供两种搜索方法：`find` 用于简单的语义搜索，`search` 用于带会话上下文的复杂检索。

## find 与 search 对比

| 方面 | find | search |
|------|------|--------|
| 意图分析 | 否 | 是 |
| 会话上下文 | 否 | 是 |
| 查询扩展 | 否 | 是 |
| 默认限制数 | 10 | 10 |
| 使用场景 | 简单查询 | 对话式搜索 |

## API 参考

### find()

基本向量相似度搜索。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| query | str | 是 | - | 搜索查询字符串 |
| target_uri | str | 否 | "" | 限制搜索范围到指定的 URI 前缀 |
| limit | int | 否 | 10 | 最大返回结果数 |
| score_threshold | float | 否 | None | 最低相关性分数阈值 |
| filter | Dict | 否 | None | 元数据过滤器 |

**FindResult 结构**

```python
class FindResult:
    memories: List[MatchedContext]   # 记忆上下文
    resources: List[MatchedContext]  # 资源上下文
    skills: List[MatchedContext]     # 技能上下文
    query_plan: Optional[QueryPlan]  # 查询计划（仅 search）
    query_results: Optional[List[QueryResult]]  # 详细结果
    total: int                       # 总数（自动计算）
```

**MatchedContext 结构**

```python
class MatchedContext:
    uri: str                         # Viking URI
    context_type: ContextType        # "resource"、"memory" 或 "skill"
    is_leaf: bool                    # 是否为叶子节点
    abstract: str                    # L0 内容
    category: str                    # 分类
    score: float                     # 相关性分数 (0-1)
    match_reason: str                # 匹配原因
    relations: List[RelatedContext]  # 关联上下文
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

**响应**

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

**示例：使用 Target URI 搜索**

**Python SDK (Embedded / HTTP)**

```python
# 仅在资源中搜索
results = client.find(
    "authentication",
    target_uri="viking://resources/"
)

# 仅在用户记忆中搜索
results = client.find(
    "preferences",
    target_uri="viking://user/memories/"
)

# 仅在技能中搜索
results = client.find(
    "web search",
    target_uri="viking://skills/"
)

# 在特定项目中搜索
results = client.find(
    "API endpoints",
    target_uri="viking://resources/my-project/"
)
```

**HTTP API**

```bash
# 仅在资源中搜索
curl -X POST http://localhost:1933/api/v1/search/find \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "query": "authentication",
    "target_uri": "viking://resources/"
  }'

# 使用分数阈值搜索
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

带会话上下文和意图分析的搜索。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| query | str | 是 | - | 搜索查询字符串 |
| target_uri | str | 否 | "" | 限制搜索范围到指定的 URI 前缀 |
| session | Session | 否 | None | 用于上下文感知搜索的会话（SDK） |
| session_id | str | 否 | None | 用于上下文感知搜索的会话 ID（HTTP） |
| limit | int | 否 | 10 | 最大返回结果数 |
| score_threshold | float | 否 | None | 最低相关性分数阈值 |
| filter | Dict | 否 | None | 元数据过滤器 |

**Python SDK (Embedded / HTTP)**

```python
from openviking.message import TextPart

# 创建带对话上下文的会话
session = client.session()
session.add_message("user", [
    TextPart(text="I'm building a login page with OAuth")
])
session.add_message("assistant", [
    TextPart(text="I can help you with OAuth implementation.")
])

# 搜索能够理解对话上下文
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

**响应**

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

**示例：不使用会话的搜索**

**Python SDK (Embedded / HTTP)**

```python
# search 也可以在没有会话的情况下使用
# 它仍然会对查询进行意图分析
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

通过模式（正则表达式）搜索内容。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | 要搜索的 Viking URI |
| pattern | str | 是 | - | 搜索模式（正则表达式） |
| case_insensitive | bool | 否 | False | 忽略大小写 |

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

**响应**

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

通过 glob 模式匹配文件。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| pattern | str | 是 | - | Glob 模式（例如 `**/*.md`） |
| uri | str | 否 | "viking://" | 起始 URI |

**Python SDK (Embedded / HTTP)**

```python
# 查找所有 markdown 文件
results = client.glob("**/*.md", "viking://resources/")
print(f"Found {results['count']} markdown files:")
for uri in results['matches']:
    print(f"  {uri}")

# 查找所有 Python 文件
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

**响应**

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

## 检索流程

```
查询 -> 意图分析 -> 向量搜索 (L0) -> 重排序 (L1) -> 结果
```

1. **意图分析**（仅 search）：理解查询意图，扩展查询
2. **向量搜索**：使用 Embedding 查找候选项
3. **重排序**：使用内容重新评分以提高准确性
4. **结果**：返回 top-k 上下文

## 处理结果

### 渐进式读取内容

**Python SDK (Embedded / HTTP)**

```python
results = client.find("authentication")

for ctx in results.resources:
    # 从 L0（摘要）开始 - 已包含在 ctx.abstract 中
    print(f"Abstract: {ctx.abstract}")

    if not ctx.is_leaf:
        # 获取 L1（概览）
        overview = client.overview(ctx.uri)
        print(f"Overview: {overview[:500]}...")
    else:
        # 加载 L2（内容）
        content = client.read(ctx.uri)
        print(f"File content: {content}")
```

**HTTP API**

```bash
# 步骤 1：搜索
curl -X POST http://localhost:1933/api/v1/search/find \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"query": "authentication"}'

# 步骤 2：读取目录结果的概览
curl -X GET "http://localhost:1933/api/v1/content/overview?uri=viking://resources/docs/auth/" \
  -H "X-API-Key: your-key"

# 步骤 3：读取文件结果的完整内容
curl -X GET "http://localhost:1933/api/v1/content/read?uri=viking://resources/docs/auth.md" \
  -H "X-API-Key: your-key"
```

### 获取关联资源

**Python SDK (Embedded / HTTP)**

```python
results = client.find("OAuth implementation")

for ctx in results.resources:
    print(f"Found: {ctx.uri}")

    # 获取关联资源
    relations = client.relations(ctx.uri)
    for rel in relations:
        print(f"  Related: {rel['uri']} - {rel['reason']}")
```

**HTTP API**

```bash
# 获取资源的关联关系
curl -X GET "http://localhost:1933/api/v1/relations?uri=viking://resources/docs/auth/" \
  -H "X-API-Key: your-key"
```

## 最佳实践

### 使用具体的查询

```python
# 好 - 具体的查询
results = client.find("OAuth 2.0 authorization code flow implementation")

# 效果较差 - 过于宽泛
results = client.find("auth")
```

### 限定搜索范围

```python
# 在相关范围内搜索以获得更好的结果
results = client.find(
    "error handling",
    target_uri="viking://resources/my-project/"
)
```

### 在对话中使用会话上下文

```python
# 对于对话式搜索，使用会话
from openviking.message import TextPart

session = client.session()
session.add_message("user", [
    TextPart(text="I'm building a login page")
])

# 搜索能够理解上下文
results = client.search("best practices", session=session)
```

### 相关文档

- [资源](02-resources.md) - 资源管理
- [会话](05-sessions.md) - 会话上下文
- [上下文层级](../concepts/03-context-layers.md) - L0/L1/L2
