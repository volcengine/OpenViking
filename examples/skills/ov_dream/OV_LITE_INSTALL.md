# OV Lite Install

Use this install prompt to ask a cloud OpenClaw agent to install or update OV Lite through the `ov_dream` skill.

```text
Please install or update OV Lite for the current cloud OpenClaw environment through the ov_dream skill, and configure it to sync memories through OpenViking serverless. Do not install the OpenViking contextEngine plugin and do not consume a plugin slot.

Important: install from an explicit OpenViking source ref, not from a local machine path. If this change has not been merged yet, use the pull-request raw source. After merge, use the main-branch raw source.

Current pre-merge source:

https://github.com/volcengine/OpenViking/pull/2136

Raw source base:

SOURCE_BASE=https://raw.githubusercontent.com/volcengine/OpenViking/refs/pull/2136/head

After this PR is merged, replace it with:

SOURCE_BASE=https://raw.githubusercontent.com/volcengine/OpenViking/main

If I have not provided real values, stop and ask me for:
- OPENVIKING_API_KEY
- OPENVIKING_AGENT_ID

Do not print the API key in logs or replies.

Run these steps:

1. Create the skill directory:

mkdir -p ~/.openclaw/skills/ov_dream/scripts

2. Download and overwrite the skill files from SOURCE_BASE:

SOURCE_BASE=https://raw.githubusercontent.com/volcengine/OpenViking/refs/pull/2136/head
curl -fsSL "$SOURCE_BASE/examples/skills/ov_dream/SKILL.md" -o ~/.openclaw/skills/ov_dream/SKILL.md
curl -fsSL "$SOURCE_BASE/examples/skills/ov_dream/scripts/dream.py" -o ~/.openclaw/skills/ov_dream/scripts/dream.py
touch ~/.openclaw/skills/ov_dream/__init__.py
touch ~/.openclaw/skills/ov_dream/scripts/__init__.py

If curl returns 404 or fails, stop immediately and tell me SOURCE_BASE is unavailable or outdated.

3. Verify the downloaded dream.py contains the latest capabilities:

grep -q 'SERVERLESS_BASE_URL' ~/.openclaw/skills/ov_dream/scripts/dream.py
grep -q 'OPENVIKING_AUTH_MODE' ~/.openclaw/skills/ov_dream/scripts/dream.py
grep -q 'viking://user/default' ~/.openclaw/skills/ov_dream/scripts/dream.py
grep -q 'is_chat_session_key' ~/.openclaw/skills/ov_dream/scripts/dream.py
grep -q 'raw jsonl fallback can accidentally sync cron/subagent transcripts' ~/.openclaw/skills/ov_dream/scripts/dream.py
grep -q 'client.add_session_message(session.session_id' ~/.openclaw/skills/ov_dream/scripts/dream.py

If any check fails, stop immediately and tell me SOURCE_BASE is not the latest expected version. Do not configure cron.

4. Create or update the environment file. If ~/.openclaw/ov_dream.env already exists and contains real values, keep the existing values and only add missing non-secret defaults. Do not overwrite a real API key or agent id with placeholders. If real values are missing, stop and ask me for OPENVIKING_API_KEY and OPENVIKING_AGENT_ID.

If the file does not exist, create it with:

cat > ~/.openclaw/ov_dream.env <<'EOF'
OPENVIKING_BASE_URL=https://api.vikingdb.cn-beijing.volces.com/openviking
OPENVIKING_API_KEY=<replace with my OpenViking serverless API key>
OPENVIKING_AGENT_ID=<replace with my OpenViking agent ID>
OPENVIKING_AUTH_MODE=serverless
EOF
chmod 600 ~/.openclaw/ov_dream.env

5. Verify sync:

cd ~/.openclaw/skills/ov_dream && set -a && . ~/.openclaw/ov_dream.env && set +a && python3 scripts/dream.py dream

6. Verify recall:

cd ~/.openclaw/skills/ov_dream && set -a && . ~/.openclaw/ov_dream.env && set +a && python3 scripts/dream.py recall "最近我在聊什么"

7. Add or update the OpenClaw cronjob to sync every 5 minutes. If a job named ov-dream-sync already exists, update or replace that job instead of creating a duplicate:

openclaw cron add ov-dream-sync --schedule "*/5 * * * *" --command 'cd ~/.openclaw/skills/ov_dream && set -a && . ~/.openclaw/ov_dream.env && set +a && python3 scripts/dream.py dream'

8. After installation, when I type:

ov recall <query>

run:

cd ~/.openclaw/skills/ov_dream && set -a && . ~/.openclaw/ov_dream.env && set +a && python3 scripts/dream.py recall "<query>"

Notes:
- Do not use local machine paths such as /Users/bytedance/OpenViking.
- Do not install the OpenViking contextEngine plugin.
- Do not consume a plugin slot.
- Do not download from an implicit default branch. Always use the explicit SOURCE_BASE above.
- Do not print the API key in logs or replies.
- OV Lite only reads chat sessions from ~/.openclaw/agents/main/sessions/sessions.json and no longer falls back to scanning latest jsonl files.
- OV Lite filters non-chat sessions containing :cron:, :heartbeat:, :subagent:, :acp:, or :hook:.
- OV Lite reuses the OpenClaw session_id when writing to OpenViking serverless and no longer creates a separate serverless session id.
```
