# OpenClaw ov_dream Install Prompt

Use this prompt when asking a cloud OpenClaw agent to install or update the `ov_dream` skill.

```text
Please install or update the ov_dream skill for the current cloud OpenClaw environment, and configure it to sync memories through OpenViking serverless. Do not install the OpenViking contextEngine plugin and do not consume a plugin slot.

Important: this skill is not in the OpenViking main branch yet. Use the upstream pull request source:

https://github.com/volcengine/OpenViking/pull/2136

Download files from:

https://raw.githubusercontent.com/volcengine/OpenViking/refs/pull/2136/head

If I have not provided real values, stop and ask me for:
- OPENVIKING_API_KEY
- OPENVIKING_AGENT_ID

Do not print the API key in logs or replies.

Run these steps:

1. Create the skill directory:

mkdir -p ~/.openclaw/skills/ov_dream/scripts

2. Download and overwrite the skill files from the pull request source. Do not download from main:

curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/refs/pull/2136/head/examples/skills/ov_dream/SKILL.md -o ~/.openclaw/skills/ov_dream/SKILL.md
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/refs/pull/2136/head/examples/skills/ov_dream/scripts/dream.py -o ~/.openclaw/skills/ov_dream/scripts/dream.py
touch ~/.openclaw/skills/ov_dream/__init__.py
touch ~/.openclaw/skills/ov_dream/scripts/__init__.py

If curl returns 404 or fails, stop immediately and tell me the PR source is unavailable.

3. Verify the downloaded dream.py contains the latest capabilities:

grep -q 'SERVERLESS_BASE_URL' ~/.openclaw/skills/ov_dream/scripts/dream.py
grep -q 'OPENVIKING_AUTH_MODE' ~/.openclaw/skills/ov_dream/scripts/dream.py
grep -q 'viking://user/default' ~/.openclaw/skills/ov_dream/scripts/dream.py
grep -q 'is_chat_session_key' ~/.openclaw/skills/ov_dream/scripts/dream.py
grep -q 'raw jsonl fallback can accidentally sync cron/subagent transcripts' ~/.openclaw/skills/ov_dream/scripts/dream.py
grep -q 'client.add_session_message(session.session_id' ~/.openclaw/skills/ov_dream/scripts/dream.py

If any check fails, stop immediately and tell me the PR source is not the latest version. Do not configure cron.

4. Create or update the environment file. If ~/.openclaw/ov_dream.env already exists and contains real values, do not overwrite them with placeholders. If real values are missing, stop and ask me for OPENVIKING_API_KEY and OPENVIKING_AGENT_ID.

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

7. Add or update the OpenClaw cronjob to sync every 5 minutes:

openclaw cron add ov-dream-sync --schedule "*/5 * * * *" --command 'cd ~/.openclaw/skills/ov_dream && set -a && . ~/.openclaw/ov_dream.env && set +a && python3 scripts/dream.py dream'

8. After installation, when I type:

ov recall <query>

run:

cd ~/.openclaw/skills/ov_dream && set -a && . ~/.openclaw/ov_dream.env && set +a && python3 scripts/dream.py recall "<query>"

Notes:
- Do not use local machine paths such as /Users/bytedance/OpenViking.
- Do not install the OpenViking contextEngine plugin.
- Do not consume a plugin slot.
- Do not download from main.
- Do not print the API key in logs or replies.
- ov_dream only reads chat sessions from ~/.openclaw/agents/main/sessions/sessions.json and no longer falls back to scanning latest jsonl files.
- ov_dream filters non-chat sessions containing :cron:, :heartbeat:, :subagent:, :acp:, or :hook:.
- ov_dream reuses the OpenClaw session_id when writing to OpenViking serverless and no longer creates a separate serverless session id.
```
