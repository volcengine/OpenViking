DeerFlow can use OpenViking as a long-term memory backend through MemoryManager. After the integration is enabled, DeerFlow writes conversation messages to OpenViking, recalls relevant memories before model calls, and injects them into the context.

## Step 1: Configure OpenViking credentials

Edit the `.env` file in the DeerFlow project root and add the API key:

{{OPENVIKING_API_KEY_BLOCK}}

## Step 2: Update DeerFlow memory configuration

Open `config.yaml` in the project root, find the `memory:` section, and replace the default DeerMem configuration with OpenViking:

```yaml
memory:
  enabled: true
  injection_enabled: true
  shutdown_flush_timeout_seconds: 30
  manager_class: openviking
  mode: middleware
  backend_config:
    base_url: {{OPENVIKING_BASE_URL}}
    owner_user_id: default
    api_key_env: OPENVIKING_API_KEY
    startup_policy: fail_fast
    failure_policy:
      read: fail_open
      write: log_and_drop
    retrieval:
      top_k: 8
      score_threshold: 0.25
      max_injection_chars: 12000
      content_mode: overview
      injection_query: >-
        user profile preferences important entities events ongoing goals
        constraints and prior decisions
```

## Step 3: Restart DeerFlow

Save `.env` and `config.yaml`, then restart DeerFlow:

```bash
make dev
```

## Step 4: Verify OpenViking integration

Check the Gateway logs from the project root:

```bash
grep -i "memory manager resolved\|openviking\|deermem" logs/gateway.log
```

Successful log examples:

```text
Memory manager resolved: OpenVikingMemoryManager (manager_class='openviking')
HTTP Request: GET {{OPENVIKING_BASE_URL}}/health "HTTP/1.1 200 OK"
```

## Step 5: Verify memory write and recall

Use the following logs to confirm that write and recall are working:

```bash
grep -Ei "messages/batch|commit|search/find|has_memory" logs/gateway.log | tail -100
```

Successful log examples:

```text
/messages/batch "HTTP/1.1 200 OK"
/commit "HTTP/1.1 200 OK"
/search/find "HTTP/1.1 200 OK"
has_memory=True
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| DeerFlow fails to start and reports an OpenViking configuration error | The OpenViking section in `config.yaml` is incomplete or malformed | Check that `manager_class: openviking` is configured and verify fields such as `base_url` and `api_key_env` |
| DeerFlow is not connected to OpenViking | `memory.manager_class` was not changed to `openviking`, or the service was not restarted after the config change | Save the configuration, restart DeerFlow, and confirm that `OpenVikingMemoryManager` appears in the logs |
| Remote authentication fails with 401 or 403 | The OpenViking API Key is missing, incorrect, or unauthorized | Check whether `OPENVIKING_API_KEY` is correctly set in `.env` |
| Retrieval fails but DeerFlow still replies | The current configuration uses `read: fail_open`, which is expected behavior | If OpenViking retrieval fails, memory will not be injected, but the main Agent response will continue |
| A response is generated but memory write fails | The current configuration uses `write: log_and_drop`, so write failures are recorded in logs | Check and fix OpenViking service, network, and authentication settings. New messages can continue to be written after recovery |
| Messages have been written, but memories are not visible immediately | OpenViking summarization and memory extraction are asynchronous | Wait for background tasks to finish, then check again |
| Memory operations are still pending during service shutdown | The system waits up to `shutdown_flush_timeout_seconds` for them to finish | If the wait times out, or OpenViking is unavailable during shutdown, some writes may not complete. Increase the timeout if needed and check OpenViking network/service availability during shutdown |
