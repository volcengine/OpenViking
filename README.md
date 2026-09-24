<div align="center">

<a href="https://openviking.ai/" target="_blank">
  <picture>
    <img alt="OpenViking" src="docs/images/ov-logo.png" width="200px" height="auto">
  </picture>
</a>

### OpenViking: The Context Database for AI Agents

English / [中文](README_CN.md) / [日本語](README_JA.md)

<a href="https://www.openviking.ai">Website</a> · <a href="https://openviking.ai/studio">Live Demo</a> · <a href="https://github.com/volcengine/OpenViking">GitHub</a> · <a href="https://github.com/volcengine/OpenViking/issues">Issues</a> · <a href="https://docs.openviking.ai/">Docs</a>

<p>
  <a href="https://github.com/volcengine/OpenViking/releases"><img src="https://img.shields.io/github/v/release/volcengine/OpenViking?color=369eff&labelColor=black&logo=github&style=flat-square" alt="release"></a>
  <a href="https://github.com/volcengine/OpenViking"><img src="https://img.shields.io/github/stars/volcengine/OpenViking?labelColor&style=flat-square&color=ffcb47" alt="stars"></a>
  <a href="https://github.com/volcengine/OpenViking/issues"><img src="https://img.shields.io/github/issues/volcengine/OpenViking?labelColor=black&style=flat-square&color=ff80eb" alt="issues"></a>
  <a href="https://github.com/volcengine/OpenViking/graphs/contributors"><img src="https://img.shields.io/github/contributors/volcengine/OpenViking?color=c4f042&labelColor=black&style=flat-square" alt="contributors"></a>
  <a href="https://github.com/volcengine/OpenViking/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-AGPLv3-white?labelColor=black&style=flat-square" alt="license"></a>
  <a href="https://github.com/volcengine/OpenViking/commits/main"><img src="https://img.shields.io/github/last-commit/volcengine/OpenViking?color=c4f042&labelColor=black&style=flat-square" alt="last commit"></a>
</p>

<p>
  <a href="https://railway.com/deploy/openviking"><img src="https://railway.com/button.svg" alt="Deploy on Railway" height="30"></a>
</p>

<p>
  <a href="https://docs.openviking.ai/en/about/01-about-us#lark-group"><img src="docs/images/community/lark.svg" width="18" height="18" alt="Lark">&nbsp;Lark</a> ·
  <a href="https://docs.openviking.ai/en/about/01-about-us#wechat-group"><img src="docs/images/community/wechat.svg" width="18" height="18" alt="WeChat">&nbsp;WeChat</a> ·
  <a href="https://discord.com/invite/eHvx8E9XF3"><img src="docs/images/community/discord.svg" width="18" height="18" alt="Discord">&nbsp;Discord</a> ·
  <a href="https://x.com/openvikingai"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/images/community/x-dark.svg"><img src="docs/images/community/x.svg" width="16" height="16" alt="X"></picture>&nbsp;X</a>
</p>

<a href="https://trendshift.io/repositories/19668" target="_blank"><img src="https://trendshift.io/api/badge/repositories/19668" alt="volcengine%2FOpenViking | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

</div>

***

## What is OpenViking

OpenViking is an open-source context database for AI agents — one filesystem for everything an agent knows: knowledge, memory, and skills.

Most agent memory is a black box: text goes in, embeddings come out, and nobody can see what was actually stored. OpenViking organizes context as a virtual filesystem under `viking://` instead. Agents navigate it like files — `ls`, `tree`, `read`, `write`, `grep` — and you can open any directory to inspect and edit what your agent knows. Every directory carries a generated summary, so agents can scan summaries first and decide what to read.

<a href="https://openviking.ai/studio" target="_blank" rel="noopener noreferrer">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/studio-playground-dark.png">
    <img src="docs/images/studio-playground.png" alt="OpenViking Studio: browse context and try semantic search">
  </picture>
</a>

