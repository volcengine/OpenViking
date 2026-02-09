# VikingVectorIndex

OpenViking 项目的高性能向量数据库模块，专为 AI Agent 场景设计，提供向量存储、检索和聚合分析能力。

## 特性

- **混合向量检索**：支持密集向量（Dense）和稀疏向量（Sparse）的混合搜索
- **多模态支持**：支持文本、图像、视频的向量化和检索
- **丰富的搜索方式**：向量搜索、ID搜索、标量搜索、随机搜索、关键词搜索
- **数据聚合分析**：支持总计数、分组计数、过滤聚合等分析操作
- **灵活的存储模式**：支持内存模式（Volatile）和持久化模式（Persistent）
- **TTL 自动过期**：支持数据生存时间管理，自动清理过期数据
- **索引自动重建**：后台任务自动检测和重建索引
- **高性能**：核心引擎基于 C++ 实现，使用 pybind11 绑定
- **线程安全**：关键数据结构支持并发访问

## 架构原理

### 整体架构

VikingVectorIndex 采用分层架构设计：

```
Application Layer (用户代码/API)
         ↓
Collection Layer (集合管理、数据操作、索引协调)
         ↓
   ┌─────┴─────┐
   ↓           ↓
Index Layer  Storage Layer
(向量检索)    (三表存储)
   ↓           ↓
C++ Engine (pybind11 绑定)
```

### 三表存储模型

VikingVectorIndex 使用三张表分离不同职责：

**C 表 (Candidate Table)**
- 存储最新的向量和标量数据
- Key: `label` (uint64)
- Value: 向量 + 字段 + 过期时间

**D 表 (Delta Table)**
- 记录数据变更历史 (PUT/DELETE)
- 用于索引增量更新和崩溃恢复
- Key: `timestamp_label`
- 定期清理：保留最旧索引版本之后的记录

**T 表 (TTL Table)**
- 按过期时间排序，加速 TTL 清理
- Key: `expire_timestamp_label`
- 后台任务定期扫描并删除过期数据

### 索引机制

**VolatileIndex (内存索引)**
- 数据全部在内存，重启后丢失
- 支持增量更新，定期重建压缩空间
- 适合：测试环境、临时数据

**PersistentIndex (持久化索引)**
- 多版本快照机制，每次持久化创建新版本目录
- 崩溃恢复：加载最新版本 + 应用增量更新
- 后台定期持久化和清理旧版本

版本目录结构：
```
versions/
  1704067200000000000/           # 版本快照
  1704067200000000000.write_done # 完成标记
```

### 核心数据流

**插入流程**：
```
用户数据 → 验证 → 生成label → 向量化
  ↓
写入C/D/T表 → 通知所有索引更新
  ↓
C++引擎更新向量索引和标量索引
```

**搜索流程**：
```
查询向量 → 索引检索 + 标量过滤
  ↓
返回labels和scores → 从C表批量获取完整数据
  ↓
构造SearchResult返回
```

### 性能优化

- **批量操作**：减少 I/O 次数
- **增量更新**：避免全量重建索引
- **C++ 加速**：向量计算使用 SIMD 优化
- **多版本快照**：写入不阻塞读取
- **延迟清理**：批量回收空间

## 快速开始

### 完整示例：从零开始

