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
        "dtype": "float32",
        "max_concurrent_gpu_searches": 1,
        "micro_batching_enabled": false,
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

### 显存感知自动模式

如果希望保留 `local` 为默认 backend、只在 GPU 有足够空闲显存时自动启用 cuVS，
可以打开以下开关：

```json
{
  "storage": {
    "vectordb": {
      "backend": "local",
      "cuvs": {
        "auto_enable": true,
        "algorithm": "brute_force",
        "auto_memory_reserve_mb": 1024,
        "auto_memory_safety_factor": 2.0,
        "auto_filter_native_threshold": 2000,
        "auto_path_filter_native_threshold": 200,
        "auto_background_rebuild": true,
        "auto_rebuild_debounce_ms": 500
      }
    }
  }
}
```

每次 lazy build/rebuild 前，auto 模式会读取当前空闲显存，并根据配置的 `dtype`
估算 device vector payload、CAGRA graph/intermediate graph（如适用）和
filter-bitset cache，再乘以 `auto_memory_safety_factor`，同时保留
`auto_memory_reserve_mb`。如果预算不足，
或者 cuVS/GPU 不可用，本次查询继续使用未改变的 native index；cuVS index 保持
dirty，后续查询会在显存释放后重新尝试。通过 admission 后若仍遇到 GPU allocation
failure，也会回退 native。显式配置 `backend: "cuvs"` 时仍保持 fail-fast，不经过
这层自动判断。

同一进程内的 local collection 会按 GPU 协调 build 和 admission，避免两个并发
build 都基于同一份过期 free-memory 观测通过准入。不同 GPU 彼此独立，warmed
search 也不会被这个协调器串行化。

auto 模式还会使用 native scalar index 返回的候选数做 filtered query 延迟路由：
候选数不超过 `auto_filter_native_threshold` 时使用 native vector recall；路径过滤
采用更低的 `auto_path_filter_native_threshold`，因为宽 URI 子树的 Trie 遍历和
bitmap union 本身可能占主要开销。默认阈值分别为 2,000 和 200，设为 0 可关闭
对应路由。阈值与硬件、维度和工作负载有关。显式 `backend: "cuvs"` 对支持的
dense query 仍固定使用 cuVS。

`auto_background_rebuild` 默认关闭。开启后，连续 mutation 会按
`auto_rebuild_debounce_ms` 合并，worker 在不持有跨后端 mutation 锁的情况下构建
新的 immutable GPU snapshot。默认 500 ms 用于避免普通 ingest 的中间 batch
反复触发构建。对于边界明确、由多次调用组成的 bulk load，可把所有写入放在
`async with backend.bulk_ingest(ctx=ctx):` scope 内：native 可见性和持久化仍按
每次调用推进，但 derived GPU maintenance 会延迟到最外层 scope 退出后只调度一次。
该 scope 只是 maintenance hint，不提供事务或原子性；退出 scope 只负责调度 rebuild，
本身不等待 GPU ready。vector backend benchmark 会额外在正式计时 search 前显式等待
最终 snapshot；无法识别 bulk 边界的调用方仍可按实际 batch 间隔调整 debounce。Auto
仍为显式启用；未开启 Auto/background rebuild 时，该 scope 对派生维护为 no-op，不改变
原生 CPU 检索、写入与 dtype 行为。snapshot dirty 期间查询直接使用当前 native index，
不会把 GPU build 时间转化成请求排队时间。worker 只在 record generation 仍匹配时
原子提交 label layout 和 GPU snapshot；过期 build 会被丢弃，并只重建最新一代。

## GPU 显存占用

使用默认的 `dtype: "float32"` 时，brute-force 的主要常驻 device payload 为
`N * dimension * 4` bytes。显式设置 `dtype: "float16"` 后，device payload
降为 `N * dimension * 2` bytes。CAGRA 还需要约 `N * graph_degree * 4` bytes
保存 graph，构建期间可能需要 `N * intermediate_graph_degree * 4` bytes 的
intermediate graph。每个缓存 filter bitset 约占 `ceil(N / 32) * 4` bytes。

