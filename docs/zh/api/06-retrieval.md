# 检索

OpenViking 提供多种检索方法，包括简单的向量相似度搜索、带会话上下文的智能检索、正则表达式匹配搜索和文件模式匹配。

## find 与 search 对比

| 方面 | find | search |
|------|------|--------|
| 意图分析 | 否 | 是 |
| 会话上下文 | 否 | 是 |
| 查询扩展 | 否 | 是 |
| 默认结果数 | 10 | 10 |
| 使用场景 | 简单查询 | 对话式搜索 |

## 检索流程

检索的核心流程如下：

```
查询 → 意图分析（仅search）→ 向量搜索（L0）→ 重排序（L1）→ 结果
```

1. **意图分析**（仅 search）：理解查询意图，扩展查询
2. **向量搜索**：使用 Embedding 查找候选项
3. **重排序**：使用内容重新评分以提高准确性
4. **结果**：返回 top-k 上下文

## API 参考

### find()

基本向量相似度搜索，无需会话上下文。

#### 1. API 实现介绍

`find()` 方法执行纯向量相似度搜索，适用于简单的查询场景。它使用分层检索器（HierarchicalRetriever）在 L0 摘要层进行初步搜索，然后在 L1/L2 层进行详细匹配。

**处理流程**：
1. 将查询文本转换为向量
2. 在指定的目标 URI 范围内执行全局向量搜索
3. 使用分层检索策略递归搜索相关目录和文件
4. 可选：使用重排序模型优化结果排序
5. 返回匹配的上下文列表

**代码入口**：
- `openviking_cli/client/sync_http.py:SyncHTTPClient.find()` - Python SDK 入口（HTTP）
- `openviking/retrieve/hierarchical_retriever.py:HierarchicalRetriever.retrieve()` - 核心检索实现
- `openviking/server/routers/search.py:find()` - HTTP 路由
- `crates/ov_cli/src/commands/search.rs:find()` - Rust CLI 命令

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| query | str | 是 | - | 搜索查询字符串 |
| target_uri | str \| List[str] | 否 | "" | 限制搜索范围到指定的 URI 前缀 |
| limit | int | 否 | 10 | 最大返回结果数 |
| node_limit | int | 否 | None | 可选 HTTP 别名；如果提供，会覆盖 limit |
| score_threshold | float | 否 | None | 最低相关性分数阈值 |
| filter | Dict | 否 | None | 元数据过滤器 |
| since | str | 否 | None | 时间下界，支持 `2h` 或 ISO 8601 / `YYYY-MM-DD`。不带时区的值按 UTC 解释。CLI `--after` 会映射到这个字段 |
| until | str | 否 | None | 时间上界，支持 `30m` 或 ISO 8601 / `YYYY-MM-DD`。不带时区的值按 UTC 解释。CLI `--before` 会映射到这个字段 |
| time_field | "updated_at" \| "created_at" | 否 | "updated_at" | since/until 使用的元数据时间字段 |
| level | str | 否 | None | 限定结果的层级范围，例如 `0`、`1`、`2` 或 `0,1,2`。CLI `--level`/`-L` 会映射到这个字段 |
| include_provenance | bool | 否 | False | 在序列化结果中附带 provenance / query-plan 细节 |
| telemetry | bool \| object | 否 | False | 在响应中附带遥测数据 |

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
    level: int                       # 层级 (0=L0, 1=L1, 2=L2)
    abstract: str                    # L0 内容
    overview: Optional[str]          # L1 概览（非叶子节点时可选）
    category: str                    # 分类
    score: float                     # 相关性分数 (0-1)
    match_reason: str                # 匹配原因
    relations: List[RelatedContext]  # 关联上下文
```

#### 3. 使用示例

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

**使用 Target URI 和时间过滤**

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

# 基础搜索
results = client.find("how to authenticate users")

# 带过滤和时间范围的搜索
recent_emails = client.find(
    "invoice",
    target_uri="viking://resources/email",
    since="7d",
    time_field="created_at",
)

# 遍历结果
for ctx in results.resources:
    print(f"URI: {ctx.uri}")
    print(f"Score: {ctx.score:.3f}")
    print(f"Type: {ctx.context_type}")
    print(f"Abstract: {ctx.abstract[:100]}...")
    print("---")
```

**使用 Target URI 限定搜索范围**

```python
# 仅在资源中搜索
results = client.find(
    "authentication",
    target_uri="viking://resources"
)

# 仅在用户记忆中搜索
results = client.find(
    "preferences",
    target_uri="viking://user/memories"
)

# 仅在技能中搜索
results = client.find(
    "web search",
    target_uri="viking://agent/skills"
)

# 在特定项目中搜索
results = client.find(
    "API endpoints",
    target_uri="viking://resources/my-project"
)
```

