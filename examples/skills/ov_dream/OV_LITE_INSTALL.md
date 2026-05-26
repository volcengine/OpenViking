# OV Lite Install

This guide installs OV Lite for OpenClaw through the `ov_dream` skill. It syncs OpenClaw chat sessions to OpenViking serverless without installing the OpenViking `contextEngine` plugin or consuming a plugin slot.

## Prerequisites

Set these values before running sync or recall:

- `OPENVIKING_API_KEY`: OpenViking serverless API key
- `OPENVIKING_AGENT_ID`: OpenViking serverless agent ID

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

Create `~/.openclaw/ov_dream.env` if it does not exist. If it already exists, keep real `OPENVIKING_API_KEY` and `OPENVIKING_AGENT_ID` values and only add missing non-secret defaults.

```bash
cat > ~/.openclaw/ov_dream.env <<'EOF'
OPENVIKING_BASE_URL=https://api.vikingdb.cn-beijing.volces.com/openviking
OPENVIKING_API_KEY=<replace with OpenViking serverless API key>
OPENVIKING_AGENT_ID=<replace with OpenViking agent ID>
OPENVIKING_AUTH_MODE=serverless
EOF
chmod 600 ~/.openclaw/ov_dream.env
```

## Verify Sync And Recall

```bash
cd ~/.openclaw/skills/ov_dream
set -a
. ~/.openclaw/ov_dream.env
set +a
python3 scripts/dream.py dream
python3 scripts/dream.py recall "最近我在聊什么"
```

## Schedule Sync

Add or update an OpenClaw cronjob to sync every 5 minutes. If `ov-dream-sync` already exists, update or replace it instead of creating a duplicate.

```bash
openclaw cron add ov-dream-sync \
  --schedule "*/5 * * * *" \
  --command 'cd ~/.openclaw/skills/ov_dream && set -a && . ~/.openclaw/ov_dream.env && set +a && python3 scripts/dream.py dream'
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