```python
from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection
import random

# Step 1: 定义集合元数据
collection_meta_data = {
    "CollectionName": "demo_collection",
    "Fields": [
        {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
        {"FieldName": "embedding", "FieldType": "vector", "Dim": 128},
        {"FieldName": "text", "FieldType": "text"},
        {"FieldName": "category", "FieldType": "text"},
        {"FieldName": "score", "FieldType": "float32"},
        {"FieldName": "priority", "FieldType": "int64"},
    ],
}

# Step 2: 创建集合（内存模式）
collection = get_or_create_local_collection(meta_data=collection_meta_data)
# 或创建持久化模式
# collection = get_or_create_local_collection(meta_data=collection_meta_data, path="./demo_db/")

# Step 3: 准备测试数据
data_list = []
categories = ["tech", "science", "art", "sports", "music"]
for i in range(1, 101):
    data_list.append({
        "id": i,
        "embedding": [random.random() for _ in range(128)],
        "text": f"This is document number {i}",
        "category": categories[i % 5],
        "score": round(random.uniform(0.5, 1.0), 2),
        "priority": random.randint(1, 10)
    })

# Step 4: 插入数据
result = collection.upsert_data(data_list)
print(f"Successfully inserted {len(result.ids)} documents")

# Step 5: 创建索引
index_meta_data = {
    "IndexName": "demo_index",
    "VectorIndex": {
        "IndexType": "flat",
        "Distance": "ip"
    },
    "ScalarIndex": ["category", "priority"],
}
collection.create_index("demo_index", index_meta_data)
print("Index created successfully")

# Step 6: 向量搜索
query_vector = [random.random() for _ in range(128)]
search_result = collection.search_by_vector(
    index_name="demo_index",
    dense_vector=query_vector,
    limit=5
)

print("\n=== Search Results ===")
for item in search_result.data:
    print(f"ID: {item.id}, Score: {item.score:.4f}")

# Step 7: 带过滤条件的搜索
search_result = collection.search_by_vector(
    index_name="demo_index",
    dense_vector=query_vector,
    limit=5,
    filters={"op": "must", "field": "category", "conds": ["tech", "science"]},
    output_fields=["text", "category", "score"]
)

print("\n=== Filtered Search Results (tech or science) ===")
for item in search_result.data:
    print(f"ID: {item.id}, Category: {item.fields.get('category')}, "
          f"Score: {item.score:.4f}, Text: {item.fields.get('text')}")

# Step 8: 清理资源
collection.close()
```

## Collection API 详细用例

### 1. 创建和管理集合

#### 1.1 创建内存集合

```python
from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection

# 定义集合元数据
meta_data = {
    "CollectionName": "my_collection",
    "Fields": [
        {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
        {"FieldName": "vector", "FieldType": "vector", "Dim": 128},
        {"FieldName": "text", "FieldType": "text"},
    ],
}

# 创建内存集合（进程结束后数据丢失）
collection = get_or_create_local_collection(meta_data=meta_data)
print(f"Collection '{collection.get_meta_data()['CollectionName']}' created in memory")
```

#### 1.2 创建持久化集合

```python
import os

# 创建持久化集合
persist_path = "./vectordb_data/my_persistent_collection"
os.makedirs(persist_path, exist_ok=True)

collection = get_or_create_local_collection(
    meta_data=meta_data,
    path=persist_path
)
print(f"Persistent collection created at: {persist_path}")

# 关闭集合
collection.close()

# 重新打开集合（数据自动恢复）
collection = get_or_create_local_collection(path=persist_path)
print("Collection reopened with all data restored")
```

#### 1.3 配置 TTL 和索引维护间隔

```python
# 自定义 TTL 清理和索引维护间隔
config = {
    "ttl_cleanup_seconds": 10,        # TTL 清理间隔 10 秒
    "index_maintenance_seconds": 60   # 索引维护间隔 60 秒
}

collection = get_or_create_local_collection(
    meta_data=meta_data,
    path="./vectordb_data/",
    config=config
)
print(f"Collection created with custom config: TTL cleanup every {config['ttl_cleanup_seconds']}s")
```

#### 1.4 更新集合元数据

```python
# 添加新字段
collection.update(
    fields=[
        {
            "FieldName": "timestamp",
            "FieldType": "int64",
            "DefaultValue": 0
        },
        {
            "FieldName": "tags",
            "FieldType": "text",
            "DefaultValue": ""
        }
    ]
)

# 验证新字段
meta = collection.get_meta_data()
print(f"Collection now has {len(meta['Fields'])} fields")
for field in meta['Fields']:
    print(f"  - {field['FieldName']}: {field['FieldType']}")
```

### 2. 数据操作

#### 2.1 插入/更新数据（Upsert）

```python
import time

# 准备数据
data_list = [
    {
        "id": 1,
        "vector": [0.1] * 128,
        "text": "First document",
        "timestamp": int(time.time()),
        "tags": "important"
    },
    {
        "id": 2,
        "vector": [0.2] * 128,
        "text": "Second document",
        "timestamp": int(time.time()),
        "tags": "review"
    },
    {
        "id": 3,
        "vector": [0.3] * 128,
        "text": "Third document",
        "timestamp": int(time.time()),
        "tags": "archive"
    }
]

# 插入数据
result = collection.upsert_data(data_list)
print(f"Inserted IDs: {result.ids}")

# 更新数据（相同 ID）
update_data = [
    {
        "id": 1,
        "vector": [0.15] * 128,
        "text": "Updated first document",
        "timestamp": int(time.time()),
        "tags": "updated"
    }
]
result = collection.upsert_data(update_data)
print(f"Updated IDs: {result.ids}")
```

