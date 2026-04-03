# RAG

## English Version

[中文版 README](./README_zh.md)

RAG is an independent RAG (Retrieval-Augmented Generation) system evaluation framework, fully compatible with the latest version of OpenViking.

### Project Structure

```
benchmark/RAG/
├── src/                        # Source code
│   ├── __init__.py
│   ├── pipeline.py              # Evaluation core pipeline
│   ├── adapters/                # Dataset adapters
│   │   ├── __init__.py
│   │   ├── base.py              # Base adapter class
│   │   ├── locomo_adapter.py    # Locomo dataset adapter
│   │   ├── syllabusqa_adapter.py # SyllabusQA dataset adapter
│   │   ├── qasper_adapter.py    # Qasper dataset adapter
│   │   └── financebench_adapter.py # FinanceBench dataset adapter
│   └── core/                    # Core components
│       ├── __init__.py
│       ├── logger.py            # Logging module
│       ├── vector_store.py      # Vector store wrapper
│       ├── llm_client.py        # LLM client wrapper
│       ├── metrics.py           # Metrics calculation
│       ├── judge_util.py        # LLM judge utility
│       └── monitor.py           # Monitoring utility
├── config/                      # Configuration files
│   ├── config.yaml              # Main configuration file
│   ├── locomo_config.yaml       # Locomo dataset configuration
│   ├── syllabusqa_config.yaml   # SyllabusQA dataset configuration
│   ├── qasper_config.yaml       # Qasper dataset configuration
│   └── financebench_config.yaml # FinanceBench dataset configuration
├── scripts/                     # Utility scripts
│   ├── __init__.py
│   ├── download_dataset.py      # Dataset download script
│   ├── sample_dataset.py        # Dataset sampling script
│   ├── prepare_dataset.py       # Unified dataset preparation script
│   └── run_sampling.py          # Custom sampling script
├── raw_data/                    # Raw dataset directory (downloaded)
├── datasets/                    # Sampled dataset directory
├── Output/                      # Output result directory
├── run.py                       # Main execution script
└── README.md
```

### Quick Start

#### 1. Install Dependencies

```bash
cd OpenViking
uv pip install -e ".[benchmark]"
source .venv/bin/activate
```

#### 2. Prepare Datasets

This project provides a complete dataset preparation workflow, including downloading, sampling, and configuration.

##### Dataset Preparation Workflow

Dataset preparation involves two main steps:

1. **Download**: Download raw datasets from official sources to the `raw_data/` directory
2. **Sample**: Sample from raw datasets (optional) to the `datasets/` directory

```
Raw data source → Download → raw_data/{dataset_name}/ → Sample → datasets/{dataset_name}/
```

##### Download Datasets

Use `download_dataset.py` to download datasets:

```bash
cd benchmark/RAG

# Download all configured datasets
python scripts/download_dataset.py

# Download a specific dataset
python scripts/download_dataset.py --dataset Locomo

# Force re-download even if already exists
python scripts/download_dataset.py --dataset Locomo --force
```

##### Sample Datasets

Use `sample_dataset.py` to sample datasets:

```bash
# Sample all datasets (use full dataset, no sampling)
python scripts/sample_dataset.py

# Sample a specific dataset (use full dataset, no sampling)
python scripts/sample_dataset.py --dataset Locomo

# Sample by QA count
python scripts/sample_dataset.py --dataset Locomo --sample-size 100

# Sample by document count (recommended)
python scripts/sample_dataset.py --dataset Locomo --num-docs 5

# Use full dataset (explicitly, no sampling)
python scripts/sample_dataset.py --dataset Locomo --full

# Specify random seed (reproducible)
python scripts/sample_dataset.py --dataset Locomo --num-docs 5 --seed 42
```

**Sampling Strategies:**

1. **Document-level sampling (recommended)**: Use `--num-docs N` to sample N documents first, preserving all QAs within documents
2. **QA-level sampling**: Use `--sample-size N` to randomly select documents until QA count reaches N
3. **Full dataset**: Use `--full` or no sampling parameters to use the complete dataset

##### One-click Preparation

Use `prepare_dataset.py` to complete downloading and sampling in one step:

