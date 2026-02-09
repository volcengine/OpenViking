# Introduction

**OpenViking** is an open-source context database designed specifically for AI Agents. OpenViking unifies the management of context (memory, resources, and skills) that Agents need through a **file system paradigm**, enabling **hierarchical context delivery** and **self-iteration**. The ultimate goal is to lower the barrier for Agent development, allowing developers to focus on business innovation rather than underlying context management.

## Why OpenViking

In the AI era, data is abundant, but high-quality context is scarce. When building AI Agents, developers often face these challenges:

- **Context Fragmentation**: Memory in code, resources in vector databases, skills scattered everywhere — difficult to manage uniformly
- **Context Explosion**: Long-running Agent tasks generate context with each execution; simple truncation or compression leads to information loss
- **Poor Retrieval Quality**: Traditional RAG uses flat storage, lacking global perspective and struggling to understand complete context
- **Context Opacity**: Traditional RAG's implicit retrieval pipeline is like a black box, making debugging difficult
- **Limited Memory Iteration**: Current memory systems only record user memories, lacking Agent-related task memories

OpenViking is designed to solve these pain points.

## Core Features

### 1. File System Management Paradigm

Moving away from traditional flat database thinking, all context is organized as a virtual file system. Agents no longer rely solely on vector search to find data — they can locate and browse data through deterministic paths and standard file system commands.

**Unified URI Identification**: Each context is assigned a unique `viking://` URI, enabling precise location and access to resources stored in different locations.

```
viking://
├── resources/              # Resources: project docs, code repos, web pages
│   └── my_project/
├── user/                   # User: preferences, habits
│   └── memories/
└── agent/                  # Agent: skills, instructions, task memories
    ├── skills/
    └── memories/
```

**Three Context Types**:

| Type | Purpose | Lifecycle |
|------|---------|-----------|
| **Resource** | Knowledge and rules (docs, code, FAQ) | Long-term, relatively static |
| **Memory** | Agent's cognition (user preferences, learned experiences) | Long-term, dynamically updated |
| **Skill** | Callable capabilities (tools, MCP) | Long-term, static |

**Unix-like API**: Familiar command-style operations

```python
client.find("user authentication")       # Semantic search
client.ls("viking://resources/")         # List directory
client.read("viking://resources/doc")    # Read content
client.abstract("viking://...")          # Get L0 abstract
client.overview("viking://...")          # Get L1 overview
```

### 2. Hierarchical Context On-Demand Loading

Stuffing massive context into prompts all at once is not only expensive but also risks exceeding model windows and introducing noise. OpenViking automatically processes context into three levels upon ingestion:

| Level | Name | Token Limit | Purpose |
|-------|------|-------------|---------|
| **L0** | Abstract | ~100 tokens | Vector search, quick filtering |
| **L1** | Overview | ~2k tokens | Rerank, content navigation |
| **L2** | Detail | Unlimited | Full content, on-demand loading |

```
viking://resources/my_project/
├── .abstract.md               # L0 layer: abstract
├── .overview.md               # L1 layer: overview
├── docs/
│   ├── .abstract.md          # Each directory has L0/L1 layers
│   ├── .overview.md
│   └── api.md                # L2 layer: full content
└── src/
```

### 3. Directory Recursive Retrieval

Single vector retrieval struggles with complex query intents. OpenViking implements an innovative **directory recursive retrieval strategy**:

1. **Intent Analysis**: Generate multiple retrieval conditions through intent analysis
2. **Initial Positioning**: Use vector retrieval to quickly locate high-scoring directories
3. **Fine Exploration**: Perform secondary retrieval within directories, updating candidate sets with high-scoring results
4. **Recursive Descent**: If subdirectories exist, recursively repeat the secondary retrieval
5. **Result Aggregation**: Return the most relevant context

This "lock onto high-scoring directories first, then explore content in detail" strategy not only finds semantically matching fragments but also understands the complete context of information.

### 4. Visualized Retrieval Traces

OpenViking's organization uses a hierarchical virtual file system structure, with all context integrated in a unified format and each entry corresponding to a unique URI, breaking away from traditional flat black-box management.

The retrieval process uses directory recursive strategy, with complete traces of directory browsing and file positioning preserved for each retrieval, enabling clear observation of problem sources and guiding retrieval logic optimization.

### 5. Automatic Session Management

OpenViking has built-in memory self-iteration loops. At the end of each session, developers can trigger memory extraction, and the system asynchronously analyzes task execution results and user feedback, automatically updating User and Agent memory directories.

**6 Memory Categories**:

| Category | Owner | Description |
|----------|-------|-------------|
| **profile** | user | User basic information |
| **preferences** | user | User preferences by topic |
| **entities** | user | Entity memories (people, projects) |
| **events** | user | Event records (decisions, milestones) |
| **cases** | agent | Learned cases |
| **patterns** | agent | Learned patterns |

Enabling Agents to become "smarter with use" through world interaction, achieving self-evolution.

## Next Steps

- [Quick Start](./02-quickstart.md) - Get started in 5 minutes
- [Architecture Overview](../concepts/01-architecture.md) - Understand system design
- [Context Types](../concepts/02-context-types.md) - Deep dive into three context types
- [Retrieval Mechanism](../concepts/07-retrieval.md) - Learn about retrieval flow
