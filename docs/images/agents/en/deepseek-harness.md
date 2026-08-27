## Step 1: Install

Run the installer:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh)
```

If GitHub is difficult to reach, use the Volcengine TOS mirror:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh)
```

The installer asks for the language, harness, download source, and OpenViking credentials:

1. Select **DeepSeek Harness**. The plugin defaults to the `web` profile; use `--dsh-profile <name>` for another profile.
2. Select **Volcengine OpenViking Cloud** and enter the API key:

{{OPENVIKING_API_KEY_BLOCK}}

## Step 2: Verify

1. Run `dsh --profile web` and open a new conversation. Confirm that “context injection · openviking-memory” appears at the top.
2. Confirm that the model has `mcp__openviking__*` tools and can call them.

## Troubleshooting

| Issue | What to check |
|---|---|
| No context injection or OpenViking tools | Run `dsh --profile web --dump-config` and confirm it contains `openviking-memory`; otherwise rerun the installer or run `dsh plugin --profile web add @openviking/dsh-memory-plugin` |
| Installed into the wrong profile | The installer defaults to `web`; rerun it with `--dsh-profile <name>` |
| `ERESOLVE @deepseek-ai/dsh-*` during install | Prerelease tags may be out of sync; install `@deepseek-ai/dsh@0.1.0-rc.6` exactly |
| Package reported missing from npm | pnpm rejects releases younger than 24 hours by default; wait and retry, or add the exact version to `minimumReleaseAgeExclude` in `pnpm-workspace.yaml` |
| Recall returns no history | Run `curl http://localhost:1933/health` to confirm the server is healthy, then check the endpoint and make sure the prompt is at least 3 characters long |
| OpenViking returns 401 / 403 | Check the API key; trusted-mode deployments must also check `OPENVIKING_ACCOUNT` and `OPENVIKING_USER` |
| Memories from other projects appear | Set `OPENVIKING_RECALL_PEER_SCOPE=actor` |
| Nothing committed after a crash | Commits run at the token threshold and session teardown; queued writes replay at the next session start |

## References

- Full guide: [DeepSeek Harness](https://docs.openviking.net/en/agent-integrations/17-dsh)
- Source: [examples/dsh-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin)
