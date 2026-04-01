---
name: wechat-archive
description: Reindex, search, summarize, and analyze the WeChat chat archive under /home/nx/chat_archive. Use when the user asks to summarize one day, analyze a specific 公众号/微信群/聊天对象, search topics like 自动驾驶/FSD/机器人, compare dates, build a topic memory card, generate watchlist alerts, or refresh the OpenViking archive index.
---

# WeChat Archive

Use this skill for the unified WeChat archive workflow backed by OpenViking.

## Scope

- Source archive: `/home/nx/chat_archive`
- Exported Markdown corpus: `/home/nx/chat_archive/.openviking_export`
- OpenViking target URI: `viking://resources/wechat_archive`
- Embedded workspace: `/home/nx/.openviking-wechat-archive-local-gpu`
- HTTP search endpoint: `http://127.0.0.1:1934`
- Local embedding endpoint: `http://127.0.0.1:8766/v1`
- Local rerank endpoint: `http://127.0.0.1:8765/v1/rerank`
- Main entrypoint: [examples/wechat_archive_agent.py](/home/nx/github/OpenViking/examples/wechat_archive_agent.py)
- Wrapper: [run_wechat_archive_agent.sh](/home/nx/github/OpenViking/bot/workspace/skills/wechat-archive/scripts/run_wechat_archive_agent.sh)
- File locator helper: [archive_locator.py](/home/nx/github/OpenViking/bot/workspace/skills/wechat-archive/scripts/archive_locator.py)

## Trigger Phrases

Use this skill immediately when the user asks any of:

- “总结 2026-03-30 的微信内容”
- “分析 动点科技/人民日报/某个公众号 最近发了什么”
- “检索聊天归档里关于自动驾驶/机器人/SOTIF 的内容”
- “比较 3 月 30 号和 3 月 31 号的新增信息”
- “刷新/重建 微信聊天索引”
- “做一个 topic memory card / watchlist alert”

## Core Workflows

### 1. Refresh index

Default to incremental indexing into the same target URI.

```bash
bot/workspace/skills/wechat-archive/scripts/run_wechat_archive_agent.sh index
```

Block until queue completion when the user explicitly asks to wait:

```bash
bot/workspace/skills/wechat-archive/scripts/run_wechat_archive_agent.sh index \
  --wait --timeout 7200 \
  --embedding-text-source content_only \
  --semantic-concurrency 2 \
  --embedding-concurrency 4 \
  --semantic-llm-timeout 180
```

If embedding rules changed and the user wants a full rebuild, remove the target first and then re-run `index`.

### 2. Semantic topic search

Use semantic search first.

```bash
bot/workspace/skills/wechat-archive/scripts/run_wechat_archive_agent.sh search "自动驾驶" --limit 5
```

The preferred path is the local HTTP server on `127.0.0.1:1934`. If the server is unavailable, `wechat_archive_agent.py` can fall back to embedded mode for non-read-only commands.

If semantic search is blocked or the user only wants quick file hits, use the locator helper:

```bash
python3 bot/workspace/skills/wechat-archive/scripts/archive_locator.py topic-grep "自动驾驶"
```

### 3. Daily summary

```bash
bot/workspace/skills/wechat-archive/scripts/run_wechat_archive_agent.sh daily-summary "2026-03-30"
```

### 4. Single chat / official account analysis

```bash
bot/workspace/skills/wechat-archive/scripts/run_wechat_archive_agent.sh chat-summary "动点科技" --date "2026-03-30"
```

### 5. Topic analysis and comparisons

```bash
bot/workspace/skills/wechat-archive/scripts/run_wechat_archive_agent.sh topic-report "FSD 特斯拉"
bot/workspace/skills/wechat-archive/scripts/run_wechat_archive_agent.sh hotspots --start-date "2026-03-30" --end-date "2026-03-31"
bot/workspace/skills/wechat-archive/scripts/run_wechat_archive_agent.sh compare-days "2026-03-30" "2026-03-31"
bot/workspace/skills/wechat-archive/scripts/run_wechat_archive_agent.sh timeline-report "自动驾驶" --start-date "2026-03-30" --end-date "2026-03-31"
bot/workspace/skills/wechat-archive/scripts/run_wechat_archive_agent.sh sender-report "新技术讨论" --date "2026-03-31"
bot/workspace/skills/wechat-archive/scripts/run_wechat_archive_agent.sh top-articles --date "2026-03-31" --limit 5
```

### 6. Durable outputs

```bash
bot/workspace/skills/wechat-archive/scripts/run_wechat_archive_agent.sh topic-memory-card "自动驾驶"
bot/workspace/skills/wechat-archive/scripts/run_wechat_archive_agent.sh watchlist-alerts
```

## File Discovery Rules

- Prefer the exported Markdown corpus for summaries and analysis.
- Use linked `document.md` files when a message points to a copied article and the article body is useful.
- Prefer `chat.md` for overview and `days/YYYY-MM-DD.md` for evidence.
- Use topic grep before reading large numbers of files.

## Operations

- If `/home/nx/chat_archive/.openviking_export` does not exist or is clearly stale, run `index` first.
- For “today/yesterday” style requests, resolve the absolute date in the response.
- For specific chat analysis, include the exact matched chat name.
- When multiple chats match a keyword, present the candidate list and state which one you analyzed.
- When search cold-start latency matters, keep `openviking-wechat-archive-server.service` enabled.
- Daily auto-refresh and HTTP service assets are bundled under `systemd/user/` in this skill.

## Bundled Assets

- [run_wechat_archive_agent.sh](/home/nx/github/OpenViking/bot/workspace/skills/wechat-archive/scripts/run_wechat_archive_agent.sh): wrapper for `examples/wechat_archive_agent.py`
- [run_wechat_archive_daily_index.sh](/home/nx/github/OpenViking/bot/workspace/skills/wechat-archive/scripts/run_wechat_archive_daily_index.sh): daily incremental index wrapper
- [run_wechat_archive_http_service.sh](/home/nx/github/OpenViking/bot/workspace/skills/wechat-archive/scripts/run_wechat_archive_http_service.sh): `1934` HTTP service wrapper
- [openviking-wechat-archive-index.service](/home/nx/github/OpenViking/bot/workspace/skills/wechat-archive/systemd/user/openviking-wechat-archive-index.service): user service for daily index refresh
- [openviking-wechat-archive-index.timer](/home/nx/github/OpenViking/bot/workspace/skills/wechat-archive/systemd/user/openviking-wechat-archive-index.timer): user timer for daily index refresh
- [openviking-wechat-archive-server.service](/home/nx/github/OpenViking/bot/workspace/skills/wechat-archive/systemd/user/openviking-wechat-archive-server.service): user service for the local HTTP search server
