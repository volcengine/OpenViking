## Step 1: Install OpenViking

On the terminal of the machine running OpenClaw, run the following command to install the OpenViking plugin:

```bash
openclaw plugins install clawhub:@openviking/openclaw-plugin && openclaw openviking setup
```

## Step 2: Copy the Base URL and API Key

After running the install command, the setup flow prompts for the Base URL and API Key. Copy them and paste them into your agent terminal:

- Base URL: Copy the following Base URL into your agent terminal
```text
https://api.vikingdb.cn-beijing.volces.com/openviking
```
- API Key: Copy the API Key shown on the page into your agent terminal

## Step 3: Restart OpenClaw

Copy the following command into the agent terminal to restart OpenClaw. After restart, the console automatically detects the agent connection status.

```bash
openclaw gateway restart
```