之前的 index-only 测试使用 `cudaMemGetInfo` 记录 build 前后的显存增量；下表每项
均为 5 个干净进程的中位数：

| 数据集 | cuVS 算法 | 实测 GPU 增量 |
| --- | --- | ---: |
| 100K x 768D | brute-force | 294 MiB |
| 1M x 768D | brute-force | 2.9 GiB |
| 100K x 1024D | brute-force | 392 MiB |
| 1M x 1024D | brute-force | 3.9 GiB |
| 1,183,514 x 100D | brute-force | 452 MiB |
| 1,183,514 x 100D | CAGRA | 872 MiB |

这些数值是 build 完成后的常驻增量，不是采样得到的 peak VRAM。allocator 状态、
cuVS 版本、CAGRA 参数、query batch 和并行 GPU workload 都可能进一步提高峰值；
它们也不包含这些进程在 build 前观测到的约 327 MiB CUDA runtime/context 基线。
因此 auto 模式会先初始化 runtime、读取剩余空闲显存，再应用保守 safety factor
和独立 reserve，而不会只按 vector payload 准入。

距离语义与原本的 OpenViking 本地后端保持一致：cosine 会先做 L2 归一化再执行 inner product；L2 的返回分数仍为 `1 - squared_l2`，分数越大越相似。

## 数据类型与原生索引行为

启用 cuVS 不会改变 OpenViking 的默认后端，也不会重写原生 CPU 索引。正常的
collection metadata 仍为 `VectorIndex.Quant=int8`，因此 native fallback
继续使用现有的、带逐向量 scale 的 int8 量化。与此同时，cuVS device dataset
和 query 使用配置的 `dtype`：默认是 float32，也可以显式选择 float16。host
record shadow 保存预处理后的 Python 浮点值；仅在创建 device dataset 和 query
时将它们 cast 为配置的 dtype。cuVS Python brute-force API 支持这两种 device
表示，但不能直接表示 OpenViking 的 scaled-int8 record 格式。

所以两条 dense search 路径不是等内存、等数值语义的比较：native 是在 CPU
量化表示上的精确检索，cuVS brute-force 是在保留的 float32 或 float16 device
表示上的精确检索，两者可能出现少量 score 或 neighbor ordering 差异。
Benchmark 必须同时报告两边的数据类型和 Recall@K，不能将结果描述为
equal-dtype 或 equal-memory。
这是首版 opt-in 集成的有意边界，现有 CPU 行为保持不变。auto 模式会根据 filter
候选阈值在两种表示之间选择；要求固定数值表示的应用应使用显式 backend，或将
native 路由阈值设为 0。

GPU 低精度存储是显式能力，不做隐式 cast。设置 `dtype: "float16"` 会把 cuVS
dataset 和每个 query 同时 cast 为 float16，brute-force 与 CAGRA 都不使用混合
query/index dtype。这是存储 cast，不是逐向量量化，必须以默认 float32 为 ground
truth 报告 Recall@K。与 native 兼容的 int8 仍需单独设计，因为 OpenViking 使用
逐向量 scale，而 cuVS brute-force 不能直接接收这种 scaled-int8 表示。CAGRA
int8 或 PQ compression 也应作为近似模式，单独报告 recall/latency/memory frontier。

集成使用 immutable GPU snapshot 和可复用的 cuVS resource/CUDA stream。host 侧
filter 与 snapshot 工作可以并行，但 `max_concurrent_gpu_searches` 默认是 1：
单 query brute-force 通常受显存带宽限制，并发 kernel 可能互相争抢带宽、反而降低
吞吐。只有在目标 GPU 与真实 workload 上测得收益后，才建议显式调大该值。

### 可选的请求微批处理

精确 brute-force 路径可以把兼容的并发请求合并为一次 cuVS matrix-query 调用：

```json
{
  "storage": {
    "vectordb": {
      "backend": "cuvs",
      "cuvs": {
        "algorithm": "brute_force",
        "max_concurrent_gpu_searches": 1,
        "micro_batching_enabled": true,
        "micro_batching_max_batch_size": 8,
        "micro_batching_max_wait_ms": 1.0
      }
    }
  }
}
```

