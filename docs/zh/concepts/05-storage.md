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
| **向量库** | 索引存储 | URI、向量、元数据和摘要等检索文本 |

### 设计优势

AGFS 保存源文件，向量库存储检索需要的 URI 引用、向量、元数据和摘要。记忆记录会把正文保存在向量记录的 abstract 字段（上限 50,000 字节），因此向量库中也可能包含可读文本。

两类后端可以分别配置。文件 API 从 AGFS 读取内容，检索则可直接返回索引中的文本，无需逐个回读源文件。备份和访问控制需要覆盖两类存储。

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

完成语义处理的目录通常有以下结构。处理前或仅创建 L0 时，部分摘要文件可能尚不存在：

```
viking://resources/docs/auth/
├── .abstract.md          # L0 摘要
├── .overview.md          # L1 概览
└── *.md                  # L2 详细内容
```

## 向量库索引

向量库存储语义索引，支持向量搜索和标量过滤。

### 本地记录格式兼容

本地后端将非向量字段打包成 JSON。新写入的当前记录和 Delta 日志使用带 32 位字节长度的
`text` 保存这份 JSON，解除原先整个 JSON 合计 65,535 字节的限制，不截断内容。

新记录带有格式版本。已有的无版本记录继续可读，更新时写入新格式；本次格式升级不需要
全库重写、重建索引或重新计算 embedding。Delta 恢复也支持新旧格式混存。

兼容方向为新版读取旧数据：旧版程序无法读取新格式。需要降级时，应恢复首次写入新格式
之前的备份，不能只替换回旧版程序。

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

以下为索引元数据示例；可用选项取决于向量后端：

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

文件系统操作协调文件与向量记录的变更。目录摘要可能继续异步刷新，失败也可能留下部分变更；各操作的具体行为见[文件系统 API](../api/03-filesystem.md)。下面的示例使用已初始化的同步 Python SDK 客户端。

### 删除同步

```python
client.rm("viking://resources/docs/auth", recursive=True)
# 自动递归删除向量库中所有 uri 以此开头的记录
```

### 移动同步

```python
client.mv(
    "viking://resources/docs/auth",
    "viking://resources/docs/authentication"
)
# 自动更新向量库中的 uri 和 parent_uri 字段
```

## 相关文档

- [架构概述](./01-architecture.md) - 系统整体架构
- [上下文层级](./03-context-layers.md) - L0/L1/L2 模型
- [Viking URI](./04-viking-uri.md) - URI 规范
- [多写存储](./14-multi-write-storage.md) - primary/backup、多写路由与一致性
- [检索机制](./07-retrieval.md) - 检索流程详解
