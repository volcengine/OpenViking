# openGauss 向量库后端

OpenViking 通过 VectorDB Adapter 接入 openGauss DataVec。上层 `find/search` 调用方式不变；SQL、索引生命周期、连接池和分布式差异封装在 Adapter 内。建议使用包含目标 DataVec 索引能力的 openGauss 版本，并以实际数据库 catalog 和 `EXPLAIN` 验证能力。

## 安装

```bash
pip install "openviking[opengauss]"
```

## 正式配置入口

索引只能通过 `storage.vectordb.opengauss` 下的 `index_type + build_params + search_params` 配置。`custom_params` 不是正式 openGauss 参数入口。

```json
{
  "storage": {
    "vectordb": {
      "backend": "opengauss",
      "name": "context",
      "dimension": 512,
      "distance_metric": "cosine",
      "opengauss": {
        "host": "127.0.0.1",
        "port": 5432,
        "user": "gaussdb",
        "password": "replace-me",
        "db_name": "openviking",
        "mode": "standalone",
        "shard_count": 32,
        "index_type": "hnsw-pq",
        "build_params": {
          "m": 16,
          "ef_construction": 64,
          "pq_m": 8,
          "pq_ksub": 256
        },
        "search_params": {
          "ef_search": 100
        },
        "parallel_workers": 4,
        "maintenance_work_mem_mb": 128,
        "connection_pool_min_size": 1,
        "connection_pool_max_size": 8
      }
    }
  }
}
```

逻辑索引类型映射到 openGauss 物理 access method：

| `index_type` | 物理方法 | 自动构建选项 |
|---|---|---|
| `hnsw` | `hnsw` | 无量化 |
| `hnsw-pq` | `hnsw` | `enable_pq=on` |
| `hnsw-rabitq` | `hnsw` | `enable_rabitq=on` |
| `ivfflat` | `ivfflat` | 无量化 |
| `ivf-pq` | `ivfflat` | `enable_pq=on` |
| `ivf-rabitq` | `ivfflat` | `enable_rabitq=on` |
| `diskann` | `diskann` | 无量化 |

## 构建参数

- **HNSW**：`m` 取值 2–100；`ef_construction` 取值 4–1000，且不小于 `2*m`。
- **IVFFlat**：`lists` 取值 1–32768。训练型索引在数据写入后创建。
- **DiskANN**：`index_size` 取值 16–1000。
- **PQ**：仅 HNSW/IVF 支持 `enable_pq`、`pq_m`、`pq_ksub`；`pq_m` 必须整除 `storage.vectordb.dimension`，范围为 1–2000；`pq_ksub` 为 1–256。IVF-PQ 额外支持 `by_residual`。Adapter 不支持 DiskANN-PQ，也不接受 `diskann` 搭配 `enable_pq` 或 `pq_m`。
- **RabitQ**：`enable_rabitq`、`rabitq_refine_type` (`none`/`SQ8`/`FP32`)、`rabitq_fht`。PQ 与 RabitQ 不能同时启用；DiskANN 不支持 RabitQ。
- **并行构建**：`parallel_workers` 为 0–32，Adapter 设置表的 `parallel_workers` 后构建或重建索引。
- **构建内存**：顶层 `maintenance_work_mem_mb` 默认为 64 MiB，范围 16–1048576。Adapter 仅在 ANN `CREATE INDEX` 事务内执行 `SET LOCAL maintenance_work_mem`，不会修改数据库全局配置。IVFFlat、PQ 或较大数据集若报告构建内存不足，应按 openGauss 错误提示提高该值。

未知参数、非法布尔类型和不兼容组合会在 OpenViking 启动时直接拒绝。

## 查询参数

查询参数通过同一事务内的 `SET LOCAL` 应用：

| 索引 | `search_params` | openGauss 参数 |
|---|---|---|
| HNSW 系列 | `ef_search` | `hnsw_ef_search` |
| HNSW 系列 | `earlystop_threshold` | `hnsw_earlystop_threshold` |
| IVF 系列 | `probes` | `ivfflat_probes` |
| IVF-PQ | `ivfpq_kreorder` | `ivfpq_kreorder` |
| DiskANN 系列 | `probes` | `diskann_probes` |
| RabitQ 系列 | `rbq_query_bits`, `rbq_refinek` | 同名 GUC |

兼容键 `hnsw_ef_search`、`hnsw_earlystop_threshold`、`ivfflat_probes` 和 `diskann_probes` 仍可读取。

分布式模式下，Adapter 会在同一查询事务内先执行 `SET LOCAL spq.propagate_set_commands = 'local'`：spq 默认值为 `none`，否则上述 `SET LOCAL` 查询参数只影响 CN 会话，DN 分片扫描仍使用服务端默认参数。

## 索引生命周期与一致性

- 普通 HNSW 可以在空表创建；IVFFlat、PQ、RabitQ 和 DiskANN 在数据存在后创建。
- `bulk_ingest` 期间只写数据，最外层 scope 结束后构建一次训练型索引。
- Adapter 比对物理 access method、operator class 和构建选项；参数变化时替换并重新验证索引。
- 只有 `pg_indexes` catalog 验证成功后才写 `_ov_index_*` metadata。历史假 metadata 会在加载时清理。
- 查询前目标物理 ANN 索引必须存在且与配置匹配，否则失败，不静默退化为顺序扫描。

## 分布式模式

`mode=distributed` 时 `host/port` 必须指向 openGauss spq CN。Adapter 启动时检查：

1. `spq`/`spq_plugin_v2` 扩展；
2. `create_distributed_table`；若 CN 兼容 Citus，可选使用 `create_reference_table`；
3. 至少一个 active DN worker，且 `run_command_on_all_nodes('SELECT 1')` 全部成功；
4. 业务表和 metadata 表在 `pg_dist_partition` 中存在已验证记录。

业务表按 `id` hash 分片。标准 spqplugin_v2 将 metadata 分别按 `table_name` 和 `index_name` hash 分片；只有明确提供 `create_reference_table` 的兼容集群才使用 reference table。openGauss 官方分布式向量示例明确覆盖普通 HNSW，Adapter 在 `mode=distributed` 下也只允许普通 HNSW。真实 CN + 2 DN 验收确认普通 HNSW 在 DN 使用 `Ann Index Scan`；同一集群中 HNSW-PQ 和 IVF-PQ 会在 SPQ 尝试构建零行 CN 逻辑表索引时失败，HNSW-RabitQ、IVFFlat 和 IVF-RabitQ 的 DN 计划仍为顺序扫描。Adapter 因此会在连接或建表前拒绝除普通 HNSW 之外的所有 distributed 索引类型；PQ、RabitQ、IVF 和 DiskANN 请使用 standalone。

## 验收

真实 ANN 验收必须同时检查：

```sql
SELECT indexdef FROM pg_indexes WHERE tablename = 'context';
EXPLAIN SELECT id FROM context ORDER BY vector <=> '[...]'::vector LIMIT 5;
```

执行计划应出现目标索引的 `Ann Index Scan`。仅看召回结果不能证明 ANN 索引已生效。
