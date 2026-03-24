# RAG Benchmark 数据集准备指南

本指南详细说明如何配置、下载和准备 RAG Benchmark 所需的数据集。

## 目录

- [整体流程](#整体流程)
- [数据集配置](#数据集配置)
- [下载数据集](#下载数据集)
- [抽样数据集](#抽样数据集)
- [一键准备](#一键准备)
- [配置文件更新](#配置文件更新)

---

## 整体流程

数据集准备包含两个主要步骤：

1. **下载**：从官方源下载原始数据集到 `raw_data/` 目录
2. **抽样**：从原始数据集中抽样（可选）到 `datasets/` 目录

```
原始数据源 → 下载 → raw_data/{dataset_name}/ → 抽样 → datasets/{dataset_name}/
```

---

## 数据集配置

在 `download_dataset.py` 中配置需要下载的数据集。

### 配置格式

```python
DATASET_SOURCES = {
    "DatasetName": {
        "source_type": "url",  # 目前支持 "url"
        "url": "https://example.com/dataset.zip",  # 单个 URL
        # 或多个 URL
        "urls": [
            "https://example.com/part1.zip",
            "https://example.com/part2.zip"
        ],
        "checksum": "",  # SHA256 校验和（可选）
        "files": [  # 需要提取的文件列表
            "file1.json",
            "file2.csv",
            "subdir/"  # 目录
        ],
        "extract_subdir": "dataset-main"  # 压缩包内的子目录（可选）
    }
}
```

### 已配置的数据集

目前已配置以下四个数据集：

1. **Locomo** - 多轮对话 QA 数据集
2. **SyllabusQA** - 教学大纲 QA 数据集
3. **Qasper** - 学术论文 QA 数据集
4. **FinanceBench** - 财务报告 QA 数据集

---

## 下载数据集

### 使用 download_dataset.py

```bash
cd benchmark/RAG

# 下载所有已配置的数据集
python scripts/download_dataset.py

# 下载指定数据集
python scripts/download_dataset.py --dataset Locomo

# 强制重新下载（即使已存在）
python scripts/download_dataset.py --dataset Locomo --force

# 下载到指定目录
python scripts/download_dataset.py --download-dir /path/to/custom/dir
```

### 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--dataset`, `-d` | 要下载的数据集名称 | `all` |
| `--download-dir` | 下载目录 | `raw_data/` |
| `--force`, `-f` | 强制重新下载 | `False` |
| `--no-verify` | 跳过文件验证 | `False` |

### 下载目录结构

```
raw_data/
├── Locomo/
│   └── locomo10.json
├── SyllabusQA/
│   ├── data/dataset_split/
│   │   ├── train.csv
│   │   ├── val.csv
│   │   └── test.csv
│   └── syllabi/
├── Qasper/
│   ├── qasper-train-v0.3.json
│   ├── qasper-dev-v0.3.json
│   └── qasper-test-v0.3.json
└── FinanceBench/
    ├── data/
    │   ├── financebench_open_source.jsonl
    │   └── financebench_document_information.jsonl
    └── pdfs/
```

---

## 抽样数据集

### 使用 sample_dataset.py

```bash
# 抽样所有数据集（全量）
python scripts/sample_dataset.py

# 抽样指定数据集
python scripts/sample_dataset.py --dataset Locomo

# 按 QA 数抽样
python scripts/sample_dataset.py --dataset Locomo --sample-size 100

# 按文档数抽样（推荐）
python scripts/sample_dataset.py --dataset Locomo --num-docs 5

# 使用全量数据（不抽样）
python scripts/sample_dataset.py --dataset Locomo --full

# 指定随机种子（可复现）
python scripts/sample_dataset.py --dataset Locomo --num-docs 5 --seed 42
```

### 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--dataset`, `-d` | 要抽样的数据集名称 | `all` |
| `--input-dir`, `-i` | 输入目录（原始数据） | `raw_data/` |
| `--output-dir`, `-o` | 输出目录（抽样后数据） | `datasets/` |
| `--sample-size`, `-n` | 抽样 QA 数量 | `None`（全量） |
| `--num-docs` | 抽样文档数量 | `None` |
| `--seed`, `-s` | 随机种子 | `42` |
| `--full` | 使用全量数据 | `False` |

### 抽样策略

#### 1. 文档级抽样（推荐）

使用 `--num-docs N` 参数，先抽样 N 个文档，保留文档内所有 QA。

```bash
# 抽样 5 个文档
python scripts/sample_dataset.py --dataset Locomo --num-docs 5
```

优点：
- 保持文档完整性
- 文档内 QA 关联性不被破坏
- 更符合实际使用场景

#### 2. QA 级抽样

使用 `--sample-size N` 参数，随机选择文档直到 QA 数达到 N。

```bash
# 抽样直到达到 100 个 QA
python scripts/sample_dataset.py --dataset Locomo --sample-size 100
```

#### 3. 全量数据

使用 `--full` 参数或不指定抽样参数，使用完整数据集。

```bash
python scripts/sample_dataset.py --dataset Locomo --full
```

### 可复现性

抽样使用固定随机种子（默认 42），保证结果可复现：

```bash
# 第一次抽样
python scripts/sample_dataset.py --dataset Locomo --num-docs 5 --seed 42

# 第二次抽样（结果相同）
python scripts/sample_dataset.py --dataset Locomo --num-docs 5 --seed 42
```

### 抽样元数据

每个抽样后的数据集目录包含 `sampling_metadata.json`，记录抽样信息：

```json
{
  "dataset": "Locomo",
  "original_num_docs": 10,
  "original_total_qas": 1986,
  "sampled_num_docs": 2,
  "sampled_total_qas": 304,
  "sample_size": null,
  "num_docs": 2,
  "seed": 42,
  "sample_mode": "stratified",
  "is_full": false
}
```

### 抽样后目录结构

```
datasets/
├── Locomo/
│   ├── locomo10.json
│   └── sampling_metadata.json
├── SyllabusQA/
│   ├── train.csv
│   ├── val.csv
│   ├── test.csv
│   ├── syllabi/
│   └── sampling_metadata.json
├── Qasper/
│   ├── qasper-train-v0.3.json
│   ├── qasper-dev-v0.3.json
│   ├── qasper-test-v0.3.json
│   └── sampling_metadata.json
└── FinanceBench/
    ├── financebench_open_source.jsonl
    ├── financebench_document_information.jsonl
    ├── pdfs/
    │   ├── WALMART_2018_10K.pdf
    │   └── AMCOR_2023Q2_10Q.pdf
    └── sampling_metadata.json
```

---

## 一键准备

使用 `prepare_dataset.py` 可以一键完成下载和抽样：

```bash
# 准备所有数据集（全量）
python scripts/prepare_dataset.py

# 准备指定数据集，抽样 5 个文档
python scripts/prepare_dataset.py --dataset Locomo --num-docs 5

# 跳过下载，仅抽样已有数据
python scripts/prepare_dataset.py --dataset Locomo --num-docs 5 --skip-download

# 跳过抽样，仅下载
python scripts/prepare_dataset.py --dataset Locomo --skip-sampling

# 使用全量数据
python scripts/prepare_dataset.py --dataset Locomo --full

# 强制重新下载
python scripts/prepare_dataset.py --dataset Locomo --force-download
```

### 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--dataset`, `-d` | 要准备的数据集名称 | `all` |
| `--download-dir` | 下载目录 | `raw_data/` |
| `--output-dir`, `-o` | 输出目录 | `datasets/` |
| `--sample-size`, `-n` | 抽样 QA 数量 | `None` |
| `--num-docs` | 抽样文档数量 | `None` |
| `--seed`, `-s` | 随机种子 | `42` |
| `--full` | 使用全量数据 | `False` |
| `--skip-download` | 跳过下载 | `False` |
| `--skip-sampling` | 跳过抽样 | `False` |
| `--force-download`, `-f` | 强制重新下载 | `False` |

### 使用示例

#### 场景 1：首次准备 Locomo 数据集

```bash
# 下载并抽样 5 个文档
python scripts/prepare_dataset.py --dataset Locomo --num-docs 5
```

#### 场景 2：使用全量数据

```bash
# 下载并使用完整数据集
python scripts/prepare_dataset.py --dataset Qasper --full
```

#### 场景 3：已有数据，重新抽样

```bash
# 跳过下载，重新抽样为 10 个文档
python scripts/prepare_dataset.py --dataset FinanceBench --num-docs 10 --skip-download
```

#### 场景 4：准备所有数据集

```bash
# 准备所有数据集，每个抽样 5 个文档
python scripts/prepare_dataset.py --num-docs 5
```

---

## 配置文件更新

准备好数据集后，需要更新评测配置文件中的 `dataset_dir` 路径。

### 配置文件位置

```
benchmark/RAG/config/
├── config.yaml          # 主配置文件
├── locomo_config.yaml
├── syllabusqa_config.yaml
├── qasper_config.yaml
└── financebench_config.yaml
```

### 更新配置

在配置文件中更新 `paths.dataset_dir`：

```yaml
paths:
  # 使用原始数据（未抽样）
  dataset_dir: "raw_data/Locomo/locomo10.json"
  
  # 或使用抽样后的数据
  dataset_dir: "datasets/Locomo/locomo10.json"
```

### 各数据集配置示例

#### Locomo

```yaml
dataset_name: "Locomo"
paths:
  dataset_dir: "datasets/Locomo/locomo10.json"
```

#### SyllabusQA

```yaml
dataset_name: "SyllabusQA"
paths:
  dataset_dir: "datasets/SyllabusQA"
```

#### Qasper

```yaml
dataset_name: "Qasper"
paths:
  dataset_dir: "datasets/Qasper/"
```

#### FinanceBench

```yaml
dataset_name: "FinanceBench"
paths:
  dataset_dir: "datasets/FinanceBench/data/financebench_open_source.jsonl"
```

---

## 完整工作流示例

### 1. 环境准备

```bash
cd OpenViking
uv pip install -e ".[benchmark]"
source .venv/bin/activate
cd benchmark/RAG
```

### 2. 配置数据集（可选）

编辑 `scripts/download_dataset.py` 中的 `DATASET_SOURCES`。

### 3. 准备数据集

```bash
# 一键准备所有数据集，每个抽样 5 个文档
python scripts/prepare_dataset.py --num-docs 5
```

### 4. 更新配置文件

编辑 `config/config.yaml` 或对应的数据集配置文件：

```yaml
dataset_name: "Locomo"
paths:
  dataset_dir: "datasets/Locomo/locomo10.json"
```

### 5. 运行评测

```bash
python run.py --config config/locomo_config.yaml
```

---

## 常见问题

### Q: 如何添加新的数据集？

A: 在 `scripts/download_dataset.py` 的 `DATASET_SOURCES` 中添加配置，然后在 `scripts/sample_dataset.py` 中实现对应的抽样函数。

### Q: 抽样结果可以复现吗？

A: 可以！使用相同的 `--seed` 参数即可得到相同的抽样结果。默认 seed 为 42。

### Q: 抽样后的文件格式和原始数据一致吗？

A: 一致！抽样后的文件格式与原始数据完全一致，可以直接用 adapter 处理。

### Q: 如何只下载不抽样？

A: 使用 `--skip-sampling` 参数：
```bash
python scripts/prepare_dataset.py --skip-sampling
```

### Q: 如何只抽样不下载？

A: 使用 `--skip-download` 参数：
```bash
python scripts/prepare_dataset.py --skip-download --num-docs 5
```

---

## 文件说明

| 文件 | 说明 |
|------|------|
| `download_dataset.py` | 数据集下载脚本 |
| `sample_dataset.py` | 数据集抽样脚本 |
| `prepare_dataset.py` | 统一数据集准备脚本 |
| `config/*.yaml` | 评测配置文件 |
| `raw_data/` | 原始数据集目录（.gitignore） |
| `datasets/` | 抽样后数据集目录（.gitignore） |
