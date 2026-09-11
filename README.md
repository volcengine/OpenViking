<div align="center">

<a href="https://openviking.ai/" target="_blank">
  <picture>
    <img alt="OpenViking" src="docs/images/ov-logo.png" width="120" height="auto">
  </picture>
</a>

### OpenViking: The Context Database for AI Agents

English / [中文](README_CN.md) / [日本語](README_JA.md)

<a href="https://www.openviking.ai">Website</a> · <a href="https://openviking.ai/studio">Live Demo</a> · <a href="https://github.com/volcengine/OpenViking">GitHub</a> · <a href="https://github.com/volcengine/OpenViking/issues">Issues</a> · <a href="https://docs.openviking.ai/">Docs</a>

[![](https://img.shields.io/github/v/release/volcengine/OpenViking?color=369eff\&labelColor=black\&logo=github\&style=flat-square)](https://github.com/volcengine/OpenViking/releases)
[![](https://img.shields.io/github/stars/volcengine/OpenViking?labelColor\&style=flat-square\&color=ffcb47)](https://github.com/volcengine/OpenViking)
[![](https://img.shields.io/github/issues/volcengine/OpenViking?labelColor=black\&style=flat-square\&color=ff80eb)](https://github.com/volcengine/OpenViking/issues)
[![](https://img.shields.io/github/contributors/volcengine/OpenViking?color=c4f042\&labelColor=black\&style=flat-square)](https://github.com/volcengine/OpenViking/graphs/contributors)
[![](https://img.shields.io/badge/license-AGPLv3-white?labelColor=black\&style=flat-square)](https://github.com/volcengine/OpenViking/blob/main/LICENSE)
[![](https://img.shields.io/github/last-commit/volcengine/OpenViking?color=c4f042\&labelColor=black\&style=flat-square)](https://github.com/volcengine/OpenViking/commits/main)

</div>

***

## What is OpenViking

OpenViking is an open-source context database for AI agents. It gives agents a place to store knowledge, remember users, and reuse experience across sessions.

Context lives in a virtual filesystem under `viking://`. Agents can browse it with `ls` and `tree`, search within a project or memory directory, and read details as needed. Directory summaries help them select context before loading full files.

[Quick start](#quick-start) · [Agent integrations](#use-it-with-your-agent) · [SDKs & tools](#build-with-openviking) · [Deployment](#deploy-in-production)

[![OpenViking Studio: browse context and try semantic search](docs/images/studio-playground.png)](https://openviking.ai/studio)

[Try OpenViking Studio](https://openviking.ai/studio) in your browser, no installation required.

## Core concepts

### Resources, memories, and skills

| Context | What it holds | Example URI |
| --- | --- | --- |
| **Resources** | Documents, repositories, and web pages an agent can reference | `viking://resources/project/` |
| **Memories** | User preferences, facts, and experience learned from sessions | `viking://~/memories/` |
| **Skills** | Instructions and supporting files for tasks an agent can perform | `viking://~/skills/` |

`viking://~` resolves to the current user's home. Resources can be shared within an account; memories and sessions belong to individual users. See [context types](https://docs.openviking.ai/en/concepts/02-context-types) and [URI namespaces](https://docs.openviking.ai/en/concepts/04-viking-uri).

### Load context in layers

Semantically processed directories have summaries alongside their content:

```text
viking://resources/project/
├── .abstract.md     # L0: brief summary for relevance checks
├── .overview.md     # L1: overview and navigation
├── api.md           # L2: full content
└── examples/
```

L0 and L1 describe a directory, rather than duplicating every file. Agents use them to decide where to look and when to read L2. [Context layers](https://docs.openviking.ai/en/concepts/03-context-layers)

### Retrieve within the directory structure

Vector search finds candidate directories, then retrieval explores their contents. `find` runs a query directly; `search` can use session context to plan what to retrieve. Use a target URI to scope a query to a project or memory subtree. [Retrieval](https://docs.openviking.ai/en/concepts/07-retrieval)

Results include source URIs; optional [retrieval telemetry](https://docs.openviking.ai/en/api/06-retrieval) and [runtime observers](https://docs.openviking.ai/en/api/18-observer) help diagnose search and processing issues.

### Turn sessions into memory

A session records messages and context use. Committing it archives the conversation and starts background memory extraction: candidates are compared with existing memories, then created, merged, or skipped. Later sessions can retrieve that knowledge. Memory policies control which types are extracted, including user preferences and agent experience. [Sessions](https://docs.openviking.ai/en/concepts/08-session)

[Architecture](https://docs.openviking.ai/en/concepts/01-architecture) · [Design rationale](https://blog.openviking.ai/post/openviking-context-database/)

## Benchmarks

OpenViking 0.3.22 has been evaluated on long-conversation user memory (LoCoMo) and multi-turn agent tasks (tau2-bench). Full results and setup details, including knowledge-base QA, are in the [benchmark report](https://blog.openviking.ai/post/openviking-benchmark-results/); reproduction scripts live in [./benchmark](./benchmark).

The memory evaluation used [Doubao 2.0 Pro](https://console.volcengine.com/ark/region:cn-beijing/model/detail?Id=doubao-seed-2-0-pro) as the VLM and [Doubao-embedding-vision-251215](https://console.volcengine.com/ark/region:cn-beijing/model/detail?Id=doubao-embedding-vision) as the embedding model.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/benchmark-dark.svg">
  <img alt="Benchmark results. LoCoMo accuracy: OpenClaw 24.20% native vs 82.08% with OpenViking; Hermes 33.38% vs 82.86%; Claude Code 57.21% vs 80.32%. tau2-bench task success: Retail 70.94% vs 77.81%; Airline 54.38% vs 66.25%." src="docs/images/benchmark-light.svg">
</picture>

- **User memory (LoCoMo)**: with OpenViking, all three agent integrations land at 80–83% accuracy — up from 24–57% on their native memory — while input tokens drop by 34.3–91.0% and query latency by 58.45–66.10%.
- **Agent experience (tau2-bench)**: experience memory lifts task success by +6.87pp (retail) and +11.87pp (airline) over the same LLM without memory.

## Quick start

Requires Python 3.10+ and access to an embedding model and a VLM (cloud or local).

```bash
pip install openviking --upgrade
openviking-server init      # configure providers and models
openviking-server doctor    # check configuration and connectivity
openviking-server           # start the server
```

`init` writes `~/.openviking/ov.conf`. Supported options include Volcengine, OpenAI, Codex OAuth, Kimi, GLM, and local Ollama. See the [configuration guide](https://docs.openviking.ai/en/guides/01-configuration) for provider setup and the [quick start docs](https://docs.openviking.ai/en/getting-started/02-quickstart) for platform instructions.

The package includes the `ov` CLI. In another terminal, import a repository and search it:

```bash
ov status
ov add-resource https://github.com/volcengine/OpenViking
# Replace TASK_ID with the returned task_id; repeat until status is completed
ov task status TASK_ID
ov ls viking://resources/
ov tree viking://resources/volcengine -L 2
ov find "what is openviking"
ov grep "openviking" --uri viking://resources/volcengine/OpenViking/docs/en
```

`ov find` returns matching context with URIs you can inspect. For client configuration (`ov config`), standalone CLI installs, and index maintenance, see [CLI setup](https://docs.openviking.ai/en/getting-started/05-cli-setup).

## Use it with your agent

Connect your agent to OpenViking for cross-session memory. Choose a native integration for automatic recall and session capture, or use MCP to give your agent memory and context tools.

<table>
<tr>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/en/agent-integrations/02-claude-code"><img src="docs/images/agents/image/claude-code/logo.png" width="32" height="32" alt=""><br><strong>Claude Code</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/en/agent-integrations/04-codex"><img src="docs/images/integrations/codex.png" width="32" height="32" alt=""><br><strong>Codex</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/en/agent-integrations/12-cursor"><img src="docs/images/agents/image/cursor/logo.png" width="32" height="32" alt=""><br><strong>Cursor</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
</td>
</tr>
<tr>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/en/agent-integrations/13-trae"><img src="docs/images/agents/image/trae/logo.png" width="32" height="32" alt=""><br><strong>TRAE</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/en/agent-integrations/03-openclaw"><img src="docs/images/integrations/openclaw.jpg" width="24" height="24" alt=""><br><strong>OpenClaw</strong></a><br>
<sub>Context&nbsp;engine</sub>
</td>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/en/agent-integrations/05-hermes"><img src="docs/images/integrations/hermes-agent.png" width="24" height="24" alt=""><br><strong>Hermes</strong></a><br>
<sub>Built-in&nbsp;memory</sub>
</td>
</tr>
<tr>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/en/agent-integrations/10-opencode"><img src="docs/images/agents/image/opencode/logo.png" width="32" height="32" alt=""><br><strong>OpenCode</strong></a><br>
<sub>Plugin&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/en/agent-integrations/11-pi"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/images/integrations/pi-dark.svg"><img src="docs/images/integrations/pi.svg" width="41" height="41" alt=""></picture><br><strong>pi</strong></a><br>
<sub>Native&nbsp;extension</sub>
</td>
<td align="center" valign="bottom" width="33%">
<a href="docs/images/agents/en/deerflow-memory-manager.md"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/images/integrations/deerflow-dark.svg"><img src="docs/images/integrations/deerflow.svg" width="19" height="24" alt=""></picture><br><strong>DeerFlow</strong></a><br>
<sub>Memory&nbsp;+&nbsp;MCP</sub>
</td>
</tr>
</table>

**General integrations**

<table>
<tr>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/en/agent-integrations/15-agent-plugins"><img src="docs/images/integrations/agent-plugins.svg" width="24" height="23" alt=""><br><strong>Agent&nbsp;Plugins&nbsp;1.0</strong></a>
</td>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/en/agent-integrations/06-mcp-clients"><img src="docs/images/integrations/mcp.svg" width="24" height="24" alt=""><br><strong>MCP&nbsp;clients</strong></a>
</td>
<td align="center" valign="bottom" width="33%">
<a href="https://docs.openviking.ai/en/agent-integrations/07-langchain-langgraph"><img src="docs/images/integrations/langchain.svg" width="24" height="24" alt=""><br><strong>LangChain</strong></a>
</td>
</tr>
</table>

For setup instructions and integration details, see [Integrations](https://openviking.ai/integrations).

## Build with OpenViking

| Tool | Use it to |
| --- | --- |
| [Python](sdk/python/README.md), [Go](sdk/go/README.md), [TypeScript](sdk/typescript/README.md) SDKs · [HTTP API](https://docs.openviking.ai/en/api/01-overview) | Add context storage, retrieval, and sessions to your application |
| [Context compilation](https://docs.openviking.ai/en/context-compilation/01-overview) | Use `ov compile` with a skill to turn source material into a wiki, knowledge graph, or report; requires VikingBot |
| [Web Studio](web-studio/README.md) | Browse stored context, run semantic search, and work with agents in a web console |

## OpenViking Helper (Beta)

OpenViking Helper is a desktop console for macOS and Windows x64 (beta). It configures supported local agent integrations, inspects recall and capture events in sessions, and syncs local memories and skills to OpenViking.

Download:

- [macOS Apple Silicon (arm64)](https://lf3-cdn-tos.bytegoofy.com/obj/tron-demo/7654844610543360265/420238785/0.0.19/darwin-arm64/openviking-helper-0.0.19-arm64.dmg)
- [macOS Intel (x64)](https://lf3-cdn-tos.bytegoofy.com/obj/tron-demo/7654844610543360265/420238785/0.0.19/darwin-x64/openviking-helper-0.0.19-x64.dmg)
- [Windows (x64)](https://lf3-cdn-tos.bytegoofy.com/obj/tron-demo/7654844610543360265/420238785/0.0.19/win32-x64/openviking-helper-0.0.19-x64.exe)

## VikingBot

VikingBot is an AI agent framework built on top of OpenViking:

```bash
pip install "openviking[bot]"
openviking-server --with-bot
ov chat   # in another terminal
```

The official Docker image bundles VikingBot and starts it by default alongside the server and console UI. Details: [VikingBot guide](https://docs.openviking.ai/en/guides/17-vikingbot).

## Deploy in production

Run the open-source server in your own environment under [AGPLv3](LICENSE). It requires no activation key. Start with [server setup](https://docs.openviking.ai/en/getting-started/03-quickstart-server) or the [Docker and deployment guide](https://docs.openviking.ai/en/guides/03-deployment).

The server supports [accounts and user isolation](https://docs.openviking.ai/en/concepts/11-multi-tenant) and opt-in [resource ACLs](https://docs.openviking.ai/en/concepts/15-acl). Configure [authentication](https://docs.openviking.ai/en/guides/04-authentication) before exposing it beyond localhost.

## Commercial editions

### Managed SaaS

[Volcano Engine](https://www.volcengine.com/product/openviking-service) hosts and operates OpenViking. Personal and Enterprise plans cover individual and team use, with migration tooling for open-source deployments. See the [service documentation](https://docs.volcengine.com/docs/84313/2374478) for plans and limits. Hosting outside China is planned on [BytePlus](https://www.byteplus.com).

### Self-managed enterprise

Deploy in your own cloud account / VPC (BYOC) or an offline environment. This edition adds distributed deployment and official support, activated by a license key. [Contact the team](https://docs.google.com/forms/d/e/1FAIpQLScQqwsm7fvKdjtNiW5rWNXJjoHPtedVzLsKSMJgObtsj2_udA/viewform).

## Research

VikingMem studies event-driven memory extraction, updates, and consolidation. OpenViking implements a subset of these capabilities.

> **VikingMem: A Memory Base Management System for Stateful LLM-based Applications**<br>
> Jiajie Fu, Junwen Chen, Mengzhao Wang, Aoxiang He, Maojia Sheng, Xiangyu Ke, Yifan Zhu, and Yunjun Gao.<br>
> arXiv:2605.29640, 2026. Presented at VLDB 2026 in September.<br>
> 📄 [Read the paper on arXiv](https://arxiv.org/abs/2605.29640) · [Read PDF](https://arxiv.org/pdf/2605.29640)

Directory-aware retrieval uses document structure to scope searches. OpenViking integrates this paper’s TrieHI index to resolve directory scopes before vector ranking.

> **Directory-Aware Query and Maintenance in Vector Databases**<br>
> Mengzhao Wang, Zheng Gong, Jingpei Hu, Jiajie Fu, Maojia Sheng, Junwen Chen, and Yifan Zhu.<br>
> arXiv:2606.16903, 2026. Accepted by ICDE.<br>
> 📄 [Read the paper on arXiv](https://arxiv.org/abs/2606.16903) · [Read PDF](https://arxiv.org/pdf/2606.16903)

VikingRAG combines semantic search with document structure; its core mechanisms are integrated into OpenViking. The paper also explores retrieval-trace reuse and on-demand multi-round retrieval.

> **VikingRAG: Accurate and Token-efficient Retrieval-augmented Generation over Structured Documents**<br>
> Peiyuan Gao, Gaoyuan Zhang, Haojie Qin, Yahui Sun, Qianyi Zhang, Yunhao Zhang, Zeyu Wang, and Wei Lu.<br>
> arXiv:2609.11390, 2026. Submitted.<br>
> 📄 [Read the paper on arXiv](https://arxiv.org/abs/2609.11390) · [Read PDF](https://arxiv.org/pdf/2609.11390)

## Partner Projects

- [deer-flow](https://github.com/bytedance/deer-flow) - Open-source long-horizon SuperAgent harness
- [NoKV](https://github.com/NoKV-Lab/NoKV) - AI native distributed file system
- [loopx](https://github.com/huangruiteng/loopx) - Lightweight loop engineering state kernel
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) - The agent that grows with you

To propose a partnership, [open an issue](https://github.com/volcengine/OpenViking/issues).

## Community & contributing

- **Docs**: [docs.openviking.ai](https://docs.openviking.ai/) · [FAQ](https://docs.openviking.ai/en/faq/faq)
- **Blog**: [blog.openviking.ai](https://blog.openviking.ai/)
- **Team**: [About us](https://docs.openviking.ai/en/about/01-about-us)
- **Chat**: 📱 [Lark Group](https://docs.openviking.ai/en/about/01-about-us#lark-group) · 💬 [WeChat](https://docs.openviking.ai/en/about/01-about-us#wechat-group) · 🎮 [Discord](https://discord.com/invite/eHvx8E9XF3) · 🐦 [X](https://x.com/openvikingai)
- **Contribute**: bug fixes and new features are both welcome — see [CONTRIBUTING.md](CONTRIBUTING.md)

## Security and privacy

For vulnerability reporting and supported versions, see [SECURITY.md](SECURITY.md)

## License

The OpenViking project uses different licenses for different components:

- **Main Project**: AGPLv3 - see the [LICENSE](./LICENSE) file for details
- **crates/ov\_cli**: Apache 2.0 - see the [LICENSE](./crates/LICENSE) for details
- **examples**: Apache 2.0 - see the [LICENSE](./examples/LICENSE) for details
- **third\_party**: Respective original licenses of third-party projects
