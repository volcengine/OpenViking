Copy the following prompt to your AI assistant (Claude Code, Codex, Cursor, Trae, and so on). It will automatically complete OpenViking CLI installation, configuration, and usage learning:

```text
First ask the user for the OpenViking API Key and store it as OPENVIKING_API_KEY.

Write the following content to ~/.openviking/ovcli.conf, replacing ${OPENVIKING_API_KEY} with the actual value provided by the user:
{
  "url": "https://api.vikingdb.cn-beijing.volces.com/openviking",
  "api_key": "${OPENVIKING_API_KEY}"
}

If ~/.openviking/ovcli.conf already exists and the content conflicts, ask the user whether to back up the original file before overwriting it.

Install OpenViking CLI:
npm i -g @openviking/cli

After installation, run:
ov --help

Explore the CLI usage and write the OpenViking CLI workflow into your long-term memory.
```
