# RAG

## 中文版

[English Version README](./README.md)

RAG 是一个独立的 RAG（检索增强生成）系统评估框架，完全兼容最新版本的 OpenViking。

### 项目结构

```
benchmark/RAG/
├── src/                        # 源代码
│   ├── __init__.py
│   ├── pipeline.py              # 评估核心流水线
│   ├── adapters/                # 数据集适配器
│   │   ├── __init__.py
│   │   ├── base.py              # 基础适配器类
│   │   ├── locomo_adapter.py    # Locomo 数据集适配器
│   │   ├── syllabusqa_adapter.py # SyllabusQA 数据集适配器
│   │   ├── qasper_adapter.py    # Qasper 数据集适配器
│   │   └── financebench_adapter.py # FinanceBench 数据集适配器
│   └── core/                    # 核心组件
│       ├── __init__.py
│       ├── logger.py            # 日志模块
│       ├── vector_store.py      # 向量存储包装器
│       ├── llm_client.py        # LLM 客户端包装器
│       ├── metrics.py           # 指标计算
│       ├── judge_util.py        # LLM 评判工具
│       └── monitor.py           # 监控工具
├── config/                      # 配置文件
│   ├── config.yaml              # 主配置文件
│   ├── locomo_config.yaml       # Locomo 数据集配置
│   ├── syllabusqa_config.yaml   # SyllabusQA 数据集配置
│   ├── qasper_config.yaml       # Qasper 数据集配置
│   └── financebench_config.yaml # FinanceBench 数据集配置
├── scripts/                     # 工具脚本
│   ├── __init__.py
│   ├── download_dataset.py      # 数据集下载脚本
│   ├── sample_dataset.py        # 数据集抽样脚本
│   ├── prepare_dataset.py       # 统一数据集准备脚本
│   └── run_sampling.py          # 自定义抽样脚本
├── raw_data/                    # 原始数据集目录（下载）
├── datasets/                    # 抽样数据集目录
├── Output/                      # 输出结果目录
├── run.py                       # 主执行脚本
└── README.md
```

### 快速开始

#### 1. 安装依赖

```bash
cd OpenViking
uv pip install -e ".[benchmark]"
source .venv/bin/activate
```

#### 2. 准备数据集

本项目提供完整的数据集准备工作流，包括下载、抽样和配置。

##### 数据集准备工作流

数据集准备包括两个主要步骤：

1. **下载**：从官方源下载原始数据集到 `raw_data/` 目录
2. **抽样**：从原始数据集抽样（可选）到 `datasets/` 目录

```
原始数据源 → 下载 → raw_data/{dataset_name}/ → 抽样 → datasets/{dataset_name}/
```

##### 下载数据集

使用 `download_dataset.py` 下载数据集：

```bash
cd benchmark/RAG

# 下载所有配置的数据集
python scripts/download_dataset.py

# 下载特定数据集
python scripts/download_dataset.py --dataset Locomo

# 强制重新下载，即使已存在
python scripts/download_dataset.py --dataset Locomo --force
```

##### 抽样数据集

使用 `sample_dataset.py` 抽样数据集：

```bash
# 抽样所有数据集（使用完整数据集，不抽样）
python scripts/sample_dataset.py

# 抽样特定数据集（使用完整数据集，不抽样）
python scripts/sample_dataset.py --dataset Locomo

# 按 QA 数量抽样
python scripts/sample_dataset.py --dataset Locomo --sample-size 100

# 按文档数量抽样（推荐）
python scripts/sample_dataset.py --dataset Locomo --num-docs 5

# 使用完整数据集（显式，不抽样）
python scripts/sample_dataset.py --dataset Locomo --full

# 指定随机种子（可重现）
python scripts/sample_dataset.py --dataset Locomo --num-docs 5 --seed 42
```

**抽样策略：**

1. **文档级抽样（推荐）**：使用 `--num-docs N` 首先抽样 N 个文档，保留文档中的所有 QA
2. **QA 级抽样**：使用 `--sample-size N` 随机选择文档，直到 QA 计数达到 N
3. **完整数据集**：使用 `--full` 或不指定抽样参数来使用完整数据集

