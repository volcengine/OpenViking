# OpenViking Vector Backend 性能 Benchmark

这个 benchmark 用来快速验收新的 VectorDB storage backend 在 OpenViking 场景下的表现。
它不直接调用 `CollectionAdapter`，而是走 `VikingVectorIndexBackend`：

- 建表使用 `CollectionSchemas.context_collection`
- 写入使用 `VikingVectorIndexBackend.upsert_many(..., ctx=...)`
- 查询使用 `VikingVectorIndexBackend.search_in_tenant(...)`
- 目录范围使用 `target_directories`，内部会编译成 `PathScope("uri", ..., depth=-1)`

它仍然不经过 OpenViking Server、AGFS、embedding 服务和 rerank；向量直接来自模拟数据或
dir-vector-dataset 的 `.fvecs` 文件。

## 先选模式

| 模式 | 参数 | 数据来源 | 适合场景 |
| --- | --- | --- | --- |
| 模拟数据 | `--workload synthetic` | runner 生成向量和目录路径 | 新 storage backend 首次接入、稳定复现、CI smoke |
| 真实数据 | `--workload dir-vector` | dir-vector-dataset 的 metadata、fvecs、ground truth | 目录过滤 + 向量检索的真实 workload 验收 |

默认是 `synthetic`。

## 再选规模

| 规模 | 参数 | synthetic 行为 | dir-vector 行为 |
| --- | --- | --- | --- |
| 少量 smoke | `--profile smoke` | 生成 128 行、8 个 query | 抽样前 128 行、前 8 个 query |
| 常规 standard | `--profile standard` | 生成 10000 行、100 个 query | 抽样前 10000 行、前 100 个 query |
| 压力 stress | `--profile stress` | 生成 100000 行、500 个 query | 抽样前 100000 行、前 500 个 query |
| 全量真实数据 | `--workload dir-vector --full` | 不适用 | 读取 dataset 全量 corpus 和 query |

`--rows`、`--queries`、`--batch-size`、`--concurrency`、`--top-k` 可以覆盖 profile 默认值。
`--full` 只对 `dir-vector` 生效；真实数据的向量维度从 `.fvecs` 读取，`--dim` 只影响 synthetic。

## 再选读写阶段

| 阶段模式 | 参数 | 行为 |
| --- | --- | --- |
| 读写一体 | `--mode read-write` | 默认模式；创建 collection、写入 OV context row，然后跑 count/get/search |
| 只写不读 | `--mode write-only` | 创建 collection 并 upsert；不跑 count、get、向量检索、过滤检索 |
| 只读不写 | `--mode read-only` | 不创建 collection、不 upsert；直接连接同名 collection 跑 count/get/search |

`read-only` 用于压测一个已经准备好的 collection。它必须和写入阶段使用相同的 `--config`、
配置里的 `vectordb.name`、`--run-id`、`--workload`、`--dataset` / `--full` / 抽样参数和
`--distance`，否则 runner 可能连到不同 collection，或用不同 query/ground truth 口径算报告。

`read-only` 不允许 `--drop-at-end`。如果想先准备数据再反复测读性能，先固定 `--run-id` 跑一次
`write-only`，后续用同一个 `--run-id` 跑 `read-only`。

## 快速开始

先准备一个 `ov.conf`，最小本地配置如下：

```json
{
  "storage": {
    "workspace": "./benchmark/results/vectordb_perf/local_data",
    "vectordb": {
      "backend": "local",
      "name": "vectordb_perf",
      "project": "default",
      "index_name": "default",
      "distance_metric": "cosine"
    }
  }
}
```

模拟数据少量 smoke：

```bash
.venv/bin/python -m benchmark.vectordb_perf.run \
  --config ./ov.conf \
  --workload synthetic \
  --profile smoke
```

模拟数据常规 benchmark：

```bash
.venv/bin/python -m benchmark.vectordb_perf.run \
  --config ./ov.conf \
  --workload synthetic \
  --profile standard
```

