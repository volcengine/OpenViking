# 架构概述

OpenViking 是为 AI Agent 设计的上下文数据库，将所有上下文（Memory、Resource、Skill）统一抽象为目录结构，支持语义检索和渐进式内容加载。

## 系统概览

```text
CLI / SDK / HTTP client
          |
      HTTP Server
          |
      Service Layer
          |
   +------+------+----------------+
   |             |                |
Retrieval     Sessions       Resource / Skill import
   |             |                |
   |       Memory extraction  Parse / Semantic queues
   |             |                |
   +-------------+----------------+
                 |
             VikingFS
           /          \
       AGFS         Vector index
```

## 核心模块

| 模块 | 职责 | 关键能力 |
|------|------|---------|
| **Client** | 统一入口 | 通过 HTTP API 提交 SDK/CLI 支持的操作 |
| **Service** | 业务逻辑 | FSService、SearchService、SessionService、ResourceService、PackService、DebugService |
| **Retrieve** | 上下文检索 | 意图分析（IntentAnalyzer）、层级检索（HierarchicalRetriever）、Rerank 精排 |
| **Session** | 会话管理 | 消息记录、使用追踪、会话压缩、记忆提交 |
| **Parse** | 上下文提取 | 文档解析（PDF/MD/HTML）、树构建（TreeBuilder）、异步语义生成 |
| **Compressor** | 记忆压缩 | Schema 驱动的记忆提取、LLM 去重决策 |
| **Storage** | 存储层 | VikingFS 虚拟文件系统、向量索引、AGFS 集成 |

## Service 层

Service 层将业务逻辑与传输层解耦，CLI 和 SDK 通过 HTTP Server 访问业务能力：

| Service | 职责 | 主要方法 |
|---------|------|----------|
| **FSService** | 文件系统操作 | ls, mkdir, rm, mv, tree, stat, read, abstract, overview, grep, glob |
| **SearchService** | 语义搜索 | search, find |
| **SessionService** | 会话管理 | session, sessions, commit, delete |
| **ResourceService** | 资源导入 | add_resource, add_skill, wait_processed |
| **PackService** | 导入导出、备份恢复 | export_ovpack, import_ovpack, backup_ovpack, restore_ovpack |
| **DebugService** | 调试服务 | observer (ObserverService) |

## 双层存储

OpenViking 采用双层存储架构，实现内容与索引分离（详见 [存储架构](./05-storage.md)）：

| 存储层 | 职责 | 内容 |
|--------|------|------|
| **AGFS** | 内容存储 | L0/L1/L2 完整内容、多媒体文件、关联关系 |
| **向量库** | 索引存储 | URI、向量、元数据和检索需要的文本（包括摘要） |

## 数据流概览

### 添加上下文

```
输入 → Parser → TreeBuilder → AGFS → SemanticQueue → 向量库
```

1. **Parser**：将源文档解析为文件与目录；是否调用模型取决于所选 Parser
2. **TreeBuilder**：移动临时目录到 AGFS，入队语义处理
3. **SemanticQueue**：异步自底向上生成 L0/L1
4. **向量库**：建立索引用于语义搜索

### 检索上下文

```
查询 → 查询准备（可选意图分析）→ 向量检索（可选目录遍历与 Rerank）→ 结果
```

1. **查询准备**：`find()` 直接使用查询；`search()` 在启用意图分析且会话有内容时生成类型化查询
2. **QUICK 检索**：`find`、图片查询和未配置 Rerank 的 `search` 直接检索向量，不展开目录
3. **THINKING 检索**：配置了 Rerank 的文本 `search` 遍历目录并重排候选项
4. **结果**：返回按相关性排序的上下文

### 会话提交

```
消息 → 归档边界 → 归档 → 记忆提取 → 存储
```

1. **消息**：累积对话消息和使用记录
2. **归档边界**：按提交参数划分归档消息和保留消息；默认归档全部当前消息
3. **归档**：生成历史片段的 L0/L1
4. **记忆提取**：根据记忆策略和 MemoryType Schema 从消息中提取记忆
5. **存储**：写入 AGFS + 向量库

## 部署模式

### HTTP 模式

用于团队共享、生产环境和跨语言集成：

```python
# Python SDK 连接 OpenViking Server
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
```

```bash
# 或使用 curl / 任意 HTTP 客户端
curl http://localhost:1933/api/v1/search/find \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"query": "how to use openviking"}'
```

- Server 作为独立进程运行（`openviking-server`）
- 客户端通过 HTTP API 连接
- 支持任何能发起 HTTP 请求的语言
- 参见 [服务部署](../guides/03-deployment.md) 了解配置方法

## 设计原则

| 原则 | 说明 |
|------|------|
| **存储层纯粹** | 存储层只做 AGFS 操作和基础向量搜索，Rerank 在检索层完成 |
| **三层信息** | L0/L1/L2 实现渐进式详情加载，节省 Token 消耗 |
| **两阶段检索** | 向量检索提供候选项；文本 search 可按配置遍历目录并重排序 |
| **单一数据源** | AGFS 保存源文件，向量记录保留检索需要的文本与元数据 |

## 相关文档

- [上下文类型](./02-context-types.md) - Resource/Memory/Skill 三种类型
- [上下文层级](./03-context-layers.md) - L0/L1/L2 模型
- [Viking URI](./04-viking-uri.md) - 统一资源标识符
- [存储架构](./05-storage.md) - 双层存储详解
- [检索机制](./07-retrieval.md) - 检索流程详解
- [上下文提取](./06-extraction.md) - 解析和提取流程
- [会话管理](./08-session.md) - 会话和记忆管理
- [事务模型](./09-transaction.md) - 写入与一致性模型
- [数据加密](./10-encryption.md) - 静态数据加密与密钥架构
- [多租户](./11-multi-tenant.md) - account / user / peer 隔离模型
- [指标](./12-metrics.md) - `/metrics` 使用方式与关键指标说明
- [用户隐私配置](./13-privacy.md) - 隐私版本管理、自动提取Skill隐私配置
