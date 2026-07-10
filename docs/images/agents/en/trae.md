## Install the TRAE Integration

Run the command for your client:

```bash
# TRAE
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae --dist tos

# TRAE CN
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae-cn --dist tos

# Both
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae,trae-cn --dist tos
```

## Verify

1. Restart TRAE after installation.
2. Confirm that `openviking` is connected in TRAE settings.
3. Start a new session and ask about a previous project or preference.

See the complete [TRAE integration guide](https://github.com/volcengine/OpenViking/blob/main/docs/en/agent-integrations/13-trae.md).

## Troubleshooting

| Problem | Suggested fix |
|---|---|
| Automatic recall does not run after installation | Quit TRAE completely, restart it, and create a new Agent session. |
| Recall runs more than once | Re-run the install command and restart TRAE. |
| Connection/authentication fails | Check `~/.openviking/ovcli.conf` and restart TRAE. |
