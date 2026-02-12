# 架构概述

OpenViking 是为 AI Agent 设计的上下文数据库，将所有上下文（Memory、Resource、Skill）统一抽象为目录结构，支持语义检索和渐进式内容加载。

## 系统概览

```
┌────────────────────────────────────────────────────────────────────────────┐
│                           OpenViking 系统架构                               │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│                              ┌─────────────┐                               │
│                              │   Client    │                               │
│                              │ (OpenViking)│                               │
│                              └──────┬──────┘                               │
│                                     │ 委托                                  │
│                              ┌──────▼──────┐                               │
│                              │   Service   │                               │
│                              │    Layer    │                               │
│                              └──────┬──────┘                               │
│                                     │                                      │
│           ┌─────────────────────────┼─────────────────────────┐            │
│           │                         │                         │            │
│           ▼                         ▼                         ▼            │
│    ┌─────────────┐          ┌─────────────┐          ┌─────────────┐      │
│    │  Retrieve   │          │   Session   │          │    Parse    │      │
│    │ (上下文检索) │          │  (会话管理)  │          │ (上下文提取) │      │
│    │             │          │             │          │             │      │
│    │ search/find │          │ add/used    │          │ 文档解析    │      │
│    │ 意图分析    │          │ commit      │          │ L0/L1/L2    │      │
│    │ Rerank     │          │ commit      │          │ 树构建      │      │
│    └──────┬──────┘          └──────┬──────┘          └──────┬──────┘      │
│           │                        │                        │             │
│           │                        │ 记忆提取               │             │
│           │                        ▼                        │             │
│           │                 ┌─────────────┐                 │             │
│           │                 │ Compressor  │                 │             │
│           │                 │ 压缩/去重    │                 │             │
│           │                 └──────┬──────┘                 │             │
│           │                        │                        │             │
│           └────────────────────────┼────────────────────────┘             │
│                                    ▼                                      │
│    ┌─────────────────────────────────────────────────────────────────┐    │
│    │                         Storage 层                               │    │
│    │              AGFS (文件内容)  +  向量库 (索引)                    │    │
│    └─────────────────────────────────────────────────────────────────┘    │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

## 核心模块

| 模块 | 职责 | 关键能力 |
|------|------|---------|
| **Client** | 统一入口 | 提供所有操作接口，委托给 Service 层 |
| **Service** | 业务逻辑 | FSService、SearchService、SessionService、ResourceService、RelationService、PackService、DebugService |
| **Retrieve** | 上下文检索 | 意图分析（IntentAnalyzer）、层级检索（HierarchicalRetriever）、Rerank 精排 |
| **Session** | 会话管理 | 消息记录、使用追踪、会话压缩、记忆提交 |
| **Parse** | 上下文提取 | 文档解析（PDF/MD/HTML）、树构建（TreeBuilder）、异步语义生成 |
| **Compressor** | 记忆压缩 | 6 种记忆分类提取、LLM 去重决策 |
| **Storage** | 存储层 | VikingFS 虚拟文件系统、向量索引、AGFS 集成 |

## Service 层

Service 层将业务逻辑与传输层解耦，便于 HTTP Server 和 CLI 复用：

| Service | 职责 | 主要方法 |
|---------|------|----------|
| **FSService** | 文件系统操作 | ls, mkdir, rm, mv, tree, stat, read, abstract, overview, grep, glob |
| **SearchService** | 语义搜索 | search, find |
| **SessionService** | 会话管理 | session, sessions, commit, delete |
| **ResourceService** | 资源导入 | add_resource, add_skill, wait_processed |
| **RelationService** | 关联管理 | relations, link, unlink |
| **PackService** | 导入导出 | export_ovpack, import_ovpack |
| **DebugService** | 调试服务 | observer (ObserverService) |

## 双层存储

OpenViking 采用双层存储架构，实现内容与索引分离（详见 [存储架构](./05-storage.md)）：

| 存储层 | 职责 | 内容 |
|--------|------|------|
| **AGFS** | 内容存储 | L0/L1/L2 完整内容、多媒体文件、关联关系 |
| **向量库** | 索引存储 | URI、向量、元数据（不存储文件内容） |

## 数据流概览

### 添加上下文

```
输入 → Parser → TreeBuilder → AGFS → SemanticQueue → 向量库
```

1. **Parser**：解析文档，创建文件和目录结构（无 LLM 调用）
2. **TreeBuilder**：移动临时目录到 AGFS，入队语义处理
3. **SemanticQueue**：异步自底向上生成 L0/L1
4. **向量库**：建立索引用于语义搜索

### 检索上下文

```
查询 → 意图分析 → 层级检索 → Rerank → 结果
```

1. **意图分析**：分析查询意图，生成 0-5 个类型化查询
2. **层级检索**：目录级递归搜索，使用优先队列
3. **Rerank**：标量过滤 + 模型重排
4. **结果**：返回按相关性排序的上下文

### 会话提交

```
消息 → 压缩 → 归档 → 记忆提取 → 存储
```

1. **消息**：累积对话消息和使用记录
2. **压缩**：保留最近 N 轮，旧消息归档
3. **归档**：生成历史片段的 L0/L1
4. **记忆提取**：从消息中提取 6 种分类记忆
5. **存储**：写入 AGFS + 向量库

## 部署模式

### 嵌入式模式

用于本地开发和单进程应用：

```python
client = OpenViking(path="./data")
```

- 自动启动 AGFS 子进程
- 使用本地向量索引
- 单例模式

### HTTP 模式

用于团队共享、生产环境和跨语言集成：

```python
# Python SDK 连接 OpenViking Server
client = SyncHTTPClient(url="http://localhost:1933", api_key="xxx")
```

```bash
# 或使用 curl / 任意 HTTP 客户端
curl http://localhost:1933/api/v1/search/find \
  -H "X-API-Key: xxx" \
  -d '{"query": "how to use openviking"}'
```

- Server 作为独立进程运行（`python -m openviking serve`）
- 客户端通过 HTTP API 连接
- 支持任何能发起 HTTP 请求的语言
- 参见 [服务部署](../guides/03-deployment.md) 了解配置方法

## 设计原则

| 原则 | 说明 |
|------|------|
| **存储层纯粹** | 存储层只做 AGFS 操作和基础向量搜索，Rerank 在检索层完成 |
| **三层信息** | L0/L1/L2 实现渐进式详情加载，节省 Token 消耗 |
| **两阶段检索** | 向量搜索召回候选 + Rerank 精排提高准确性 |
| **单一数据源** | 所有内容从 AGFS 读取，向量库仅存储引用和索引 |

## 相关文档

- [上下文类型](./02-context-types.md) - Resource/Memory/Skill 三种类型
- [上下文层级](./03-context-layers.md) - L0/L1/L2 模型
- [Viking URI](./04-viking-uri.md) - 统一资源标识符
- [存储架构](./05-storage.md) - 双层存储详解
- [检索机制](./07-retrieval.md) - 检索流程详解
- [上下文提取](./06-extraction.md) - 解析和提取流程
- [会话管理](./08-session.md) - 会话和记忆管理
