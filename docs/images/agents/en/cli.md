# CLI Integration

## Install from the command line

Run the following command in your terminal to install OpenViking CLI and start configuration:

```bash
npm i -g @openviking/cli && ov config
```

Use this service URL when prompted:

```text
https://api.vikingdb.cn-beijing.volces.com/openviking
```

After configuration, inspect CLI usage with:

```bash
ov --help
```

## Install through an agent conversation

The Agent CLI setup flow will be improved in a follow-up PR. For now, send the following instruction to your agent:

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
