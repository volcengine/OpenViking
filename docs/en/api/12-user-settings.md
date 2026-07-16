# Agent Evolution User Settings

User settings control whether future session commits generate or update `trajectories` and `experiences`. Settings are isolated per user and stored at `viking://user/<user_id>/settings/user_config.json`.

## Effective Behavior

- `agent_evolution_enabled` defaults to `false` when it is not explicitly configured.
- Disabling Agent Evolution does not stop session archiving, Working Memory, or other memory types.
- Disabling it does not delete existing trajectories or experiences and does not affect experience retrieval or reads.
- When enabled, session-level `memory_policy.memory_types` can still restrict memory types for one commit.

This API does not provide a persistent user-level `memory_types` setting. Use session `memory_policy.memory_types` for per-commit filtering.

## Get Settings

```http
GET /api/v1/user-settings/memory
```

`override` contains the value explicitly stored for the current user. `effective` includes deployment and built-in defaults. GET does not create a configuration file.

```bash
curl http://localhost:1933/api/v1/user-settings/memory \
  -H "X-API-Key: your-key"
```

Example response:

```json
{
  "status": "ok",
  "result": {
    "override": {
      "agent_evolution_enabled": null
    },
    "effective": {
      "agent_evolution_enabled": false
    }
  }
}
```

Python SDK:

```python
settings = await client.get_memory_settings()
```

CLI:

```bash
ov user-settings memory
```

## Update Settings

```http
PATCH /api/v1/user-settings/memory
Content-Type: application/json
```

`agent_evolution_enabled` accepts a boolean, `null`, or omission. `null` clears the user override; omission leaves the setting unchanged.

```bash
curl -X PATCH http://localhost:1933/api/v1/user-settings/memory \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"agent_evolution_enabled": true}'
```

Python SDK:

```python
await client.patch_memory_settings(agent_evolution_enabled=True)
await client.patch_memory_settings(agent_evolution_enabled=None)
```

CLI:

```bash
ov user-settings set-memory --agent-evolution-enabled true
ov user-settings set-memory --agent-evolution-enabled false
ov user-settings set-memory --clear-agent-evolution-enabled
```

## Related Documentation

- [Sessions](05-sessions.md) - Create sessions, configure `memory_policy`, and commit
- [Retrieval](06-retrieval.md) - Retrieve existing experiences
