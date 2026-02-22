<div align="center">

<picture>
  <img alt="OpenViking" src="docs/images/banner.jpg" width="100%" height="auto">
</picture>

### OpenViking: The Context Database for AI Agents

English / [中文](README_CN.md)

<a href="https://www.openviking.ai">Website</a> · <a href="https://github.com/volcengine/OpenViking">GitHub</a> · <a href="https://github.com/volcengine/OpenViking/issues">Issues</a> · <a href="https://www.openviking.ai/docs">Docs</a>

[![][release-shield]][release-link]
[![][github-stars-shield]][github-stars-link]
[![][github-issues-shield]][github-issues-shield-link]
[![][github-contributors-shield]][github-contributors-link]
[![][license-shield]][license-shield-link]
[![][last-commit-shield]][last-commit-shield-link]

👋 Join our Community

📱 <a href="./docs/en/about/01-about-us.md#lark-group">Lark Group</a> · <a href="./docs/en/about/01-about-us.md#wechat-group">WeChat</a> · <a href="https://discord.com/invite/eHvx8E9XF3">Discord</a> · <a href="https://x.com/openvikingai">X</a>

</div>

---

## Overview

### Challenges in Agent Development

In the AI era, data is abundant, but high-quality context is hard to come by. When building AI Agents, developers often face these challenges:

- **Fragmented Context**: Memories are in code, resources are in vector databases, and skills are scattered, making them difficult to manage uniformly.
- **Surging Context Demand**: An Agent's long-running tasks produce context at every execution. Simple truncation or compression leads to information loss.
- **Poor Retrieval Effectiveness**: Traditional RAG uses flat storage, lacking a global view and making it difficult to understand the full context of information.
- **Unobservable Context**: The implicit retrieval chain of traditional RAG is like a black box, making it hard to debug when errors occur.
- **Limited Memory Iteration**: Current memory is just a record of user interactions, lacking Agent-related task memory.

### The OpenViking Solution

**OpenViking** is an open-source **Context Database** designed specifically for AI Agents.

We aim to define a minimalist context interaction paradigm for Agents, allowing developers to completely say goodbye to the hassle of context management. OpenViking abandons the fragmented vector storage model of traditional RAG and innovatively adopts a **"file system paradigm"** to unify the structured organization of memories, resources, and skills needed by Agents.

With OpenViking, developers can build an Agent's brain just like managing local files:

- **Filesystem Management Paradigm** → **Solves Fragmentation**: Unified context management of memories, resources, and skills based on a filesystem paradigm.
- **Tiered Context Loading** → **Reduces Token Consumption**: L0/L1/L2 three-tier structure, loaded on demand, significantly saving costs.
- **Directory Recursive Retrieval** → **Improves Retrieval Effect**: Supports native filesystem retrieval methods, combining directory positioning with semantic search to achieve recursive and precise context acquisition.
- **Visualized Retrieval Trajectory** → **Observable Context**: Supports visualization of directory retrieval trajectories, allowing users to clearly observe the root cause of issues and guide retrieval logic optimization.
- **Automatic Session Management** → **Context Self-Iteration**: Automatically compresses content, resource references, tool calls, etc., in conversations, extracting long-term memory, making the Agent smarter with use.

---

## Quick Start

### Prerequisites

Before starting with OpenViking, please ensure your environment meets the following requirements:

- **Python Version**: 3.10 or higher
- **Operating System**: Linux, macOS, Windows
- **Network Connection**: A stable network connection is required (for downloading dependencies and accessing model services)

### 1. Installation

#### Python Package

```bash
pip install openviking
```

