# 检索机制

OpenViking 使用全局向量检索，并可在召回完成后对候选结果执行一次 Rerank。会话意图分析是独立的前置阶段。

## 概览

```
查询 → 意图分析（可选）→ 全局向量检索 → Rerank（可选）→ 结果
```

## find() vs search()

| 特性 | find() | search() |
|------|--------|----------|
| 会话上下文 | 不使用 | 可选，传入 `session_id` 时使用 |
| 意图分析 | 不使用 | 有会话内容且启用时使用 LLM |
| 查询数量 | 单一查询 | 零个或多个 TypedQuery |
| 延迟 | 通常较低 | 取决于意图分析、查询数量和是否 Rerank |
| 适用场景 | 简单查询 | 复杂任务 |

`search` 的 `limit` 用于每条规划查询；多条查询合并后的总数可能超过它，当前也不保证跨查询去重。

### 使用示例

以下示例使用已配置的同步 Python SDK 客户端 `client`。

```python
# find(): 简单查询
results = client.find(
    query="OAuth 认证",
    target_uri="viking://resources/",
)

# search(): 复杂任务（需要会话上下文）
session_info = client.create_session()
client.add_message(
    session_id=session_info["session_id"], role="user",
    content="我们正在为项目设计 OAuth 登录流程。",
)
results = client.search(
    query="帮我创建一个 RFC 文档",
    session_id=session_info["session_id"],
)
```

## 意图分析

当 `retrieval.enable_intent=true` 且会话包含摘要或消息时，IntentAnalyzer 使用 LLM 分析查询意图，生成零个或多个 TypedQuery。该阶段使用的模型可通过 [`query_planner`](../guides/01-configuration.md#query-planner) 配置项单独指定，未设置时回退到 `vlm`。

### 输入

- 会话压缩摘要
- 最近 5 条消息
- 当前查询

### 输出

```python
@dataclass
class TypedQuery:
    query: str              # 重写后的查询
    context_type: ContextType  # MEMORY/RESOURCE/SKILL
    intent: str             # 查询目的
    priority: int           # 1-5 优先级
```

### 查询风格

| 类型 | 风格 | 示例 |
|------|------|------|
| **skill** | 动词开头 | "创建 RFC 文档"、"提取 PDF 表格" |
| **resource** | 名词短语 | "RFC 文档模板"、"API 使用指南" |
| **memory** | "用户XX" | "用户的代码规范偏好" |

### 特殊情况

- **0 个查询**：闲聊、问候等不需要检索的场景
- **多个查询**：复杂任务可能需要技能 + 资源 + 记忆

## 全局检索

`HierarchicalRetriever` 对每条查询执行一次全局向量检索。目标目录、上下文类型、权限、元数据过滤和 `level` 一起限定搜索范围。未指定 `level` 时，文本查询可直接命中 L0/L1/L2；不再先定位目录再逐层搜索。

### 检索模式

| 模式 | 向量候选数 | Rerank |
|------|------------|--------|
| QUICK | `limit` | 不执行 |
| THINKING，且配置了可用的 Rerank | `2 × limit` | 对召回候选统一执行一次，返回最多 `limit` 条 |
| THINKING，未配置可用的 Rerank | `limit` | 不执行 |

`find()` 使用 QUICK。`search()` 配置了可用的 Rerank 时自动使用 THINKING，否则使用 QUICK。这是内部检索模式，不是 LLM 的思考参数，也不控制会话意图分析。意图分析生成的多条查询仍分别检索和排序，再按现有方式汇总。

图像查询跳过文本 Rerank，候选数为 `limit`；未指定 `level` 时默认搜索 L2。

## Rerank 策略

Rerank 只处理本次全局召回的候选，不会触发下一轮检索。例如 `limit=10`，启用 Rerank 时先召回向量分数最高的 20 条，再按 Rerank 结果返回最多 10 条；未启用时直接召回 10 条。

- 使用候选索引记录的 `abstract` 字段作为 Rerank 文本。
- 分数与排序使用 Rerank 分数，否则使用向量分数；访问频次、更新时间和父目录分数不参与加权。
- 分数阈值在 Rerank 后应用；未启用 Rerank 时使用向量分数。
- Rerank 请求失败或返回无效结果时，保留已有的向量分数回退行为。
- 不再使用目录优先队列、父子分数传播或多轮收敛判断。

支持的 provider 和配置见 [Rerank 配置](../guides/01-configuration.md#rerank)。

## 检索结果

以下是服务端内部类型的字段节选；HTTP/SDK 响应字段以[检索 API](../api/06-retrieval.md)为准。

### MatchedContext

```python
@dataclass
class MatchedContext:
    uri: str                # 资源 URI
    context_type: ContextType
    level: int              # 0=摘要，1=概览，2=详情
    abstract: str           # L0 摘要
    score: float            # 最终分数
```

### FindResult

```python
@dataclass
class FindResult:
    memories: List[MatchedContext]
    resources: List[MatchedContext]
    skills: List[MatchedContext]
    query_plan: Optional[QueryPlan]      # search() 时有
    query_results: Optional[List[QueryResult]]
    total: int
```

## 相关文档

- [架构概述](./01-architecture.md) - 系统整体架构
- [存储架构](./05-storage.md) - 向量库索引
- [上下文层级](./03-context-layers.md) - L0/L1/L2 模型
- [上下文类型](./02-context-types.md) - 三种上下文类型