##### 一键准备

使用 `prepare_dataset.py` 一步完成下载和抽样：

```bash
# 准备所有数据集（使用完整数据集，不抽样）
python scripts/prepare_dataset.py

# 准备特定数据集，抽样 5 个文档
python scripts/prepare_dataset.py --dataset Locomo --num-docs 5

# 使用完整数据集（显式，不抽样）
python scripts/prepare_dataset.py --dataset Locomo --full

# 跳过下载，只抽样现有数据
python scripts/prepare_dataset.py --dataset Locomo --num-docs 5 --skip-download

# 跳过抽样，只下载
python scripts/prepare_dataset.py --dataset Locomo --skip-sampling
```

##### 更新配置文件

准备数据集后，需要更新评估配置文件中的 `dataset_path`。

**配置文件位置：**

```
benchmark/RAG/config/
├── config.yaml          # 主配置文件
├── locomo_config.yaml
├── syllabusqa_config.yaml
├── qasper_config.yaml
└── financebench_config.yaml
```

**数据集配置示例：**

- **Locomo**：
  ```yaml
  dataset_name: "Locomo"
  paths:
    dataset_path: "datasets/Locomo/locomo10.json"
  ```
- **SyllabusQA**：
  ```yaml
  dataset_name: "SyllabusQA"
  paths:
    dataset_path: "datasets/SyllabusQA"
  ```
- **Qasper**：
  ```yaml
  dataset_name: "Qasper"
  paths:
    dataset_path: "datasets/Qasper"
  ```
- **FinanceBench**：
  ```yaml
  dataset_name: "FinanceBench"
  paths:
    dataset_path: "datasets/FinanceBench/financebench_open_source.jsonl"
  ```

**注意：** 对于像 SyllabusQA 和 Qasper 这样有多个文件的数据集，`dataset_path` 应设置为目录路径，适配器会自动查找并加载所有相关文件。

#### 3. 配置 LLM

在 `config/*.yaml` 中编辑 LLM 配置。此配置用于：

- **答案生成**：从检索的上下文生成答案
- **LLM 作为评判者评估**：使用 LLM 评估生成答案的质量

#### 4. 配置 OpenViking

如果需要使用自定义 OpenViking 配置（用于数据摄取和检索），在 benchmark/RAG 目录中创建 `ov.conf` 文件。这将覆盖默认的 OpenViking 设置。

您可以参考 OpenViking 根目录中的 `examples/ov.conf.example` 了解配置格式。

#### 5. 运行评估

```bash
cd benchmark/RAG

# 运行完整评估（数据摄取、答案生成、评估和数据删除）
python run.py --config config/locomo_config.yaml

# 只运行数据摄取和答案生成阶段
python run.py --config config/locomo_config.yaml --step gen

# 只运行评估阶段（需要前一步生成的答案）
python run.py --config config/locomo_config.yaml --step eval

# 只运行数据删除阶段
python run.py --config config/locomo_config.yaml --step del
```

### 支持的数据集

| 数据集              | 类型   | 文档数  | 问题数  | 特点                                             |
| ---------------- | ---- | ---- | ---- | ---------------------------------------------- |
| **Locomo**       | 多轮对话 | 10   | 1540 | 长对话理解，4 种问题类型（事实性、时间性、推理、理解）                   |
| **SyllabusQA**   | 教学大纲 | 39   | 5078 | 教育领域，6 种问题类型（单一事实、多事实、单一推理、多推理、总结、是/否）         |
| **Qasper**       | 学术论文 | 1585 | 5049 | 研究领域，1585 篇 NLP 论文，3 种答案类型（抽取式、自由形式、是/否）       |
| **FinanceBench** | 金融领域 | 84   | 150  | 金融领域，开源子集包含 150 个 QA 对，3 种问题类型（领域相关、指标生成、新颖生成） |

### 如何使用不同的数据集