```bash
# Prepare all datasets (use full dataset, no sampling)
python scripts/prepare_dataset.py

# Prepare a specific dataset, sample 5 documents
python scripts/prepare_dataset.py --dataset Locomo --num-docs 5

# Use full dataset (explicitly, no sampling)
python scripts/prepare_dataset.py --dataset Locomo --full

# Skip download, only sample existing data
python scripts/prepare_dataset.py --dataset Locomo --num-docs 5 --skip-download

# Skip sampling, only download
python scripts/prepare_dataset.py --dataset Locomo --skip-sampling
```

##### Update Configuration Files

After preparing the datasets, you need to update the `dataset_path` in the evaluation configuration files.

**Configuration File Locations:**

```
benchmark/RAG/config/
├── config.yaml          # Main configuration file
├── locomo_config.yaml
├── syllabusqa_config.yaml
├── qasper_config.yaml
└── financebench_config.yaml
```

**Dataset Configuration Examples:**

- **Locomo**:
  ```yaml
  dataset_name: "Locomo"
  paths:
    dataset_path: "datasets/Locomo/locomo10.json"
  ```
- **SyllabusQA**:
  ```yaml
  dataset_name: "SyllabusQA"
  paths:
    dataset_path: "datasets/SyllabusQA"
  ```
- **Qasper**:
  ```yaml
  dataset_name: "Qasper"
  paths:
    dataset_path: "datasets/Qasper"
  ```
- **FinanceBench**:
  ```yaml
  dataset_name: "FinanceBench"
  paths:
    dataset_path: "datasets/FinanceBench/financebench_open_source.jsonl"
  ```

**Note:** For datasets with multiple files like SyllabusQA and Qasper, `dataset_path` should be set to the directory path, and the adapter will automatically find and load all relevant files.

#### 3. Configure LLM

Edit LLM configuration in `config/*.yaml`. This configuration is used for both:

- **Answer generation**: Generating answers from retrieved context
- **LLM-as-judge evaluation**: Using LLM to evaluate the quality of generated answers

#### 4. Configure OpenViking

If you need to use custom OpenViking configuration (for data ingestion and retrieval), create an `ov.conf` file in the benchmark/RAG directory. This will override the default OpenViking settings.

You can refer to `examples/ov.conf.example` in the OpenViking root directory for the configuration format.

#### 5. Run Evaluation

```bash
cd benchmark/RAG

# Run complete evaluation (data ingestion, answer generation, evaluation, and data deletion)
python run.py --config config/locomo_config.yaml

# Only run data ingestion and answer generation stage
python run.py --config config/locomo_config.yaml --step gen

# Only run evaluation stage (requires generated answers from previous step)
python run.py --config config/locomo_config.yaml --step eval

# Only run data deletion stage
python run.py --config config/locomo_config.yaml --step del
```

### Supported Datasets

| Dataset          | Type       | Docs | QAs  | Characteristics                                                                                                                |
| ---------------- | ---------- | ---- | ---- | ------------------------------------------------------------------------------------------------------------------------------ |
| **Locomo**       | Multi-turn | 10   | 1540 | Long conversation understanding, 4 question types (factual, temporal, reasoning, understanding)                                |
| **SyllabusQA**   | Syllabus   | 39   | 5078 | Education domain, 6 question types (single factual, multi factual, single reasoning, multi reasoning, summarization, yes/no)   |
| **Qasper**       | Academic   | 1585 | 5049 | Research domain, 1585 NLP papers, 3 answer types (extractive, free-form, yes/no)                                               |
| **FinanceBench** | Financial  | 84   | 150  | Financial domain, open-source subset with 150 QA pairs, 3 question types (domain-relevant, metrics-generated, novel-generated) |

### How to Use Different Datasets

Each dataset has its own configuration file in the `config/` directory. To use a specific dataset:

1. **Choose a dataset configuration file**:
   - `config/locomo_config.yaml` - For Locomo dataset
   - `config/syllabusqa_config.yaml` - For SyllabusQA dataset
   - `config/qasper_config.yaml` - For Qasper dataset
   - `config/financebench_config.yaml` - For FinanceBench dataset
2. **Run evaluation with the chosen configuration**:
   ```bash
   # Evaluate with Locomo dataset
   python run.py --config config/locomo_config.yaml

   # Evaluate with SyllabusQA dataset
   python run.py --config config/syllabusqa_config.yaml

   # Evaluate with Qasper dataset
   python run.py --config config/qasper_config.yaml

   # Evaluate with FinanceBench dataset
   python run.py --config config/financebench_config.yaml
   ```
