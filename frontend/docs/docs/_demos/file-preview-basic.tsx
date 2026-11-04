import { useState } from 'react';
import { FilePreview } from '@openviking/react/components/file-preview';
import type { FilePreviewTab } from '@openviking/react/components/file-preview';
import '@openviking/react/components/file-preview/style.css';

const markdown = `# LLM Wiki

Compile heterogeneous knowledge sources into durable, connected knowledge.

## Objective

Turn the supplied sources into reusable pages instead of a pile of summaries.
`;

const dashboard = (
  <div style={{ padding: 16, color: '#8f959e', fontSize: 13 }}>
    A content tab renders whatever the host passes — a dashboard, a graph, a
    summary. The component does not know what it is.
  </div>
);

export default function FilePreviewBasic() {
  const [activeHint, setActiveHint] = useState('');

  const tabs: [FilePreviewTab, ...FilePreviewTab[]] = [
    {
      kind: 'file',
      key: 'file',
      title: 'SKILL.md',
      source: {
        content: markdown,
        fileName: 'SKILL.md',
        path: 'viking://system/compile/llm-wiki/SKILL.md',
      },
      onCopyContent: (text) => {
        void navigator.clipboard.writeText(text);
        setActiveHint('Content copied');
      },
      onCopyPath: (path) => {
        void navigator.clipboard.writeText(path);
        setActiveHint('Path copied');
      },
      onOpenParent: (path) => setActiveHint(`Open parent of ${path}`),
    },
    {
      kind: 'content',
      key: 'dashboard',
      title: 'Dashboard',
      content: dashboard,
    },
  ];

  return (
    <div style={{ display: 'grid', gap: 8 }}>
      <div style={{ height: 320 }}>
        <FilePreview tabs={tabs} aria-label="File preview" />
      </div>
      <p style={{ margin: 0, color: '#8f959e', fontSize: 12 }}>
        {activeHint ||
          'Switch tabs, copy the path or the content, or toggle preview and source.'}
      </p>
    </div>
  );
}