真实数据必须先下载。`--workload dir-vector` 默认会跑 `wiki` 和 `arxiv` 两个真实数据集，
runner 不会自动下载数据，也不会从 GitHub clone 后直接生成 `.fvecs`。先到
dir-vector-dataset 仓库 README 的 File Download 链接下载这两组数据文件：

https://github.com/KurtPatrickHere/dir-vector-dataset

下载后把文件解压或移动到同一个本地目录，例如：

```bash
mkdir -p benchmark/data/dir-vector-dataset-files
# 把下载得到的 corpus/query/vector/ground-truth 文件放进这个目录
```

`--dataset-root` 必须指向“直接包含数据文件”的目录，不是 GitHub repo 目录，也不是一个空目录。
runner 会先校验 `wiki` 和 `arxiv` 所需文件；缺文件时会直接列出 missing files 和下载地址。
调试时可以用 `--dataset wiki` 或 `--dataset arxiv` 只跑单个数据集。各 dataset 对应文件见下方“真实数据文件”。

真实数据少量抽样：

```bash
.venv/bin/python -m benchmark.vectordb_perf.run \
  --config ./ov.conf \
  --workload dir-vector \
  --dataset-root benchmark/data/dir-vector-dataset-files \
  --profile smoke
```

真实数据全量：

```bash
.venv/bin/python -m benchmark.vectordb_perf.run \
  --config ./ov.conf \
  --workload dir-vector \
  --dataset-root ./benchmark/data/dir-vector-dataset-files \
  --full
```

默认保留测试 collection，方便复查。需要运行后清理：

```bash
--drop-at-end
```

runner 会把配置里的 `name` 改成 `<name>_bench_<run-id>`，避免覆盖正式 collection。

分离写入和读取时要固定 `--run-id`。例如先写入：

```bash
.venv/bin/python -m benchmark.vectordb_perf.run \
  --config ./ov.conf \
  --workload dir-vector \
  --dataset-root ./benchmark/data/dir-vector-dataset-files \
  --full \
  --run-id ovbench_full_001 \
  --mode write-only
```

再只跑读取和检索：

```bash
.venv/bin/python -m benchmark.vectordb_perf.run \
  --config ./ov.conf \
  --workload dir-vector \
  --dataset-root ./benchmark/data/dir-vector-dataset-files \
  --full \
  --run-id ovbench_full_001 \
  --mode read-only \
  --output-dir benchmark/results/vectordb_perf/ovbench_full_001_read
```

## 写入数据结构

两种 workload 最终都会转换成 OV context row，写入字段如下：

| 字段 | 示例 | 说明 |
| --- | --- | --- |
| `id` | `syn-1` | 主键；真实数据使用原始 doc id |
| `uri` | `viking://resources/bench/synthetic/d0_1/d1_0/syn-1` | OV URI；目录过滤只看这个字段 |
| `type` | `file` | OV context type 内的资源类型 |
| `context_type` | `resource` | 固定按资源检索 |
| `vector` | `[0.1, ...]` | dense vector；synthetic 生成，dir-vector 读取 `.fvecs` |
| `created_at` / `updated_at` | `2026-01-01T00:00:01Z` | 确定性时间戳 |
| `active_count` | `0` | OV 访问计数 |
| `level` | `2` | L2 detail/content 层；查询也限制 `level=[2]` |
| `name` | `syn-1` | 展示名 |
| `description` / `tags` / `search_tags` | `cat_1` | 标量字段 |
| `abstract` | `source_id:syn-1 category:cat_1` | 检索返回字段，也用于 ground truth 轻量匹配 |
| `content` | `benchmark content ...` | text 字段，匹配真实 OV schema |
| `account_id` | `bench_account` | 用于 backend tenant filter |
| `owner_user_id` | `bench_user` | OV owner 字段 |

所以这个 benchmark 不是“普通向量库 row + filter”，而是 OV 的 context collection row。