#### Rust CLI (Optional)

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/crates/ov_cli/install.sh | bash
```

Or build from source:

```bash
cargo install --git https://github.com/volcengine/OpenViking ov_cli
```

### 2. Model Preparation

OpenViking requires the following model capabilities:
- **VLM Model**: For image and content understanding
- **Embedding Model**: For vectorization and semantic retrieval

#### Supported VLM Providers

OpenViking supports multiple VLM providers:

| Provider | Model | Get API Key |
|----------|-------|-------------|
| `volcengine` | doubao | [Volcengine Console](https://console.volcengine.com/ark) |
| `openai` | gpt | [OpenAI Platform](https://platform.openai.com) |
| `anthropic` | claude | [Anthropic Console](https://console.anthropic.com) |
| `deepseek` | deepseek | [DeepSeek Platform](https://platform.deepseek.com) |
| `gemini` | gemini | [Google AI Studio](https://aistudio.google.com) |
| `moonshot` | kimi | [Moonshot Platform](https://platform.moonshot.cn) |
| `zhipu` | glm | [Zhipu Open Platform](https://open.bigmodel.cn) |
| `dashscope` | qwen | [DashScope Console](https://dashscope.console.aliyun.com) |
| `minimax` | minimax | [MiniMax Platform](https://platform.minimax.io) |
| `openrouter` | (any model) | [OpenRouter](https://openrouter.ai) |
| `vllm` | (local model) | — |

> 💡 **Tip**: OpenViking uses a **Provider Registry** for unified model access. The system automatically detects the provider based on model name keywords, so you can switch between providers seamlessly.

#### Provider-Specific Notes

<details>
<summary><b>Volcengine (Doubao)</b></summary>

Volcengine supports both model names and endpoint IDs. Using model names is recommended for simplicity:

```json
{
  "vlm": {
    "provider": "volcengine",
    "model": "doubao-seed-1-6-240615",
    "api_key": "your-api-key",
    "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
  }
}
```

You can also use endpoint IDs (found in [Volcengine ARK Console](https://console.volcengine.com/ark)):

```json
{
  "vlm": {
    "provider": "volcengine",
    "model": "ep-20241220174930-xxxxx",
    "api_key": "your-api-key",
    "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
  }
}
```

</details>

<details>
<summary><b>Zhipu AI (智谱)</b></summary>

If you're on Zhipu's coding plan, use the coding API endpoint:

```json
{
  "vlm": {
    "provider": "zhipu",
    "model": "glm-4-plus",
    "api_key": "your-api-key",
    "api_base": "https://open.bigmodel.cn/api/coding/paas/v4"
  }
}
```

</details>

<details>
<summary><b>MiniMax (中国大陆)</b></summary>

For MiniMax's mainland China platform (minimaxi.com), specify the API base:

```json
{
  "vlm": {
    "provider": "minimax",
    "model": "abab6.5s-chat",
    "api_key": "your-api-key",
    "api_base": "https://api.minimaxi.com/v1"
  }
}
```

</details>

<details>
<summary><b>Local Models (vLLM)</b></summary>

Run OpenViking with your own local models using vLLM:

```bash
# Start vLLM server
vllm serve meta-llama/Llama-3.1-8B-Instruct --port 8000
```

```json
{
  "vlm": {
    "provider": "vllm",
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "api_key": "dummy",
    "api_base": "http://localhost:8000/v1"
  }
}
```

</details>

### 3. Environment Configuration

#### Configuration Template

Create a configuration file `~/.openviking/ov.conf`:

```json
{
  "embedding": {
    "dense": {
      "api_base" : "<api-endpoint>",   // API endpoint address
      "api_key"  : "<your-api-key>",   // Model service API Key
      "provider" : "<provider-type>",  // Provider type: "volcengine" or "openai" (currently supported)
      "dimension": 1024,               // Vector dimension
      "model"    : "<model-name>"      // Embedding model name (e.g., doubao-embedding-vision-250615 or text-embedding-3-large)
    }
  },
  "vlm": {
    "api_base" : "<api-endpoint>",     // API endpoint address
    "api_key"  : "<your-api-key>",     // Model service API Key
    "provider" : "<provider-type>",    // Provider type (volcengine, openai, deepseek, anthropic, etc.)
    "model"    : "<model-name>"        // VLM model name (e.g., doubao-seed-1-8-251228 or gpt-4-vision-preview)
  }
}
```

> **Note**: For embedding models, currently only `volcengine` (Doubao) and `openai` providers are supported. For VLM models, we support multiple providers including volcengine, openai, deepseek, anthropic, gemini, moonshot, zhipu, dashscope, minimax, and more.

#### Configuration Examples

👇 Expand to see the configuration example for your model service:

<details>
<summary><b>Example 1: Using Volcengine (Doubao Models)</b></summary>

```json
{
  "embedding": {
    "dense": {
      "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
      "api_key"  : "your-volcengine-api-key",
      "provider" : "volcengine",
      "dimension": 1024,
      "model"    : "doubao-embedding-vision-250615"
    }
  },
  "vlm": {
    "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
    "api_key"  : "your-volcengine-api-key",
    "provider" : "volcengine",
    "model"    : "doubao-seed-1-8-251228"
  }
}
```

</details>

<details>
<summary><b>Example 2: Using OpenAI Models</b></summary>

```json
{
  "embedding": {
    "dense": {
      "api_base" : "https://api.openai.com/v1",
      "api_key"  : "your-openai-api-key",
      "provider" : "openai",
      "dimension": 3072,
      "model"    : "text-embedding-3-large"
    }
  },
  "vlm": {
    "api_base" : "https://api.openai.com/v1",
    "api_key"  : "your-openai-api-key",
    "provider" : "openai",
    "model"    : "gpt-4-vision-preview"
  }
}
```

</details>

#### Set Environment Variable

After creating the configuration file, set the environment variable to point to it (Linux/macOS):

```bash
export OPENVIKING_CONFIG_FILE=~/.openviking/ov.conf
```

On Windows, use one of the following:

PowerShell:

```powershell
$env:OPENVIKING_CONFIG_FILE = "$HOME/.openviking/ov.conf"
```

Command Prompt (cmd.exe):

```bat
set "OPENVIKING_CONFIG_FILE=%USERPROFILE%\.openviking\ov.conf"
```

> 💡 **Tip**: You can also place the configuration file in other locations, just specify the correct path in the environment variable.

### 4. Run Your First Example

> 📝 **Prerequisite**: Ensure you have completed the environment configuration in the previous step.

Now let's run a complete example to experience the core features of OpenViking.

#### Create Python Script

Create `example.py`:

```python
import openviking as ov