scheduler 只会合并使用同一个 immutable GPU snapshot、同一个 prepared filter、
同一个实际 top-k 的请求；GPU 返回的每一行会映射回原请求，因此标量/路径过滤和
结果条数语义不变。

当 immutable snapshot clean、属于当前 generation，且请求没有 filter 或命中已准备好的
device filter cache 时，可走 warm admission fast path。该路径会 pin snapshot/filter，
并在 caller 不获取 device-search gate 的情况下直接入队。dirty、cold 或 stale snapshot，
device filter cache miss/eviction、rebuild 和 device filter materialization 仍走 gated
preparation。准备完成后，caller 先入队并释放 gate，再等待结果；只有 micro-batch worker
会在持有 device-search gate 时执行 matrix search，所以 caller 不会持 gate 等待 worker。

collection window 是延迟与吞吐的权衡。它只限制 scheduler 为收集兼容请求而主动等待的
时间：从最早的 compatible request 起最多主动等待配置值；它不是 enqueue-to-dispatch
latency 上限。worker 调度、前一个 GPU call 或 gated device preparation 都可能使实际
dispatch 更晚。并发充足时，最多由配置上限数量的 query 共用一次 GPU call。

参数约束如下：

- `micro_batching_max_batch_size` 范围为 1 到 8；
- `micro_batching_max_wait_ms` 范围为 0 到 100 ms；设为 `0` 表示不主动等待，但仍可
  opportunistically 合并已经同时在队列中的兼容请求；
- micro-batching 仅支持 `algorithm: "brute_force"`，并要求
  `max_concurrent_gpu_searches: 1`。

该能力默认关闭，是 OpenViking 自己的 micro-batcher，不等同于 cuVS 官方名为
Dynamic Batching 的组件。首版只支持 exact brute-force；CAGRA 和并发 dispatch 多个
batch 会在独立验证后再开放。Auto 模式也可使用这些选项，但被路由到原生 CPU 的请求
不会进入 GPU batch queue。single-row 与 matrix-query 在近似并列分数处可能有顺序
差异，调参时应同时验证结果集合重合度和 score。

## 最小功能验证

仓库提供的 smoke test 不依赖 embedding 或 VLM 服务：

```bash
python examples/cuvs_smoke.py

# 验证 CAGRA 图索引
python examples/cuvs_smoke.py --algorithm cagra

# 验证显式 float16 路径
python examples/cuvs_smoke.py --dtype float16
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

- cuVS 只接管 pure dense search；sparse/hybrid query 在 `fallback_to_native=true` 时走原生本地索引。
- local 集成通过 native scalar/path index 生成 prefilter，因此继承原生 DSL、`date_time`、`geo_point` 和 path depth 的过滤语义，而不是在 Python 重复实现。
- 每次 GPU rebuild 会向 native engine 注册一次 cuVS label 顺序。新过滤条件直接复用 native scalar/path index 的 bitmap，再投影为 cuVS row bitset，不再用 Python 扫描所有 host-side records。
- `filter_cache_size` 会保留最近使用的 GPU bitset 或 native 路由决策，并在数据更新时失效；auto 模式在进入 cuVS search 前预判候选数，不同的首次过滤条件可通过 native engine 的共享读路径并行计算，命中已缓存的 native 路由时则直接进入 native index。generation 校验会阻止跨 mutation 计算出的旧结果写入路由缓存。
- GPU index 使用 immutable snapshot 和可复用的 cuVS resources/CUDA stream；默认关闭的 micro-batching 可让 compatible warm request 绕过 caller 侧 gate 入队，并由唯一持有 device-search gate 执行 matrix search 的 worker 合批。mutation 和 snapshot commit 使用跨后端写锁。
- 默认情况下，每次 upsert/delete 后仍由下一次查询同步重建；开启 `auto_background_rebuild` 后，dirty 期间查询走 native，连续写被合并为后台重建。
- cuVS 索引不作为权威持久化数据；进程重启时会从 OpenViking 本地 store 重建，因此不受 cuVS 跨版本序列化格式变化影响。
- `brute_force` 适合功能对齐和 ground truth；CAGRA 的 graph/search 参数需要在后续结合召回率、QPS、延迟和显存进行调优。
