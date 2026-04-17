<div align="center">

<a href="https://openviking.ai/" target="_blank">
  <picture>
    <img alt="OpenViking" src="docs/images/ov-logo.png" width="200px" height="auto">
  </picture>
</a>

### OpenViking: The Context Database for AI Agents

English / [中文](README_CN.md) / [日本語](README_JA.md)

<a href="https://www.openviking.ai">Website</a> · <a href="https://github.com/volcengine/OpenViking">GitHub</a> · <a href="https://github.com/volcengine/OpenViking/issues">Issues</a> · <a href="./docs">Docs</a>

[![](https://img.shields.io/github/v/release/volcengine/OpenViking?color=369eff\&labelColor=black\&logo=github\&style=flat-square)](https://github.com/volcengine/OpenViking/releases)
[![](https://img.shields.io/github/stars/volcengine/OpenViking?labelColor\&style=flat-square\&color=ffcb47)](https://github.com/volcengine/OpenViking)
[![](https://img.shields.io/github/issues/volcengine/OpenViking?labelColor=black\&style=flat-square\&color=ff80eb)](https://github.com/volcengine/OpenViking/issues)
[![](https://img.shields.io/github/contributors/volcengine/OpenViking?color=c4f042\&labelColor=black\&style=flat-square)](https://github.com/volcengine/OpenViking/graphs/contributors)
[![](https://img.shields.io/badge/license-AGPLv3-white?labelColor=black\&style=flat-square)](https://github.com/volcengine/OpenViking/blob/main/LICENSE)
[![](https://img.shields.io/github/last-commit/volcengine/OpenViking?color=c4f042\&labelColor=black\&style=flat-square)](https://github.com/volcengine/OpenViking/commits/main)

👋 Join our Community

📱 <a href="./docs/en/about/01-about-us.md#lark-group">Lark Group</a> · <a href="./docs/en/about/01-about-us.md#wechat-group">WeChat</a> · <a href="https://discord.com/invite/eHvx8E9XF3">Discord</a> · <a href="https://x.com/openvikingai">X</a>

<a href="https://trendshift.io/repositories/19668" target="_blank"><img src="https://trendshift.io/api/badge/repositories/19668" alt="volcengine%2FOpenViking | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

</div>

***

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

***

## Quick Start

### Prerequisites

Before starting with OpenViking, please ensure your environment meets the following requirements:

- **Python Version**: 3.10 or higher
- **Go Version**: 1.22 or higher (Required for building AGFS components)
- **C++ Compiler**: GCC 9+ or Clang 11+ (Required for building core extensions)
- **Operating System**: Linux, macOS, Windows
- **Network Connection**: A stable network connection is required (for downloading dependencies and accessing model services)

### 1. Installation

#### Python Package

```bash
pip install openviking --upgrade --force-reinstall
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

| Provider       | Description              | Setup                                                                                                                                                                                                              |
| -------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `volcengine`   | Volcengine Doubao Models | [Volcengine Console](https://console.volcengine.com/ark/region:ark+cn-beijing/overview?briefPage=0\&briefType=introduce\&type=new\&utm_content=OpenViking\&utm_medium=devrel\&utm_source=OWO\&utm_term=OpenViking) |
| `openai`       | OpenAI Official API      | [OpenAI Platform](https://platform.openai.com)                                                                                                                                                                     |
| `openai-codex` | Codex VLM                | Use `openviking-server init`                                                                                                                                                                                       |
| `kimi`         | Kimi Code Membership     | Use `openviking-server init`                                                                                                                                                                                       |
| `glm`          | GLM Coding Plan          | Use `openviking-server init`                                                                                                                                                                                       |

#### Provider-Specific Notes

<details>
<summary><b>Volcengine (Doubao)</b></summary>

Volcengine supports both model names and endpoint IDs. Using model names is recommended for simplicity:

```json
{
  "vlm": {
    "provider": "volcengine",
    "model": "doubao-seed-2-0-pro-260215",
    "api_key": "your-api-key",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

You can also use endpoint IDs (found in [Volcengine ARK Console](https://console.volcengine.com/ark/region:ark+cn-beijing/overview?briefPage=0\&briefType=introduce\&type=new\&utm_content=OpenViking\&utm_medium=devrel\&utm_source=OWO\&utm_term=OpenViking):

```json
{
  "vlm": {
    "provider": "volcengine",
    "model": "ep-20241220174930-xxxxx",
    "api_key": "your-api-key",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

</details>

<details>
<summary><b>OpenAI</b></summary>

Use OpenAI's official API:

```json
{
  "vlm": {
    "provider": "openai",
    "model": "gpt-4o",
    "api_key": "your-api-key",
    "api_base": "https://api.openai.com/v1"
  }
}
```

You can also use a custom OpenAI-compatible endpoint:

```json
{
  "vlm": {
    "provider": "openai",
    "model": "gpt-4o",
    "api_key": "your-api-key",
    "api_base": "https://your-custom-endpoint.com/v1"
  }
}
```

</details>

<details>
<summary><b>OpenAI Codex (OAuth)</b></summary>

Use this provider when you want OpenViking to call Codex VLM through your ChatGPT/Codex OAuth session instead of a standard OpenAI API key:

```bash
openviking-server init
# choose OpenAI Codex when prompted
openviking-server doctor
```

```json
{
  "vlm": {
    "provider": "openai-codex",
    "model": "gpt-5.3-codex",
    "api_base": "https://chatgpt.com/backend-api/codex",
    "temperature": 0.0,
    "max_retries": 2
  }
}
```

> 💡 **Tip**:
>
> - `openai-codex` does not require `vlm.api_key` when Codex OAuth is available
> - OpenViking stores its own Codex auth state at `~/.openviking/codex_auth.json`
> - `openviking-server doctor` validates that the current Codex auth is usable

</details>

<details>
<summary><b>Kimi Coding (Subscription)</b></summary>

Use this provider when you want OpenViking to call the dedicated Kimi Coding subscription endpoint directly:

```bash
openviking-server init
# choose Kimi Coding when prompted
openviking-server doctor
```

```json
{
  "vlm": {
    "provider": "kimi",
    "model": "kimi-code",
    "api_key": "your-kimi-subscription-api-key",
    "api_base": "https://api.kimi.com/coding",
    "temperature": 0.0,
    "max_retries": 2
  }
}
```

> 💡 **Tip**:
>
> - `kimi` applies the recommended Kimi Coding defaults automatically, including the default Kimi Coding user agent
> - `kimi-code` and `kimi-coding` are accepted aliases for the provider name
> - `kimi-code` is normalized to Kimi's upstream coding model automatically

</details>

<details>
<summary><b>GLM Coding Plan (Subscription)</b></summary>

Use this provider when you want OpenViking to call Z.AI's OpenAI-compatible Coding Plan endpoint directly:

```bash
openviking-server init
# choose GLM Coding Plan when prompted
openviking-server doctor
```

```json
{
  "vlm": {
    "provider": "glm",
    "model": "glm-4.6v",
    "api_key": "your-zai-api-key",
    "api_base": "https://api.z.ai/api/coding/paas/v4",
    "temperature": 0.0,
    "max_retries": 2
  }
}
```

> 💡 **Tip**:
>
> - `glm`, `zhipu`, `zai`, `z-ai`, and `z.ai` all resolve to the same first-class GLM provider
> - The default endpoint is the Coding Plan endpoint, not the general Z.AI endpoint
> - Use a vision-capable model such as `glm-4.6v` or `glm-5v-turbo` for multimodal parsing

</details>

### 3. Environment Configuration

#### Quick Setup for Local Models (Ollama)

If you want to run OpenViking with local models via [Ollama](https://ollama.ai), the interactive setup wizard handles everything automatically:

```bash
openviking-server init
```

The wizard will:

- Detect and install Ollama if needed
- Recommend and pull suitable embedding and VLM models for your hardware
- Generate a ready-to-use `ov.conf` configuration file

To validate your setup at any time:

```bash
openviking-server doctor
```

`doctor` checks local prerequisites (config file, Python version, embedding/VLM provider connectivity, disk space) without requiring a running server.

> For cloud API providers (Volcengine, OpenAI, Gemini, etc.), continue with the manual configuration below.

#### Server Configuration Template

The recommended first-time flow is:

```bash
openviking-server init
openviking-server doctor
```

If you choose `OpenAI Codex` inside `openviking-server init`, the wizard can import existing Codex auth or start the Codex sign-in flow for you.

If you prefer manual configuration, create `~/.openviking/ov.conf`, remove the comments before copy:

```json
{
  "storage": {
    "workspace": "/home/your-name/openviking_workspace"
  },
  "log": {
    "level": "INFO",
    "output": "stdout"                 // Log output: "stdout" or "file"
  },
  "embedding": {
    "dense": {
      "api_base" : "<api-endpoint>",   // API endpoint address
      "api_key"  : "<your-api-key>",   // Model service API Key
      "provider" : "<provider-type>",  // Provider type: "volcengine" or "openai" (currently supported)
      "dimension": 1024,               // Vector dimension
      "model"    : "<model-name>"      // Embedding model name (e.g., doubao-embedding-vision-251215 or text-embedding-3-large)
    },
    "max_concurrent": 10               // Max concurrent embedding requests (default: 10)
  },
  "vlm": {
    "api_base" : "<api-endpoint>",     // API endpoint address
    "api_key"  : "<your-api-key>",     // Model service API Key (optional for openai-codex)
    "provider" : "<provider-type>",    // Provider type (volcengine, openai, openai-codex, kimi, glm, etc.)
    "model"    : "<model-name>",       // VLM model name (e.g., doubao-seed-2-0-pro-260215 or gpt-4-vision-preview)
    "max_concurrent": 100              // Max concurrent LLM calls for semantic processing (default: 100)
  }
}
```

> **Note**: For embedding models, supported providers are `volcengine` (Doubao), `openai`, `jina`, `voyage`, `minimax`, `vikingdb`, and `gemini` (requires `pip install "google-genai>=1.0.0"`). For VLM models, common providers include `volcengine`, `openai`, `openai-codex`, `kimi`, and `glm`.

#### Server Configuration Examples

👇 Expand to see the configuration example for your model service:

<details>
<summary><b>Example 1: Using Volcengine (Doubao Models)</b></summary>

```json
{
  "storage": {
    "workspace": "/home/your-name/openviking_workspace"
  },
  "log": {
    "level": "INFO",
    "output": "stdout"                 // Log output: "stdout" or "file"
  },
  "embedding": {
    "dense": {
      "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
      "api_key"  : "your-volcengine-api-key",
      "provider" : "volcengine",
      "dimension": 1024,
      "model"    : "doubao-embedding-vision-251215"
    },
    "max_concurrent": 10
  },
  "vlm": {
    "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
    "api_key"  : "your-volcengine-api-key",
    "provider" : "volcengine",
    "model"    : "doubao-seed-2-0-pro-260215",
    "max_concurrent": 100
  }
}
```

</details>

<details>
<summary><b>Example 2: Using OpenAI Models</b></summary>

```json
{
  "storage": {
    "workspace": "/home/your-name/openviking_workspace"
  },
  "log": {
    "level": "INFO",
    "output": "stdout"                 // Log output: "stdout" or "file"
  },
  "embedding": {
    "dense": {
      "api_base" : "https://api.openai.com/v1",
      "api_key"  : "your-openai-api-key",
      "provider" : "openai",
      "dimension": 3072,
      "model"    : "text-embedding-3-large"
    },
    "max_concurrent": 10
  },
  "vlm": {
    "api_base" : "https://api.openai.com/v1",
    "api_key"  : "your-openai-api-key",
    "provider" : "openai",
    "model"    : "gpt-4-vision-preview",
    "max_concurrent": 100
  }
}
```

</details>

<details>
<summary><b>Example 3: Using Google Gemini Embedding</b></summary>

Install the required package first:

```bash
pip install "google-genai>=1.0.0"
```

```json
{
  "storage": {
    "workspace": "/home/your-name/openviking_workspace"
  },
  "embedding": {
    "dense": {
      "provider": "gemini",
      "api_key": "your-google-api-key",
      "model": "gemini-embedding-2-preview",
      "dimension": 3072
    },
    "max_concurrent": 10
  },
  "vlm": {
    "api_base" : "https://api.openai.com/v1",
    "api_key"  : "your-openai-api-key",
    "provider" : "openai",
    "model"    : "gpt-4o",
    "max_concurrent": 100
  }
}
```

Get your Google API key at <https://aistudio.google.com/apikey>

</details>

<details>
<summary><b>Example 4: Using Volcengine Embedding + Codex VLM</b></summary>

Use `openviking-server init` and choose `OpenAI Codex`, then run `openviking-server doctor`.

```json
{
  "storage": {
    "workspace": "/home/your-name/openviking_workspace"
  },
  "embedding": {
    "dense": {
      "api_base" : "https://ark.cn-beijing.volces.com/api/v3",
      "api_key"  : "your-volcengine-api-key",
      "provider" : "volcengine",
      "dimension": 1024,
      "model"    : "doubao-embedding-vision-251215"
    }
  },
  "vlm": {
    "api_base" : "https://chatgpt.com/backend-api/codex",
    "provider" : "openai-codex",
    "model"    : "gpt-5.3-codex",
    "max_concurrent": 100
  }
}
```

</details>

#### Set Server Configuration Environment Variable

After creating the configuration file, set the environment variable to point to it (Linux/macOS):

```bash
export OPENVIKING_CONFIG_FILE=~/.openviking/ov.conf # by default
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

#### CLI/Client Configuration Examples

👇 Expand to see the configuration example for your CLI/Client:

Example: ovcli.conf for visiting localhost server

```json
{
  "url": "http://localhost:1933",
  "timeout": 60.0,
  "output": "table"
}
```

After creating the configuration file, set the environment variable to point to it (Linux/macOS):

```bash
export OPENVIKING_CLI_CONFIG_FILE=~/.openviking/ovcli.conf # by default
```

On Windows, use one of the following:

PowerShell:

```powershell
$env:OPENVIKING_CLI_CONFIG_FILE = "$HOME/.openviking/ovcli.conf"
```

Command Prompt (cmd.exe):

```bat
set "OPENVIKING_CLI_CONFIG_FILE=%USERPROFILE%\.openviking\ovcli.conf"
```

### 4. Run Your First Example

> 📝 **Prerequisite**: Ensure you have completed the configuration (ov.conf and ovcli.conf) in the previous step.

Now let's run a complete example to experience the core features of OpenViking.

#### Launch Server

```bash
openviking-server doctor
openviking-server
```

If you configured `provider=openai-codex`, `openviking-server doctor` already validates Codex auth.

or you can run in background

```bash
nohup openviking-server > /data/log/openviking.log 2>&1 &
```

#### Run the CLI

```bash
ov status
ov add-resource https://github.com/volcengine/OpenViking # --wait
ov ls viking://resources/
ov tree viking://resources/volcengine -L 2
# wait some time for semantic processing if not --wait
ov find "what is openviking"
ov grep "openviking" --uri viking://resources/volcengine/OpenViking/docs/zh
```

Congratulations! You have successfully run OpenViking 🎉

### VikingBot Quick Start

VikingBot is an AI agent framework built on top of OpenViking. Here's how to get started:

```bash
# Option 1: Install VikingBot from PyPI (recommended for most users)
pip install "openviking[bot]"

# Option 2: Install VikingBot from source (for development)
uv pip install -e ".[bot]"

# Start OpenViking server with Bot enabled
openviking-server --with-bot

# In another terminal, start interactive chat
ov chat
```

If you use the official Docker image, `vikingbot` is already bundled in the image and starts by default together with the OpenViking server and console UI. You can disable it at runtime with either `--without-bot` or `-e OPENVIKING_WITH_BOT=0`.

***

## Server Deployment Details

For production environments, we recommend running OpenViking as a standalone HTTP service to provide persistent, high-performance context support for your AI Agents.

🚀 **Deploy OpenViking on Cloud**:
To ensure optimal storage performance and data security, we recommend deploying on **Volcengine Elastic Compute Service (ECS)** using the **veLinux** operating system. We have prepared a detailed step-by-step guide to get you started quickly.

👉 **[View: Server Deployment & ECS Setup Guide](./docs/en/getting-started/03-quickstart-server.md)**

## OpenClaw Context Plugin Details

- Test Dataset: Effect testing based on LoCoMo10 (<https://github.com/snap-research/locomo>) long-range dialogues (1,540 cases in total after removing category5 without ground truth)
- Experimental Groups: Since users may not disable OpenClaw's native memory when using OpenViking, we added experimental groups with native memory enabled or disabled
- OpenViking Version: 0.1.18
- Model: seed-2.0-code
- Evaluation Script: <https://github.com/ZaynJarvis/openclaw-eval/tree/main>

| Experimental Group                          | Task Completion Rate | Cost: Input Tokens (Total) |
| ------------------------------------------- | -------------------- | -------------------------- |
| OpenClaw(memory-core)                       | 35.65%               | 24,611,530                 |
| OpenClaw + LanceDB (-memory-core)           | 44.55%               | 51,574,530                 |
| OpenClaw + OpenViking Plugin (-memory-core) | 52.08%               | 4,264,396                  |
| OpenClaw + OpenViking Plugin (+memory-core) | 51.23%               | 2,099,622                  |

- Experimental Conclusions:
  After integrating OpenViking:

* With native memory enabled: 43% improvement over original OpenClaw with 91% reduction in input token cost; 15% improvement over LanceDB with 96% reduction in input token cost.
* With native memory disabled: 49% improvement over original OpenClaw with 83% reduction in input token cost; 17% improvement over LanceDB with 92% reduction in input token cost.

👉 **[View: OpenClaw Context Plugin](examples/openclaw-plugin/README.md)**

👉 **[View: OpenCode Memory Plugin Example](examples/opencode-memory-plugin/README.md)**

👉 **[View: Claude Code Memory Plugin Example](examples/claude-code-memory-plugin/README.md)**

\--

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

***

## Advanced Reading

### Documentation

For more details, please visit our [Full Documentation](./docs/en/).

### Community & Team

For more details, please see: **[About Us](./docs/en/about/01-about-us.md)**

### Join the Community

OpenViking is still in its early stages, and there are many areas for improvement and exploration. We sincerely invite every developer passionate about AI Agent technology:

- Light up a precious **Star** for us to give us the motivation to move forward.
- Visit our **[Website](https://www.openviking.ai)** to understand the philosophy we convey, and use it in your projects via the **[Documentation](https://www.openviking.ai/docs)**. Feel the change it brings and give us feedback on your truest experience.
- Join our community to share your insights, help answer others' questions, and jointly create an open and mutually helpful technical atmosphere:
  - 📱 **Lark Group**: Scan the QR code to join → [View QR Code](./docs/en/about/01-about-us.md#lark-group)
  - 💬 **WeChat Group**: Scan the QR code to add assistant → [View QR Code](./docs/en/about/01-about-us.md#wechat-group)
  - 🎮 **Discord**: [Join Discord Server](https://discord.com/invite/eHvx8E9XF3)
  - 🐦 **X (Twitter)**：[Follow us](https://x.com/openvikingai)
- Become a **Contributor**, whether submitting a bug fix or contributing a new feature, every line of your code will be an important cornerstone of OpenViking's growth.

Let's work together to define and build the future of AI Agent context management. The journey has begun, looking forward to your participation!

### Star Trend

[![Star History Chart](https://api.star-history.com/svg?repos=volcengine/OpenViking\&type=timeline\&legend=top-left)](https://www.star-history.com/#volcengine/OpenViking\&type=timeline\&legend=top-left)

## License

The OpenViking project uses different licenses for different components:

- **Main Project**: AGPLv3 - see the [LICENSE](./LICENSE) file for details
- **crates/ov\_cli**: Apache 2.0 - see the [LICENSE](./crates/ov_cli/LICENSE) for details
- **examples**: Apache 2.0 - see the [LICENSE](./examples/LICENSE) for details
- **third\_party**: Respective original licenses of third-party projects

<!-- Link Definitions -->