# Initialize OpenViking client with data directory
client = ov.SyncOpenViking(path="./data")

try:
    # Initialize the client
    client.initialize()

    # Add resource (supports URL, file, or directory)
    add_result = client.add_resource(
        path="https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md"
    )
    root_uri = add_result['root_uri']

    # Explore the resource tree structure
    ls_result = client.ls(root_uri)
    print(f"Directory structure:\n{ls_result}\n")

    # Use glob to find markdown files
    glob_result = client.glob(pattern="**/*.md", uri=root_uri)
    if glob_result['matches']:
        content = client.read(glob_result['matches'][0])
        print(f"Content preview: {content[:200]}...\n")

    # Wait for semantic processing to complete
    print("Wait for semantic processing...")
    client.wait_processed()

    # Get abstract and overview of the resource
    abstract = client.abstract(root_uri)
    overview = client.overview(root_uri)
    print(f"Abstract:\n{abstract}\n\nOverview:\n{overview}\n")

    # Perform semantic search
    results = client.find("what is openviking", target_uri=root_uri)
    print("Search results:")
    for r in results.resources:
        print(f"  {r.uri} (score: {r.score:.4f})")

    # Close the client
    client.close()

except Exception as e:
    print(f"Error: {e}")
```

#### Run the Script

```bash
python example.py
```

#### Expected Output

```
Directory structure:
...

Content preview: ...

Wait for semantic processing...
Abstract:
...

Overview:
...

Search results:
  viking://resources/... (score: 0.8523)
  ...
```

Congratulations! You have successfully run OpenViking 🎉

---

## Server Deployment

For production environments, we recommend running OpenViking as a standalone HTTP service to provide persistent, high-performance context support for your AI Agents.

🚀 **Deploy OpenViking on Cloud**:
To ensure optimal storage performance and data security, we recommend deploying on **Volcengine Elastic Compute Service (ECS)** using the **veLinux** operating system. We have prepared a detailed step-by-step guide to get you started quickly.

👉 **[View: Server Deployment & ECS Setup Guide](./docs/en/getting-started/03-quickstart-server.md)**

---

## Core Concepts

After running the first example, let's dive into the design philosophy of OpenViking. These five core concepts correspond one-to-one with the solutions mentioned earlier, together building a complete context management system:

### 1. Filesystem Management Paradigm → Solves Fragmentation

We no longer view context as flat text slices but unify them into an abstract virtual filesystem. Whether it's memories, resources, or capabilities, they are mapped to virtual directories under the `viking://` protocol, each with a unique URI.

This paradigm gives Agents unprecedented context manipulation capabilities, enabling them to locate, browse, and manipulate information precisely and deterministically through standard commands like `ls` and `find`, just like a developer. This transforms context management from vague semantic matching into intuitive, traceable "file operations". Learn more: [Viking URI](./docs/en/concepts/04-viking-uri.md) | [Context Types](./docs/en/concepts/02-context-types.md)

```
viking://
├── resources/              # Resources: project docs, repos, web pages, etc.
│   ├── my_project/
│   │   ├── docs/
│   │   │   ├── api/
│   │   │   └── tutorials/
│   │   └── src/
│   └── ...
├── user/                   # User: personal preferences, habits, etc.
│   └── memories/
│       ├── preferences/
│       │   ├── writing_style
│       │   └── coding_habits
│       └── ...
└── agent/                  # Agent: skills, instructions, task memories, etc.
    ├── skills/
    │   ├── search_code
    │   ├── analyze_data
    │   └── ...
    ├── memories/
    └── instructions/
```

