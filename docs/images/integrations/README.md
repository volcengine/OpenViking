# Integration logo sources

These are the same product marks used by the OpenViking website and documentation.

- `codex.png`: [documentation catalog artwork](https://lf3-static.bytednsdoc.com/obj/eden-cn/lm_sth/ljhwZthlaukjlkulzlp/agent_logo/codex.png), referenced by `docs/images/agents/en/index.json`.
- `openclaw.jpg`, `hermes-agent.png`, `pi.svg`: reused from `openviking_playground/src/assets/brand/` (`ca4fb82`). The OpenClaw file contains JPEG data and uses the matching extension here.
- `pi-dark.svg`: the same monochrome pi mark with a light foreground for dark backgrounds.

The READMEs also reuse Claude Code, Cursor, TRAE, and OpenCode logos from `docs/images/agents/image/`.

- `agent-plugins.svg`: [Agent Plugins official site icon](https://agent-plugins.org/icon.svg), including its theme-aware fill.
- `mcp.svg`: [Model Context Protocol official favicon](https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/main/docs/favicon.svg).
- `langchain.svg`: [LangChain official product icon](https://cdn.prod.website-files.com/65b8cd72835ceeacd4449a53/69981d0819e88e3d4dd7b917_langchain%20icon.svg), linked by https://www.langchain.com/langchain.
- `deerflow.svg`: [DeerFlow official vector mark](https://raw.githubusercontent.com/bytedance/deer-flow/main/frontend/public/images/deer.svg). `deerflow-dark.svg` uses the same paths with a white foreground.

HTML display sizes account for artwork padding: padded PNGs use 32px boxes, full-bleed marks use 24px, and pi uses 41px because its mark occupies about 59% of its viewBox. Visible marks target a 24px longest side, preserve aspect ratio, and align the names along the bottom of each row. DeerFlow's portrait mark uses 19×24px. No raster logos were resampled.
