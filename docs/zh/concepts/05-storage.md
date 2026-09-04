# 存储架构

OpenViking 采用双层存储架构，分离内容存储和索引存储。

## 概览

```
┌─────────────────────────────────────────┐
│            VikingFS (URI 抽象层)         │
│            URI 映射 · 层级访问           │
└────────────────┬────────────────────────┘
        ┌────────┴────────┐
        │                 │
┌───────▼────────┐  ┌─────▼───────────┐
│   向量库索引    │  │      AGFS       │
│   (语义搜索)    │  │   (内容存储)    │
└────────────────┘  └─────────────────┘
```

## 双层存储

| 存储层 | 职责 | 存储内容 |
|--------|------|----------|
| **AGFS** | 内容存储 | L0/L1/L2 完整内容、多媒体文件 |
| **向量库** | 索引存储 | URI、向量、元数据；兼容的 VikingDB 集合还可持久化用于 grep 召回的有界正文投影 |

### 设计优势

1. **职责清晰**：向量库负责检索，AGFS 仍是权威内容源
2. **按后端优化**：兼容的 VikingDB 集合可持久化有界派生正文，其他后端只存引用和索引字段
3. **读取一致**：常规文件读取来自 AGFS，向量库中的正文只是派生检索投影
4. **独立扩展**：向量库和 AGFS 可分别扩展

集合 Schema 兼容时，VikingDB 后端可持久化最多 1 MiB 的有界 `content` 投影，用于 grep 的 VikingDB FullText/BM25 候选召回。精确匹配仍从 AGFS 读取召回文件。其他后端会在写入向量库前丢弃 `content`。

> 注：AGFS 已经重写为 Rust 实现（RAGFS）

## VikingFS 虚拟文件系统

VikingFS 是统一的 URI 抽象层，屏蔽底层存储细节。

### URI 映射

```
viking://resources/docs/auth  →  /local/{account_id}/resources/docs/auth
viking://~/memories        →  /local/{account_id}/user/{user_id}/memories
viking://~/skills          →  /local/{account_id}/user/{user_id}/skills
```

### 核心 API

| 方法 | 说明 |
|------|------|
| `read(uri)` | 读取文件内容 |
| `write(uri, data)` | 写入文件 |
| `mkdir(uri)` | 创建目录 |
| `rm(uri)` | 删除文件/目录（同步删除向量） |
| `mv(old, new)` | 移动/重命名（同步更新向量 URI） |
| `abstract(uri)` | 读取 L0 摘要 |
| `overview(uri)` | 读取 L1 概览 |
| `find(query, uri)` | 语义搜索 |

## AGFS 底层存储

AGFS 提供 POSIX 风格的文件操作，支持多种后端。

### 单后端与多写模式

默认情况下，AGFS 使用一个后端作为内容存储。配置 `storage.agfs.backups` 后，OpenViking 会启用多写模式：

- 顶层 `storage.agfs.backend` 是 primary，作为权威写入目标。
- `storage.agfs.backups.items[]` 是 backup，用于副本、迁移或读加速。
- Python SDK、HTTP API 和 CLI 的文件系统接口保持不变。
- 多写内部使用 `.redirect.json` 和 `.sync_log.json` 维护 redirect 映射与同步进度，这些文件对用户不可见。

更多概念说明见 [多写存储](./14-multi-write-storage.md)，配置示例见 [多写存储指南](../guides/13-multi-write-storage.md)。

### 后端类型

| 后端 | 说明 | 配置 |
|------|------|------|
| `localfs` | 本地文件系统 | `path` |
| `s3fs` | S3 兼容存储 | `bucket`, `endpoint` |
| `memory` | 内存存储（测试用） | - |

### 目录结构

每个上下文目录遵循统一结构：

```
viking://resources/docs/auth/
├── .abstract.md          # L0 摘要
├── .overview.md          # L1 概览
└── *.md                  # L2 详细内容
```

## 向量库索引

向量库存储语义索引，支持向量搜索和标量过滤。当前统一 Schema 定义了下列字段；`content` 是否实际持久化取决于前述后端和集合 Schema。

### Context 集合 Schema

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 主键 |
| `uri` | path | Viking URI |
| `type` | string | 预留资源子类型 |
| `context_type` | string | resource/memory/skill |
| `vector` | vector | 密集向量 |
| `sparse_vector` | sparse_vector | 稀疏向量 |
| `created_at` | date_time | 创建时间 |
| `updated_at` | date_time | 最后更新时间 |
| `active_count` | int64 | 使用次数 |
| `level` | int64 | L0/L1/L2 层级 |
| `name` | string | 名称 |
| `description` | string | 描述 |
| `tags` | string | 标签 |
| `search_tags` | list&lt;string&gt; | 检索标签 |
| `abstract` | string | L0 摘要文本 |
| `content` | text | 有界全文投影（兼容的 VikingDB 集合） |
| `account_id` | string | 账户作用域 |
| `owner_user_id` | string | 所属用户作用域 |

### 索引策略

```python
index_meta = {
    "IndexType": "flat_hybrid",  # 混合索引
    "Distance": "cosine",        # 余弦距离
    "Quant": "int8",             # 量化方式
}
```

### 后端支持

| 后端 | 说明 |
|------|------|
| `local` | 本地持久化 |
| `http` | HTTP 远程服务 |
| `volcengine` | 火山引擎 VikingDB |

## 向量同步

VikingFS 自动维护向量库与 AGFS 的一致性。

### 删除同步

```python
viking_fs.rm("viking://resources/docs/auth", recursive=True)
# 自动递归删除向量库中所有 uri 以此开头的记录
```

### 移动同步

```python
viking_fs.mv(
    "viking://resources/docs/auth",
    "viking://resources/docs/authentication"
)
# 自动更新向量库中受影响的 uri 值
```

## 相关文档

- [架构概述](./01-architecture.md) - 系统整体架构
- [上下文层级](./03-context-layers.md) - L0/L1/L2 模型
- [Viking URI](./04-viking-uri.md) - URI 规范
- [多写存储](./14-multi-write-storage.md) - primary/backup、多写路由与一致性
- [检索机制](./07-retrieval.md) - 检索流程详解
