# VikingDB path_glob 基准测试

用于验证 OpenViking **远程 VikingDB glob** 特性（`feat: support remote vikingdb
glob`）的基准测试套件。它对比 `glob.engine="auto"`（把 glob 下推到 VikingDB
`path_glob`）与本地 `glob.engine="fs"` 两条路径，覆盖 **效果**（漏召回/错召回）
和 **性能**（延迟）两个维度。

执行导入、校验或基准测试前，需要先启动 `openviking-server`。

## 新执行者需要先知道什么

这个 benchmark 只验证路径/URI 维度的 glob 能力。它不验证解析质量、文件内容
搜索、Embedding 质量或向量语义召回。预期行为是：

- `engine=fs` 遍历本地 AGFS 文件树并在本地做文件名匹配。
- `engine=auto` 在达到配置阈值后使用 VikingDB `path_glob`。
- 两个引擎对每个默认 pattern 都应返回相同的 URI 集合。

执行前需要确认：

1. `openviking-server` 已配置为使用 VikingDB 后端的向量库。
2. 服务能写入当前配置的 workspace。
3. benchmark 根目录 `viking://resources/benchmark/glob` 为空，或者你明确要用同一份
   manifest 复用旧数据。
4. 对比 `fs` 和 `auto` 时，需要修改 `ov.conf` 中的 `glob.engine` 并重启服务。
   `auto` 测试建议设置 `switch_to_remote_threshold=1`，强制每个非空 benchmark
   子树都走远端 `path_glob`。

## 目录结构

```
vikingdb_path_glob/
├── common/                       # 共享、与引擎无关的工具
│   ├── dataset.py                # 规模桶、仿真 monorepo 目录树、manifest 读写
│   ├── glob_match.py             # 与 globset 对齐的匹配器 -> ground truth
│   ├── patterns.py               # 有界默认 pattern + 可选压力 pattern
│   └── inspect_dataset.py        # 内存采样：核验目录深度分布与各 pattern 命中比例
├── performance/                  # 性能测试（fs vs auto 延迟）
│   ├── step0_prepare_data.py     # 生成 100w 个 1 字节文件 + manifest
│   ├── step1_add_resource.py     # 导入（vectors_only），可断点续传
│   ├── step2_verify.py           # 轮询直到两面 glob 数据就绪
│   └── step3_benchmark.py        # 按规模测延迟；fs vs auto 对比
└── effectiveness/                # 效果测试（与 manifest ground truth 对比）
    └── step2_quality.py          # 各规模 fs 与 auto 的 FN/FP
```

## 核心思路

Glob 只匹配文件的 **路径/URI**，从不读取文件内容或向量值。由此得到三条贯穿
整个套件的设计：

1. **一份数据同时喂两个引擎。** 一次 `processing_mode="vectors_only"` 的
   `add_resource` 会同时填充两面：本地 AGFS 文件树（`engine=fs` 使用）和
   VikingDB 集合的 `uri` 记录（`engine=auto` 使用）。因此 `fs` 与 `auto` 是在
   **完全相同**的 URI 集合上对比。
2. **文件只有 1 字节。** 空（0 字节）文件会被导入器跳过，所以每个生成的文件
   只写一个占位字符。100w 个文件的内容总量约 1 MB。
3. **ground truth 是 manifest，不是任一引擎。** `step0` 会写出全部路径的
   manifest；`common/glob_match.py` 用两个引擎都认可的 glob 子集在 manifest 上
   计算期望命中。`fs` 和 `auto` 都与这份相同的 ground truth 对比，因此可以对
   每个引擎独立地暴露漏召回（FN）和错召回（FP）。

## 用一份数据覆盖多个规模

数据集被切成互不相交的顶层规模桶，因此对某个桶路径执行 glob 得到精确数据量，
对根目录执行 glob 则覆盖全部：

| 规模 | glob 根目录（uri） | 文件数 |
|------|--------------------|-------:|
| 10   | `.../glob/scale_500/scale_10`  | 10 |
| 50   | `.../glob/scale_500/scale_50`  | 50 |
| 100  | `.../glob/scale_500/scale_100` | 100 |
| 200  | `.../glob/scale_500/scale_200` | 200 |
| 500  | `.../glob/scale_500` | 500 |
| 1k   | `.../glob/scale_1k`  | 1,000 |
| 2k   | `.../glob/scale_2k`  | 2,000 |
| 5k   | `.../glob/scale_5k`  | 5,000 |
| 10w  | `.../glob/scale_10w` | 100,000 |
| 20w  | `.../glob/scale_20w` | 200,000 |
| 50w  | `.../glob/scale_50w` | 500,000 |
| 100w | `.../glob`（根）     | 1,000,000 |