## 测试阶段

每次运行会创建一个带 run id 后缀的测试 collection，然后执行：

| 阶段 | 内容 |
| --- | --- |
| `setup` | 创建 collection 和索引 schema |
| `ingest` | 通过 backend upsert OV context row |
| `validate` | count、get、过滤 count |
| `vector_search` | `search_in_tenant`，无指定目录 |
| `filtered_vector_search` | `search_in_tenant`，带 `target_directories` |
| `cleanup` | 仅在传 `--drop-at-end` 时删除测试 collection |

功能错误会导致非零退出码；性能慢只记录到报告里。

`ingest` 会把每个 `--batch-size` 分组通过一次 backend `upsert_many` 调用写入，
而不是逐行调用 `upsert`。对本地后端，这会把每批记录合并到一次 store 持久化操作；
store 与 vector index 之间不提供跨组件事务。远端 adapter 还可根据请求上限进一步拆批，
因此一次 `upsert_many` 也不代表一个远端请求或跨子批事务。批量接口只提供完整记录
upsert；需要保留未提供字段的 partial update 仍使用单条 `upsert`。当前 runner 是该接口
在仓库内的首个调用方；本改动不自动改变现有 embedding、migration 或 ovpack 写入流程。

`--mode write-only` 只会出现 `setup` / `ingest` / 可选 `cleanup` 阶段。
`--mode read-only` 只会出现 `validate` / `vector_search` / `filtered_vector_search` 阶段。
运行中会向 stderr 输出进度，包括当前 collection、写入条数、查询条数和速率。

进度输出示例：

```text
[vectordb_perf] start run_id=ovbench_full_001 workload=dir-vector:wiki mode=write-only records=1941679 queries=456 dim=1024
[vectordb_perf] collection=vectordb_perf_bench_ovbench_full_001_wiki
[vectordb_perf] setup: create collection
[vectordb_perf] setup: collection ready
[vectordb_perf] ingest: start batch_size=1000
[vectordb_perf] ingest: 120000/1941679 (6.2%), 3950.4/s
[vectordb_perf] ingest: 240000/1941679 (12.4%), 4021.7/s
```

## 真实数据文件

