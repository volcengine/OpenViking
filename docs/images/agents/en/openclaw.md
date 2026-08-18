## Step 1: Install the OpenViking plugin

On the machine that runs OpenClaw:

```bash
openclaw plugins install clawhub:@openviking/openclaw-plugin && openclaw openviking setup
```

## Step 2: Point setup at Volcengine Cloud

`openclaw openviking setup` asks for the Base URL and API key. Paste these:

- Base URL:

```text
https://api.vikingdb.cn-beijing.volces.com/openviking
```

- API key: paste the API key from this page

Or do it in one shot:

```bash
openclaw openviking setup --base-url https://api.vikingdb.cn-beijing.volces.com/openviking --api-key <API-key-from-this-page>
```

## Step 3: Restart OpenClaw

```bash
openclaw gateway restart
```

The console will pick up the agent connection after restart.