#### 2.2 插入带 TTL 的数据

```python
import time

# 插入 5 秒后过期的数据
ttl_data = [
    {
        "id": 100,
        "vector": [1.0] * 128,
        "text": "Temporary document",
        "timestamp": int(time.time()),
        "tags": "temp"
    }
]

result = collection.upsert_data(ttl_data, ttl=5)
print(f"Inserted temporary data with ID: {result.ids}")

# 立即获取数据（成功）
fetch_result = collection.fetch_data([100])
print(f"Immediately fetched: {len(fetch_result.items)} items")

# 等待 TTL 过期
print("Waiting 10 seconds for TTL expiration...")
time.sleep(10)

# 再次获取（失败）
fetch_result = collection.fetch_data([100])
print(f"After TTL expiration: {fetch_result.ids_not_exist}")
```

#### 2.3 批量获取数据

```python
# 获取多条数据
primary_keys = [1, 2, 3, 999]  # 999 不存在
fetch_result = collection.fetch_data(primary_keys)

print(f"Found {len(fetch_result.items)} items")
for item in fetch_result.items:
    print(f"  ID: {item.fields['id']}, Text: {item.fields['text']}")

print(f"Not found IDs: {fetch_result.ids_not_exist}")
```

#### 2.4 删除数据

```python
# 删除单条数据
collection.delete_data(primary_keys=[2])
print("Deleted ID: 2")

# 删除多条数据
collection.delete_data(primary_keys=[3, 100])
print("Deleted IDs: 3, 100")

# 验证删除
fetch_result = collection.fetch_data([1, 2, 3])
print(f"Remaining items: {len(fetch_result.items)}")
print(f"Not found: {fetch_result.ids_not_exist}")
```

#### 2.5 清空所有数据

```python
# 删除所有数据（保留集合和索引结构）
collection.delete_all_data()
print("All data deleted")

# 验证
fetch_result = collection.fetch_data([1])
print(f"Items after delete_all: {len(fetch_result.items)}")
```

### 3. 索引管理

#### 3.1 创建不同类型的索引

```python
# 创建基本向量索引
basic_index_meta = {
    "IndexName": "basic_index",
    "VectorIndex": {
        "IndexType": "flat",
        "Distance": "ip"
    }
}
collection.create_index("basic_index", basic_index_meta)

# 创建带标量索引的向量索引
scalar_index_meta = {
    "IndexName": "scalar_index",
    "VectorIndex": {
        "IndexType": "flat",
        "Distance": "l2"
    },
    "ScalarIndex": ["category", "priority", "timestamp"]
}
collection.create_index("scalar_index", scalar_index_meta)

# 创建混合索引（密集+稀疏向量）
hybrid_index_meta = {
    "IndexName": "hybrid_index",
    "VectorIndex": {
        "IndexType": "flat_hybrid",
        "Distance": "ip",
        "SearchWithSparseLogitAlpha": 1.0
    }
}
collection.create_index("hybrid_index", hybrid_index_meta)

# 列出所有索引
indexes = collection.list_indexes()
print(f"Total indexes: {len(indexes)}")
for idx_name in indexes:
    print(f"  - {idx_name}")
```

#### 3.2 更新索引

```python
# 更新索引的标量字段和描述
collection.update_index(
    index_name="basic_index",
    scalar_index=["text", "tags"],
    description="Updated basic index with text and tags fields"
)

# 获取索引元数据
index_meta = collection.get_index_meta_data("basic_index")
print(f"Index: {index_meta['IndexName']}")
print(f"Description: {index_meta.get('Description', 'N/A')}")
print(f"Scalar Index: {index_meta.get('ScalarIndex', [])}")
```

#### 3.3 删除索引

```python
# 删除索引（不影响数据）
collection.drop_index("hybrid_index")
print("Index 'hybrid_index' dropped")

# 验证
remaining_indexes = collection.list_indexes()
print(f"Remaining indexes: {remaining_indexes}")
```

### 4. 向量搜索

#### 4.1 基本向量搜索

