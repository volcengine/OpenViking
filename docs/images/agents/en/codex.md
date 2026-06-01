# Codex Memory Plugin

Add persistent cross-session memory to Codex. The plugin recalls relevant memory before each user prompt, captures conversation updates after each turn, commits before compaction, and connects Codex to the OpenViking MCP endpoint.

## Install

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/codex-memory-plugin/setup-helper/install.sh)
```

After installation:

```bash
source ~/.zshrc
codex
```

Approve hooks on first launch if Codex asks for review.

## Verify

```bash
type codex
```

Expected output: `codex is a shell function`.

Set `OPENVIKING_DEBUG=1` to write events to `~/.openviking/logs/codex-hooks.log`.

## Notes

Codex does not trigger hooks on `SIGTERM`, `Ctrl+C`, or `/exit`. The plugin cleans up orphan sessions on the next `SessionStart` with an idle TTL.
