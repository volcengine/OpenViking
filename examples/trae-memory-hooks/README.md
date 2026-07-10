# OpenViking Memory Hooks for TRAE

This package provides dedicated TRAE and TRAE CN lifecycle adapters. It does not reuse Claude Code transcript parsing: TRAE capture reads `prompt`, `text_content`, and `last_assistant_message` from the `Stop` event and stores sessions with `tr-` or `trcn-` prefixes.

Use the shared installer:

```bash
bash examples/memory-plugin-shared/install.sh --harness trae,trae-cn
```

See the [TRAE integration guide](../../docs/en/agent-integrations/13-trae.md).