### 2. Tiered Context Loading → Reduces Token Consumption

Stuffing massive amounts of context into a prompt all at once is not only expensive but also prone to exceeding model windows and introducing noise. OpenViking automatically processes context into three levels upon writing:
- **L0 (Abstract)**: A one-sentence summary for quick retrieval and identification.
- **L1 (Overview)**: Contains core information and usage scenarios for Agent decision-making during the planning phase.
- **L2 (Details)**: The full original data, for deep reading by the Agent when absolutely necessary.

Learn more: [Context Layers](./docs/en/concepts/03-context-layers.md)

```
viking://resources/my_project/
├── .abstract               # L0 Layer: Abstract (~100 tokens) - Quick relevance check
├── .overview               # L1 Layer: Overview (~2k tokens) - Understand structure and key points
├── docs/
│   ├── .abstract          # Each directory has corresponding L0/L1 layers
│   ├── .overview
│   ├── api/
│   │   ├── .abstract
│   │   ├── .overview
│   │   ├── auth.md        # L2 Layer: Full content - Load on demand
│   │   └── endpoints.md
│   └── ...
└── src/
    └── ...
```

### 3. Directory Recursive Retrieval → Improves Retrieval Effect

Single vector retrieval struggles with complex query intents. OpenViking has designed an innovative **Directory Recursive Retrieval Strategy** that deeply integrates multiple retrieval methods:

1. **Intent Analysis**: Generate multiple retrieval conditions through intent analysis.
2. **Initial Positioning**: Use vector retrieval to quickly locate the high-score directory where the initial slice is located.
3. **Refined Exploration**: Perform a secondary retrieval within that directory and update high-score results to the candidate set.
4. **Recursive Drill-down**: If subdirectories exist, recursively repeat the secondary retrieval steps layer by layer.
5. **Result Aggregation**: Finally, obtain the most relevant context to return.

This "lock high-score directory first, then refine content exploration" strategy not only finds the semantically best-matching fragments but also understands the full context where the information resides, thereby improving the globality and accuracy of retrieval. Learn more: [Retrieval Mechanism](./docs/en/concepts/07-retrieval.md)

### 4. Visualized Retrieval Trajectory → Observable Context

OpenViking's organization uses a hierarchical virtual filesystem structure. All context is integrated in a unified format, and each entry corresponds to a unique URI (like a `viking://` path), breaking the traditional flat black-box management mode with a clear hierarchy that is easy to understand.

The retrieval process adopts a directory recursive strategy. The trajectory of directory browsing and file positioning for each retrieval is fully preserved, allowing users to clearly observe the root cause of problems and guide the optimization of retrieval logic. Learn more: [Retrieval Mechanism](./docs/en/concepts/07-retrieval.md)

### 5. Automatic Session Management → Context Self-Iteration

OpenViking has a built-in memory self-iteration loop. At the end of each session, developers can actively trigger the memory extraction mechanism. The system will asynchronously analyze task execution results and user feedback, and automatically update them to the User and Agent memory directories.

- **User Memory Update**: Update memories related to user preferences, making Agent responses better fit user needs.
- **Agent Experience Accumulation**: Extract core content such as operational tips and tool usage experience from task execution experience, aiding efficient decision-making in subsequent tasks.

This allows the Agent to get "smarter with use" through interactions with the world, achieving self-evolution. Learn more: [Session Management](./docs/en/concepts/08-session.md)

---

## Project Architecture

The OpenViking project adopts a clear modular architecture design. The main directory structure is as follows:

```
OpenViking/
├── openviking/              # Core source code directory
│   ├── core/               # Core modules: client, engine, filesystem, etc.
│   ├── models/             # Model integration: VLM and Embedding model encapsulation
│   ├── parse/              # Resource parsing: file parsing, detection, OVPack handling
│   ├── retrieve/           # Retrieval module: semantic retrieval, directory recursive retrieval
│   ├── storage/            # Storage layer: vector DB, filesystem queue, observers
│   ├── session/            # Session management: history, memory extraction
│   ├── message/            # Message processing: formatting, conversion
│   ├── prompts/            # Prompt templates: templates for various tasks
│   ├── utils/              # Utilities: config, helpers
│   └── bin/                # Command line tools
├── docs/                    # Project documentation
│   ├── zh/                 # Chinese documentation
│   ├── en/                 # English documentation
│   └── images/             # Documentation images
├── examples/                # Usage examples
├── tests/                   # Test cases
│   ├── client/             # Client tests
│   ├── engine/             # Engine tests
│   ├── integration/        # Integration tests
│   ├── session/            # Session tests
│   └── vectordb/           # Vector DB tests
├── src/                     # C++ extensions (high-performance index and storage)
│   ├── common/             # Common components
│   ├── index/              # Index implementation
│   └── store/              # Storage implementation
├── third_party/             # Third-party dependencies
├── pyproject.toml           # Python project configuration
├── setup.py                 # Setup script
├── LICENSE                  # Open source license
├── CONTRIBUTING.md          # Contributing guide
├── AGENT.md                 # Agent development guide
└── README.md                # Project readme
```