```python
import random

# 准备测试数据
test_data = [
    {"id": i, "vector": [random.random() for _ in range(128)],
     "text": f"Document {i}", "category": ["tech", "science", "art"][i % 3]}
    for i in range(1, 51)
]
collection.upsert_data(test_data)

# 创建索引
collection.create_index("test_index", {
    "IndexName": "test_index",
    "VectorIndex": {"IndexType": "flat", "Distance": "ip"},
    "ScalarIndex": ["category"]
})

# 执行向量搜索
query_vector = [random.random() for _ in range(128)]
result = collection.search_by_vector(
    index_name="test_index",
    dense_vector=query_vector,
    limit=10
)

print("=== Top 10 Similar Documents ===")
for i, item in enumerate(result.data, 1):
    print(f"{i}. ID: {item.id}, Score: {item.score:.4f}")
```

#### 4.2 带过滤条件的向量搜索

```python
# 过滤特定类别
result = collection.search_by_vector(
    index_name="test_index",
    dense_vector=query_vector,
    limit=5,
    filters={"op": "must", "field": "category", "conds": ["tech"]},
    output_fields=["text", "category"]
)

print("\n=== Tech Category Results ===")
for item in result.data:
    print(f"ID: {item.id}, Category: {item.fields['category']}, "
          f"Text: {item.fields['text']}, Score: {item.score:.4f}")
```

#### 4.3 范围过滤搜索

```python
# 添加带优先级的数据
priority_data = [
    {"id": i, "vector": [random.random() for _ in range(128)],
     "text": f"Priority doc {i}", "priority": i}
    for i in range(1, 21)
]
collection.upsert_data(priority_data)

# 搜索优先级在 5-15 之间的文档
result = collection.search_by_vector(
    index_name="test_index",
    dense_vector=query_vector,
    limit=10,
    filters={"op": "range", "field": "priority", "gte": 5, "lte": 15},
    output_fields=["text", "priority"]
)

print("\n=== Priority Range [5, 15] Results ===")
for item in result.data:
    print(f"ID: {item.id}, Priority: {item.fields['priority']}, "
          f"Score: {item.score:.4f}")
```

#### 4.4 分页搜索

```python
# 第一页（前 10 条）
page1 = collection.search_by_vector(
    index_name="test_index",
    dense_vector=query_vector,
    limit=10,
    offset=0,
    output_fields=["text"]
)

print("\n=== Page 1 (offset=0, limit=10) ===")
for item in page1.data:
    print(f"ID: {item.id}, Text: {item.fields['text']}")

# 第二页（10-20 条）
page2 = collection.search_by_vector(
    index_name="test_index",
    dense_vector=query_vector,
    limit=10,
    offset=10,
    output_fields=["text"]
)

print("\n=== Page 2 (offset=10, limit=10) ===")
for item in page2.data:
    print(f"ID: {item.id}, Text: {item.fields['text']}")
```

### 5. 其他搜索方式

#### 5.1 通过 ID 搜索相似文档

```python
# 使用 ID=5 的向量搜索相似文档
result = collection.search_by_id(
    index_name="test_index",
    id=5,
    limit=5,
    output_fields=["text"]
)

print("\n=== Similar to Document ID=5 ===")
for item in result.data:
    print(f"ID: {item.id}, Text: {item.fields['text']}, Score: {item.score:.4f}")
```

#### 5.2 随机搜索

```python
# 随机获取 10 条文档
result = collection.search_by_random(
    index_name="test_index",
    limit=10,
    output_fields=["text", "category"]
)

print("\n=== Random 10 Documents ===")
for item in result.data:
    print(f"ID: {item.id}, Category: {item.fields.get('category')}, "
          f"Text: {item.fields['text']}")

# 带过滤的随机搜索
result = collection.search_by_random(
    index_name="test_index",
    limit=5,
    filters={"op": "must", "field": "category", "conds": ["science"]},
    output_fields=["text"]
)

print("\n=== Random 5 Science Documents ===")
for item in result.data:
    print(f"ID: {item.id}, Text: {item.fields['text']}")
```

#### 5.3 标量字段排序搜索

```python
# 按优先级降序排列
result = collection.search_by_scalar(
    index_name="test_index",
    field="priority",
    order="desc",
    limit=5,
    output_fields=["text", "priority"]
)

print("\n=== Top 5 by Priority (Descending) ===")
for item in result.data:
    print(f"ID: {item.id}, Priority: {item.fields['priority']}, "
          f"Score: {item.score}")

# 按优先级升序排列，带过滤
result = collection.search_by_scalar(
    index_name="test_index",
    field="priority",
    order="asc",
    limit=5,
    filters={"op": "range", "field": "priority", "gte": 5},
    output_fields=["text", "priority"]
)

print("\n=== Top 5 by Priority (Ascending, priority >= 5) ===")
for item in result.data:
    print(f"ID: {item.id}, Priority: {item.fields['priority']}, "
          f"Score: {item.score}")
```

