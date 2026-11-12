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

即使表中额外存在 ANN 索引，flat 检索仍保持精确。重新连接时会校验配置与
持久化元数据中的维度、距离和索引类型，ANN 索引缺失或无效会明确报错。
修改 `ef_search` 在重新连接后生效，不需要重建索引；修改索引类型或构建参数
需要安排索引/collection 重建。

Sparse term 以 JSONB 和倒排表存储。Sparse-only 只召回有正向 term overlap
的记录；hybrid 合并有限 dense/sparse 候选，在候选并集中补齐分数后排序，
不保证全局融合 TopK。`sparse_weight` 与 embedding 配置仍沿用既有语义。

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