---

## Advanced Reading

For more details, please visit our [Full Documentation](./docs/en/).

---

## Community & Team

### About Us

OpenViking is an open-source context database initiated and maintained by the **ByteDance Volcengine Viking Team**.

The Viking team focuses on unstructured information processing and intelligent retrieval, accumulating rich commercial practical experience in context engineering technology:

- **2019**: VikingDB vector database supported large-scale use across all ByteDance businesses.
- **2023**: VikingDB sold on Volcengine public cloud.
- **2024**: Launched developer product matrix: VikingDB, Viking KnowledgeBase, Viking MemoryBase.
- **2025**: Created upper-layer application products like AI Search and Vaka Knowledge Assistant.
- **Oct 2025**: Open-sourced [MineContext](https://github.com/volcengine/MineContext), exploring proactive AI applications.
- **Jan 2026**: Open-sourced OpenViking, providing underlying context database support for AI Agents.

For more details, please see: **[About Us](./docs/en/about/01-about-us.md)**

---

### Join the Community

OpenViking is still in its early stages, and there are many areas for improvement and exploration. We sincerely invite every developer passionate about AI Agent technology:

- Light up a precious **Star** for us to give us the motivation to move forward.
- Visit our [**Website**](https://www.openviking.ai) to understand the philosophy we convey, and use it in your projects via the [**Documentation**](https://www.openviking.ai/docs). Feel the change it brings and give us feedback on your truest experience.
- Join our community to share your insights, help answer others' questions, and jointly create an open and mutually helpful technical atmosphere:
  - 📱 **Lark Group**: Scan the QR code to join → [View QR Code](./docs/en/about/01-about-us.md#lark-group)
  - 💬 **WeChat Group**: Scan the QR code to add assistant → [View QR Code](./docs/en/about/01-about-us.md#wechat-group)
  - 🎮 **Discord**: [Join Discord Server](https://discord.com/invite/eHvx8E9XF3)
  - 🐦 **X (Twitter)**：[Follow us](https://x.com/openvikingai)
- Become a **Contributor**, whether submitting a bug fix or contributing a new feature, every line of your code will be an important cornerstone of OpenViking's growth.

Let's work together to define and build the future of AI Agent context management. The journey has begun, looking forward to your participation!

---

### Star Trend

[![Star History Chart](https://api.star-history.com/svg?repos=volcengine/OpenViking&type=timeline&legend=top-left)](https://www.star-history.com/#volcengine/OpenViking&type=timeline&legend=top-left)

---

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](./LICENSE) file for details.


<!-- Link Definitions -->

[release-shield]: https://img.shields.io/github/v/release/volcengine/OpenViking?color=369eff&labelColor=black&logo=github&style=flat-square
[release-link]: https://github.com/volcengine/OpenViking/releases
[license-shield]: https://img.shields.io/badge/license-apache%202.0-white?labelColor=black&style=flat-square
[license-shield-link]: https://github.com/volcengine/OpenViking/blob/main/LICENSE
[last-commit-shield]: https://img.shields.io/github/last-commit/volcengine/OpenViking?color=c4f042&labelColor=black&style=flat-square
[last-commit-shield-link]: https://github.com/volcengine/OpenViking/commits/main
[github-stars-shield]: https://img.shields.io/github/stars/volcengine/OpenViking?labelColor&style=flat-square&color=ffcb47
[github-stars-link]: https://github.com/volcengine/OpenViking
[github-issues-shield]: https://img.shields.io/github/issues/volcengine/OpenViking?labelColor=black&style=flat-square&color=ff80eb
[github-issues-shield-link]: https://github.com/volcengine/OpenViking/issues
[github-contributors-shield]: https://img.shields.io/github/contributors/volcengine/OpenViking?color=c4f042&labelColor=black&style=flat-square
[github-contributors-link]: https://github.com/volcengine/OpenViking/graphs/contributors
