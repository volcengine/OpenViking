[Hermes Agent](https://hermes-agent.nousresearch.com/) (Nous Research) includes OpenViking as a built-in memory provider. No plugin installation is required. Point Hermes to your OpenViking service to enable native memory storage, recall, and extraction.

## Step 1: Run the Hermes memory setup wizard

```bash
hermes memory setup
```

## Step 2: Copy the Base URL and Authentication management

After running the setup command, Hermes prompts for the Base URL and Authentication management. Copy them and paste them into Hermes:

- Base URL: Copy the following Base URL into Hermes:
```text
https://api.vikingdb.cn-beijing.volces.com/openviking
```
- Authentication management: Copy the Authentication management shown on the page into your Hermes terminal
- Tenant account / user / agent ID: Used for multi-tenant deployments

The configuration is saved to Hermes `config.yaml` and `.env` files.

## Step 3: Verify Hermes memory status

```bash
hermes memory status
```

After configuration, Hermes uses the OpenViking memory provider to inject context, prefetch relevant memories, and sync and extract memories after sessions. Available tools include `viking_search`, `viking_read`, `viking_browse`, `viking_remember`, `viking_forget`, and `viking_add_resource`.

## Reference docs

- [Hermes - OpenViking memory provider documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking) - Full configuration guide
