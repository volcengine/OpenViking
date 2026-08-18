[Hermes Agent](https://hermes-agent.nousresearch.com/) includes OpenViking as a built-in memory provider. You do not install a plugin. This page is only for connecting Hermes to OpenViking Service on Volcengine. For a self-hosted server, use the public docs site instead.

## Step 1: Run the memory setup wizard

```bash
hermes memory setup openviking
```

## Step 2: Choose Volcengine Cloud and paste the API key

The wizard already knows the cloud endpoint. You only need the API key from this page.

1. If you see **OpenViking config source**, choose **Create new OpenViking profile**. If you already ran the shared plugin installer (Claude Code / Codex / Cursor / TRAE), choose **Use existing OpenViking profile** and you are done.
2. On **OpenViking connection**, keep the default **OpenViking Service (VolcEngine Cloud)** and press Enter.
3. The wizard prints the endpoint itself:

```text
https://api.vikingdb.cn-beijing.volces.com/openviking
```

4. When it asks for **OpenViking API key**, paste the API key from this page.
5. Keep the default Hermes peer ID (`hermes`) unless you need another one.

You do not paste a Base URL by hand. A user API key does not need tenant account / user IDs.

## Step 3: Verify

```bash
hermes memory status
```

You should see `Provider: openviking` and `Status: available`. Start a new Hermes session after that.

Hermes then injects context, prefetches related memories, and syncs after sessions. Tools: `viking_search`, `viking_read`, `viking_browse`, `viking_remember`, `viking_forget`, `viking_add_resource`.

## Reference

- [Hermes — OpenViking memory provider](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking)
