# Install OpenViking Context Engine for OpenClaw

This plugin is an example context-engine extension located in `examples/openclaw-contextengine-plugin`.

## 1) Copy plugin files to OpenClaw extension directory

```bash
mkdir -p ~/.openclaw/extensions/contextengine-openviking
cp examples/openclaw-contextengine-plugin/{index.ts,types.ts,config.ts,client.ts,retrieval.ts,injection.ts,ingestion.ts,tools.ts,skill-tool-memory.ts,fallback.ts,telemetry.ts,openclaw.plugin.json,package.json,tsconfig.json} \
  ~/.openclaw/extensions/contextengine-openviking/
```

## 2) Install dependencies in plugin directory

```bash
cd ~/.openclaw/extensions/contextengine-openviking
npm install
```

## 3) Enable plugin and assign context-engine slot

```bash
openclaw config set plugins.enabled true
openclaw config set plugins.slots.contextEngine contextengine-openviking
openclaw config set plugins.entries.contextengine-openviking.config.mode "local"
openclaw config set plugins.entries.contextengine-openviking.config.retrieval.enabled true --json
openclaw config set plugins.entries.contextengine-openviking.config.retrieval.injectMode "simulated_tool_result"
openclaw config set plugins.entries.contextengine-openviking.config.retrieval.scoreThreshold 0.15
openclaw config set plugins.entries.contextengine-openviking.config.ingestion.writeMode "compact_batch"
openclaw config set plugins.entries.contextengine-openviking.config.ingestion.maxBatchMessages 200
```

## 4) Start OpenClaw

```bash
openclaw gateway
```

## 5) Verify

- Confirm plugin slot is configured:

```bash
openclaw config get plugins.slots.contextEngine
```

- Run local tests in the plugin source folder:

```bash
cd /path/to/OpenViking/examples/openclaw-contextengine-plugin
pnpm exec vitest
```

## Notes

- This example uses OpenViking HTTP endpoint `http://127.0.0.1:1933` by default.
- Retrieval failures are handled gracefully and do not block assembly.
