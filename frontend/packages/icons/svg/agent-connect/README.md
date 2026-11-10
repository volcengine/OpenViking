# AgentConnect icons

These icons are exported from the designer-supplied [AgentConnect node 10468:23946](https://www.figma.com/design/ecpd1ZGHKRYibm1ODKEpM6/OpenViking-Console?node-id=10468-23946).

The nine catalog icons, Codex detail icon and copy icon preserve the supplied artwork, crops and geometry. Embedded raster payloads are reduced to at most 96 × 96 pixels (above twice the displayed size), and SVG IDs are prefixed by filename to avoid collisions between different icons.

The MCP, CLI, API and SDK SVGs preserve the `GenericIcon` artwork from the user-supplied console reference (`src/pages/ConnectAgent/connectAgentUi.tsx`). They use `currentColor` and are bundled locally, without an icon service or runtime download.

DeepSeek Harness, DeerFlow, pi, ZCode and Kimi Code use the original PNG logos from [the local agent catalog](../../../../../docs/images/agents/image), which are also used by the console reference. Each is resized to 96 × 96 pixels and embedded as an SVG alpha mask, preserving the monochrome silhouette while following `currentColor` in both themes, without remote requests.

Agent logos remain their respective owners' marks. No new icon package or external CDN is required.

Codex's connected outer white backdrop is made transparent while its internal white symbol is retained. Its list icon no longer uses multiply blending, and the detail outline reads the component's theme border variable so both variants work on dark backgrounds.
