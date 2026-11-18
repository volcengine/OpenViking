# pgvector 目录过滤索引优化

pgvector 的目录过滤使用租户条件和归一化 URI 范围。百万行实测中，普通
`uri` 索引没有被用于这些目录范围查询：即使目录只命中约百行，查询仍先扫描
整个租户的候选。与查询表达式一致的 B-tree 复合索引可以显著减少小目录和
空目录的扫描成本；对覆盖整个租户的范围，收益有限。

本文记录索引方案与实测依据。**当前后端不会自动创建这个表达式索引**；
它只在隔离压测 collection 上验证过，测试结束随表删除。基础配置与能力边界
见 [PostgreSQL + pgvector](25-pgvector.md)。

## 目录过滤如何执行

`PathScope("uri", "viking://resources/g00/d000", depth)` 在 path 字段上转换为
`/resources/g00/d000` 范围，并与 `account_id`、`level`、ACL 等条件一起编译为
参数化 SQL。`level=0/1/2` 分别对应 L0 摘要、L1 概览、L2 内容；它与路径深度
是两个独立条件。

| `depth` | 范围 |
| --- | --- |
| 小于 0 | 当前节点及全部后代 |
| 等于 0 | 当前节点的精确路径 |
| 大于 0 | 当前节点及相对路径段数不超过 `depth` 的后代 |

查询对存储的 `uri` 应用下列 SQL 表达式：

```sql
CASE
    WHEN uri IS NULL THEN NULL
    WHEN btrim(uri) = ''
      OR rtrim(btrim(uri), '/') = '' THEN '/'
    ELSE rtrim(btrim(uri), '/')
END
```

下文用 `N(uri)` 表示这个表达式；它不是新增列或数据库函数。无限深度查询
可概括为：

```text
account_id = :account_id
AND (N(uri) = :path OR N(uri) LIKE :child_pattern ESCAPE '\')
AND level = :level
```

例如 `:path` 为 `/resources/g00/d000`，子路径模式为
`/resources/g00/d000/%`。路径中的 `%`、`_` 和 `\` 会先转义；末尾 `/`
用于区分目录边界。正深度查询还会计算匹配路径的相对段数。

SQL 实现见
[`_sql_normalized_path` 与 `_build_path_scope_clause`](https://github.com/volcengine/OpenViking/blob/eaf44199aacfb601f059202bd8d04246e5248f2e/openviking/storage/vectordb_adapters/opengauss/sql.py#L191-L224)。
pgvector 复用这部分过滤编译逻辑。

## 为什么普通 URI 索引没有解决问题

普通 B-tree 索引的键是原始 `uri`，目录条件比较的却是
`CASE/btrim/rtrim` 表达式。此次执行计划只使用 `account_id` 索引，再对取出的
租户行执行路径过滤。

百万行表中，单目录查询命中 100 行，但租户条件取出 100,000 个候选。
并行 Bitmap Heap Scan 的路径过滤合计移除约 99,900 行。由于租户行交错存储、
`work_mem=4MB`，计划还出现 lossy bitmap 和额外的索引条件重查。
纯过滤 `count(*)` 的 p50 为 141.11 ms，flat Top-10 为 141.42 ms，
说明这一组的主要开销在扫描和过滤，而不是最后 100 行的距离计算。

`level=0/1` 可以利用既有的 `level` 和 `account_id` 索引做 BitmapAnd，
在本次数据分布中将候选缩小到 1,000 行。`level=2` 占数据的 98%，其选择性
不足以解决这个扫描问题。实际收益取决于各层记录比例与租户物理布局。

## 复合表达式索引

第一列使用 `account_id`，第二列使用与目录查询相同的归一化 URI 表达式，
并指定 `text_pattern_ops`：

```sql
-- 将 public."context" 替换为实际 collection 表，
-- 并为每个表选择独立的索引名称。
CREATE INDEX context_account_uri_scope_idx
ON public."context" (
    account_id,
    (CASE
        WHEN uri IS NULL THEN NULL
        WHEN btrim(uri) = ''
          OR rtrim(btrim(uri), '/') = '' THEN '/'
        ELSE rtrim(btrim(uri), '/')
    END) text_pattern_ops
);