`--workload dir-vector` 使用
[KurtPatrickHere/dir-vector-dataset](https://github.com/KurtPatrickHere/dir-vector-dataset)
发布的数据文件。需要先手动下载；下载入口在该仓库 README 的 File Download / Google Drive
链接里。

默认真实数据 benchmark 会跑 `wiki` 和 `arxiv` 两行。`arxiv_category` 不在默认两数据集里，
需要时显式传 `--dataset arxiv_category`。

默认两个真实数据集的全量规模如下，行数按 `.fvecs` 文件统计，也是 runner 的实际读取口径：

| `--dataset` | corpus 向量条数 | query 条数 | 向量维度 |
| --- | ---: | ---: | ---: |
| `wiki` | 1,941,679 | 456 | 1024 |
| `arxiv` | 2,763,543 | 1,000 | 1024 |

`--dataset all --full` 会依次跑这两组数据，合计 4,705,222 条 corpus 向量和 1,456 个 query。

| `--dataset` | 期望文件 |
| --- | --- |
| `wiki` | `dbpedia_dir_2m_corpus.jsonl`、`dbpedia_dir_2m_corpus_vectors.fvecs`、`dbpedia_dir_2m_query.jsonl`、`dbpedia_dir_2m_query_vectors.fvecs`、`dbpedia_dir_2m_groundtruth.tsv` |
| `arxiv` | `arxiv_corpus_metadata.json`、`arxiv_corpus_vectors.fvecs`、`arxiv_query_constraint.json`、`arxiv_query_vectors.fvecs`、`arxiv_ground_truth.txt` |
| `arxiv_category` | `arxiv_corpus_metadata.json`、`arxiv_corpus_vectors.fvecs`、`arxiv_category_query_constraint.json`、`arxiv_category_query_vectors.fvecs`、`arxiv_category_ground_truth.txt` |

把这些文件放到同一个目录，然后用 `--dataset-root` 指向该目录。

## 其他 ov.conf 示例

Volcengine VikingDB：

```json
{
  "storage": {
    "vectordb": {
      "backend": "volcengine",
      "name": "vectordb_perf",
      "project": "default",
      "index_name": "default",
      "distance_metric": "cosine",
      "volcengine": {
        "region": "cn-beijing",
        "ak": "YOUR_AK",
        "sk": "YOUR_SK"
      }
    }
  }
}
```

自定义 adapter 类：

```json
{
  "storage": {
    "vectordb": {
      "backend": "my_project.adapters.MyCollectionAdapter",
      "name": "vectordb_perf",
      "project": "default",
      "index_name": "default",
      "custom_params": {
        "endpoint": "http://127.0.0.1:9000",
        "token": "YOUR_TOKEN"
      }
    }
  }
}
```

## 常用参数

| 参数 | 说明 |
| --- | --- |
| `--config` | OpenViking 配置文件路径 |
| `--output-dir` | 报告输出目录；默认在 `benchmark/results/vectordb_perf/<run-id>/` |
| `--run-id` | 本次运行标识；会进入 collection 名和报告 |
| `--profile` | `smoke`、`standard`、`stress` |
| `--mode` | `read-write`、`write-only`、`read-only`；默认 `read-write` |
| `--workload` | `synthetic` 或 `dir-vector` |
| `--dataset-root` | dir-vector 数据目录 |
| `--dataset` | `all`、`wiki`、`arxiv`、`arxiv_category`；默认 `all`，即依次跑 `wiki` 和 `arxiv` |
| `--full` | dir-vector 全量读取 corpus 和 query；不加时按 `--rows` / `--queries` 抽样 |
| `--rows` | synthetic 行数；dir-vector 抽样行数 |
| `--queries` | 查询数 |
| `--dim` | synthetic 向量维度 |
| `--batch-size` | 每次 backend `upsert_many` 调用的记录数；远端 adapter 可按请求上限再拆分 |
| `--concurrency` | 查询并发 |
| `--top-k` | 检索返回条数 |
| `--distance` | `ip`、`l2`、`cosine` |
| `--drop-at-end` | 运行结束后删除测试 collection |

## 报告输出

主要输出文件：

| 文件 | 内容 |
| --- | --- |
| `summary_zh.md` | 中文摘要，优先看这个；包含数据规模、Recall@K、QPS、延迟和环境 |
| `run_summary.json` | 汇总结果，适合自动化读取；包含 `workload`、`quality`、`phase_summary` |
| `events.jsonl` | 每次操作的延迟、成功状态和错误 |
| `phase_summary.csv` | 按阶段聚合的吞吐和延迟 |
| `environment.json` | runner 本机环境观测 |
| `run_config.json` | 本次运行参数 |

`summary_zh.md` 里重点看两张表：

| 表 | 内容 |
| --- | --- |
| 数据规模 | `records` 是本次计划读取的数据条数，`inserted` 是实际写入 backend 的条数，`queries` 是实际查询数 |
| 召回与 QPS | `vector_search` 和 `filtered_vector_search` 的 QPS、平均延迟、P95 延迟、gt_recall@K |

gt_recall@K 按 query 的 ground truth 命中率统计。`--full` 才是官方全量 recall；
非 `--full` 的真实数据报告会标成 `sampled_subset`，只用于 smoke sanity，不代表官方全量 recall。

## 资源限制口径

runner 会记录本机 CPU、内存、平台、GPU 探测和 cgroup 信息，但不主动限制资源。
如果 backend 连接的是远端服务，远端实例规格需要人工记录或用部署系统控制。

本地要控制资源，建议在 Docker、cgroup 或 CI runner 层限制 CPU/内存，然后把报告里的
`environment.json` 和部署规格一起归档。