每个数据集在 `config/` 目录中都有自己的配置文件。要使用特定数据集：

1. **选择数据集配置文件**：
   - `config/locomo_config.yaml` - 用于 Locomo 数据集
   - `config/syllabusqa_config.yaml` - 用于 SyllabusQA 数据集
   - `config/qasper_config.yaml` - 用于 Qasper 数据集
   - `config/financebench_config.yaml` - 用于 FinanceBench 数据集
2. **使用选定的配置运行评估**：
   ```bash
   # 使用 Locomo 数据集评估
   python run.py --config config/locomo_config.yaml

   # 使用 SyllabusQA 数据集评估
   python run.py --config config/syllabusqa_config.yaml

   # 使用 Qasper 数据集评估
   python run.py --config config/qasper_config.yaml

   # 使用 FinanceBench 数据集评估
   python run.py --config config/financebench_config.yaml
   ```
3. **自定义配置（可选）**：
   您可以复制数据集配置文件并修改它以满足您的需求：
   ```bash
   cp config/locomo_config.yaml config/my_custom_config.yaml
   # 编辑 config/my_custom_config.yaml 以满足您的偏好
   python run.py --config config/my_custom_config.yaml
   ```

### 配置指南

RAG 使用 YAML 配置文件来控制评估过程。每个数据集在 `config/` 目录中都有自己的配置文件。

**关键配置部分：**

1. **基本配置**：
   - `dataset_name`：正在评估的数据集名称
2. **适配器配置**：
   - `adapter.module`：数据集适配器的 Python 模块路径
   - `adapter.class_name`：数据集适配器的类名
3. **执行配置**：
   - `max_workers`：并发工作线程数
   - `ingest_workers`：文档摄取的工作线程数
   - `retrieval_topk`：要检索的文档数
   - `max_queries`：限制要处理的查询数（null = 全部）
   - `skip_ingestion`：跳过文档摄取（使用现有索引）
   - `ingest_mode`：文档摄取模式（"directory" 或 "per\_file"）
   - `retrieval_instruction`：检索的自定义指令（默认为空）
4. **路径配置**：
   - `dataset_dir`：数据集文件或目录的路径
   - `doc_output_dir`：处理文档的目录
   - `vector_store`：向量索引存储的目录
   - `output_dir`：评估结果的目录
   - `log_file`：日志文件的路径
5. **LLM 配置**：
   - `llm.model`：LLM 模型名称
   - `llm.temperature`：生成温度
   - `llm.base_url`：API 基础 URL
   - `llm.api_key`：API 密钥（保持安全）

### 评估流程概述

评估过程包括 5 个主要阶段：

1. **数据准备**
   - 将原始数据集转换为 OpenViking 友好格式
   - 处理文档以进行摄取
2. **数据摄取**
   - 将处理后的文档摄取到 OpenViking 向量存储中
   - 为文档创建嵌入
   - 存储向量索引以进行检索
3. **答案生成**
   - 对于每个问题，从向量存储中检索相关文档
   - 使用检索的上下文和问题构建提示
   - 使用 LLM 生成答案
4. **评估**
   - 使用 LLM 作为评判者评估生成的答案与黄金答案的质量
   - 计算指标（召回率、F1、准确率）
5. **数据删除**
   - 清理向量存储并删除摄取的文档

### 评估指标

- **Recall**：检索召回率
- **F1 Score**：答案 F1 分数
- **Accuracy**：LLM 评判分数（0-4）
- **Latency**：检索延迟
- **Token Usage**：令牌使用量

### 输出文件

评估结果保存在 `Output/` 目录中，结构如下：

```
Output/
└── {dataset_name}/
    └── experiment_{experiment_name}/
        ├── generated_answers.json       # LLM 生成的答案
        ├── qa_eval_detailed_results.json # 详细评估结果
        ├── benchmark_metrics_report.json # 聚合指标报告
        ├── docs/                         # 处理后的文档（如果 skip_ingestion=false）
        └── benchmark.log                 # 日志文件
```