基础 URI：`viking://resources/benchmark/glob`。

10、50、100、200 是 `scale_500` 内互不重叠的确定性子桶，剩余 140 条仍保留
原来的 monorepo 路径。`step0_prepare_data.py` 会直接生成最终目录，因此只需导入
一次 `scale_500`，即可测试这五个规模，不需要在导入后再执行 `mv`。

## 数据布局（贴近真实业务）

每个桶内部是一棵仿真的多服务 monorepo，路径深浅不一，来自若干带权重的路径
模板，因此目录级数有真实方差而非单一固定形状：

- **浅**：`tools/x.sh`（1 级）、`apps/web/x.ts`（2 级）
- **中**：`libs/core/src/x.py`（3 级）、`services/auth/src/mod_03/x.go`（4 级）
- **深**：`services/auth/src/api/mod_03/handlers/x.py`（6 级）、
  `services/auth/src/main/python/acme/platform/mod_03/x.py`（8 级）
- 另有 `docs/<topic>/x.json` 配置文档、`services/<svc>/tests/test_x.py` 测试文件

普通单桶的目录级数覆盖 **1~8 级**。精确子桶会给选中的 `scale_500` 路径
增加一级目录，因此整个 `scale_500` 覆盖 **1~9 级**；100w 根视图再增加一层
桶名前缀，覆盖 **2~10 级**。可用
`python3 -m vikingdb_path_glob.common.inspect_dataset` 自行核验。

## 查询 pattern（限制结果集大小）

默认 pattern 覆盖空结果、绝对数量标记、文件名窗口，以及结构与安全语法子集。
每个 pattern 在所有规模下的 manifest 真值都不超过 1,000 条，避免大量结果传输
干扰延迟统计，也避免远端返回上限影响效果判断。每个 pattern 带 `tier` 与
`target` 标签，脚本会连同 manifest 精确计算的真值一并输出：

| 类别 | pattern | 目标命中 |
|------|---------|---------|
| absolute | `**/definitely-missing/**/*.py` | 0 |
| absolute | `**/migrations/*.sql` | 恰 10 个（仅 100w 根） |
| absolute | `**/legacy-gateway/**/*.go` | 恰 100 个（仅 100w 根） |
| window | `**/file_0000000.*` | 0–7 |
| window | `**/file_000000?.*` | 7–70 |
| window | `**/file_00000??.*` | 73–645 |
| window | `**/file_00000??.py` | 15–179 |
| window | `**/file_00000??.{json,yaml}` | 4–50 |
| structure | `**/tests/test_file_00000??.py` | 6–55 |
| structure | `**/docs/**/file_00000??.json` | 1–23 |
| structure | `**/mod_0[0-4]/**/file_0000???.py` | 19–268 |
| structure | `**/mod_0?/**/file_0000???.ts` | 22–227 |
| structure | `**/services/auth/**/file_0000???.py` | 6–87 |

两个 landmark 子树只存在于 padding 桶中，因此仅在 100w 根视图下出现精确的
10 / 100 条命中，在单桶规模下为 0。`STRESS_PATTERNS` 仍保留 `**/*.py` 这类
宽匹配，用于显式测试返回上限和大结果传输，但默认效果与性能命令不会执行它们。

## 使用方法

### 快速路径

从本目录执行：

```bash
cd benchmark/retrieval/glob/vikingdb_path_glob/performance

# 1. 生成数据与 manifest。
python3 step0_prepare_data.py

# 2. 渐进导入；该命令可重复执行并自动续跑。
python3 step1_add_resource.py --buckets scale_500

# 3. 校验已导入切片。
python3 step2_verify.py --scales 10 50 100 200 500

# 4. 导完目标桶后，分别在 glob.engine=fs 和 glob.engine=auto 下测试效果与性能。
python3 ../effectiveness/step2_quality.py --engine-label fs
python3 step3_benchmark.py --engine-label fs
python3 ../effectiveness/step2_quality.py --engine-label auto
python3 step3_benchmark.py --engine-label auto --compare step3_result_fs.json
```

### 1. 生成数据

```bash
cd performance/
python3 step0_prepare_data.py            # 完整 100w 文件 + manifest
python3 step0_prepare_data.py --smoke    # 极小数据集，用于快速试跑
```

### 2. 导入（vectors_only）

启动 `openviking-server`，然后：

```bash
python3 step1_add_resource.py            # 以 package 为粒度，可断点续传
```

如果要渐进式验证，可以先导入一个或几个规模桶，验证通过后再继续导入剩余数据。
脚本会按导入单元记录进度，重复执行时会自动跳过已完成单元：