3. **Customize configuration (optional)**:
   You can copy a dataset configuration file and modify it to suit your needs:
   ```bash
   cp config/locomo_config.yaml config/my_custom_config.yaml
   # Edit config/my_custom_config.yaml with your preferences
   python run.py --config config/my_custom_config.yaml
   ```

### Configuration Guide

RAG uses YAML configuration files to control the evaluation process. Each dataset has its own configuration file in the `config/` directory.

**Key Configuration Sections:**

1. **Basic Configuration**:
   - `dataset_name`: Name of the dataset being evaluated
2. **Adapter Configuration**:
   - `adapter.module`: Python module path for the dataset adapter
   - `adapter.class_name`: Class name of the dataset adapter
3. **Execution Configuration**:
   - `max_workers`: Number of concurrent worker threads
   - `ingest_workers`: Number of worker threads for document ingestion
   - `retrieval_topk`: Number of documents to retrieve
   - `max_queries`: Limit the number of queries to process (null = all)
   - `skip_ingestion`: Skip document ingestion (use existing index)
   - `ingest_mode`: Document ingestion mode ("directory" or "per\_file")
   - `retrieval_instruction`: Custom instruction for retrieval (empty by default)
4. **Path Configuration**:
   - `dataset_dir`: Path to dataset file or directory
   - `doc_output_dir`: Directory for processed documents
   - `vector_store`: Directory for vector index storage
   - `output_dir`: Directory for evaluation results
   - `log_file`: Path to log file
5. **LLM Configuration**:
   - `llm.model`: LLM model name
   - `llm.temperature`: Generation temperature
   - `llm.base_url`: API base URL
   - `llm.api_key`: API key (keep secure)

### Evaluation Process Overview

The evaluation process consists of 5 main stages:

1. **Data Preparation**
   - Convert raw dataset into OpenViking-friendly format
   - Process documents for ingestion
2. **Data Ingestion**
   - Ingest processed documents into OpenViking vector store
   - Create embeddings for documents
   - Store vector index for retrieval
3. **Answer Generation**
   - For each question, retrieve relevant documents from vector store
   - Build prompt with retrieved context and question
   - Generate answer using LLM
4. **Evaluation**
   - Use LLM-as-judge to evaluate generated answers against gold answers
   - Calculate metrics (Recall, F1, Accuracy)
5. **Data Deletion**
   - Clean up vector store and remove ingested documents

### Evaluation Metrics

- **Recall**: Retrieval recall rate
- **F1 Score**: Answer F1 score
- **Accuracy**: LLM judge score (0-4)
- **Latency**: Retrieval latency
- **Token Usage**: Token consumption

### Output Files

Evaluation results are saved in the `Output/` directory with the following structure:

```
Output/
└── {dataset_name}/
    └── experiment_{experiment_name}/
        ├── generated_answers.json       # Generated answers from LLM
        ├── qa_eval_detailed_results.json # Detailed evaluation results
        ├── benchmark_metrics_report.json # Aggregated metrics report
        ├── docs/                         # Processed documents (if skip_ingestion=false)
        └── benchmark.log                 # Log file
```

**Vector Store Database Location:**
The vector index (document database) is stored in the path specified by `vector_store` in the configuration file. By default, this is:

```
datasets/{dataset_name}/viking_store_index_dir
```

#### File descriptions and examples

**1.** **`benchmark_metrics_report.json`** **- Summary Report**

- **What it contains**: Aggregated metrics report with overall performance scores

Example:

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

**Field descriptions:**

- `Insertion Efficiency`: Document ingestion performance statistics
- `Query Efficiency`: Per-query performance averages
- `Performance Metrics`: Core evaluation scores (0-4 scale for Accuracy)

***

**2.** **`generated_answers.json`** **- Generated Answers**

- **What it contains**: All questions, retrieved contexts, and LLM-generated answers

Example (single result):

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

**Field descriptions:**

- `_global_index`: Unique query identifier
- `question`: The question being asked
- `gold_answers`: Ground truth answers
- `retrieval.uris`: URIs of retrieved documents
- `llm.final_answer`: Answer generated by LLM
- `metrics.Recall`: Retrieval recall score (0-1)
- `token_usage`: Token consumption statistics

***