### 6. 数据聚合分析

#### 6.1 总计数

```python
# 获取索引中的总文档数
agg_result = collection.aggregate_data(
    index_name="test_index",
    op="count"
)

print(f"\n=== Total Document Count ===")
print(f"Total: {agg_result.total_count}")
```

#### 6.2 分组计数

```python
# 按类别分组统计
agg_result = collection.aggregate_data(
    index_name="test_index",
    op="count",
    field="category"
)

print("\n=== Count by Category ===")
for group in agg_result.groups:
    print(f"{group['value']}: {group['count']}")
```

#### 6.3 带过滤条件的聚合

```python
# 统计优先级 >= 10 的文档，按类别分组
agg_result = collection.aggregate_data(
    index_name="test_index",
    op="count",
    field="category",
    filters={"op": "range", "field": "priority", "gte": 10}
)

print("\n=== Count by Category (priority >= 10) ===")
for group in agg_result.groups:
    print(f"{group['value']}: {group['count']}")
```

#### 6.4 聚合后过滤

```python
# 统计每个类别的文档数，只返回数量 >= 5 的类别
agg_result = collection.aggregate_data(
    index_name="test_index",
    op="count",
    field="category",
    cond={"gt": 5}
)

print("\n=== Categories with Count > 5 ===")
for group in agg_result.groups:
    print(f"{group['value']}: {group['count']}")
```

### 7. 高级特性

#### 7.1 自动 ID 生成

```python
# 不指定主键的集合（使用自动生成的 AUTO_ID）
auto_id_meta = {
    "CollectionName": "auto_id_collection",
    "Fields": [
        {"FieldName": "content", "FieldType": "text"},
        {"FieldName": "embedding", "FieldType": "vector", "Dim": 64},
    ]
}

auto_collection = get_or_create_local_collection(meta_data=auto_id_meta)

# 插入数据（无需指定 ID）
data = [
    {"content": "Document A", "embedding": [random.random() for _ in range(64)]},
    {"content": "Document B", "embedding": [random.random() for _ in range(64)]},
    {"content": "Document C", "embedding": [random.random() for _ in range(64)]}
]

result = auto_collection.upsert_data(data)
auto_ids = result.ids
print(f"Auto-generated IDs: {auto_ids}")

# 使用自动生成的 ID 获取数据
fetch_result = auto_collection.fetch_data(auto_ids[:2])
print(f"\nFetched {len(fetch_result.items)} items using auto-generated IDs")
for item in fetch_result.items:
    print(f"  Content: {item.fields['content']}")

auto_collection.close()
```

#### 7.2 向量归一化

```python
import math

# 创建支持向量归一化的集合
normalized_meta = {
    "CollectionName": "normalized_vectors",
    "Fields": [
        {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
        {"FieldName": "vector", "FieldType": "vector", "Dim": 128},
    ],
    "VectorIndex": {
        "NormalizeVector": True  # 启用向量归一化
    }
}

norm_collection = get_or_create_local_collection(meta_data=normalized_meta)

# 插入非归一化向量（系统会自动归一化）
raw_vector = [i * 0.1 for i in range(128)]
norm_collection.upsert_data([{"id": 1, "vector": raw_vector}])

# 创建索引
norm_collection.create_index("norm_index", {
    "IndexName": "norm_index",
    "VectorIndex": {"IndexType": "flat", "Distance": "ip"}
})

# 搜索时向量也会自动归一化
query = [i * 0.05 for i in range(128)]
result = norm_collection.search_by_vector(
    index_name="norm_index",
    dense_vector=query,
    limit=1
)

print("Vector normalization enabled")
print(f"Search result score: {result.data[0].score:.4f}")

norm_collection.close()
```

## 过滤条件详解

### 支持的操作符

#### 1. `must` - 值必须在列表中

```python
# 单个值
filters = {"op": "must", "field": "category", "conds": ["tech"]}

# 多个值（OR 关系）
filters = {"op": "must", "field": "status", "conds": ["active", "pending", "review"]}
```

#### 2. `range` - 范围查询

