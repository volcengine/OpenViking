# VikingDB BM25 Grep 基准测试

用于评估 OpenViking grep 检索配合 VikingDB BM25 引擎的基准测试套件。

## 目录结构

```
vikingdb_bm25/
├── ai_wiki.txt              # 合成数据生成的原始文本
├── effectiveness/            # 检索效果测试（召回率/精确率/F1）
│   ├── step1_add_resource.py
│   ├── step2_reindex.py
│   └── step3_quality.py
└── performance/              # 检索性能测试（延迟 + 大规模召回）
    ├── step0_prepare_data.py
    ├── step1_add_resource.py
    ├── step2_reindex.py
    └── step3_benchmark.py
```

## Effectiveness — 检索效果

测试 grep 在真实代码仓库中是否能找到**所有**匹配文件。

**数据来源：** 真实代码仓库（手动下载，放置于 `~/.openviking/data/benchmark/`）。

| 步骤 | 脚本 | 说明 |
|------|------|------|
| 1 | `step1_add_resource.py` | 导入代码仓库（不建索引，速度快） |
| 2 | `step2_reindex.py` | 通过 openviking-server 异步构建索引（并发=16，轮询） |
| 3 | `step3_quality.py` | SDK grep 与 fs 引擎 ground truth 对比（缓存） |

### 使用方法

```bash
# 步骤 1：导入代码仓库（不建索引）
cd effectiveness/
python3 step1_add_resource.py --source ~/.openviking/data/benchmark/OpenViking-main

# 步骤 2：构建向量索引（需 openviking-server 运行中）
python3 step2_reindex.py
# 可选参数：--concurrency N  （默认：16）

# 步骤 3：评估检索质量
#   首次运行必须使用 engine=fs 生成 ground truth 缓存：
#     1. 设置 ov.conf: "grep": {"engine": "fs"}
#     2. 重启服务
python3 step3_quality.py --keywords grep reindex SyncHTTPClient

#   后续运行可使用任意引擎（ground truth 从缓存读取）：
#     1. 设置 ov.conf: "grep": {"engine": "auto", "switch_to_remote_threshold": 0}
#     2. 重启服务
python3 step3_quality.py --keywords grep reindex SyncHTTPClient

# 可选参数：--regenerate-ground-truth  （强制重算，需 engine=fs）
```

## Performance — 检索性能

在大规模合成数据集（默认 10 万文件）上测试 grep 速度和召回率。

**数据来源：** 从 `ai_wiki.txt` 生成，按已知概率注入目标单词。

| 步骤 | 脚本 | 说明 |
|------|------|------|
| 0 | `step0_prepare_data.py` | 生成合成数据集（dir_xxx/wiki_xxx.txt） |
| 1 | `step1_add_resource.py` | 导入数据（不建索引，速度快） |
| 2 | `step2_reindex.py` | 通过 openviking-server 异步构建索引（并发=16，轮询） |
| 3 | `step3_benchmark.py` | 测量延迟和召回率（ground truth 来自 fs 引擎，缓存） |

### 目标单词

12 个单词，分 4 个概率层级：

| 概率 | 单词 | 预期命中数（每 10 万文件） |
|------|------|---------------------------|
| 50% | quantumnexus, synapseflow, deepvector | ~50,000 |
| 10% | bm25engine, vikingcore, retrievex | ~10,000 |
| 0.1% | zephyrhash, cryptolattice, nebulalink | ~100 |
| 0.01% | xenoform, quarkpulse, omegabind | ~10 |

### 使用方法

```bash
cd performance/

# 步骤 0：生成数据（默认：100 目录 x 1000 文件 = 10 万文件）
python3 step0_prepare_data.py

# 步骤 1：导入数据（不建索引，速度快）
python3 step1_add_resource.py

# 步骤 2：构建向量索引（需 openviking-server 运行中）
python3 step2_reindex.py
# 可选参数：--concurrency N  （默认：16）

# 步骤 3：基准测试 — 用不同引擎配置各跑一次
#   运行 A：fs 引擎（首次运行同时生成 ground truth 缓存）
#     1. 设置 ov.conf: "grep": {"engine": "fs"}
#     2. 重启服务
python3 step3_benchmark.py --engine-label fs

#   运行 B：auto 引擎（bm25）
#     1. 设置 ov.conf: "grep": {"engine": "auto", "switch_to_remote_threshold": 0}
#     2. 重启服务
python3 step3_benchmark.py --engine-label auto --compare step3_result_fs.json
```

## 核心概念

- **Effectiveness（效果测试）** 将 grep 结果与 fs 引擎的 ground truth 对比（本地缓存）
- **Performance（性能测试）** 对比不同引擎的延迟和匹配数（ground truth 来自 fs 引擎，本地缓存）
- 两个 ground truth 缓存均存储在 `~/.openviking/data/benchmark/.ground_truth/`
- 每个 step3 首次运行必须使用 ov.conf 的 `engine=fs` 来生成 ground truth；后续运行可使用任意引擎
- 两者遵循相同流程：导入（不建索引）→ 构建索引 → 评估/测试
- 两者均支持**断点续传**（导入和索引各有独立进度文件）
- 切换 grep 引擎需修改 `ov.conf` 并重启服务，在不同运行之间对比