**3.** **`qa_eval_detailed_results.json`** **- Detailed Evaluation**

- **What it contains**: Per-question evaluation including LLM judge reasoning and scores

Example (single result):

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

**Field descriptions:**

- `metrics.F1`: Answer F1 score (0-1)
- `metrics.Accuracy`: LLM judge score (0-4, 4 = perfect)
- `llm_evaluation.reasoning`: LLM judge's reasoning for the score
- `llm_evaluation.normalized_score`: Final normalized score

***

**4.** **`benchmark.log`** **- Execution Log**

- **What it contains**: Detailed execution log with timestamps, warnings, and errors
- **How to view**: Open directly in any text editor

***

**5.** **`docs/`** **- Processed Documents**

- **What it contains**: Processed documents in Markdown format (if `skip_ingestion=false`)
- **How to view**: Open `.md` files directly in any Markdown viewer or text editor

### Benchmark Results Reference

Below are the benchmark results (top-5 retrieval) for reference:

| Dataset          | Queries Evaluated | Average F1 Score | Average Recall | Average Accuracy (0-4) | Normalized Accuracy |
| ---------------- | ----------------- | ---------------- | -------------- | ---------------------- | ------------------- |
| **FinanceBench** | 12                | 0.224            | 0.694          | 2.5                    | 0.625               |
| **Locomo**       | 80                | 0.254            | 0.592          | 2.4                    | 0.600               |
| **Qasper**       | 60                | 0.293            | 0.614          | 2.12                   | 0.529               |
| **SyllabusQA**   | 90                | 0.344            | 0.675          | 2.54                   | 0.636               |

**Test Configuration Details:**

- **LLM Model:** `doubao-seed-2-0-pro-260215`
- **API Base URL:** `https://ark.cn-beijing.volces.com/api/v3`
- **Temperature:** 0 (deterministic)
- **Retrieval Top-K:** 5
- **Max Workers:** 8
- **Ingest Workers:** 8
- **Ingest Mode:** directory
- **Retrieval Instruction:** (empty)
- **Evaluation Metrics:** Recall, F1 Score, Accuracy (0-4 scale)

All datasets used the same LLM and execution configuration, with dataset-specific adapters and paths configured in their respective YAML files.

### Reproducing the Experiment

To reproduce the benchmark results, follow these steps:

```bash
cd OpenViking/benchmark/RAG

# 1. Install dependencies (if not already installed)
uv pip install -e ".[benchmark]"
source .venv/bin/activate

# 2. Download all datasets
python scripts/download_dataset.py

# 3. Run one-click sampling for all datasets with the same parameters as the benchmark
python scripts/run_sampling.py

# 4. Configure your LLM API key
# Edit the configuration files in config/ and set your API key in the llm.api_key field

# 5. Run evaluation for each dataset
python run.py --config config/locomo_config.yaml
python run.py --config config/syllabusqa_config.yaml
python run.py --config config/qasper_config.yaml
python run.py --config config/financebench_config.yaml

# 6. Check results in Output/{dataset_name}/experiment_test_top_5/
```

**Note:** The `run_sampling.py` script will sample the following:
- Locomo: 3 documents, 80 QAs
- SyllabusQA: 7 documents, 90 QAs
- Qasper: 8 documents, 60 QAs
- FinanceBench: 3 documents, 12 QAs
All with seed=42 for reproducibility.

### Advanced Configuration

#### Retrieval Instruction Configuration

You can configure a custom retrieval instruction in the `config.yaml` file to guide the retrieval process. This instruction is prepended to each query during retrieval.

**Configuration Example:**

```yaml
# ===========Execution Configuration============
# Instruction for retrieval, empty by default
# Recommended format: "Target_modality: xxx.\nInstruction:xxx.\nQuery:"
retrieval_instruction: "Target_modality: text.\nInstruction:Locate the part of the conversation where the speakers discuss.\nQuery:"
```

**Recommended Format:**

- `Target_modality: xxx.` - Specify the target modality (e.g., text, image, audio)
- `Instruction: xxx.` - Provide specific instructions for retrieval
- `Query:` - Mark the start of the actual query

When `retrieval_instruction` is empty, the system will use the raw question for retrieval.

#### Customizing Prompts

RAG uses dataset-specific and question-type-specific prompts to guide LLM answer generation. You can customize these prompts in the adapter files under `src/adapters/` to improve evaluation results.

