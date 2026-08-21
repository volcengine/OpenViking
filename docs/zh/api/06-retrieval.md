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
| query | str | 否 | "" | 搜索查询字符串；未提供 `image_url` 时必填 |
| image_url | str | 否 | None | 图片查询，支持 `data:image/...;base64,...`、`http(s)://` 或 `viking://` URI；需要 multimodal embedding 模型 |
| target_uri | str \| List[str] | 否 | "" | 限制搜索范围到指定的 URI 前缀 |
| context_type | str \| List[str] | 否 | None | 限定一个或多个 `ContextType` 取值：`memory`、`resource` 或 `skill` |
| tags | List[str] | 否 | None | 显式检索标签，必须是严格的 `k=v` 格式。多个 tags 之间是 AND 关系，结果必须同时包含所有请求的标签 |
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

**目标解析说明**：
- `target_uri` 为空时，非 ROOT 检索默认搜索当前用户根 `viking://user/{user}` 和公共 `viking://resources`。
- 如需在文件系统和检索操作中把当前用户的 peer 集合过滤到某一个 peer，发送 `X-OpenViking-Actor-Peer: <peer_id>`，或用 SDK/CLI client 的 `actor_peer_id` 初始化。见 [多租户：Peer 集合过滤](../concepts/11-multi-tenant.md#peer-restricted-view)。
- `viking://user/memories`、`viking://user/resources`、`viking://user/skills` 等当前用户短写 target URI 会按认证请求身份 canonicalize。

**图片搜索说明**：
- 图片查询会以图片向量作为 query，默认检索目标范围内的 L2 resource 叶子节点；结果不限于图片文件，图片与文本/图片资源的相似度由 multimodal embedding 模型决定。
- 纯文本 embedding 模型仍会索引图片 summary，但会拒绝图片查询输入。
- 已有图片资源保持现有向量不变；图片向量召回只作用于开启该能力后向量化的图片，或之后手动 reindex 的图片。

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

**按 Context Type 搜索**

```bash
curl -X POST http://localhost:1933/api/v1/search/find \
    -H "Content-Type: application/json" \
    -H "X-API-Key: your-key" \
    -d '{
        "query": "authentication",
        "context_type": ["memory", "resource"]
}'
```

**图片搜索**

```bash
curl -X POST http://localhost:1933/api/v1/search/find \
    -H "Content-Type: application/json" \
    -H "X-API-Key: your-key" \
    -d '{
        "image_url": "viking://resources/images/cat.png",
        "limit": 10
    }'
```

**按显式检索标签搜索**

```bash
curl -X POST http://localhost:1933/api/v1/search/find \
    -H "Content-Type: application/json" \
    -H "X-API-Key: your-key" \
    -d '{
        "query": "rollback runbook",
        "tags": ["env=prod", "team=search"]
    }'
```

Tags 必须使用严格的 `k=v` 字符串。传入多个 tags 时，`find()` 会要求全部命中；上面的例子只返回显式检索标签同时包含 `env=prod` 和 `team=search` 的上下文。

**Python SDK**

```python
import openviking as ov
from openviking.retrieve import ContextType

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

# 仅搜索 memories 和 resources
typed_results = client.find(
    "authentication",
    context_type=[ContextType.MEMORY, ContextType.RESOURCE],
)

# 按本地图片、bytes、data URI、HTTP URL 或 viking:// URI 搜索
image_results = client.find(image="/path/to/photo.png")

# 按显式检索标签搜索。多个 tags 之间是 AND 关系。
tagged_results = client.find(
    "rollback runbook",
    tags=["env=prod", "team=search"],
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

# 仅在当前用户资源中搜索
results = client.find(
    "private docs",
    target_uri="viking://user/resources"
)

# 检索时把 peer 集合过滤到一个 peer
peer_client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="your-key",
    actor_peer_id="web-visitor-alice",
)
peer_results = peer_client.find("invoice follow-up")

# 仅在技能中搜索
results = client.find(
    "web search",
    target_uri="viking://user/skills"
)

# 在特定项目中搜索
results = client.find(
    "API endpoints",
    target_uri="viking://resources/my-project"
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
# 基础搜索
openviking find "how to authenticate users"

# 指定 URI 范围
openviking find "how to authenticate users" --uri "viking://resources"

# 限定上下文类型
openviking find "authentication" --context-type memory,resource

# 带时间过滤
openviking find "invoice" --after 7d

# 带限制数量
openviking find "how to authenticate users" --limit 20

# 限定层级范围 (仅 L0)
openviking find "how to authenticate users" --level 0

# 限定层级范围 (L1 和 L2)，使用短选项
openviking find "how to authenticate users" -L 1,2

# 图片查询统一使用 --image；可传本地路径、viking://、http(s):// 或 data:image URI
openviking find --image ./query.png --uri "viking://resources/images" --limit 5

# 使用已入库图片搜索
openviking find --image "viking://resources/images/cat.png" --uri "viking://resources/images" --limit 5

# 使用公网图片 URL 搜索
openviking find --image "https://example.com/images/cat.png" --uri "viking://resources/images" --limit 5

# 图文联合检索
openviking find "红色海报风格" --image ./poster.png --uri "viking://resources/images"
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
| query | str | 否 | "" | 搜索查询字符串；未提供 `image_url` 时必填 |
| image_url | str | 否 | None | 图片查询，支持 `data:image/...;base64,...`、`http(s)://` 或 `viking://` URI；需要 multimodal embedding 模型 |
| target_uri | str \| List[str] | 否 | "" | 限制搜索范围到指定的 URI 前缀 |
| session | Session | 否 | None | 用于上下文感知搜索的会话（SDK）|
| session_id | str | 否 | None | 用于上下文感知搜索的会话 ID（HTTP）|
| context_type | str \| List[str] | 否 | None | 限定一个或多个 `ContextType` 取值：`memory`、`resource` 或 `skill` |
| tags | List[str] | 否 | None | 显式检索标签，必须是严格的 `k=v` 格式。多个 tags 之间是 AND 关系，结果必须同时包含所有请求的标签 |
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

`search()` 使用和 `find()` 相同的目标解析和显式标签过滤规则，包括由 `X-OpenViking-Actor-Peer` 或 SDK `actor_peer_id` 选择的 peer 集合过滤。提供 `image_url` 时，`search()` 会直接执行图片检索并跳过会话 query planning。

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
        "context_type": "skill",
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

**图片搜索**

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
    context_type=ContextType.SKILL,
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

**图片搜索**

```python
results = client.search("similar poster", image="/path/to/poster.png")
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
# 带会话 ID 的搜索
openviking search "best practices" --session-id abc123

# 限定上下文类型
openviking search "best practices" --context-type skill

# 带时间过滤的搜索
openviking search "watch vs scheduled" --after 2026-03-15 --before 2026-03-20

# 不带会话的搜索（仍进行意图分析）
openviking search "how to implement OAuth 2.0 authorization code flow"

# 限定层级范围（仅 L0）
openviking search "best practices" --level 0

# 限定层级范围（L1 和 L2），使用短选项
openviking search "how to implement OAuth" -L 1,2

# 图片查询同样使用 --image；会直接检索并跳过 session planning
openviking search "similar poster" --image ./poster.png --uri "viking://resources/images"
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

把检索结果直接组装成可注入的上下文块。`mode="list"`（默认）返回排序命中列表，行为与旧版 `search()` 完全一致；`mode="context"` 打开组装面：预算控制、档位降级、跨轮去重和可选的 LLM 摘要都在服务端一次请求内完成。

#### 1. API 实现介绍

Agent 插件每轮注入上下文时，过去需要按类型逐个检索、再逐条回读全文，在客户端拼装。组装收敛到服务端后，插件只发一次请求，所有 Harness 插件共享同一套预算、降级与去重实现。

**处理流程**：
1. **L1 查询理解**：可选，结合 Session 最近消息做有界意图扩展（最多 3 条查询，超时熔断，失败回退原查询）
2. **L0 检索**：按 `quotas` 分桶独立检索，或不设配额时全域检索一次
3. **L2 组装**：token 预算内填充档位（全员先落到各自类别的默认档，再用剩余预算按分数序加深），超限退档不截断
4. **L3 重写**：可选，把组装结果压成带 URI 引用的 digest（超时熔断，失败仍返回未重写的 `rendered`；精确返回 `NO_RELEVANT_MEMORY` 时记为 `stats.rewrite="no_relevant"`，Coding Agent 客户端不会再回退注入 `rendered`）

**代码入口**：
- `openviking/server/routers/search.py:_search_context()` - HTTP 路由分支
- `openviking/retrieve/context_assembler/pipeline.py:assemble_context()` - 组装编排
- `openviking/retrieve/context_assembler/budget.py:plan_entries()` - 预算与档位填充
- `openviking/retrieve/context_assembler/tiers.py` - 各来源类型的概览档提取

#### 2. 接口和参数说明

**L0 检索域**：`query`、`image_url`、`context_type`、`limit`、`score_threshold`、`filter`、`tags`、`since`/`until` 与 list 模式一致。`limit` 只约束 quota-free 检索；一旦 `purpose` 或显式 `quotas` 启用分桶检索，各分类配额就是唯一候选上限。`target_uri` 在 context 模式下暂不支持（返回 400）；`level` 被忽略，档位由 `detail` 决定。

**L1 查询理解**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `session_id` | str | None | 提供后才能启用查询扩展与服务端去重 |
| `query_expansion` | `off` \| `auto` | `auto` | `auto` 时结合 Session 做有界扩展；无 session 或失败时自动回退为原查询 |

**L2 组装**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `limit` | int | 10 | 仅作为 quota-free 检索的候选条目上限；`purpose` 或 `quotas` 启用分桶后被忽略 |
| `max_tokens` | int | 1600 | 唯一的预算参数，采用感知 CJK 的启发式估算（codepoint ≥ 0x3000 记 1.5 token/字，其余按 chars/4） |
| `quotas` | object | None | 各桶绝对条数上限；键取 `events`/`entities`/`preferences`/`experiences`/`resources`/`skills`。显式传入后忽略 `limit` |
| `purpose` | `chat` \| `coding` | None | 按下表的绝对分类配额启用六域分桶采样；仅在未显式传 `quotas` 时生效 |
| `detail` | `abstract` \| `overview` \| `full` \| object | None | 为每条结果请求同一个起始档和最高档；请求档不可用或装不进预算时仍逐档退档而不截断。省略时按类别取默认档（见下）。也可传按类别的对象，如 `{"events":"overview","preferences":"abstract"}`，未列出的类别仍取默认档。`"auto"` 是已废弃的写法，等价于省略 |
| `dedup_turns` | int | 0 | 跨轮冷却轮数，需要 `session_id`；账本存在 `{session_uri}/.recall_log.json` |
| `exclude_uris` | string[] | [] | 无状态去重兜底，最多 200 条，与 `dedup_turns` 取并集 |
| `peer_scope` | `actor` \| `all` | `all` | `actor` 排除其他 peer，但仍保留全局、User 自有和当前 Actor Peer 内容 |
| `other_peer_penalty` | number \| object | 按类型默认值 | 对其他 peer 结果施加的分数折损 |

**L3 重写**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `rewrite` | bool \| `auto` | `false` | 服务端 digest 重写；`auto` 时仅在配置了 query_planner 模型时启用 |
| `rewrite_max_bullets` | int | 6 | digest 条数上限（1–20） |

**档位规则**

- **Purpose 预设**：`chat` 使用 `events:3, entities:3, preferences:1, experiences:1, resources:1, skills:1`；`coding` 使用 `events:1, entities:2, preferences:1, experiences:1, resources:3, skills:2`。这些值是每个分类的绝对上限，不是权重。各桶结果汇总后仍会去重并全局排序，但不会再被第二个全局 `limit` 截断
- **按类别的默认档**：省略 `detail` 时，各类别落在下表的档位；只有 `events` 会因此读文件，其余类别零 I/O

  | 类别 | 默认档 | 剩余预算可加深到 | 原因 |
  |------|--------|------------------|------|
  | `events` | 概览档 | 全文档 | 唯一正文足够长、`# Summary` 抽取能真正压缩的类型 |
  | `entities` / `preferences` / `experiences` | 摘要档 | 摘要档 | 正文本身很短，且写入侧把整篇正文存进了摘要标量，摘要档即完整内容 |
  | `resources` / `skills` | 摘要档 | 摘要档 | 语义处理生成的 256 字符摘要；正文可能很大或含凭据，加深需显式指定 |
  | `memories` | 摘要档 | 摘要档 | 四个具名类型之外的内置记忆类型——`cases`、`patterns`、`tools`、`trajectories`、技能使用记忆。只有 quota-free 检索会命中它们；它们没有自己的检索桶，`quotas` 不能指定，但 `detail` 和 `other_peer_penalty` 可以 |
  | 目录命中 | 概览档 | 概览档 | 目录没有摘要，读 `.overview.md` 侧车；全文档对目录无意义 |

- **保底**：每条结果至少给出 `uri`。记忆类摘要缺失或超出单条上限时回落到概览档：写入侧把整篇正文存进了摘要标量，所以对记忆类别而言概览档在内容阶梯上位于摘要档*之下*，这次替换披露得更少。而 `resources` / `skills` 的摘要是语义处理生成的短摘要，同样的替换会去读调用方没有请求的正文，因此这两类直接退成裸 `uri`，不向上加深
- **显式 `detail`**：把该档作为全部结果请求的起点和上限；装不下的条目仍逐档退档而不截断。上述记忆类概览档替换是实际档位唯一可能高于指定档的情况，且仅因为它比指定档携带的内容更少
- **概览档按来源取骨架**：记忆文件取开头的 `# Summary` 段，代码文件取函数与类签名（复用 `code_outline`），长文档取标题树加首段
- **单条上限**：`max_tokens ÷ 候选条数 × 2`，对除裸 `uri` 外的所有档位一律生效；某一档超出该上限时退回上一档，不做截断。预算仍有剩余时，最后一轮加深不受该上限约束，只受 `max_tokens` 约束

#### 3. 使用示例

**HTTP API**

```bash
# 基础上下文组装
curl -X POST http://localhost:1933/api/v1/search/search \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENVIKING_API_KEY" \
  -d '{"query":"这个分支改了什么","mode":"context","max_tokens":1600}'

# 会话感知：查询扩展 + 跨轮去重
curl -X POST http://localhost:1933/api/v1/search/search \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENVIKING_API_KEY" \
  -d '{
    "query":"继续刚才的重构",
    "mode":"context",
    "session_id":"cc-1a2b3c",
    "query_expansion":"auto",
    "dedup_turns":5,
    "purpose":"coding",
    "max_tokens":3000
  }'

# 开启服务端 digest 重写
curl -X POST http://localhost:1933/api/v1/search/search \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENVIKING_API_KEY" \
  -d '{"query":"档位设计","mode":"context","max_tokens":3000,"rewrite":true}'
```

**响应**

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
        "text": "# Summary\n档位模型改为按类别定义默认档\n...",
        "origin": "self"
      },
      {
        "uri": "viking://user/default/memories/entities/software/openviking_fs.md",
        "category": "entities",
        "score": 0.43,
        "detail": "abstract",
        "text": "OpenViking FS 存储层……",
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

| 字段 | 类型 | 说明 |
|------|------|------|
| `entries[].uri` | string | 条目 URI，任何档位都必然存在，可用 MCP `read` 下钻 |
| `entries[].category` | string | `events`/`entities`/`preferences`/`experiences`/`resources`/`skills`，或 `memories`（四个具名类型之外的内置记忆类型） |
| `entries[].detail` | string | 实际档位：`full`、`overview`、`abstract` 或 `uri` |
| `entries[].text` | string | 该档位的正文；`uri` 档为空 |
| `rendered` | string | 扁平 XML 上下文块，可直接注入；重写返回 `no_relevant` 时为空 |
| `digest` | string | 重写成功时的摘要；失败或压缩器判定无相关记忆时为空字符串 |
| `stats` | object | 预算用量、档位分布、扩展与重写状态（`off`、`ok`、`no_relevant`、`failed` 或 `timeout`）、去重账本状态；某个检索域失败时附带 `retrieval_errors`，用于区分「检索坏了」和「确实没有相关记忆」 |

当 `stats.rewrite` 为 `no_relevant` 时，响应仍保留 `entries` 供检查，但 `digest` 和
`rendered` 都为空字符串。这样即使客户端尚未识别新状态，也不会回退注入原文。本轮
没有交付任何内容，因此这些 URI 也不会进入 `dedup_turns` 账本，之后真正相关的那一轮
仍能召回它们。

**校验规则**

- `mode="list"` 下显式携带任何 context 专用参数 → 400
- `mode="context"` 下传 `target_uri` → 400
- `quotas` 出现未知键 → 400
- context 模式下被忽略的字段（`level`、`purpose` 或显式配额生效时的 `limit`）会记录在 `stats.ignored`

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
| node_limit | int | 否 | 256 | 最大返回节点数。省略时默认使用 256；如需更多结果，请显式传入更大的整数 |
| exclude_uri | str | 否 | None | 要排除在搜索之外的 URI 前缀 |
| level_limit | int | 否 | Python SDK: 5；HTTP API / CLI / Go SDK: 10 | 最大目录遍历深度。Go SDK 当前使用 HTTP API 默认值。 |

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
# 基础搜索
openviking grep "authentication" --uri viking://resources

# 忽略大小写
openviking grep "authentication" --uri viking://resources --ignore-case

# 指定深度限制
openviking grep "TODO" --uri viking://resources --level-limit 3
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
- `sdk/python/openviking_sdk/client.py:SyncHTTPClient.glob()` - Python SDK 入口（HTTP）
- `openviking/server/routers/search.py:glob()` - HTTP 路由
- `crates/ov_cli/src/commands/search.rs:glob()` - Rust CLI 命令

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| pattern | str | 是 | - | Glob 模式（例如 `**/*.md`）|
| uri | str | 否 | "viking://" | 起始 URI |
| node_limit | int | 否 | 256 | 最大返回匹配数。省略时默认使用 256；如需更多结果，请显式传入更大的整数 |

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

# 查找所有 markdown 文件（默认最多返回 256 条）
results = client.glob("**/*.md", "viking://resources")
print(f"Found {results['count']} markdown files:")
for uri in results['matches']:
    print(f"  {uri}")

# 查找所有 Python 文件，并显式放宽返回上限
results = client.glob("**/*.py", "viking://resources", node_limit=1024)
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
