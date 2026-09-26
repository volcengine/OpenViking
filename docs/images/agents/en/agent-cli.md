Copy the following prompt to your AI assistant (Claude Code, Codex, Cursor, Trae, and so on). Ask it to install and configure the CLI, then verify the connection:

```text
First ask the user for the OpenViking API Key and store it as OPENVIKING_API_KEY.

Write the following content to ~/.openviking/ovcli.conf, replacing ${OPENVIKING_API_KEY} with the actual value provided by the user. The CLI does not expand environment variables in this file:
{
  "url": "{{OPENVIKING_BASE_URL}}",
  "api_key": "${OPENVIKING_API_KEY}"
}

If ~/.openviking/ovcli.conf already exists and the content conflicts, ask the user whether to back up the original file before overwriting it.

Install OpenViking CLI:
npm i -g @openviking/cli

After installation, run:
ov config validate
ov health
ov --help

Read ov --help and the help for individual commands, then summarize common operations. Save the workflow in long-term memory, including only usage notes, not the API key or raw config file.
```
