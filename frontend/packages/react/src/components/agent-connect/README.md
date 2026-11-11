# AgentConnect

English / [中文](README_CN.md)

A connection view matching [Figma node 10468:23946](https://www.figma.com/design/ecpd1ZGHKRYibm1ODKEpM6/OpenViking-Console?node-id=10468-23946). Two tabs, fourteen agents, four developer methods, icons and localized content are built in. Hosts supply `apiKey` and may override the inherited language with `locale: 'zh-CN' | 'en-US'`; `baseUrl` optionally overrides the default cloud endpoint.

```tsx
import { AgentConnect } from '@openviking/react/components/agent-connect';
import '@openviking/react/components/agent-connect/style.css';

<AgentConnect apiKey={apiKey} locale="en-US" />;
```

## Design and interaction contract

- Each code block has its own copy control. Reveal, hide and copy use icon buttons with localized tooltips and accessible labels; successful copies show a check icon and announce feedback. Blocks containing an API key are masked by default and can reveal or hide it; only one block per guide can reveal credentials at a time. Installer commands use the same controls. Block copying always uses the original key; full-guide copying retains its existing format. Successful `onCopy` callbacks receive the actual block or guide text. Guide or credential changes restore masking and reset feedback.

- Guides use `react-markdown`, `remark-gfm`, `rehype-raw` and `rehype-sanitize` to render Markdown and allowlisted HTML, including bold text, line breaks, collapsible details, lists and tables. Scripts, event attributes and unsafe link protocols are filtered. Tags inside code remain literal text, guide images are not loaded, and copying retains the original Markdown.
- Installer commands explicitly select the current agent via `--harness` and include `cli` for onboarding. Claude Code maps to `claude`, Kimi Code to `kimicode`, DeepSeek Harness to `dsh`, and TRAE to the international `trae` client.
- Installer URLs and keys use shell single-quote escaping to preserve literal argument values. Shell escaping is not applied to JSON, Python or plain-text guides. Custom Hermes endpoints use the **Custom** connection steps with the supplied URL.
- Guides remove trailing slashes from the base URL when appending paths such as `/mcp`. The API example's `url` variable also uses this normalized base before appending `/api/v1/...`. Installer arguments and standalone URLs without appended paths preserve the host's original value.
- Changing guides, credentials or endpoints creates a fresh clipboard interaction: feedback and pending state reset, including when returning to a previous guide. Clipboard writes already started cannot be cancelled, but stale completions neither update feedback nor invoke `onCopy`.
- Hosts may provide `agreementUrl` to render a mouse- and keyboard-accessible agreement link. Without it, only the data access notice is shown, with no reference to an unavailable agreement.

- Language is supplied by the host through `locale`; the component and demo provide no language selection UI.
- The default width is 1000px and minimum height is 949px, with a 200px list, 40px column gap and 660px detail. Typography, pill tabs, row heights, icons, instructions and background glows follow the design screenshot.
- The first tab and Codex are selected initially. Selection is local by default; `activeTabId` and `selectedId` support controlled use, with optional notification callbacks. Tabs support arrow keys, Home and End.
- The mainstream tab includes the full local catalog: Codex, Claude Code, Doubao Work, WorkBuddy, TRAE, Cursor, OpenClaw, Hermes Agent, OpenCode, DeepSeek Harness, DeerFlow, pi, ZCode and Kimi Code. The apps tab and vscode are removed. The developer tab lists MCP, CLI, API and SDK in the same layout. A selection outside the active group falls back to its first item; local selections are remembered per group.
- `showOverseasTag` defaults to `false`, hiding the Overseas tag. When `true`, Codex, Claude Code, Cursor, OpenClaw, Hermes Agent, OpenCode and pi display an additional Overseas tag (境外 in Chinese), retaining their integration tags. The designation follows the agent product's origin, not the deployment region of a selected model.
- DeerFlow includes switchable MemoryManager and MCP guides, defaulting to MemoryManager. Switching guides resets copy feedback. New icons use original logos from the local catalog as alpha masks, preserving their silhouette while following the theme text color for dark-mode visibility.
- CLI combines local `cli.md` and `agent-cli.md` into “Manual terminal installation” and “Install via AI Agent” internal tabs, defaulting to manual installation. Tabs match the reference's bottom border, 24px spacing and primary active underline with theme colors. Copying uses only the active guide; switching resets feedback.
- Metadata and instructions are bundled snapshots of the [local catalog](../../../../../../docs/images/agents), without runtime JSON or Markdown fetching. Codex, Claude Code, TRAE, Cursor, OpenCode, DeepSeek Harness, pi, ZCode and Kimi Code use the reference component's official installer instructions; other entries use their local documents. Refresh snapshots with `node packages/react/src/components/agent-connect/generate-content.mjs`.
- Display masks the API key by default, with an explicit reveal control on code blocks; copying uses the original key and substituted service URL. Keys are not stored in DOM attributes or persistence. Changing locale, credentials, endpoint, group or item resets copy feedback.
- The Clipboard API copies instructions. Success displays「已复制」and invokes `onCopy`; failure displays「复制失败，请重试」and allows retry. Displayed installation commands are never executed by this component.
- Original SVG exports live in the [icon directory](../../../../icons/svg/agent-connect/README.md) and are bundled into JavaScript; hosts need no asset URLs.
- Colors use the theme baseline, with dark mode following the host's `.dark` class. Narrow containers scroll the original layout horizontally to preserve its proportions.
- Codex icons use a transparent outer backdrop and normal blending, preserving the internal white symbol. The detail outline follows the theme border to avoid darkened artwork or a bright frame in dark mode.

The former `tabs`, `items`, `detail` and `labels` inputs have been removed to meet the explicit requirement for fixed design copy and icons.

See the [component docs](../../../../../docs/docs/en/components/agent-connect.mdx) and [workspace README](../../../../../README.md).
