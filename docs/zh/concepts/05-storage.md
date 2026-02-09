# 存储架构

OpenViking 采用双层存储架构，分离内容存储和索引存储。

## 概览

```
┌─────────────────────────────────────────┐
│            VikingFS (URI 抽象层)         │
│    URI 映射 · 层级访问 · 关联管理        │
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
| **AGFS** | 内容存储 | L0/L1/L2 完整内容、多媒体文件、关联关系 |
| **向量库** | 索引存储 | URI、向量、元数据（不存文件内容） |

### 设计优势

1. **职责清晰**：向量库只负责检索，AGFS 负责存储
2. **内存优化**：向量库不存储文件内容，节省内存
3. **单一数据源**：所有内容从 AGFS 读取，向量库只存引用
4. **独立扩展**：向量库和 AGFS 可分别扩展

## VikingFS 虚拟文件系统

VikingFS 是统一的 URI 抽象层，屏蔽底层存储细节。

### URI 映射

```
viking://resources/docs/auth  →  /local/resources/docs/auth
viking://user/memories        →  /local/user/memories
viking://agent/skills         →  /local/agent/skills
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
| `relations(uri)` | 获取关联列表 |
| `find(query, uri)` | 语义搜索 |

### 关联管理

VikingFS 通过 `.relations.json` 管理资源间的关联：

```python
# 创建关联
viking_fs.link(
    from_uri="viking://resources/docs/auth",
    uris=["viking://resources/docs/security"],
    reason="相关安全文档"
)

# 获取关联
relations = viking_fs.relations("viking://resources/docs/auth")
```

## AGFS 底层存储

AGFS 提供 POSIX 风格的文件操作，支持多种后端。

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
├── .relations.json       # 关联
└── *.md                  # L2 详细内容
```

## 向量库索引

向量库存储语义索引，支持向量搜索和标量过滤。

### Context 集合 Schema

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 主键 |
| `uri` | string | 资源 URI |
| `parent_uri` | string | 父目录 URI |
| `context_type` | string | resource/memory/skill |
| `is_leaf` | bool | 是否叶子节点 |
| `vector` | vector | 密集向量 |
| `sparse_vector` | sparse_vector | 稀疏向量 |
| `abstract` | string | L0 摘要文本 |
| `name` | string | 名称 |
| `description` | string | 描述 |
| `created_at` | string | 创建时间 |
| `active_count` | int64 | 使用次数 |

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
# 自动更新向量库中的 uri 和 parent_uri 字段
```

## 相关文档

- [架构概述](./01-architecture.md) - 系统整体架构
- [上下文层级](./03-context-layers.md) - L0/L1/L2 模型
- [Viking URI](./04-viking-uri.md) - URI 规范
- [检索机制](./07-retrieval.md) - 检索流程详解
