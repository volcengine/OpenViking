# VikingDB BM25 Grep 基准测试

用于评估 OpenViking grep 检索配合 VikingDB BM25 引擎的基准测试套件。

## 目录结构

```
vikingdb_bm25/
├── ai_wiki.txt              # 合成数据生成的原始文本
├── effectiveness/            # 检索效果测试（召回率/精确率/F1）
│   ├── step1_add_resource.py
│   └── step2_quality.py
└── performance/              # 检索性能测试（延迟 + 大规模返回匹配数）
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
| 1 | `step1_add_resource.py` | 导入代码仓库（含建索引，一次性导入） |
| 2 | `step2_quality.py` | SDK grep 与 fs 引擎 ground truth 对比（缓存） |

### 使用方法

```bash
# 步骤 1：导入代码仓库（含建索引，一次性导入）
cd effectiveness/
python3 step1_add_resource.py --source ~/.openviking/data/benchmark/OpenViking-main

# 步骤 2：评估检索质量
#   首次运行必须使用 engine=fs 生成 ground truth 缓存：
#     1. 设置 ov.conf: "grep": {"engine": "fs"}
#     2. 重启服务
python3 step2_quality.py --keywords grep reindex SyncHTTPClient

#   后续运行可使用任意引擎（ground truth 从缓存读取）：
#     1. 设置 ov.conf: "grep": {"engine": "auto", "switch_to_remote_threshold": 0}
#     2. 重启服务
python3 step2_quality.py --keywords grep reindex SyncHTTPClient

# 可选参数：--regenerate-ground-truth  （强制重算，需 engine=fs）
```

## Performance — 检索延迟

在大规模合成数据集（默认 20 万文件）上测试 grep 速度和返回匹配数。

**数据来源：** 从 `ai_wiki.txt` 生成，按已知概率注入目标单词。

| 步骤 | 脚本 | 说明 |
|------|------|------|
| 0 | `step0_prepare_data.py` | 生成合成数据集（dir_xxx/wiki_xxx.txt） |
| 1 | `step1_add_resource.py` | 导入数据（不建索引，速度快） |
| 2 | `step2_reindex.py` | 通过 openviking-server 异步构建索引（并发=16，轮询） |
| 3 | `step3_benchmark.py` | 使用 `node_limit=256` 测量延迟和返回匹配数 |

### 目标单词

15 个单词，分 5 个概率层级：

这些词组定义在 `performance/step0_prepare_data.py` 中，并由 `performance/step3_benchmark.py` 复用。

| 概率 | 单词 | 预期命中数（每 20 万文件） |
|------|------|---------------------------|
| 1% | heliofract, prismcache, fluxkernel | ~2,000 |
| 0.1% | auroracode, kiteshade, glyphvector | ~200 |
| 0.1% | cortexmint, latticewave, spiralsync | ~200 |
| 0.05% | ripplehash, embertrace, novaframe | ~100 |
| 0.01% | zephyrloom, quartzrelay, nebulaindex | ~20 |

### 使用方法

```bash
cd performance/

# 步骤 0：生成数据（默认：200 目录 x 1000 文件 = 20 万文件）
python3 step0_prepare_data.py

# 可选：追加更多数据，用于水平扩容，不覆盖已有目录
python3 step0_prepare_data.py --start-dir 100 --num-dirs 100

# 步骤 1：导入数据（不建索引，速度快）
python3 step1_add_resource.py

# 步骤 2：构建向量索引（需 openviking-server 运行中）
python3 step2_reindex.py
# 可选参数：--concurrency N  （默认：16）

# 步骤 3：基准测试 — 用不同引擎配置各跑一次
#   运行 A：fs 引擎
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
- **Performance（性能测试）** 对比不同引擎的延迟和返回匹配数，不生成 ground truth
- **Effectiveness** 直接一次性导入真实代码仓并建索引，然后执行效果评估
- **Performance** 先导入合成数据（不建索引），再异步建向量索引，最后执行延迟基准测试
- **Performance** 的导入与 reindex 步骤支持**断点续传**（各有独立进度文件）
- 切换 grep 引擎需修改 `ov.conf` 并重启服务，在不同运行之间对比
- 如需水平扩展合成数据集，可用新的 `--start-dir` 再运行步骤 0，然后重跑步骤 1 和步骤 2。