```bash
# 小规模试跑
python3 step1_add_resource.py --buckets scale_500
python3 step2_verify.py --scales 10 50 100 200 500

# 在较大桶内先导入部分单元
python3 step1_add_resource.py --buckets scale_10w --max-units 20
python3 step2_verify.py --scales 10w --timeout 60

# 中等规模继续验证
python3 step1_add_resource.py --buckets scale_1k scale_2k scale_5k
python3 step2_verify.py --scales 1k 2k 5k

# 继续导入剩余桶；已完成单元会自动跳过
python3 step1_add_resource.py
```

大规模运行可用分片并行加速。各分片按 round-robin 切分导入单元、互不相交，并
各自写独立进度文件。例如开 24 个进程：

```bash
for i in $(seq 0 23); do
  python3 step1_add_resource.py --shard-count 24 --shard-index "$i" &
done
wait
```

### 3. 校验两面数据就绪

远程面因异步向量化会滞后。分别按引擎校验：

```bash
# ov.conf: glob = {"engine": "fs"}   -> 重启服务
python3 step2_verify.py
# ov.conf: glob = {"engine": "auto", "switch_to_remote_threshold": 1} -> 重启
python3 step2_verify.py --heal
```

`--heal` 会先对基准子树做一次 `check_consistency`，若发现有记录已写入 AGFS 但
未落到远程 VikingDB（大批量导入偶发的单条丢失），则用 `reindex` 补齐后再校验。

### 4. 效果测试（与 manifest 对比 FN/FP）

```bash
# ov.conf: glob = {"engine": "fs"} -> 重启
python3 ../effectiveness/step2_quality.py --engine-label fs

# ov.conf: glob = {"engine": "auto", "switch_to_remote_threshold": 1} -> 重启
python3 ../effectiveness/step2_quality.py --engine-label auto
```

`switch_to_remote_threshold=1` 会强制每个非空子树都走远程 VikingDB path_glob。
每个 pattern 输出 Recall / Precision / F1，并 dump FN/FP 的 URI。默认 pattern
都能装入配置的 `node_limit`，因此任何 FN 或 FP 都按正确性问题处理。

### 5. 性能测试（fs vs auto）

```bash
# ov.conf: glob = {"engine": "fs"} -> 重启
python3 step3_benchmark.py --engine-label fs

# ov.conf: glob = {"engine": "auto", "switch_to_remote_threshold": 1} -> 重启
python3 step3_benchmark.py --engine-label auto --compare step3_result_fs.json
```

`--compare` 那次运行会打印 fs 与 auto 的延迟/加速比表格，并标记出两引擎命中数
不一致的 pattern。

## 输出与指标

生成的数据与 manifest 位于：

```text
~/.openviking/data/benchmark/glob_synthetic_v2/
├── files/                  # 生成的 1 字节源文件
├── manifest/               # 每个桶一份路径 manifest + index.json
└── effectiveness/<engine>/ # 效果测试结果与 FN/FP dump
```

性能结果写在当前工作目录：

```text
step3_result_fs.json
step3_result_auto.json
```

效果指标重点看：

- `truth_count`：manifest 计算出的期望文件命中数。
- `found_count`：OpenViking `glob` 实际返回的文件数。
- `recall`、`fn`：漏召回情况。召回关键 pattern 应为 `recall=1.0` 且 `fn=0`。
- `precision`、`fp`：错召回情况。应为 `precision=1.0` 且 `fp=0`。
- `miss/*.json`：用于排查 mismatch 的 FN/FP URI 样例。

性能指标重点看：

- `avg_ms`、`min_ms`、`max_ms`：某个 `(scale, pattern)` 的多次请求延迟。
- `matches`：在 benchmark `node_limit` 下返回的文件数。
- 对比表中的 `speedup`：`fs_avg_ms / auto_avg_ms`；大于 `1.0x` 表示 `auto`
  更快，小于 `1.0x` 表示 `auto` 更慢。
- 对比表中的 `*`：两个引擎返回数量不同。此时应先看效果测试，确认是否存在
  漏召回/错召回，再解读该行性能。

## 常见检查

- 如果 `step2_verify.py` 在 `auto` 模式超时，可能是远端 VikingDB 面还没追平。
  可以稍后重跑，或加 `--heal` 先做一致性修复再轮询。
- 如果默认 pattern 被截断，先确认 `node_limit` 不低于 2,000。
  `STRESS_PATTERNS` 中的宽匹配仍可能触发远端返回上限。
- 如果发现没有走 `auto`，确认 `glob.engine=auto`、`switch_to_remote_threshold=1`，
  并确认集合 schema 中存在 `uri` 字段。
- 如果 `step1_add_resource.py` 中途停止，使用相同参数重跑即可；已完成导入单元
  会根据 progress 文件自动跳过。
