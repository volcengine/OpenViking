DeerFlow can use OpenViking as a long-term memory backend through MemoryManager. After the integration is enabled, DeerFlow writes conversation messages to OpenViking, recalls relevant memories before model calls, and injects them into the context.

## Step 1: Configure OpenViking credentials

Set the OpenViking base URL and API key in the environment that starts DeerFlow:

```bash
export OPENVIKING_BASE_URL="https://api.vikingdb.cn-beijing.volces.com/openviking"
export OPENVIKING_API_KEY="[TODO]your-api-key"
```

If you use `.env` or a deployment platform to manage environment variables, add the same variables there and make sure the DeerFlow process can read them at startup.

## Step 2: Update DeerFlow memory configuration

Enable MemoryManager in the DeerFlow configuration and point the provider to OpenViking:

```yaml
memory:
  enabled: true
  provider: openviking
  openviking:
    base_url: ${OPENVIKING_BASE_URL}
    api_key: ${OPENVIKING_API_KEY}
    auto_write: true
    auto_recall: true
    inject_recalled_memory: true
```

Enable write, recall, and injection together so DeerFlow can persist long-term memory during conversations and reuse relevant memories in later tasks.

## Step 3: Restart DeerFlow

Save the configuration and restart DeerFlow:

```bash
pnpm dev
```

If DeerFlow is deployed through Docker, a process manager, or a cloud service, use the corresponding restart flow and verify that the new environment variables are available at runtime.

## Step 4: Verify OpenViking connectivity

After startup, check the DeerFlow logs and confirm that MemoryManager initializes successfully without OpenViking authentication or network errors.

You can also check OpenViking connectivity directly:

```bash
curl -H "Authorization: Bearer ${OPENVIKING_API_KEY}" \
  "${OPENVIKING_BASE_URL}/health"
```

A service status response means OpenViking is reachable.

## Step 5: Verify memory write and recall

Start a DeerFlow conversation with a stable fact, for example:

```text
Remember that my DeerFlow test project uses OpenViking as the long-term memory backend.
```

Then start a new conversation and ask:

```text
What does my DeerFlow test project use as the long-term memory backend?
```

If DeerFlow answers OpenViking, memory write, recall, and context injection are working.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| MemoryManager fails to initialize | Check `memory.enabled`, `provider`, and whether DeerFlow is loading the expected configuration file |
| OpenViking returns 401 / 403 | Verify `OPENVIKING_API_KEY`, confirm it has not expired, and make sure requests use the `Bearer` prefix |
| Memories are not written | Confirm `auto_write` is enabled and check DeerFlow logs for write failures |
| Memories are not recalled | Confirm `auto_recall` and `inject_recalled_memory` are enabled and OpenViking already has related memories |
| Works locally but not after deployment | Check whether `OPENVIKING_BASE_URL` and `OPENVIKING_API_KEY` are injected into the deployed runtime |
