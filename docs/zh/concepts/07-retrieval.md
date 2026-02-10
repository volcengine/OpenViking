# 检索机制

OpenViking 采用两阶段检索：意图分析 + 层级检索 + Rerank。

## 概览

```
查询 → 意图分析 → 层级检索 → Rerank → 结果
         ↓           ↓          ↓
     TypedQuery  目录递归    精排评分
```

## find() vs search()

| 特性 | find() | search() |
|------|--------|----------|
| 会话上下文 | 不需要 | 需要 |
| 意图分析 | 不使用 | 使用 LLM 分析 |
| 查询数量 | 单一查询 | 0-5 个 TypedQuery |
| 延迟 | 低 | 较高 |
| 适用场景 | 简单查询 | 复杂任务 |

### 使用示例

```python
# find(): 简单查询
results = await client.find(
    "OAuth 认证",
    target_uri="viking://resources/"
)

# search(): 复杂任务（需要会话上下文）
results = await client.search(
    "帮我创建一个 RFC 文档",
    session_info=session
)
```

## 意图分析

IntentAnalyzer 使用 LLM 分析查询意图，生成 0-5 个 TypedQuery。

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

HierarchicalRetriever 使用优先队列递归搜索目录结构。

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
| MEMORY | `viking://user/memories`, `viking://agent/memories` |
| RESOURCE | `viking://resources` |
| SKILL | `viking://agent/skills` |

### 递归搜索算法

```python
while dir_queue:
    current_uri, parent_score = heapq.heappop(dir_queue)

    # 搜索子节点
    results = await search(parent_uri=current_uri)

    for r in results:
        # 分数传播
        final_score = 0.5 * embedding_score + 0.5 * parent_score

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
| `SCORE_PROPAGATION_ALPHA` | 0.5 | 50% embedding + 50% parent |
| `MAX_CONVERGENCE_ROUNDS` | 3 | 收敛检测轮数 |
| `GLOBAL_SEARCH_TOPK` | 3 | 全局搜索候选数 |
| `MAX_RELATIONS` | 5 | 每资源最大关联数 |

## Rerank 策略

Rerank 在 THINKING 模式下对候选结果精排。

### 触发条件

- 配置了 Rerank AK/SK
- 使用 THINKING 模式（search() 默认）

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

| 后端 | 模型 |
|------|------|
| Volcengine | doubao-seed-rerank |

## 检索结果

### MatchedContext

```python
@dataclass
class MatchedContext:
    uri: str                # 资源 URI
    context_type: ContextType
    is_leaf: bool           # 是否文件
    abstract: str           # L0 摘要
    score: float            # 最终分数
    relations: List[RelatedContext]  # 关联上下文
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