##### Locomo Dataset Prompts (src/adapters/locomo\_adapter.py)

Locomo has 4 question categories, each with specific instructions:

- **Category 1 (Factual Extraction)**:
  ```
  Extract the exact factual answer from the conversation.
  - Use the exact words from the context when possible
  - If multiple items, separate with commas
  ```
- **Category 2 (Time-related)**:
  ```
  Answer the time-related question.
  - Pay close attention to DATE labels in the conversation
  - Calculate relative time (e.g., "10 years ago") when needed
  - Use the exact dates from the context
  ```
- **Category 3 (Reasoning)**:
  ```
  Reason and infer based on the conversation.
  - Use ONLY the facts in the context
  - State your conclusion clearly (e.g., "Likely yes", "Probably no")
  - Do NOT explain your reasoning or provide any basis/justification
  - Only output your final conclusion, nothing else
  - Do NOT invent information
  ```
- **Category 4 (Understanding/Significance)**:
  ```
  Understand the meaning and significance.
  - Focus on what the speakers mean, not just what they say
  - Identify symbolism or implied meaning
  - Use wording from the context when possible
  ```

##### SyllabusQA Dataset Prompts (src/adapters/syllabusqa\_adapter.py)

SyllabusQA has 6 question types:

- **single factual**: Extract single factual answer
- **multi factual**: Extract multiple factual answers
- **single reasoning**: Simple logical reasoning
- **multi reasoning**: Complex reasoning
- **summarization**: Summarize relevant information
- **yes/no**: Yes/No questions

##### Qasper Dataset Prompts (src/adapters/qasper\_adapter.py)

Qasper has 3 answer types:

- **extractive**: Extract exact answer from paper
- **free\_form**: Free-form answer in own words
- **yes\_no**: Yes/No questions

##### FinanceBench Dataset Prompts (src/adapters/financebench\_adapter.py)

FinanceBench has 3 question types:

- **domain-relevant**: Financial domain questions
- **metrics-generated**: Calculate financial metrics
- **novel-generated**: Novel financial questions

##### How to Customize Prompts

1. Open the adapter file for your dataset (e.g., `src/adapters/locomo_adapter.py`)
2. Locate the `CATEGORY_INSTRUCTIONS` dictionary
3. Modify the prompt text for the question type(s) you want to improve
4. Re-run the evaluation with the modified prompts

### Adding New Datasets

1. Create a new adapter class in `src/adapters/`, inheriting from `BaseAdapter`
2. Create corresponding configuration file in `config/`
3. Implement necessary methods:
   - `data_prepare()`: Data preprocessing
   - `load_and_transform()`: Load and transform data
   - `build_prompt()`: Build prompt
   - `post_process_answer()`: Post-process answer

### Integration with OpenViking

This project integrates with OpenViking through:

- Using `openviking` client for data ingestion and retrieval
- Configuring OpenViking connection via `ov.conf`
- Supporting dynamic loading of OpenViking's latest features

### Frequently Asked Questions (FAQ)

**Q: How do I skip the data ingestion stage if I already have a vector index?**
A: Set `skip_ingestion: true` in the configuration file. This will use the existing vector index.

**Q: Can I run only the evaluation stage without re-ingesting documents?**
A: Yes! First run `--step gen` to generate answers, then run `--step eval` to evaluate the generated answers.

**Q: What should I do if I get an API key error?**
A: Make sure you have set a valid API key in the `llm.api_key` field of your configuration file. Keep your API key secure and do not commit it to version control.

**Q: How can I limit the number of queries processed for testing?**
A: Set `max_queries` in the configuration file to the number of queries you want to process (e.g., `max_queries: 10`).

**Q: What's the difference between "directory" and "per\_file" ingest modes?**
A:

- "directory": Treats the entire directory as one document
- "per\_file": Treats each file as a separate document

**Q: How do I customize the retrieval instruction?**
A: Set `retrieval_instruction` in the configuration file. The recommended format is:
`"Target_modality: xxx.\nInstruction:xxx.\nQuery:"`

**Q: Where can I find the evaluation results?**
A: Results are saved in the directory specified by `output_dir` in the configuration file. By default, this is `Output/{dataset_name}/experiment_{experiment_name}/`.

### License

Same license as OpenViking.