[Try OpenViking Studio](https://openviking.ai/studio) in your browser, no installation required. [Self-host Web Studio](web-studio/README.md).

## Why OpenViking

- **One filesystem for knowledge, memory, and skills.** Resources hold documents and code; memories retain user preferences and experience; skills define how to perform tasks — not just extracted facts, but the full context, each with a `viking://` URI for browsing and retrieval. → [Viking URI](https://docs.openviking.ai/en/concepts/04-viking-uri) · [Context types](https://docs.openviking.ai/en/concepts/02-context-types)
- **Search a directory, not the whole index.** Scope semantic search to a project or memory subtree instead of scanning a flat vector pool. `find` runs a query directly; `search` plans retrieval from session context. → [Retrieval](https://docs.openviking.ai/en/concepts/07-retrieval)
- **Read the summary before the source.** Generated directory abstracts (L0) and overviews (L1) let agents judge relevance before opening full content (L2). → [Context layers](https://docs.openviking.ai/en/concepts/03-context-layers)
- **Sessions become files you can read.** Committing a session archives the conversation and extracts memories as Markdown you can inspect, edit, and merge. With VikingBot enabled, `ov compile` organizes source material into a wiki, knowledge graph, or report. → [Sessions](https://docs.openviking.ai/en/concepts/08-session) · [Context compilation](https://docs.openviking.ai/en/context-compilation/01-overview)

[Architecture](https://docs.openviking.ai/en/concepts/01-architecture) · [Design rationale](https://blog.openviking.ai/post/openviking-context-database/)

```
viking://
├── resources/              # Resources: project docs, repos, web pages, etc.
│   └── my_project/
│       ├── docs/
│       │   ├── api/
│       │   └── tutorials/
│       └── src/
└── user/
    └── {user_id}/
        ├── memories/
        │   └── preferences/
        │       ├── writing_style
        │       └── coding_habits
        ├── resources/
        │   └── private_project/
        ├── skills/
        │   ├── search_code
        │   └── analyze_data
        └── peers/
            └── web-visitor-alice/
```

The three loading tiers:

- **L0 (Abstract)**: a one-sentence summary for quick relevance checks.
- **L1 (Overview)**: core information and usage scenarios for planning.
- **L2 (Details)**: the full original data, read only when needed.

Semantically processed directories carry L0/L1 summaries, so agents can judge relevance before reading full files:

```
viking://resources/my_project/
├── .abstract.md           # L0: quick relevance check
├── .overview.md           # L1: structure and key points
└── docs/
    ├── .abstract.md
    ├── .overview.md
    └── api/
        ├── auth.md         # L2: full content, loaded on demand
        └── endpoints.md
```

## Proof it works

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

Build your own integration with the [Python](sdk/python/README.md), [Go](sdk/go/README.md), or [TypeScript](sdk/typescript/README.md) SDK, or the [HTTP API](https://docs.openviking.ai/en/api/01-overview).

## Use it with your agent

Connect your agent to OpenViking for cross-session memory. Choose a native integration for automatic recall and session capture, or use MCP to give your agent memory and context tools.

<table>
<tbody>
<tr>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/02-claude-code"><img src="docs/images/integrations/logos/claude-code.png" width="32" height="32" alt=""><br><strong>Claude</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/04-codex"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/images/integrations/logos/openai-dark.svg"><img src="docs/images/integrations/logos/openai.svg" width="32" height="32" alt=""></picture><br><strong>Codex</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/12-cursor"><img src="docs/images/integrations/logos/cursor.png" width="32" height="32" alt=""><br><strong>Cursor</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/13-trae"><img src="docs/images/integrations/logos/trae.png" width="32" height="32" alt=""><br><strong>TRAE</strong></a><br>
<sub>Hooks&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/03-openclaw"><img src="docs/images/integrations/logos/openclaw.png" width="32" height="32" alt=""><br><strong>OpenClaw</strong></a><br>
<sub>Context&nbsp;engine</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/05-hermes"><img src="docs/images/integrations/logos/hermes-agent.png" width="32" height="32" alt=""><br><strong>Hermes</strong></a><br>
<sub>Built-in</sub>
</td>
</tr>
</tbody>
<tbody>
<tr>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/10-opencode"><img src="docs/images/integrations/logos/opencode.png" width="32" height="32" alt=""><br><strong>OpenCode</strong></a><br>
<sub>Plugin&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/11-pi"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/images/integrations/logos/pi-dark.svg"><img src="docs/images/integrations/logos/pi.svg" width="32" height="32" alt=""></picture><br><strong>pi</strong></a><br>
<sub>Native&nbsp;extension</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="docs/images/agents/en/deerflow-memory-manager.md"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/images/integrations/logos/deerflow-dark.svg"><img src="docs/images/integrations/logos/deerflow.svg" width="32" height="32" alt=""></picture><br><strong>DeerFlow</strong></a><br>
<sub>Plugin&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/17-dsh"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/images/integrations/logos/dsh-dark.svg"><img src="docs/images/integrations/logos/dsh.svg" width="32" height="32" alt=""></picture><br><strong>DSH</strong></a><br>
<sub>Plugin&nbsp;+&nbsp;MCP</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="docs/images/agents/en/doubao-work.md"><img src="docs/images/integrations/logos/doubao-work.png" width="32" height="32" alt=""><br><strong>Doubao&nbsp;Work</strong></a><br>
<sub>Connector</sub>
</td>
<td align="center" valign="bottom" width="16%">
<a href="https://docs.openviking.ai/en/agent-integrations/07-langchain-langgraph"><img src="docs/images/integrations/logos/langchain.svg" width="32" height="32" alt=""><br><strong>LangChain</strong></a><br>
<sub>Tools&nbsp;+&nbsp;store</sub>
</td>
</tr>
</tbody>
</table>

**General integrations**

<table>
<tr>
<td align="center" valign="bottom" width="50%">
<a href="https://docs.openviking.ai/en/agent-integrations/15-agent-plugins"><img src="docs/images/integrations/logos/agent-plugins.svg" width="32" height="32" alt=""><br><strong>Agent&nbsp;Plugins&nbsp;1.0</strong></a>
</td>
<td align="center" valign="bottom" width="50%">
<a href="https://docs.openviking.ai/en/agent-integrations/06-mcp-clients"><img src="docs/images/integrations/logos/mcp.svg" width="32" height="32" alt=""><br><strong>MCP&nbsp;clients</strong></a>
</td>
</tr>
</table>

For setup instructions and integration details, see [Integrations](https://openviking.ai/integrations).

## Desktop App (Beta)

The desktop app is a console for macOS and Windows x64 (beta). It configures supported local agent integrations, inspects recall and capture events in sessions, and syncs local memories and skills to OpenViking.

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

<table>
<tr>
<td width="50%" valign="top">

<img src="docs/images/commercial-saas.png" alt="Managed SaaS" width="100%" />

<h3>☁️ Managed SaaS</h3>
<p><a href="https://www.volcengine.com/product/openviking-service">Volcano Engine</a> hosts and operates OpenViking. Personal and Enterprise plans cover individual and team use, with migration tooling for open-source deployments. See the <a href="https://docs.volcengine.com/docs/84313/2374478">service documentation</a> for plans and limits. Hosting outside China is planned on <a href="https://www.byteplus.com">BytePlus</a>.</p>

</td>
<td width="50%" valign="top">

<img src="docs/images/commercial-self-hosted.png" alt="Self-Managed" width="100%" />

<h3>🏢 Self-Managed</h3>
<p>Deploy in your own cloud account / VPC (BYOC) or an offline environment. This edition adds distributed deployment and official support, activated by a license key. <a href="https://docs.google.com/forms/d/e/1FAIpQLScQqwsm7fvKdjtNiW5rWNXJjoHPtedVzLsKSMJgObtsj2_udA/viewform">Contact the team</a>.</p>

</td>
</tr>
</table>

## Research

**Memory that evolves with your agent.** VikingMem develops an event-driven approach to extracting, updating, and consolidating long-term memory, giving stateful agents a way to retain useful experience as interactions accumulate. OpenViking open-sources a subset of these core capabilities.

> **VikingMem: A Memory Base Management System for Stateful LLM-based Applications**<br>
> Jiajie Fu, Junwen Chen, Mengzhao Wang, Aoxiang He, Maojia Sheng, Xiangyu Ke, Yifan Zhu, and Yunjun Gao.<br>
> arXiv:2605.29640, 2026. Presented at VLDB 2026 in September.<br>
> 📄 [Read the paper on arXiv](https://arxiv.org/abs/2605.29640) · [Read PDF](https://arxiv.org/pdf/2605.29640)

**Directory structure as retrieval context.** This paper provides the formal foundations, index design, and experimental evidence behind OpenViking’s directory-aware retrieval. It defines directory-scoped query and maintenance operations and introduces TrieHI, which OpenViking integrates to resolve directory scopes before vector ranking. This connects the filesystem paradigm to retrieval: agents can search a project or memory subtree, retain its surrounding context, and reorganize it as knowledge evolves.

> **Directory-Aware Query and Maintenance in Vector Databases**<br>
> Mengzhao Wang, Zheng Gong, Jingpei Hu, Jiajie Fu, Maojia Sheng, Junwen Chen, and Yifan Zhu.<br>
> arXiv:2606.16903, 2026. Accepted by ICDE.<br>
> 📄 [Read the paper on arXiv](https://arxiv.org/abs/2606.16903) · [Read PDF](https://arxiv.org/pdf/2606.16903)

**Retrieve the evidence you need with fewer tokens.** VikingRAG combines semantic search with document structure, exposing relevant directory segments as evidence gaps arise. Its core mechanisms are integrated into OpenViking. The paper further explores reusing retrieval traces and escalating to multi-round retrieval only when needed, reducing repeated exploration while preserving answer quality.

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

## Community & Contributing

- **Docs**: [docs.openviking.ai](https://docs.openviking.ai/) · [FAQ](https://docs.openviking.ai/en/faq/faq)
- **Blog**: [blog.openviking.ai](https://blog.openviking.ai/)
- **Team**: [About us](https://docs.openviking.ai/en/about/01-about-us)
- **Chat**: <a href="https://docs.openviking.ai/en/about/01-about-us#lark-group"><img src="docs/images/community/lark.svg" width="18" height="18" alt="Lark">&nbsp;Lark</a> · <a href="https://docs.openviking.ai/en/about/01-about-us#wechat-group"><img src="docs/images/community/wechat.svg" width="18" height="18" alt="WeChat">&nbsp;WeChat</a> · <a href="https://discord.com/invite/eHvx8E9XF3"><img src="docs/images/community/discord.svg" width="18" height="18" alt="Discord">&nbsp;Discord</a> · <a href="https://x.com/openvikingai"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/images/community/x-dark.svg"><img src="docs/images/community/x.svg" width="16" height="16" alt="X"></picture>&nbsp;X</a>
- **Contribute**: bug fixes and new features are both welcome — see [CONTRIBUTING.md](CONTRIBUTING.md)

<a href="https://github.com/volcengine/OpenViking/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=volcengine/OpenViking&amp;columns=15&amp;max=120" alt="OpenViking contributors" />
</a>

## Security and privacy

For vulnerability reporting and supported versions, see [SECURITY.md](SECURITY.md)

## License

The OpenViking project uses different licenses for different components:

- **Main Project**: AGPLv3 - see the [LICENSE](./LICENSE) file for details
- **crates/ov\_cli**: Apache 2.0 - see the [LICENSE](./crates/LICENSE) for details
- **examples**: Apache 2.0 - see the [LICENSE](./examples/LICENSE) for details. The Hermes plugin in `examples/hermes-plugin` retains its [MIT license](./examples/hermes-plugin/LICENSE).
- **third\_party**: Respective original licenses of third-party projects


### Optional retrieval triggers on existing memories

`memory.triggers` adds retrieval-only views to existing Event, Entity and Preference
files. It does not create another topic/scene/item hierarchy or replace native OV
retrieval. This is an OV adaptation of the write-time cue idea in
[T-Mem](https://github.com/Sherlockwz/T-Mem), not a reproduction of its full pipeline.

```json
{
  "memory": {
    "triggers": {"enabled": true, "recall_enabled": true}
  }
}
```

The feature is off by default and does not require a rerank provider.
After a normal memory write, a separate model call generates source-anchored cues;
the original extraction schema, visible evidence and primary embedding are unchanged.
Concept/bridge cues apply to Events, Entities and Preferences; Event files may also
have descriptive scene and hypothetical horizon cues. Generation validates an exact
source quote per cue, which establishes provenance but does not prove an association
is useful or factually implied. Hypothetical cues are never answer evidence.

Accepted cues are cached in the file's hidden `MEMORY_FIELDS.retrieval_triggers`
metadata and individually embedded in `<collection>_memory_triggers`, pointing to
the original URI. A body hash invalidates stale cues. No new memory files or types
are created. Existing unmodified files are not automatically backfilled.

Both `find` and `search` retain their ordinary memory candidates and add trigger
candidates. The union is deduplicated by canonical URI and ordered by reciprocal
rank fusion, `sum(1 / (60 + rank))`, with the caller's `limit` applied afterwards.
Fused scores and explicit caller score thresholds use RRF rank scores, not cosine
similarity. The default fused threshold is zero; a configured similarity/rerank
threshold does not filter RRF scores. There are no fixed
Scene/Item quotas. Trigger fusion makes no rerank call, including for QUICK `find`.
Trigger text is never returned as evidence; current visible bodies are read instead. Different dated
files are not merged merely because their summaries are similar.

The auxiliary collection uses the same account, user, directory, tag and ACL scope;
current file permissions and body hashes are rechecked at retrieval. Deletes and
moves maintain the auxiliary records. A generation failure is logged and retains
the normal memory. Commit and embedding queue workers share a cross-event-loop
asynchronous limiter. Set `recall_enabled` to false to retain cached/indexed
cues while using ordinary retrieval. Generation adds model/embedding cost at write
time; recall adds an auxiliary vector lookup, evidence reads and local rank fusion.

### Optional query-only rewriting before search

`search` can call an explicitly configured `query_planner` even without a session.
Set `retrieval.query_rewrite_only` to use only its rewritten query text, ignoring
the planner's context type, intent and priority. Caller-supplied user, target URI,
metadata filters, level and access controls still constrain every retrieval.
This avoids a model-selected `resource` type excluding results from a search
that the caller already restricted to memories.

For the repository-supported local Ollama planner, pull
`guoxuter/ov_intent_analysis_sft:v7_q8` separately and configure:

```json
{
  "query_planner": {
    "provider": "litellm",
    "model": "ollama/guoxuter/ov_intent_analysis_sft:v7_q8",
    "api_base": "http://127.0.0.1:11434",
    "api_key": "no-key",
    "temperature": 0,
    "extra_request_body": {"think": false}
  },
  "retrieval": {
    "enable_intent": true,
    "query_rewrite_only": true
  }
}
```

Call the SDK's `search`, not `find`, to invoke the planner:

```python
result = await client.search(
    query="When did Caroline go to the LGBTQ support group?",
    target_uri="viking://~/memories",
    limit=100,
    options={"context_type": ["memory"], "level": 2},
)
```

Query-only rewriting is off by default and can be enabled independently of
`memory.triggers`. When both are enabled, each rewritten query reaches ordinary
retrieval and Trigger recall. Without an explicit planner or session context,
search keeps using the original query; `enable_intent: false` disables planning
entirely. `find` does not call the planner. Effective query plans serialize the
unused `context_type` as `null`.

Rewriting adds model latency and can change or lose query meaning; it does not
guarantee better recall or accuracy. Multiple rewrites keep search's existing
per-query limits and ordered result concatenation, without global fusion across
queries. Truncating that combined list can discard later query groups.
