# OpenClaw Plugin

## Step 1: Install OpenViking

Run the following command in the terminal where OpenClaw is installed:

```bash
openclaw plugins install clawhub:@openviking/openclaw-plugin && openclaw openviking setup
```

## Step 2: Enter connection information

The setup command will prompt for:

- Base URL: `https://api.vikingdb.cn-beijing.volces.com/openviking`
- API Key: copy the API Key shown in the OpenViking console

## Step 3: Restart OpenClaw

Restart OpenClaw so the plugin can load the new configuration:

```bash
openclaw gateway restart
```
