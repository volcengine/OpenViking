# Agent CLI Integration

For AI agents. Send the following instruction to your agent so it can install and configure OpenViking CLI, then learn how to use it.

```text
Write the following content to ~/.openviking/ovcli.conf:
{"url":"https://api.vikingdb.cn-beijing.volces.com/openviking","api_key":"Please ask user for Volcengine OpenViking API key."}

Ask the user for the API Key. If ~/.openviking/ovcli.conf already exists and the content conflicts, ask the user whether to back up the original file before overwriting it.

Install OpenViking CLI:
npm i -g @openviking/cli

After installation, run:
ov --help

Explore the CLI usage and write the OpenViking CLI workflow into your long-term memory.
```