**向量存储数据库位置：**
向量索引（文档数据库）存储在配置文件中 `vector_store` 指定的路径中。默认情况下，这是：

```
datasets/{dataset_name}/viking_store_index_dir
```

#### 文件描述和示例

**1.** **`benchmark_metrics_report.json`** **- 摘要报告**

- **包含内容**：聚合指标报告，包含整体性能分数

示例：

```json
{
    "Insertion Efficiency (Total Dataset)": {
        "Total Insertion Time (s)": 131.98,
        "Total Input Tokens": 142849,
        "Total Output Tokens": 52077,
        "Total Embedding Tokens": 95626
    },
    "Query Efficiency (Average Per Query)": {
        "Average Retrieval Time (s)": 0.17,
        "Average Input Tokens": 3364.46,
        "Average Output Tokens": 15.5
    },
    "Dataset": "Locomo",
    "Total Queries Evaluated": 100,
    "Performance Metrics": {
        "Average F1 Score": 0.318,
        "Average Recall": 0.724,
        "Average Accuracy (Hit 0-4)": 2.36,
        "Average Accuracy (normalization)": 0.59
    }
}
```

**字段描述：**

- `Insertion Efficiency`：文档摄取性能统计
- `Query Efficiency`：每个查询的性能平均值
- `Performance Metrics`：核心评估分数（Accuracy 为 0-4 分制）

***

**2.** **`generated_answers.json`** **- 生成的答案**

- **包含内容**：所有问题、检索的上下文和 LLM 生成的答案

示例（单个结果）：

```json
{
  "_global_index": 0,
  "sample_id": "conv-26",
  "question": "Would Caroline pursue writing as a career option?",
  "gold_answers": ["LIkely no; though she likes reading, she wants to be a counselor"],
  "category": "3",
  "evidence": ["D7:5", "D7:9"],
  "retrieval": {
    "latency_sec": 0.288,
    "uris": ["viking://resources/...", "viking://resources/..."]
  },
  "llm": {
    "final_answer": "Not mentioned"
  },
  "metrics": {
    "Recall": 1.0
  },
  "token_usage": {
    "total_input_tokens": 2643,
    "llm_output_tokens": 2
  }
}
```

**字段描述：**

- `_global_index`：唯一查询标识符
- `question`：正在询问的问题
- `gold_answers`：真实答案
- `retrieval.uris`：检索文档的 URI
- `llm.final_answer`：LLM 生成的答案
- `metrics.Recall`：检索召回分数（0-1）
- `token_usage`：令牌消耗统计

***

**3.** **`qa_eval_detailed_results.json`** **- 详细评估**

- **包含内容**：每个问题的评估，包括 LLM 评判者的推理和分数

示例（单个结果）：

```json
{
  "_global_index": 2,
  "question": "When did Caroline go to the LGBTQ support group?",
  "gold_answers": ["7 May 2023"],
  "llm": {
    "final_answer": "7 May 2023 (the day before the chat at 1:56 pm on 8 May, 2023)"
  },
  "metrics": {
    "Recall": 1.0,
    "F1": 0.375,
    "Accuracy": 4
  },
  "llm_evaluation": {
    "prompt_used": "Locomo_0or4",
    "reasoning": "The generated answer explicitly includes the exact date 7 May 2023 that matches the gold answer...",
    "normalized_score": 4
  }
}
```

**字段描述：**

- `metrics.F1`：答案 F1 分数（0-1）
- `metrics.Accuracy`：LLM 评判分数（0-4，4 = 完美）
- `llm_evaluation.reasoning`：LLM 评判者对分数的推理
- `llm_evaluation.normalized_score`：最终标准化分数

***

**4.** **`benchmark.log`** **- 执行日志**

- **包含内容**：详细的执行日志，带有时间戳、警告和错误
- **如何查看**：在任何文本编辑器中直接打开

***

**5.** **`docs/`** **- 处理后的文档**

- **包含内容**：Markdown 格式的处理文档（如果 `skip_ingestion=false`）
- **如何查看**：在任何 Markdown 查看器或文本编辑器中直接打开 `.md` 文件