**CLI**

```bash
# 基础搜索
openviking find "how to authenticate users"

# 指定 URI 范围
openviking find "how to authenticate users" --uri "viking://resources"

# 带时间过滤
openviking find "invoice" --after 7d

# 带限制数量
openviking find "how to authenticate users" --limit 20

# 限定层级范围 (仅 L0)
openviking find "how to authenticate users" --level 0

# 限定层级范围 (L1 和 L2)，使用短选项
openviking find "how to authenticate users" -L 1,2
```

**响应示例**

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

带会话上下文和意图分析的智能检索。

#### 1. API 实现介绍

`search()` 方法在 `find()` 的基础上增加了会话上下文理解和意图分析能力。它可以根据历史对话更好地理解用户查询意图，执行查询扩展，提供更相关的搜索结果。

**处理流程**：
1. 加载会话上下文（如果提供了 session_id）
2. 分析查询意图，结合对话历史理解真实需求
3. 扩展查询以提高召回率
4. 执行与 `find()` 相同的分层检索流程
5. 返回带查询计划的搜索结果

**代码入口**：
- `openviking_cli/client/sync_http.py:SyncHTTPClient.search()` - Python SDK 入口（HTTP）
- `openviking/retrieve/hierarchical_retriever.py:HierarchicalRetriever.retrieve()` - 核心检索实现
- `openviking/server/routers/search.py:search()` - HTTP 路由
- `crates/ov_cli/src/commands/search.rs:search()` - Rust CLI 命令

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| query | str | 是 | - | 搜索查询字符串 |
| target_uri | str \| List[str] | 否 | "" | 限制搜索范围到指定的 URI 前缀 |
| session | Session | 否 | None | 用于上下文感知搜索的会话（SDK）|
| session_id | str | 否 | None | 用于上下文感知搜索的会话 ID（HTTP）|
| limit | int | 否 | 10 | 最大返回结果数 |
| node_limit | int | 否 | None | 可选 HTTP 别名；如果提供，会覆盖 limit |
| score_threshold | float | 否 | None | 最低相关性分数阈值 |
| filter | Dict | 否 | None | 元数据过滤器 |
| since | str | 否 | None | 时间下界，支持 `2h` 或 ISO 8601 / `YYYY-MM-DD`。不带时区的值按 UTC 解释。CLI `--after` 会映射到这个字段 |
| until | str | 否 | None | 时间上界，支持 `30m` 或 ISO 8601 / `YYYY-MM-DD`。不带时区的值按 UTC 解释。CLI `--before` 会映射到这个字段 |
| time_field | "updated_at" \| "created_at" | 否 | "updated_at" | since/until 使用的元数据时间字段 |
| level | str | 否 | None | 限定结果的层级范围，例如 `0`、`1`、`2` 或 `0,1,2`。CLI `--level`/`-L` 会映射到这个字段 |
| include_provenance | bool | 否 | False | 在序列化结果中附带 provenance / query-plan 细节 |
| telemetry | bool \| object | 否 | False | 在响应中附带遥测数据 |

#### 3. 使用示例

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

**不带会话的搜索（仍会进行意图分析）**

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
    session=session,
    since="2h"
)

for ctx in results.resources:
    print(f"Found: {ctx.uri}")
    print(f"Abstract: {ctx.abstract[:200]}...")
```

**不使用会话的搜索**

```python
# search 也可以在没有会话的情况下使用
# 它仍然会对查询进行意图分析
results = client.search(
    "how to implement OAuth 2.0 authorization code flow"
)

for ctx in results.resources:
    print(f"Found: {ctx.uri} (score: {ctx.score:.3f})")
```

**CLI**

```bash
# 带会话 ID 的搜索
openviking search "best practices" --session-id abc123

# 带时间过滤的搜索
openviking search "watch vs scheduled" --after 2026-03-15 --before 2026-03-20

# 不带会话的搜索（仍进行意图分析）
openviking search "how to implement OAuth 2.0 authorization code flow"

# 限定层级范围（仅 L0）
openviking search "best practices" --level 0

