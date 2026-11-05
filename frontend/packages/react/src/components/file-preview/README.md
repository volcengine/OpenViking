# FilePreview

English / [中文](README_CN.md)

Multi-format file preview. The host supplies the content and file name; the component handles format detection, renderer loading, the header, tabs, view switching and editing.

> **This is currently a placeholder implementation.** The shell (tab row, content header row, tab switching, copying, editing, the ref handle) is implemented in its final shape and is interactive today; the file body is not yet wired to FileViewer, and file tabs fall back to plain text. The behavior described below is the target.

The caller does not need to know whether this is Markdown or PDF, and does not register renderers — the file name decides everything:

```tsx
<FilePreview
  tabs={[
    {
      kind: 'file',
      key: 'file',
      source: { content, fileName: 'SKILL.md', path: 'viking://…/SKILL.md' },
    },
  ]}
/>
```

## Why FileViewer is the foundation

The hard part of multi-format preview is not rendering one format but format routing, on-demand renderer loading, large-file admission and the state machine. Those already have mature implementations; rewriting them only produces a worse copy.

So this component builds on `FileViewerCore` from [`@ark-design/ui`](https://www.npmjs.com/package/@ark-design/ui) and fills in what OpenViking needs on top: header row, tabs, view switching, editing and business slots.

`@ark-design/ui` is an **optional peer dependency**: only consumers of this module need to install it, and other modules are unaffected without it.

```bash
npm install @ark-design/ui
```

The built-in renderers cover code, plain text, JSON, Markdown, HTML, spreadsheets, PDF, Word, PowerPoint, audio and video, CAD and 3D models. Extensionless files match by name, for example `Dockerfile` and `Makefile`. The supported set is fixed and not open to override.

## Content is driven by tabs

Preview content is a list of tabs, and each tab's body has one of two sources:

| Kind              | Render path                      | Typical use                                   |
| ----------------- | -------------------------------- | --------------------------------------------- |
| `kind: 'file'`    | Rendered by FileViewer by format | File source, code, documents                  |
| `kind: 'content'` | Fully rendered by the host       | Experience dashboards, wiki boards, summaries |

Both tab kinds share the shell — the tab row and the content header row (path breadcrumb, copy, permission marker, header slots) — and differ only in where the body comes from. The active tab is internal state; the host does not track it, and external jumps use the ref handle's `setActiveTab`.

When a directory is selected the first tab may be a content tab such as an L0/L1 summary, so the component never assumes the first tab is a file.

## State is decided by the component

Loading, failure and unsupported format are not expressed through props; the component derives them from the content and file type: no content means loading, content carrying an error means failure, and a file name with no matching renderer means unsupported. All three states can be replaced with host content through slots.

Editing works the same way: the draft, the saving state and failure hints are all internal. The host supplies the full text through `editing.loadContent` and performs the save through the async `editing.save`; `editing.onSave` notifies only after a successful save with the saved text, while a failure keeps the draft and shows a hint. `editing.onCancel` notifies after the draft is discarded and does not fire on a successful save.

Execution capability and success notification are separate: file operations come from `operations.openParent`, `operations.download` and `operations.openUri`, and only after success do `onOpenParent`, `onDownload` and `onOpenUri` fire. Omitting an executor makes that operation unavailable; omitting a notification does not affect it. Nothing fires on failure.

Copying is done by the component. `onCopyPath` and `onCopyContent` notify the copied value after a successful path or body copy and do not fire on failure; omitting them does not disable copying. A path copy control appears when a path is present, and a file tab's body copy control is disabled when there is no copyable text.

## What the component does not own

- It makes no business requests. Downloads, saves and navigation are executed by the host; the success notifications do not perform them.
- It does not judge who may view or edit — permission markers are rendered by the host and passed in through a slot.
- It embeds no business content. Dashboards, graphs and summaries are injected through tabs or slots, and the component does not know what they are.

## Docs and development

- The props table and runnable examples are in the docs site under `Components → FilePreview` (to be completed alongside the implementation).
- Development and build commands are in the [UI workspace README](../../../../../README.md); module conventions are in the [package README](../../../README.md).
- The standalone stylesheet entry `@openviking/react/components/file-preview/style.css` is separate from the JS entry and can be imported on demand.
