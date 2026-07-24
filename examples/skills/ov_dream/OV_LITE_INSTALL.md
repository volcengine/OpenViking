# OV Lite Install

This guide installs OV Lite for OpenClaw through the `ov_dream` skill. It syncs OpenClaw chat sessions to OpenViking serverless without installing the OpenViking `contextEngine` plugin or consuming a plugin slot.

## Prerequisites

Set these values before running sync or recall:

- `OPENVIKING_API_KEY`: OpenViking serverless API key

Do not print API keys in logs, shell history snippets, or replies.

## Install Or Update

Choose the OpenViking source ref explicitly. Use `main` after this guide has been merged, or replace `SOURCE_BASE` with another trusted raw source when testing an unmerged change.

```bash
SOURCE_BASE=https://raw.githubusercontent.com/volcengine/OpenViking/main

mkdir -p ~/.openclaw/skills/ov_dream/scripts

curl -fsSL "$SOURCE_BASE/examples/skills/ov_dream/SKILL.md" \
  -o ~/.openclaw/skills/ov_dream/SKILL.md
curl -fsSL "$SOURCE_BASE/examples/skills/ov_dream/scripts/dream.py" \
  -o ~/.openclaw/skills/ov_dream/scripts/dream.py

touch ~/.openclaw/skills/ov_dream/__init__.py
touch ~/.openclaw/skills/ov_dream/scripts/__init__.py
```

If any download fails, stop and verify `SOURCE_BASE`.

## Verify Files

```bash
grep -q 'SERVERLESS_BASE_URL' ~/.openclaw/skills/ov_dream/scripts/dream.py
grep -q 'OPENVIKING_AUTH_MODE' ~/.openclaw/skills/ov_dream/scripts/dream.py
grep -q 'viking://user/default' ~/.openclaw/skills/ov_dream/scripts/dream.py
grep -q 'is_chat_session_key' ~/.openclaw/skills/ov_dream/scripts/dream.py
grep -q 'raw jsonl fallback can accidentally sync cron/subagent transcripts' ~/.openclaw/skills/ov_dream/scripts/dream.py
grep -q 'client.add_session_message(session.session_id' ~/.openclaw/skills/ov_dream/scripts/dream.py
```

If any check fails, the downloaded `dream.py` is not the expected OV Lite version.

## Configure Serverless Auth

Create `~/.openclaw/ov_dream.env` if it does not exist. If it already exists, keep the real `OPENVIKING_API_KEY` value and only add missing non-secret defaults.

```bash
cat > ~/.openclaw/ov_dream.env <<'EOF'
OPENVIKING_BASE_URL=https://api.vikingdb.cn-beijing.volces.com/openviking
OPENVIKING_API_KEY=<replace with OpenViking serverless API key>
OPENVIKING_AUTH_MODE=serverless
EOF
chmod 600 ~/.openclaw/ov_dream.env
```

## Force Recall Routing

Create an idempotent guard script that keeps the workspace instruction in `~/.openclaw/workspace/AGENTS.md`. The script appends the rule only when the recall command is missing, so it is safe to run during every cron sync:

````bash
cat > ~/.openclaw/skills/ov_dream/ensure_agents.sh <<'EOF'
#!/bin/bash
set -euo pipefail

AGENTS_MD="${OPENCLAW_AGENTS_MD:-$HOME/.openclaw/workspace/AGENTS.md}"
mkdir -p "$(dirname "$AGENTS_MD")"
touch "$AGENTS_MD"

if ! grep -Fq 'cd ~/.openclaw/skills/ov_dream && ./run.sh recall "<query>"' "$AGENTS_MD"; then
  cat >> "$AGENTS_MD" <<'AGENTS_EOF'

## OV Dream Recall

When the user's message starts with `ov recall ` after ignoring leading whitespace, treat everything after `ov recall` as the recall query, remove leading and trailing whitespace from that query, and run:

```bash
cd ~/.openclaw/skills/ov_dream && ./run.sh recall "<query>"
```

Do not answer from general reasoning, do not explain what will be done, and do not ask for confirmation. If the recall query is empty, ask the user for the query.
AGENTS_EOF
fi
EOF

chmod +x ~/.openclaw/skills/ov_dream/ensure_agents.sh
~/.openclaw/skills/ov_dream/ensure_agents.sh
````

## Verify Sync And Recall

```bash
cd ~/.openclaw/skills/ov_dream
set -a
. ~/.openclaw/ov_dream.env
set +a
python3 scripts/dream.py dream
python3 scripts/dream.py recall "最近我在聊什么"
```

## Update Existing Sync Job

Use the existing OpenClaw cronjob that already syncs OV Lite messages. Do not create a second job. Find the current `ov-dream-sync` job id, then edit that job so it checks `AGENTS.md` before running the normal dream sync:

```bash
openclaw cron list

openclaw cron edit <existing-ov-dream-sync-job-id> \
  --message 'cd ~/.openclaw/skills/ov_dream && ./ensure_agents.sh && ./run.sh dream' \
  --tools exec
```

## Recall Command

When the user asks for `ov recall <query>`, run:

```bash
cd ~/.openclaw/skills/ov_dream
set -a
. ~/.openclaw/ov_dream.env
set +a
python3 scripts/dream.py recall "<query>"
```

## Behavior Notes

- OV Lite reads chat sessions from `~/.openclaw/agents/main/sessions/sessions.json`.
- OV Lite does not fall back to scanning latest raw jsonl files.
- OV Lite filters non-chat sessions containing `:cron:`, `:heartbeat:`, `:subagent:`, `:acp:`, or `:hook:`.
- OV Lite reuses the OpenClaw `session_id` when writing to OpenViking serverless.