### 基准测试结果参考

以下是基准测试结果（top-5 检索），仅供参考：

| 数据集              | 评估查询数 | 平均 F1 分数 | 平均召回率 | 平均准确率（0-4）| 标准化准确率 |
| ---------------- | ------ | -------- | ----- | ------------ | ------- |
| **FinanceBench** | 12     | 0.224    | 0.694 | 2.5          | 0.625   |
| **Locomo**       | 80     | 0.254    | 0.592 | 2.4          | 0.600   |
| **Qasper**       | 60     | 0.293    | 0.614 | 2.12         | 0.529   |
| **SyllabusQA**   | 90     | 0.344    | 0.675 | 2.54         | 0.636   |

**测试配置详情：**

- **LLM 模型：** `doubao-seed-2-0-pro-260215`
- **API 基础地址：** `https://ark.cn-beijing.volces.com/api/v3`
- **温度参数：** 0（确定性输出）
- **检索 Top-K：** 5
- **最大工作线程数：** 8
- **摄取工作线程数：** 8
- **摄取模式：** directory
- **检索指令：** （空）
- **评估指标：** Recall、F1 分数、Accuracy（0-4 分制）

所有数据集使用相同的 LLM 和执行配置，特定于数据集的适配器和路径在各自的 YAML 文件中配置。

### 复现实验

要复现基准测试结果，请按照以下步骤操作：

```bash
cd OpenViking/benchmark/RAG

# 1. 安装依赖（如果尚未安装）
uv pip install -e ".[benchmark]"
source .venv/bin/activate

# 2. 下载所有数据集
python scripts/download_dataset.py

# 3. 对所有数据集运行一键抽样，使用与基准测试相同的参数
python scripts/run_sampling.py

# 4. 配置您的 LLM API 密钥
# 编辑 config/ 目录下的配置文件，在 llm.api_key 字段中设置您的 API 密钥

# 5. 为每个数据集运行评估
python run.py --config config/locomo_config.yaml
python run.py --config config/syllabusqa_config.yaml
python run.py --config config/qasper_config.yaml
python run.py --config config/financebench_config.yaml

# 6. 在 Output/{dataset_name}/experiment_test_top_5/ 中查看结果
```

**注意：** `run_sampling.py` 脚本将进行以下抽样：
- Locomo：3 个文档，80 个 QA
- SyllabusQA：7 个文档，90 个 QA
- Qasper：8 个文档，60 个 QA
- FinanceBench：3 个文档，12 个 QA
所有抽样使用 seed=42 以确保可重现性。

### 高级配置

#### 检索指令配置

您可以在 `config.yaml` 文件中配置自定义检索指令，以指导检索过程。此指令在检索期间添加到每个查询的前面。

**配置示例：**

```yaml
# ===========Execution Configuration============
# Instruction for retrieval, empty by default
# Recommended format: "Target_modality: xxx.\nInstruction:xxx.\nQuery:"
retrieval_instruction: "Target_modality: text.\nInstruction:Locate the part of the conversation where the speakers discuss.\nQuery:"
```

**推荐格式：**

- `Target_modality: xxx.` - 指定目标模态（例如，文本、图像、音频）
- `Instruction: xxx.` - 为检索提供具体指令
- `Query:` - 标记实际查询的开始

当 `retrieval_instruction` 为空时，系统将使用原始问题进行检索。

#### 自定义提示

RAG 使用特定于数据集和问题类型的提示来指导 LLM 答案生成。您可以在 `src/adapters/` 下的适配器文件中自定义这些提示，以提高评估结果。

##### Locomo 数据集提示（src/adapters/locomo\_adapter.py）

Locomo 有 4 个问题类别，每个类别都有特定的指令：

- **类别 1（事实提取）**：
  ```
  从对话中提取准确的事实答案。
  - 尽可能使用上下文中的确切词语
  - 如果有多个项目，用逗号分隔
  ```
