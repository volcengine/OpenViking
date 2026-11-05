# AgentConnect

English / [中文](README_CN.md)

An agent connection view. The host supplies the tabs, the items of the active tab and the selected item's detail; the component renders the list and the detail, copies the body when asked, and reports switch intent.

```tsx
import { AgentConnect } from '@openviking/react/components/agent-connect';
import '@openviking/react/components/agent-connect/style.css';

<AgentConnect
  tabs={tabs}
  activeTabId={activeTabId}
  items={items}
  selectedId={selectedId}
  detail={detail}
  onTabChange={setActiveTabId}
  onSelect={setSelectedId}
  onCopy={trackCopy}
/>;
```

Where the catalog comes from and how the body is rendered are the host's business — the component does not touch them. Copying the body is the component's own job.

## Why the body is a host-supplied node

Connection instructions are naturally Markdown with code blocks, and hosts do not share one catalog source: one fetches a remote JSON document, another may read local files. If the component fetched the catalog and rendered Markdown itself, it would have to know the host's catalog address, caching strategy and Markdown dialect, and end up tied to one documentation site.

This component goes the other way: **the host provides the items and the body, and the component lays them out, copies the body and reports intent.**

- The body is a node, so the host can pass rendered Markdown, plain text or any component.
- The plain text for copying is separate from the body used for display: display can be formatted, while copying must be clean text.
- Tabs and the selected item are controlled; the component caches no catalog.

## What the component owns

- Rendering the tab row and highlighting the active tab.
- Rendering the left item list: icon, name, one-line summary and selected state.
- Rendering the right detail: title, icon, description, tags, body and the copy control.
- Copying: done by the component, notifying the copied text on success.

## What the component does not own

- It does not fetch the catalog, render Markdown or judge whether an item is connectable.
- It does not own the tabs, the selected item or the body — all controlled.
- It does not take over routing or analytics: switching tabs and selecting an item report intent, and `onCopy` is only a success notification.
- It does not embed any specific agent name or access method; items are entirely declared by the host.

## Docs and development

- The props table and runnable examples are in the [component docs](../../../../../docs/docs/en/components/agent-connect.mdx) ([Chinese](../../../../../docs/docs/zh/components/agent-connect.mdx)).
- Development and build commands are in the [UI workspace README](../../../../../README.md); module conventions are in the [package README](../../../README.md).
- The standalone stylesheet entry is `@openviking/react/components/agent-connect/style.css`, separate from the JS entry and imported on demand.

This is currently a placeholder component: it renders a fixed-size empty shell and consumes no props. The above describes the target behavior.