ANALYZE public."context";
```

`account_id` 限定租户；表达式键与查询匹配；`text_pattern_ops` 支持这里的
路径前缀 `LIKE` 范围以及精确匹配。空目录可以通过索引确认没有候选，
不必遍历整个租户。规划器仍根据统计信息和成本选择执行方式。

索引不改变路径、租户或 ACL 语义，也不单独优化正深度的段数计算。
它是标量 B-tree 索引；向量距离仍由 flat 或 HNSW 路径计算。
flat 查询设置 `enable_indexscan=off` 以保持精确检索，但仍允许 Bitmap Scan，
因此可以使用该索引。

对同一个百万行、100 行命中的查询，索引后的关键计划为：

```text
Bitmap Heap Scan
  Actual Rows: 100
  Exact Heap Blocks: 77
  Rows Removed by Filter: 0
  BitmapOr
    Bitmap Index Scan on the scope index: 2 rows (exact path)
    Bitmap Index Scan on the scope index: 98 rows (child prefix)
```

这 2 行分别为 L0/L1，98 行为 L2 文件；节点摘录省略了完整条件与距离排序。
加上 `level=2` 后只返回该层的候选。

## 实测方法

测试于 **2026-11-13** 执行，代码版本为
`eaf44199aacfb601f059202bd8d04246e5248f2e`。
通过真实 `collection adapter.query` 查询 PostgreSQL；测量包含连接池、
SQL 和结果读取，不包含 HTTP、embedding 生成或完整递归检索流程。

| 项目 | 设置 |
| --- | --- |
| 主机 | macOS arm64，18 逻辑 CPU，48 GiB 内存 |
| 数据库 | PostgreSQL 17.6，pgvector 0.8.7，本机 loopback |
| PostgreSQL | `shared_buffers=128MB`、`work_mem=4MB`、`max_parallel_workers_per_gather=2`、`lc_collate=C` |
| 连接池 | `min_size=1`、`max_size=4` |
| 向量 | 128 维随机单位向量，cosine 距离；另测 20,000 行 4096 维 flat |
| 数据规模 | 100,000 行基线与 1,000,000 行主测 |
| 百万行布局 | 10 个交错租户，每租户 10 个组、每组 100 个目录、每目录 100 行 |
| 层级比例 | 每目录 L0/L1 各 1 行，L2 98 行；L2 中一半放在 `sub` 下 |
| 查询 | 限定租户 `a0`，Top-10；目录范围、深度及 level 分别测试 |
| 样本 | 每条件预热 5 次；十万行 flat 与 level 补测各 100 次，其余每条件 200 次 |
| 并发 | 闭环客户端线程，1 或 4 |

百万行布局包含 10,000 个 `(account_id, 目录路径)` 组合。数据通过二进制
COPY 导入，属于合成测试集；不是生产请求或写入吞吐测试。
当前配置与表达式索引按顺序测试，未控制 OS 页缓存。
20 个查询向量使用与首批数据相同的随机 seed（基准 seed 为 `20261113`），
对应语料前 20 行，其中 2 行属于租户 `a0`。Recall 仅描述这组有限的语料内查询。

共记录 98 组条件、14,800 次测量请求。各条件汇总见
[主测 CSV](/benchmarks/pgvector-directory-filtering-20261113/measurements.csv)
和 [level 补测 CSV](/benchmarks/pgvector-directory-filtering-20261113/levels-measurements.csv)。
CSV 保留条件、样本数、命中数、延迟分位数、QPS 和已测量的 Recall；
不包含逐请求结果 ID 或完整执行计划。

### 百万行：OV level 过滤

以下均为单并发 flat Top-10，单位为 ms。level 补测单独重建相同分布的
百万行数据，每条件测量 100 次。

| level | 范围 | 命中行 | 当前 p50 | 当前 p99 | 表达式索引 p50 | 表达式索引 p99 |
| --- | --- | --- | --- | --- | --- | --- |
| 2 | 单目录子树 | 98 | 144.28 | 197.60 | 0.48 | 0.95 |
| 2 | 空目录 | 0 | 139.95 | 174.44 | 0.38 | 0.82 |
| 2 | 租户根目录 | 98,000 | 145.01 | 252.04 | 143.62 | 209.90 |
| 0 | 单目录子树 | 1 | 7.29 | 9.18 | 0.60 | 1.21 |
| 1 | 单目录子树 | 1 | 6.98 | 9.53 | 0.50 | 0.93 |

L2 单目录的 p50 改善约 300 倍；租户根目录仍需处理约十万行，
路径索引的收益有限。L0/L1 已能利用层级索引，但路径索引进一步缩小了候选范围。

### 百万行：路径深度与并发

下面不额外限定 level，因此单目录包括 2 个摘要和 98 个文件。
单位为 ms，均为单并发 flat Top-10。

| 范围 | 命中行 | 当前 p50 | 当前 p99 | 表达式索引 p50 | 表达式索引 p99 |
| --- | --- | --- | --- | --- | --- |
| 租户根目录 | 100,000 | 155.16 | 214.50 | 139.60 | 192.18 |
| 一级目录子树 | 10,000 | 140.67 | 167.60 | 11.77 | 12.45 |
| 单目录子树 | 100 | 141.42 | 181.79 | 0.46 | 0.65 |
| `depth=1` | 51 | 143.07 | 176.39 | 0.55 | 0.92 |
| `depth=0` | 2 | 132.26 | 162.81 | 0.34 | 0.49 |
| 空目录 | 0 | 137.67 | 168.53 | 0.36 | 0.65 |

单目录、并发 4 时，QPS 从 13.6 提升到 3,524.9，p99 从 456.80 ms
降到 10.11 ms。这里包含连接池等待与额外连接的建立/释放成本，
不是 4 条固定保活连接的纯 SQL 吞吐。

### 十万行：HNSW 后过滤

HNSW 使用 `m=16`、`ef_construction=64`、`ef_search=100`，
`iterative_scan=strict_order`，扫描上限保持数据库默认值。
单并发下，以同一数据、同一组 20 个查询向量的 flat 结果作为真值，
Recall 分母为 `min(10, 命中行数)`。

| 范围 | 命中行 | flat p50 ms | HNSW 配置 p50 ms | 平均 Recall@10 |
| --- | --- | --- | --- | --- |
| 租户根目录 | 10,000 | 12.43 | 3.41 | 52.5% |
| 单目录子树 | 100 | 10.75 | 18.93 | 67.0% |
| 空目录 | 0 | 10.54 | 29.81 | 不适用 |

这三种范围的计划使用 HNSW Index Scan。单目录的一次计划在返回 10 行前
过滤掉 6,155 个候选，空目录过滤掉 19,916 个候选。配置 HNSW 不代表每次都
走 ANN：此次 `depth=0/1` 使用标量 Bitmap Scan 加精确排序，其 100% Recall
不能归因于 HNSW。

该组测试没有验证“表达式索引 + HNSW”的组合，也没有调优 HNSW 参数。
不能据此认定 HNSW 普遍不适合目录过滤，或将返回满 Top-10 等同于高召回。

### 4096 维的边界

4096 维仅测 20,000 行 flat，以下不限定 level，单并发：

| 范围 | 命中行 | 当前 p50 ms | 表达式索引 p50 ms |
| --- | --- | --- | --- |
| 租户根目录 | 2,000 | 13.13 | 14.13 |
| 单目录子树 | 100 | 5.40 | 4.19 |
| 空目录 | 0 | 4.77 | 3.44 |

该组的 URI 扫描、向量处理和 TOAST 成本与百万行 128 维测试不同。
当前 adapter 的 `vector` HNSW 上限为 2000 维，4096 维使用 flat；
不能把 128 维的索引加速比或 ANN 结果外推到百万行 4096 维场景。

## 如何验证与落地

先在代表性数据上添加上述索引并执行 `ANALYZE`，然后给实际 adapter SQL
加上 `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`。同时记录过滤命中数、
扫描/重查行数、heap block、p50/p95/p99、吞吐以及 ANN Recall。
窄范围的计划应能利用 scope 索引收窄候选；覆盖整个租户时，规划器可能继续
选择原有 account 索引或其他扫描方式。

索引名称不是索引定义校验。部署前确认目标 collection 的字段及表达式，
避免用 `IF NOT EXISTS` 掩盖同名但定义不同的索引。在线建索引可评估
`CREATE INDEX CONCURRENTLY`，它需要在事务块之外执行，并检查构建后索引
是否有效；本次压测使用的是加载完成后的普通 `CREATE INDEX`。
额外索引带来的磁盘占用和写入维护成本尚未单独测量。

手工索引的回退示例：

```sql
DROP INDEX public.context_account_uri_scope_idx;
```

后续自动化可复用同一个 SQL 表达式构造器，分别处理新 collection 建索引、
已有 collection 的迁移和物理索引定义校验。还需验证尾斜杠、空路径、
LIKE 特殊字符、不同深度、ACL 条件、目录倾斜、并发写入及不同数据库内存配置。
按候选规模选择精确或 ANN 检索属于后续方案，当前没有自动切换策略。

本次是本机预热后的合成压测，p99 样本有限，也未覆盖真实 ACL 复杂度、
完整服务链路、长期负载、冷缓存、租户物理聚集或分区。
结果用于判断索引方向，不能作为生产延迟或容量承诺。
