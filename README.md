# OpenViking Skills

This branch distributes OpenViking compile skills without the application source code.
Each directory contains one skill and any supporting files it needs.

| Skill | Purpose |
| --- | --- |
| [daily-report](daily-report/SKILL.md) | Compile evidence-grounded daily work reports. |
| [knowledge-distillation](knowledge-distillation/SKILL.md) | Distill knowledge across multiple sources. |
| [knowledge-graph](knowledge-graph/SKILL.md) | Build an evidence-grounded knowledge graph. |
| [llm-wiki](llm-wiki/SKILL.md) | Build and maintain an evidence-grounded wiki. |
| [ov-session-report](ov-session-report/SKILL.md) | Generate weekly reports from local Q&A session logs. |

## Install

With an OpenViking server configured, install a skill using its directory URL:

```bash
ov skills add https://github.com/volcengine/OpenViking/tree/skills/llm-wiki
```

Replace `llm-wiki` with another skill directory from the table.
The CLI shallow-clones this skills-only branch and imports the selected directory.

## Source and license

Initial contents were copied unchanged from
[OpenViking's compile skills](https://github.com/volcengine/OpenViking/tree/f6ae7814d47523d203c822ba53e3d729c8e1c089/examples/compile/ov-compile-skills).
This branch is a distribution snapshot; changes on `main` are not synchronized automatically.
The skills retain the [Apache-2.0 license](LICENSE) from the original `examples` directory.
