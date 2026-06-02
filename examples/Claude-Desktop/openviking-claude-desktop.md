# OpenViking + Claude Desktop Integration

This example demonstrates how to integrate Claude Desktop and Claude Code with OpenViking to enable persistent memory, automatic recall, automatic memory extraction, and Windows‑native automation.

This file is located under `examples/Claude-Desktop/` to match the folder structure used for Claude Desktop–related examples.

Repository: https://github.com/itzsamehfawzi/openviking-claude-desktop

---

## Overview

This integration provides a Windows‑native bridge between Claude Desktop / Claude Code and OpenViking’s memory engine. It enables:

- Persistent memory across sessions  
- Automatic memory recall before every Claude Code prompt  
- Automatic memory extraction and commit after each session  
- A self‑healing watchdog that restarts the integration if it crashes  
- Windows Task Scheduler automation for autosave and health checks  

Built on **OpenViking v0.3.16** with full Apache 2.0 attribution.

---

## Features

### MCP Bridge
- Exposes **12 ov_*** tools to Claude Desktop via MCP.
- Allows Claude Code to read and write memory through OpenViking.

### Auto‑Recall
- Before every Claude Code prompt, the integration performs a memory search.
- Injects relevant memories into the prompt context automatically.

### Auto‑Capture
- At the end of each Claude Code session:
  - Extracts memories  
  - Commits them to OpenViking  
  - Saves session summaries  

### Self‑Healing Watchdog
- Ensures the integration never permanently exits.
- Automatically restarts after crashes.
- Restart counter resets after 10 minutes of uptime.

### Windows Task Scheduler Automation
- Autosave every 30 minutes.
- Health alerts every 15 minutes.
- Fully Windows‑native (no cron or external schedulers).

### Embedding Selector
- Ollama‑first embedding generation.
- Jina Cloud fallback when Ollama is unavailable.

### Verification Script
Includes a **26‑point verification script** that confirms:

- MCP tools are registered  
- Memory operations work  
- Auto‑recall and auto‑capture are active  
- Watchdog is functioning  
- Scheduler tasks are installed  

---

## Requirements

- Windows 11  
- Python 3.13  
- Node.js 20  
- OpenViking v0.3.16  
- Claude Desktop (latest)  
- Claude Code extension enabled  

---

## Testing

This integration was tested on:

- Windows 11  
- Python 3.13  
- Node.js 20  
- OpenViking v0.3.16  

All **26 verification checks** passed successfully.

