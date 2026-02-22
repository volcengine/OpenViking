# Converting OpenViking Skills to OpenClaw Slash Commands

To expose an OpenViking skill as a slash command in OpenClaw, add these 4 lines to the SKILL.md frontmatter:

```yaml
---
name: ovm                          # Short command name (becomes /ovm)
description: Add memories...       # Existing description
user-invocable: true               # Expose as slash command
command-dispatch: tool             # Route directly to tool (no model)
command-tool: exec                 # Use exec tool
command-arg-mode: raw              # Pass raw args to command
---
```

## How It Works

| Config | Purpose |
|--------|---------|
| `user-invocable: true` | Registers the skill as a native slash command |
| `command-dispatch: tool` | Bypasses the model, calls tool directly |
| `command-tool: exec` | Uses the exec tool to run shell commands |
| `command-arg-mode: raw` | Passes user input raw to the command |

When a user types `/ovm some text`, OpenClaw executes:
```bash
ov add-memory "some text"
```

## Example Skills

| Skill | Command | ov CLI |
|-------|---------|--------|
| adding-memory | `/ovm` | `ov add-memory` |
| adding-resource | `/ovr` | `ov add-resource` |
| searching-context | `/ovs` | `ov search` |

## Requirements

- `ov` CLI must be installed and configured at `~/.openviking/ovcli.conf`
- OpenClag gateway needs `commands.native: true` (default for Telegram/Discord)
