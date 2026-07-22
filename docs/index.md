---
layout: home

hero:
  name: OpenViking Docs
  text: Context database documentation
  tagline: A focused reference for building agent memory, resources, skills, and retrieval flows with OpenViking.
  image:
    src: /ov-logo.png
    alt: OpenViking
  actions:
    - theme: brand
      text: Read English Docs
      link: /en/getting-started/01-introduction
    - theme: alt
      text: 查看中文文档
      link: /zh/getting-started/01-introduction

features:
  - title: One Filesystem for Context
    details: Memories, resources, and skills live under viking:// URIs with L0/L1/L2 tiered loading — agents browse context with ls, tree, and find.
    link: /en/concepts/02-context-types
  - title: Retrieval You Can Debug
    details: Directory-recursive retrieval locates the best directory first, then drills down; every query keeps its trajectory for inspection.
    link: /en/concepts/07-retrieval
  - title: Works With Your Agent
    details: Claude Code, Codex, Cursor, Trae, OpenCode, MCP clients, and LangChain integrations inject recall and auto-commit session memory.
    link: /en/agent-integrations/01-overview
  - title: Run It in Production
    details: Docker and server deployment, authentication, encryption, telemetry, and the full HTTP API reference.
    link: /en/guides/03-deployment
---