- **类别 2（时间相关）**：
  ```
  回答与时间相关的问题。
  - 密切关注对话中的 DATE 标签
  - 必要时计算相对时间（例如，"10 年前"）
  - 使用上下文中的确切日期
  ```
- **类别 3（推理）**：
  ```
  基于对话进行推理和推断。
  - 仅使用上下文中的事实
  - 清楚地陈述您的结论（例如，"可能是"，"可能不是"）
  - 不要解释您的推理或提供任何依据/理由
  - 只输出您的最终结论，别无其他
  - 不要编造信息
  ```
- **类别 4（理解/意义）**：
  ```
  理解含义和意义。
  - 关注说话者的意思，而不仅仅是他们说的话
  - 识别象征意义或隐含意义
  - 尽可能使用上下文中的措辞
  ```

##### SyllabusQA 数据集提示（src/adapters/syllabusqa\_adapter.py）

SyllabusQA 有 6 种问题类型：

- **single factual**：提取单个事实答案
- **multi factual**：提取多个事实答案
- **single reasoning**：简单逻辑推理
- **multi reasoning**：复杂推理
- **summarization**：总结相关信息
- **yes/no**：是/否问题

##### Qasper 数据集提示（src/adapters/qasper\_adapter.py）

Qasper 有 3 种答案类型：

- **extractive**：从论文中提取准确答案
- **free\_form**：用自己的话自由回答
- **yes\_no**：是/否问题

##### FinanceBench 数据集提示（src/adapters/financebench\_adapter.py）

FinanceBench 有 3 种问题类型：

- **domain-relevant**：金融领域问题
- **metrics-generated**：计算金融指标
- **novel-generated**：新颖的金融问题

##### 如何自定义提示

1. 打开您的数据集的适配器文件（例如，`src/adapters/locomo_adapter.py`）
2. 找到 `CATEGORY_INSTRUCTIONS` 字典
3. 修改您想要改进的问题类型的提示文本
4. 使用修改后的提示重新运行评估

### 添加新数据集

1. 在 `src/adapters/` 中创建一个新的适配器类，继承自 `BaseAdapter`
2. 在 `config/` 中创建相应的配置文件
3. 实现必要的方法：
   - `data_prepare()`：数据预处理
   - `load_and_transform()`：加载和转换数据
   - `build_prompt()`：构建提示
   - `post_process_answer()`：后处理答案

### 与 OpenViking 集成

本项目通过以下方式与 OpenViking 集成：

- 使用 `openviking` 客户端进行数据摄取和检索
- 通过 `ov.conf` 配置 OpenViking 连接
- 支持动态加载 OpenViking 的最新功能

### 常见问题（FAQ）

**问：如果我已经有向量索引，如何跳过数据摄取阶段？**
答：在配置文件中设置 `skip_ingestion: true`。这将使用现有的向量索引。

**问：我可以只运行评估阶段而不重新摄取文档吗？**
答：可以！首先运行 `--step gen` 生成答案，然后运行 `--step eval` 评估生成的答案。

**问：如果我收到 API 密钥错误，应该怎么办？**
答：确保您在配置文件的 `llm.api_key` 字段中设置了有效的 API 密钥。保持您的 API 密钥安全，不要将其提交到版本控制中。

**问：如何限制测试处理的查询数量？**
答：在配置文件中设置 `max_queries` 为您想要处理的查询数量（例如，`max_queries: 10`）。

**问："directory" 和 "per\_file" 摄取模式有什么区别？**
答：

- "directory"：将整个目录视为一个文档
- "per\_file"：将每个文件视为一个单独的文档

**问：如何自定义检索指令？**
答：在配置文件中设置 `retrieval_instruction`。推荐格式为：
`"Target_modality: xxx.\nInstruction:xxx.\nQuery:"`

**问：我在哪里可以找到评估结果？**
答：结果保存在配置文件中 `output_dir` 指定的目录中。默认情况下，这是 `Output/{dataset_name}/experiment_{experiment_name}/`。

### 许可证

与 OpenViking 相同的许可证。
