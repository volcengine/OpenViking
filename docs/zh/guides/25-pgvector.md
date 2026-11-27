# PostgreSQL + pgvector

设置 `storage.vectordb.backend="pgvector"` 后，collection 元数据、dense 向量、
标量字段和 sparse 倒排记录存储在 PostgreSQL 中。OpenViking 通过有连接数上限的
psycopg2 连接池访问数据库，向量检索在 PostgreSQL 进程内执行。
Embedding 仍由配置的模型提供方生成。

## 安装与配置

安装客户端可选依赖：

```bash
pip install 'openviking[pgvector]'
```

在 PostgreSQL 服务端安装 **0.8.0 或更新版本**的 pgvector，然后在 OpenViking
使用的数据库内启用扩展：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
SELECT extversion FROM pg_extension WHERE extname = 'vector';
```

扩展需安装在 `public` schema。连接账号需要访问该 schema、创建 collection
表和索引、读写数据的权限。并发初始化通过 PostgreSQL advisory lock 串行化。
元数据使用 `_ov_collection_meta` 和 `_ov_index_<collection>` 表。
独立部署应使用不同数据库或 collection 名称，不能复用 openGauss collection 的名称。

在服务端环境变量中设置 DSN；可按 PostgreSQL DSN 规范配置 `sslmode`、
`sslrootcert` 等连接参数：

```bash
export OV_POSTGRES_DSN='host=127.0.0.1 port=5432 dbname=openviking user=ov password=CHANGE_ME'
```

将下面片段合并到现有配置，向量维度需与 embedding 模型输出一致：

```json
{
  "storage": {
    "vectordb": {
      "backend": "pgvector",
      "name": "context",
      "dimension": 4096,
      "distance_metric": "cosine",
      "pgvector": {
        "dsn_env": "OV_POSTGRES_DSN",
        "index_type": "flat",
        "connection_pool_min_size": 1,
        "connection_pool_max_size": 10
      }
    }
  }
}
```

这是 Cluster 级后端配置；Account 独立 `vectordb` 配置目前仅接受
`http`、`volcengine`、`vikingdb`。

## 检索和索引参数

| `pgvector` 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `dsn_env` | `OV_POSTGRES_DSN` | 保存连接 DSN 的环境变量名称 |
| `index_type` | `flat` | 精确 dense 检索，或原生 `hnsw` ANN |
| `connection_pool_min_size` | `1` | 每个 adapter 的最小连接数 |
| `connection_pool_max_size` | `10` | 每个 adapter 的最大连接数，不能小于最小值 |
| `m` | `16` | HNSW 每层连接数，范围 2–100 |
| `ef_construction` | `64` | HNSW 构建候选列表，范围 4–1000，至少是 `m` 的两倍 |
| `ef_search` | `100` | HNSW 检索候选列表，范围 1–1000 |
| `candidate_limit` | `1000` | sparse/hybrid 或时间衰减排序中每路候选预算，范围 1–10000 |

Dense 使用全精度 `vector` 类型，支持 1–16000 维；该类型的原生 HNSW
最多支持 **2000 维**。4096 维模型需使用 `index_type="flat"`，不会自动截断
或量化 embedding。当前未实现 `halfvec`、二进制量化和 IVFFlat。

不超过 2000 维的模型可选择：

```json
{
  "dsn_env": "OV_POSTGRES_DSN",
  "index_type": "hnsw",
  "m": 16,
  "ef_construction": 64,
  "ef_search": 100
}
```

`distance_metric` 支持 `cosine`、`l2`、`ip`、`l1`，结果遵循 OpenViking
分数越高排名越靠前的约定。标量、租户、ACL 和 URI 范围过滤转换为参数化 SQL。
HNSW 开启 strict iterative scanning 来改善带过滤的召回，但不保证 ANN
召回率或过滤后 TopK 数量；需用真实过滤条件验证召回率和 p99。
pgvector HNSW 的 cosine 索引不包含零向量。

Cosine 检索同样从 flat 结果以及 hybrid/时间衰减的 dense 候选中排除零向量。
原生 dense 查询在距离排序和分页之后排除 NaN：PostgreSQL 将 NaN 排在所有
有限距离之后，因此零向量不会挤掉有效结果或改变有效分页。hybrid 的 sparse
分支在候选预算截取之前排除零向量，并通过 CASE 保证标量过滤通过后才计算范数。
仅 sparse 查询仍允许这些记录参与检索。普通 dense 查询只将查询向量转为字符串
一次，供两个 SQL 参数复用。

即使表中额外存在 ANN 索引，flat 检索仍保持精确。重新连接时会校验配置与
持久化元数据中的维度、距离和索引类型，ANN 索引缺失或无效会明确报错。
修改 `ef_search` 在重新连接后生效，不需要重建索引；修改索引类型或构建参数
需要安排索引/collection 重建。

Sparse term 以 JSONB 和倒排表存储。Sparse-only 只召回有正向 term overlap
的记录；hybrid 合并有限 dense/sparse 候选，在候选并集中补齐分数后排序，
不保证全局融合 TopK。`sparse_weight` 与 embedding 配置仍沿用既有语义。

## 目录过滤调优

### 选择检索实现

`pgvector.search_implementation` 只接受 `native`（默认）和 `enhanced`：

- `native` 使用社区原生 pgvector SQL，由 PostgreSQL 选择执行计划。
- `enhanced` 使用字节增强检索，当前通过 `ov_filtered_knn` envelope-v1 API 实现。
  初始化时校验扩展归属、函数签名、PG 版本和权限；能力缺失或 dense 索引不支持时
  明确报错，不自动回退。运行时错误、权限错误和预算不足直接传播。

选择与过滤条件无关：无过滤、账号、ACL 和路径过滤查询均使用所选实现。
正常账号绑定搜索仍添加 `account_id`，两种实现都保留这些条件、MVCC 和 RLS。
此配置选择查询实现，不更换数据库扩展二进制，也不选择或创建索引；
安装字节扩展的数据库也可以使用 `native`。

旧配置 `filtered_knn` 和 `unfiltered_use_native` 已移除，传入会触发配置校验错误。
迁移时明确选择 `search_implementation`；`filtered_knn_options` 保留为增强入口的参数，
仅在 `enhanced` 下使用。其 `mode="exact"` 选择增强算法，不改变检索实现。

能力探测只在 adapter 初始化的 `_connect()` 中执行，结果会缓存并复制到 Collection。
`native` 不探测增强能力。数据库升级为字节扩展或修改检索实现后，
需要重新创建 adapter 及其 Collection，通常通过重启或滚动重启服务完成。
连接池补建或替换连接、重新加载 Collection 不会刷新实现选择和能力探测结果。

目前新能力支持 PostgreSQL 17、最多 2000 维的全精度 HNSW 向量。
Dense、hybrid 和 time-decay 共用候选 hook，sparse-only 沿用倒排路径。
候选和字段在同一条 SQL 中读取，单次 DEBUG 日志包含 route、stop_reason 和预算统计，
空结果也保留这些信息。

原生路径中的 HNSW 扫描使用 `ef_search`（1–1000），不使用增强入口的 search width、
访问预算或 recover。增强路径对于 adapter 收到的 `None` 或 `{}` 同样调用增强入口。

```json
{
  "index_type": "hnsw",
  "search_implementation": "enhanced",
  "filtered_knn_options": {
    "mode": "adaptive",
    "exact_threshold": 1000,
    "search_width": 100,
    "max_graph_visits": 20000,
    "max_heap_fetches": 200000,
    "eligible_memory_limit": 67108864,
    "recover": false
  }
}
```

64 位扩展的 `eligible_memory_limit` 范围为 1 MiB–4 GiB，单位为字节，
4 GiB 对应 `4294967296`，默认仍为 64 MiB。它是单请求按需分配的累计内存
检查预算，检查间可能短暂超额；SPI executor 和最终 JSON 的内存未完全计入。
32 位扩展仍限制为最高 1 GiB。

参数经过严格校验。`mode="exact"` 强制精确候选检索；
`exact_threshold=0` 对非空资格集合选择 ANN。
Search width 至少等于候选预算。ANN 可能返回部分合格结果，并记录停止原因。
当前资格构建仍扫描完整过滤集合；生产启用前需验证召回率、扫描成本和 p99。

增强路径的纯 dense 分页使用 `candidate_k = limit + offset`，实际
`search_width = max(配置值, candidate_k)`。配置的 `search_width` 是起始宽度，
不是请求硬上限；候选预算和实际宽度的硬上限均为 `100000`。
超过上限时 adapter 在执行查询前抛出 `ValueError`，不会截断分页或回退原生 SQL；
`mode="exact"` 也受此限制。直接调用扩展时，超限 `candidate_k` 返回 SQLSTATE
`22023`，消息为 `ov_filtered_knn: candidate_k must be between 1 and 100000`。
这些扩展预算限制不适用于原生 SQL 路径，`limit=0` 仍返回空结果。
合法预算内，ANN 候选不足可能导致分页少于 `limit` 条，甚至为空；
停止原因仅记录在 DEBUG 诊断中，不作为 `SearchResult` 的额外字段返回。

测试恢复机制时，设置 `search_implementation="enhanced"`，确保查询进入增强遍历。
以下配置固定 width=2000、开启恢复并限制总图访问次数；可分别测试
`max_graph_visits=100000`、`200000`、`400000`，对比 Recall@10 和延迟。
恢复取消启发式停止，但仍受总访问次数、heap 和内存预算限制；访问上限不保证召回率。

```json
{
  "index_type": "hnsw",
  "search_implementation": "enhanced",
  "filtered_knn_options": {
    "search_width": 2000,
    "exact_threshold": 10000,
    "max_graph_visits": 200000,
    "max_heap_fetches": 2000000,
    "eligible_memory_limit": 4294967296,
    "recover": true
  }
}
```

大规模 collection 的归一化 URI 范围过滤可评估租户与路径的 B-tree
复合表达式索引。索引 SQL、level/depth 语义、百万行实测和部署边界见
[pgvector 目录过滤索引优化](26-pgvector-directory-filtering.md)。
该索引目前需要手工评估和部署，后端不会自动创建。

## 数据与部署边界

批量 upsert 和 sparse 倒排更新在同一事务中完成，失败时整批回滚。
数据与索引元数据在服务重启后保留。配置 PostgreSQL 复制、备份和 PITR 后，
这些数据库记录可获得相应的保护。

当前不支持 TTL 写入和原生 BM25/full-text 检索。`content` 字段保存在
PostgreSQL 中（服务写入链路仍沿用现有的 1 MB content payload 限制），
grep 沿用文件系统回退。初始化会执行 collection DDL；
当前不提供自动 schema 迁移或损坏索引自动修复。

修改 backend 配置不会导入原有 local 向量。需要创建新 collection 并重新索引
资源，或单独执行经过核验的迁移；在完成核验前保留旧向量库。

资源文件、队列、Account/Task 存储和 PathLock 各自独立配置。仅共享 pgvector
不能让本地 workspace 安全地供多个 OpenViking 进程使用。
共享存储和协调配置请参考[部署指南](03-deployment.md)；
PostgreSQL PathLock 是独立功能。

## 集成测试

测试使用随机 collection 名称，完成后删除测试数据。请使用启用了 pgvector 的
测试数据库：

```bash
export OV_PGVECTOR_TEST_DSN='host=127.0.0.1 port=5432 dbname=ov_test user=ov'
pytest tests/storage/test_pgvector_adapter.py
```
