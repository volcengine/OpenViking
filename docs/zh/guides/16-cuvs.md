# 使用 NVIDIA cuVS 进行本地向量检索

OpenViking 的 `cuvs` 后端保留本地后端的记录持久化、标量索引、稀疏检索和故障恢复，只把 dense vector search 交给 NVIDIA cuVS。这样可以先验证 GPU 检索链路，而不需要重新实现一个完整的向量数据库。

## 环境要求

- Linux x86_64 或 aarch64
- 可见的 NVIDIA GPU；cuVS 26.06 预编译包要求 Ampere 或更新架构
- CUDA 12.2+；安装与本机 CUDA 大版本匹配的 Python 包
- Python 3.11+（cuVS 26.06 的 Python wheel 要求）

CUDA 12：

```bash
pip install -e .
pip install cuvs-cu12 'cupy-cuda12x[ctk]' --extra-index-url=https://pypi.nvidia.com
```

CUDA 13：

```bash
pip install -e .
pip install cuvs-cu13 'cupy-cuda13x[ctk]' --extra-index-url=https://pypi.nvidia.com
```

CuPy 的 `[ctk]` extra 会安装 cuVS Python 互操作路径所需的 CUDA toolkit
headers；即使宿主机已有 CUDA driver、但没有完整 toolkit，也建议保留该 extra。

## 配置

先用 `brute_force` 跑通精确检索：

```json
{
  "storage": {
    "workspace": "/data/openviking",
    "vectordb": {
      "backend": "cuvs",
      "distance_metric": "cosine",
      "cuvs": {
        "algorithm": "brute_force",
        "fallback_to_native": true,
        "filter_cache_size": 16
      }
    }
  }
}
```

数据量增大后可以切换到 CAGRA，并直接传入 cuVS 的构建与查询参数：

```json
{
  "storage": {
    "vectordb": {
      "backend": "cuvs",
      "cuvs": {
        "algorithm": "cagra",
        "build_params": {
          "graph_degree": 64,
          "intermediate_graph_degree": 128,
          "build_algo": "nn_descent"
        },
        "search_params": {
          "itopk_size": 64,
          "search_width": 1
        }
      }
    }
  }
}
```

距离语义与原本的 OpenViking 本地后端保持一致：cosine 会先做 L2 归一化再执行 inner product；L2 的返回分数仍为 `1 - squared_l2`，分数越大越相似。

## 数据类型与原生索引行为

启用 cuVS 不会改变 OpenViking 的默认后端，也不会重写原生 CPU 索引。正常的
collection metadata 仍为 `VectorIndex.Quant=int8`，因此 native fallback
继续使用现有的、带逐向量 scale 的 int8 量化。与此同时，当前 cuVS runtime
在 GPU 上保留 float32 shadow，因为 cuVS Python brute-force API 支持
float32/float16，但不能直接表示 OpenViking 的 scaled-int8 record 格式。

所以两条 dense search 路径不是等内存、等数值语义的比较：native 是在 CPU
量化表示上的精确检索，cuVS brute-force 是在保留的 float32 向量上的精确检索，
两者可能出现少量 score 或 neighbor ordering 差异。Benchmark 必须同时报告
两边的数据类型和 Recall@K，不能将结果描述为 equal-dtype 或 equal-memory。
这是首版 opt-in 集成的有意边界，现有 CPU 行为保持不变。

GPU 低精度存储作为后续显式能力实现，不做隐式 cast。第一步可以让 cuVS dataset
与 query 可配置为 float16，并以 float32 为 ground truth 测 Recall@K。与 native
兼容的 int8 需要单独设计，因为 OpenViking 使用逐向量 scale，而 cuVS
brute-force 不能直接接收这种 scaled-int8 表示。CAGRA int8 或 PQ compression
也应作为近似模式，单独报告 recall/latency/memory frontier。

## 最小功能验证

仓库提供的 smoke test 不依赖 embedding 或 VLM 服务：

```bash
python examples/cuvs_smoke.py

# 验证 CAGRA 图索引
python examples/cuvs_smoke.py --algorithm cagra
```

核心调用方式如下：

```python
from openviking.storage.vectordb.collection.local_collection import (
    get_or_create_local_collection,
)

collection = get_or_create_local_collection(
    meta_data={
        "CollectionName": "cuvs_smoke",
        "Fields": [
            {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            {"FieldName": "account_id", "FieldType": "string"},
            {"FieldName": "uri", "FieldType": "path"},
        ],
    },
    config={
        "dense_search": {
            "backend": "cuvs",
            "algorithm": "brute_force",
            "fallback_to_native": True,
        }
    },
)
collection.create_index(
    "default",
    {
        "IndexName": "default",
        "VectorIndex": {"IndexType": "flat", "Distance": "cosine"},
        "ScalarIndex": ["account_id", "uri"],
    },
)
collection.upsert_data(
    [
        {"id": "a", "vector": [1, 0, 0, 0], "account_id": "demo", "uri": "/docs/a"},
        {"id": "b", "vector": [0, 1, 0, 0], "account_id": "demo", "uri": "/docs/b"},
    ]
)
result = collection.search_by_vector(
    "default",
    dense_vector=[1, 0, 0, 0],
    limit=2,
    filters={"op": "must", "field": "account_id", "conds": ["demo"]},
)
assert [item.id for item in result.data] == ["a", "b"]
collection.close()
```

## 当前阶段的限制

- cuVS 只接管 dense search。sparse/hybrid query，以及无法安全转成 bitset 的过滤条件，会在 `fallback_to_native=true` 时走原生本地索引。
- 当前支持转成 cuVS prefilter 的 DSL 包括 `and`、`or`、`must`、`must_not`、`contains`、`range`、`range_out` 和 path depth。`date_time`、`geo_point` 暂时回退原生索引。
- `filter_cache_size` 会在 GPU 上保留最近重复使用的过滤 bitset，并在数据更新时失效；新过滤条件的第一次查询仍需扫描 host-side records 来计算 predicate。
- 每次 upsert/delete 后会在下一次查询时重建 GPU 索引。这保证了首版更新语义正确，但不适合写密集负载。
- cuVS 索引不作为权威持久化数据；进程重启时会从 OpenViking 本地 store 重建，因此不受 cuVS 跨版本序列化格式变化影响。
- `brute_force` 适合功能对齐和 ground truth；CAGRA 的 graph/search 参数需要在后续结合召回率、QPS、延迟和显存进行调优。
