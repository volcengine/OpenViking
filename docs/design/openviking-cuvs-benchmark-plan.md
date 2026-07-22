# OpenViking cuVS benchmark plan

相关文档：[集成计划](./openviking-cuvs-integration-plan.md)、
[初步结果](../../benchmark/cuvs/PRELIMINARY_RESULTS.md)。

## 目标

本 benchmark 不追求单个“最高 QPS”数字，而是回答四个对集成决策更有用的问题：

1. 在精确检索下，cuVS brute-force 相比 OpenViking native flat 的延迟和吞吐拐点在哪里？
2. 在相同 Recall@K 下，CAGRA 能提供多少延迟、吞吐和容量收益？
3. 标量过滤、Python 调用和记录回表后，GPU 优势还剩多少？
4. 当前延迟重建策略对冷启动和写后首查造成多大代价？

cuVS 官方建议从 build time、search quality 和 search performance 三个维度比较向量索引；
本方案沿用这个原则，并增加 OpenViking 集成层与 mutation lifecycle 的测量。

参考：

- [cuVS: comparing vector search index performance](https://docs.rapids.ai/api/cuvs/stable/getting_started/)
- [cuVS Bench](https://docs.rapids.ai/api/cuvs/stable/cuvs_bench/)

## 被测后端

| 名称 | OpenViking 配置 | 作用 |
| --- | --- | --- |
| Native exact | `backend=local`, `IndexType=flat` | CPU flat 基线；精确性相对于其实际存储表示定义 |
| cuVS exact | `backend=cuvs`, `algorithm=brute_force` | GPU 在配置的 device dtype（默认 float32，可显式选择 float16）表示上的精确检索 |
| cuVS ANN | `backend=cuvs`, `algorithm=cagra` | 在固定 Recall@K 下比较 ANN 性能 |

可以使用 `cuvs-bench` 的 HNSWlib 或其他算法作为算法级参考，但不能把它们当作
OpenViking 端到端基线，因为它们没有经过相同的过滤、label mapping 和记录回表路径。

### Dtype 与非侵入性原则

cuVS 是 opt-in dense-search sidecar，不修改默认 backend，也不改变 native CPU
索引的默认 `int8` 量化。cuVS host shadow 保存预处理后的 Python 浮点值；仅在创建
device dataset 和 query 时将它们 cast 为配置的 `dtype`，默认是 float32，也可以
显式选择 float16。因此：

- L1 index-only harness 在隔离 kernel 时显式锁定 float32 CPU / float32 GPU；评估低精度
  frontier 时另跑配置为 float16 的 cuVS，并相对于 exact reference 报告 Recall@K；
- L2/L3 保留真实应用行为，即 native int8 / cuVS 配置的 device dtype，并同时报告实际
  dtype、Recall@K 和内存占用；
- L2/L3 不能表述为等 dtype、等内存或完全相同数值语义的比较；
- native fallback 始终继续使用原有 int8 CPU index，启用 cuVS 不触发迁移或重写。

低精度 GPU 路线作为显式配置分开评估：使用 `dtype=float16` 将 cuVS dataset/query
cast 为 float16，并与 float32 exact ground truth 比较 Recall@K、延迟和显存；随后再
评估 CAGRA int8 或 VPQ。native 的逐向量 scale int8 不能直接映射到 cuVS
brute-force，若要求数值兼容，需要自定义 int8 distance/top-k 或候选召回后按 scale
rerank，不能用简单 cast 代替。

## 与 agent-memory benchmark 的关系

OpenViking 仓库已经包含多类 benchmark，它们可以作为 cuVS 集成的质量与端到端 guardrail：

| 仓库目录 | 主要问题 | 在 cuVS 评测中的用途 |
| --- | --- | --- |
| `benchmark/locomo/` | 长期对话记忆、跨 session QA | memory quality 回归；比较 local exact、cuVS exact、CAGRA |
| `benchmark/longmemeval/openviking/` | 信息提取、多 session/时间推理、更新与拒答 | 首选的长期记忆质量 benchmark |
| `benchmark/RAG/` | LoCoMo、Qasper、FinanceBench、SyllabusQA | 检查通用 RAG 质量和领域差异 |
| `benchmark/tau2/llm/` | memory 对 agent tool-task success 的贡献 | 端到端任务收益，不用于隔离 vector-search 性能 |
| `benchmark/custom/session_contention_benchmark.py` | Server 混合负载的 QPS、延迟、错误率和积压 | 扩展为 `local`/`cuvs` backend matrix，测真实服务退化 |

这些质量 benchmark 不能替代 ANN benchmark。LoCoMo 只有少量长对话，LongMemEval-S 也以
500 个问题和每题有限 session 为主；单个 user 的向量规模通常不足以让 GPU 展现优势。它们最适合
验证“替换后端后答案质量不下降”，而不是证明最大 QPS。

领域内建议优先考虑以下公开 benchmark：

1. [LoCoMo（ACL 2024）](https://aclanthology.org/2024.acl-long.747/)：使用广泛，覆盖长期对话
   QA、事件总结和多模态对话；适合作为兼容性基线。
2. [LongMemEval（ICLR 2025）](https://github.com/xiaowu0162/LongMemEval)：覆盖信息提取、
   multi-session reasoning、时间推理、knowledge update 和 abstention；官方 harness 同时支持
   retrieval 与 QA 评测，适合做主要 memory-quality 结果。
3. [MemBench（Findings of ACL 2025）](https://aclanthology.org/2025.findings-acl.989/)：同时考虑
   factual/reflective memory、participation/observation 场景，以及 accuracy、recall、capacity、
   temporal efficiency，适合补充系统容量与效率。
4. [MemoryAgentBench（ICLR 2026）](https://github.com/HUST-AI-HYZ/MemoryAgentBench)：覆盖
   accurate retrieval、test-time learning、long-range understanding 和 conflict resolution，适合
   评估更广义的 agent memory 能力。
5. [LongMemEval-V2](https://github.com/xiaowu0162/LongMemEval-V2)：面向 agent trajectory memory，
   最大 haystack 达到约 1.15 亿 token，并同时考察 answer accuracy 和 query latency；它是后续
   展示“大规模 agent memory + cuVS”价值最匹配的公开方向。

推荐最终报告采用三张相互独立的 scoreboard：

- **Vector index performance**：本方案的 L1/L2，展示 latency、QPS、recall、build 和 memory。
- **Memory quality**：LongMemEval 为主、LoCoMo 为辅，固定 embedder、reader、judge、top-k 和
  context budget，只改变 vector backend。
- **Agent task utility**：TAU-2 或 MemoryAgentBench，比较 no-memory、native-memory 和
  cuVS-memory 的任务成功率与总成本。

为了让公开 memory benchmark 达到能够体现 GPU 的规模，不应简单复制相同 memory。建议使用
LongMemEval 的可扩展 filler sessions、LongMemEval-V2 trajectory haystack，或者独立的真实噪声
corpus，把总索引扩展到 100K/1M/5M/10M，同时保留原题 evidence 作为 retrieval ground truth。

## 三层 benchmark

### L1：索引层

只测 native flat、cuVS brute-force 和 CAGRA 的 build/search，不包含 embedding、HTTP、
持久化写入和结果回表。这一层最容易解释 GPU kernel 与算法本身的收益。

测量：

- build wall time，其中单独记录 host-to-device transfer；
- warm search 的 p50、p95、p99 和 QPS；
- Recall@10、Recall@100；
- peak host RSS、peak GPU memory 和 index size。

### L2：OpenViking collection 层

通过 `search_by_vector()` 发起查询，包含 Python adapter、filter translation、label mapping
和记录回表，但使用预先生成的 query vectors，避免 embedding 服务掩盖检索差异。

这一层应作为当前 feature integration 的主要结果，因为它测量了用户实际经过的代码路径。

### L3：服务层

通过 OpenViking server 发起请求，分别报告：

- vector-ready 请求：客户端直接提供 query vector；
- full retrieval 请求：包含 embedding 和其他业务处理。

full retrieval 只用于判断向量检索在总延迟中的占比，不用于证明 cuVS kernel 的加速比。

## 数据集矩阵

先跑能够代表 OpenViking 的 synthetic workload，再用公开 ANN 数据集交叉验证。

| 维度 | 建议取值 | 说明 |
| --- | --- | --- |
| Vector count | 10K、100K、1M、5M、10M | 用来找到 GPU break-even point |
| Dimension | 128、768、1024 | 128 对齐 SIFT；768/1024 接近常见 embedding |
| Metric | cosine/IP，补充 L2 | cosine 在两端使用相同的 L2 normalization |
| K | 10、100 | 同时覆盖常见 retrieval 与较大候选集 |
| Query batch | 1、8、32、128、512 | 区分低延迟与吞吐场景 |

公开数据集优先使用 cuVS Bench 已支持且带 ground truth 的数据：

- SIFT-1M，128D，L2；
- GloVe-1.1M，100D，angular；
- Deep-10M，96D，angular；
- Wiki-all 1M/10M，768D，用于更接近 RAG embedding 的场景。

## 公平性与正确性

- 同一份 base vectors、query vectors、metric、K 和 normalization 输入所有后端。
- 用 native exact 或 cuVS brute-force 生成 ground truth；精确后端必须达到 Recall@K = 1.0。
- CAGRA 只在固定 recall bucket 下比较，例如 Recall@10 >= 0.95 和 >= 0.99。
- CAGRA 参数至少扫描：`graph_degree={32,64}`、
  `intermediate_graph_degree={64,96}`、`itopk_size={32,64,128}`。
- 固定 CPU threads、NUMA placement 和 GPU 数量，并在结果中记录硬件、软件版本和配置。
- 每组先 warm up，再至少执行 10K queries；重复五轮，报告中位数与离散程度。
- cold build、cold first query 和 warm steady state 分开报告。

## 过滤 benchmark

过滤会影响 CAGRA 的搜索路径，也是本集成区别于裸 cuVS benchmark 的关键部分。

每个规模测试以下 selectivity：100%、10%、1%、0.1%，并包含两种分布：

- uniform：符合条件的 label 在数据集中均匀分布；
- clustered/skewed：符合条件的数据集中在少数类别或路径前缀。

每个过滤场景重新生成 filtered ground truth，同时报告 Recall@K、p95 和 QPS。不能用未过滤
ground truth 计算 recall。

## 生命周期 benchmark

当前实现保留 host shadow，并在 upsert/delete 后把 GPU index 标脏；下一次查询执行批量重建。
因此必须独立测量：

- 首次建库时间；
- 进程重启后的 rehydrate + rebuild 时间；
- 1、100、10K 和 1% 数据变更后的 write latency；
- 从 write 返回到下一次成功查询的总时间；
- rebuild 期间的 peak host/GPU memory。

这个结果决定延迟重建适合的 workload 边界。steady-state search 图不能把 rebuild 时间平均掉，
否则会对写密集 workload 产生误导。

## 当前集成的吞吐限制

默认路径仍按一次公开 API 请求提交一个 query。当前已提供默认关闭、仅适用于 brute-force 的
request micro-batching：它可把兼容的并发单 query 请求合并为最多 8 行的 matrix-query。
对于当前 generation 的 clean immutable snapshot，无 filter 或 device filter cache hit 可通过
warm fast path 直接入队，不获取 caller 侧 device gate；dirty/cold snapshot、filter cache miss
和 device filter preparation 仍先走串行的 gated preparation。入队后只有 micro-batch worker
会在持有 device-search gate 时执行 GPU matrix search，caller 不会持 gate 等待结果。

这消除了旧版“一次 GPU search 全程持有 caller/index lock”的主要并发瓶颈，但每次 dispatch
仍会创建 query/result device array，并在返回前把结果同步到 host。因此：

- batch > 1 的 L1 结果仍代表显式 vector-index batch 的能力上限，不等同于公开 API 吞吐；
- L2/L3 应继续以单 query 请求为主，同时分别测默认关闭和 opt-in micro-batching，并记录
  实际 batch-size 分布、`batch_wait`、`gpu_gate_queue`、QPS 与 P95；
- 下一阶段优先评估 persistent query/result buffers、allocator reuse 和 host 同步优化，再决定
  是否支持 CAGRA micro-batching 或多个并行 batch dispatch；OpenViking 的 scheduler 与 cuVS
  官方的 Dynamic Batching 组件不是同一实现。

## 主要图表

1. p50/p95 latency vs vector count：native exact 对比 cuVS exact，展示 break-even point。
2. QPS vs Recall@10 frontier：CAGRA 参数扫描结果，并标出 0.95/0.99 recall bucket。
3. build/rebuild/first-query time vs vector count：展示冷启动和延迟重建代价。
4. p95/QPS vs filter selectivity：分别展示 uniform 和 skewed filter。
5. host RSS/GPU memory vs vector count：说明容量上限和 host shadow 成本。

## 第一阶段最小矩阵

先用较小但足以观察趋势的矩阵建立可重复 harness：

- synthetic 1024D cosine，100K/1M/5M vectors；
- K=10，batch=1 和 128；
- native exact、cuVS exact、CAGRA；
- CAGRA Recall@10 >= 0.95 和 >= 0.99；
- 无过滤、1% uniform filter；
- 记录 build、warm p50/p95/p99、QPS、recall、host RSS 和 GPU memory；
- 额外执行一次 100-record update，记录 write + next-query rebuild latency。

第一阶段结束后再决定是否下载 10M/100M 公开数据集，避免在 harness 尚未稳定时消耗大量
存储和 GPU 时间。

## 建议的实现结构

```text
benchmark/cuvs/
├── README.md
├── generate_dataset.py
├── run_index_benchmark.py
├── run_collection_benchmark.py
├── configs/
│   ├── smoke.yaml
│   └── scale.yaml
└── plot_results.py
```

每次运行输出一个自描述 JSON/JSONL 文件，至少包含 git revision、backend、algorithm、全部参数、
dataset hash、hardware metadata、软件版本、raw latency samples 和 summary。图表必须能够只依赖
这些结果文件离线重建。