```python
# 大于等于
filters = {"op": "range", "field": "score", "gte": 0.5}

# 小于等于
filters = {"op": "range", "field": "priority", "lte": 10}

# 范围（闭区间）
filters = {"op": "range", "field": "age", "gte": 18, "lte": 65}

# 大于
filters = {"op": "range", "field": "price", "gt": 100}

# 小于
filters = {"op": "range", "field": "discount", "lt": 0.5}
```

#### 3. `time_range` - 时间范围查询（date_time）

`date_time` 字段使用 `datetime.isoformat()` 格式，例如 `2026-02-06T12:34:56.123456`。
不带时区的时间会按**本地时区**解析。

```python
# 大于等于（ISO 时间字符串）
filters = {
    "op": "time_range",
    "field": "created_at",
    "gte": "2026-02-01T00:00:00"
}

# 时间范围（闭区间）
filters = {
    "op": "time_range",
    "field": "created_at",
    "gte": "2026-02-01T00:00:00",
    "lte": "2026-02-07T23:59:59"
}
```

#### 4. `geo_range` - 地理范围查询（geo_point）

`geo_point` 字段写入格式为 `"longitude,latitude"`，其中：
- `longitude` ∈ (-180, 180)
- `latitude` ∈ (-90, 90)

`radius` 支持 `m` 和 `km` 单位。

```python
filters = {
    "op": "geo_range",
    "field": "f_geo_point",
    "center": "116.412138,39.914912",
    "radius": "10km"
}
```

### 复杂过滤示例

```python
# 示例1: 查找特定类别且高优先级的文档
result = collection.search_by_vector(
    index_name="test_index",
    dense_vector=query_vector,
    filters={
        "op": "must",
        "field": "category",
        "conds": ["tech", "science"]
    },
    limit=10
)

# 示例2: 查找特定分数范围的文档
result = collection.search_by_vector(
    index_name="test_index",
    dense_vector=query_vector,
    filters={
        "op": "range",
        "field": "score",
        "gte": 0.7,
        "lte": 0.95
    },
    limit=10
)
```

## 最佳实践

### 1. 选择合适的存储模式

- **内存模式**：适合临时数据、测试环境、性能敏感场景
- **持久化模式**：适合生产环境、数据需要持久保存的场景

### 2. 索引设计

- 为常用的过滤字段创建标量索引
- 根据向量类型选择合适的距离度量（IP 或 L2）
- 归一化向量时使用 IP 距离

### 3. 性能优化

- 使用批量操作减少 I/O 次数
- 合理设置 limit 和 offset 进行分页
- 避免频繁的 delete_all 操作
- 对于大数据集，使用过滤条件缩小搜索范围

### 4. 资源管理

- 使用完毕后调用 `collection.close()` 释放资源
- 合理设置 TTL 自动清理过期数据
- 定期监控索引大小和内存使用

## API 参考

### Collection 方法

| 方法 | 说明 | 返回值 |
|------|------|--------|
| `create_index(name, meta)` | 创建索引 | Index |
| `drop_index(name)` | 删除索引 | None |
| `list_indexes()` | 列出所有索引 | List[str] |
| `get_index_meta_data(name)` | 获取索引元数据 | Dict |
| `update_index(name, scalar_index, description)` | 更新索引 | None |
| `upsert_data(data_list, ttl)` | 插入/更新数据 | UpsertResult |
| `fetch_data(primary_keys)` | 获取数据 | FetchResult |
| `delete_data(primary_keys)` | 删除数据 | None |
| `delete_all_data()` | 删除所有数据 | None |
| `search_by_vector(...)` | 向量搜索 | SearchResult |
| `search_by_id(...)` | ID 搜索 | SearchResult |
| `search_by_random(...)` | 随机搜索 | SearchResult |
| `search_by_scalar(...)` | 标量排序搜索 | SearchResult |
| `search_by_keywords(...)` | 关键词搜索 | SearchResult |
| `search_by_multimodal(...)` | 多模态搜索 | SearchResult |
| `aggregate_data(...)` | 数据聚合 | AggregateResult |
| `get_meta_data()` | 获取集合元数据 | Dict |
| `update(fields)` | 更新集合字段 | None |
| `close()` | 关闭集合 | None |
| `drop()` | 删除集合 | None |

## 贡献

欢迎提交 Issue 和 Pull Request！

## 许可证

本项目遵循 OpenViking 项目的许可证协议。
