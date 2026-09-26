# 检索机制

OpenViking 结合向量搜索和目录遍历检索上下文。`search()` 可先分析查询意图；配置 Rerank 后，可进一步调整候选排序。

## 概览

```text
find：查询 → QUICK 向量检索 → 结果
search：查询 + 可选会话 → 可选意图分析 → 每条查询检索 → 合并结果
                                      ├─ 未配置 Rerank / 图片查询：QUICK
                                      └─ 已配置 Rerank 的文本查询：THINKING 层级检索
```

## find() vs search()

| 特性 | find() | search() |
|------|--------|----------|
| 会话上下文 | 不使用 | 可选，传入 `session_id` 时使用 |
| 意图分析 | 不使用 | 有会话内容且启用时使用 LLM |
| 查询数量 | 单一查询 | 零个或多个 TypedQuery |
| 检索路径 | QUICK，不递归、不 Rerank、不加热度权重 | 由 Rerank 配置和查询类型决定 |
| 延迟 | 通常较低 | 取决于意图分析、查询数量和检索路径 |
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

## 层级检索

以下流程描述 THINKING 路径。QUICK 直接向量检索，不执行目录递归。THINKING 使用优先队列展开目录，并在候选评分时调用 Rerank。

### 流程

```
Step 1: 根据 context_type 确定根目录
        ↓
Step 2: 全局向量搜索定位起始目录
        ↓
Step 3: 合并起始点 + Rerank 评分
        ↓
Step 4: 递归搜索（优先队列）
        ↓
Step 5: 转换为 MatchedContext
```

### 根目录映射

| context_type | 根目录 |
|--------------|--------|
| MEMORY | 当前用户空间中的记忆；设置 `actor_peer_id` 时限于用户记忆和该 peer 的记忆 |
| RESOURCE | `viking://resources` 和当前用户空间中的资源；设置 `actor_peer_id` 时加入该 peer 的资源 |
| SKILL | `viking://~/skills` 与 `viking://agent/skills` |

显式 `target_uri` 优先于上述类型默认值。公开 API 不指定范围时，默认搜索当前用户空间和账户共享资源；需要共享技能时应显式加入 `viking://agent/skills`。权限和 peer 可见性仍然生效。

### 递归搜索算法

以下是流程示意，省略了队列优先级编码、去重和停止条件的实现细节。

```python
while dir_queue:
    current_uri, parent_score = heapq.heappop(dir_queue)

    # 搜索子节点
    results = await search(parent_uri=current_uri)

    for r in results:
        # 分数传播
        final_score = score_propagation_alpha * embedding_score + (1 - score_propagation_alpha) * parent_score

        if final_score > threshold:
            collected.append(r)

            if not r.is_leaf:  # 目录继续递归
                heapq.heappush(dir_queue, (r.uri, final_score))

    # 收敛检测
    if topk_unchanged_for_3_rounds:
        break
```

### 关键参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `retrieval.score_propagation_alpha` | 1.0 | 分数传播混合中子节点自身分数的权重；`1.0` 表示仅使用子节点自身分数，忽略父节点分数 |
| `MAX_CONVERGENCE_ROUNDS` | 3 | 收敛检测轮数 |
| `GLOBAL_SEARCH_TOPK` | 10 | 全局候选数的下限，实际至少为查询 `limit` |

## Rerank 策略

Rerank 在 THINKING 模式下对候选结果精排。

### 触发条件

- 配置了可用的 Rerank 模型及其认证信息
- 文本 `search()` 在配置 Rerank 后选择 THINKING；`find()` 和图片查询走 QUICK
- 如果 rerank 返回无效结果或 API 调用失败，会回退到向量分数

### 评分方式

```python
if rerank_client and mode == THINKING:
    scores = rerank_client.rerank_batch(query, documents)
else:
    scores = [r["_score"] for r in results]  # 向量分数
```

### 使用位置

1. **起始点评估**：评估全局搜索的候选目录
2. **递归搜索**：评估每层的子节点

### 后端支持

可配置的 provider 包括 `vikingdb`（Volcengine）、`cohere`、`openai`（兼容接口）、`litellm` 和 `jev`（`vikingdb` 的默认模型为 `doubao-seed-rerank`）。模型名与认证方式取决于 provider，详见[配置指南](../guides/01-configuration.md)。

## 检索结果

以下是服务端内部类型的字段节选；HTTP/SDK 响应字段以[检索 API](../api/06-retrieval.md)为准。

### MatchedContext

```python
@dataclass
class MatchedContext:
    uri: str                # 资源 URI
    context_type: ContextType
    is_leaf: bool           # 是否文件
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