# 限定层级范围（L1 和 L2），使用短选项
openviking search "how to implement OAuth" -L 1,2
```

**响应示例**

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

通过模式（正则表达式）搜索内容。

#### 1. API 实现介绍

`grep()` 方法在文件系统中执行正则表达式匹配搜索，用于查找包含特定模式的文件和内容行。与语义搜索不同，grep 是精确的模式匹配。

**处理流程**：
1. 从指定 URI 开始遍历文件系统
2. 对每个文件内容进行正则表达式匹配
3. 收集匹配的行和位置信息
4. 返回匹配结果列表

**代码入口**：
- `openviking_cli/client/sync_http.py:SyncHTTPClient.grep()` - Python SDK 入口（HTTP）
- `openviking/server/routers/search.py:grep()` - HTTP 路由
- `crates/ov_cli/src/commands/search.rs:grep()` - Rust CLI 命令

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | 要搜索的 Viking URI |
| pattern | str | 是 | - | 搜索模式（正则表达式）|
| case_insensitive | bool | 否 | False | 忽略大小写 |
| node_limit | int | 否 | None | 最大返回节点数 |
| exclude_uri | str | 否 | None | 要排除在搜索之外的 URI 前缀 |
| level_limit | int | 否 | 5 | 最大目录遍历深度 |

#### 3. 使用示例

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
# 基础搜索
openviking grep viking://resources "authentication"

# 忽略大小写
openviking grep viking://resources "authentication" --ignore-case

# 指定深度限制
openviking grep viking://resources "TODO" --level-limit 3
```

**响应示例**

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

#### 1. API 实现介绍

`glob()` 方法使用文件通配符模式匹配 URI，类似于 Unix shell 的 glob 功能。用于按名称模式查找文件和目录。

**支持的模式语法**：
- `*` 匹配任意字符（除路径分隔符）
- `**` 递归匹配任意目录
- `?` 匹配单个字符
- `[]` 匹配字符范围

**代码入口**：
- `openviking_cli/client/sync_http.py:SyncHTTPClient.glob()` - Python SDK 入口（HTTP）
- `openviking/server/routers/search.py:glob()` - HTTP 路由
- `crates/ov_cli/src/commands/search.rs:glob()` - Rust CLI 命令

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| pattern | str | 是 | - | Glob 模式（例如 `**/*.md`）|
| uri | str | 否 | "viking://" | 起始 URI |
| node_limit | int | 否 | None | 最大返回匹配数 |

#### 3. 使用示例

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

# 查找所有 markdown 文件
results = client.glob("**/*.md", "viking://resources")
print(f"Found {results['count']} markdown files:")
for uri in results['matches']:
    print(f"  {uri}")

# 查找所有 Python 文件
results = client.glob("**/*.py", "viking://resources")
print(f"Found {results['count']} Python files")
```

**CLI**

```bash
# 查找所有 markdown 文件
openviking glob "**/*.md" --uri viking://resources

# 查找所有 Python 文件
openviking glob "**/*.py"
```

**响应示例**

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

## 处理结果

### 渐进式读取内容

检索结果通常只包含 L0 摘要，你可以根据需要渐进式加载更多详细内容。

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

results = client.find("authentication")

for ctx in results.resources:
    # 从 L0（摘要）开始 - 已包含在 ctx.abstract 中
    print(f"Abstract: {ctx.abstract}")

    if ctx.level < 2:
        # 获取 L1（概览）用于目录
        overview = client.overview(ctx.uri)
        print(f"Overview: {overview[:500]}...")
    else:
        # 加载 L2（内容）用于文件
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
curl -X GET "http://localhost:1933/api/v1/content/overview?uri=viking://resources/docs/auth" \
    -H "X-API-Key: your-key"

# 步骤 3：读取文件结果的完整内容
curl -X GET "http://localhost:1933/api/v1/content/read?uri=viking://resources/docs/auth.md" \
    -H "X-API-Key: your-key"
```

### 获取关联资源

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

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
curl -X GET "http://localhost:1933/api/v1/relations?uri=viking://resources/docs/auth" \
    -H "X-API-Key: your-key"
```

## 最佳实践

### 使用具体的查询

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# 好 - 具体的查询
results = client.find("OAuth 2.0 authorization code flow implementation")

# 效果较差 - 过于宽泛
results = client.find("auth")
```

### 限定搜索范围

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# 在相关范围内搜索以获得更好的结果
results = client.find(
    "error handling",
    target_uri="viking://resources/my-project"
)
```

### 在对话中使用会话上下文

```python
import openviking as ov
from openviking.message import TextPart

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# 对于对话式搜索，使用会话
session = client.session()
session.add_message("user", [
    TextPart(text="I'm building a login page")
])

# 搜索能够理解上下文
results = client.search("best practices", session=session)
```

## 相关文档

- [资源](02-resources.md) - 资源管理
- [会话](05-sessions.md) - 会话上下文
- [上下文层级](../concepts/03-context-layers.md) - L0/L1/L2
