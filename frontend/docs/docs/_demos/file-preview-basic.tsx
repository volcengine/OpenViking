import { useState } from 'react';
import { FilePreview } from '@openviking/react/components/file-preview';
import type { FilePreviewTab } from '@openviking/react/components/file-preview';
import '@openviking/react/components/file-preview/style.css';

const markdown = `# LLM Wiki

Compile heterogeneous knowledge sources into durable, connected knowledge.

## Objective

Turn the supplied sources into reusable pages instead of a pile of summaries.
`;

const diagramSvg =
  'data:image/svg+xml;charset=utf-8,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="140">' +
      '<rect width="240" height="140" fill="#eef2f8"/>' +
      '<rect x="20" y="24" width="80" height="36" rx="6" fill="#2563c5"/>' +
      '<rect x="140" y="24" width="80" height="36" rx="6" fill="#7aa7e8"/>' +
      '<rect x="80" y="88" width="80" height="30" rx="6" fill="#a9c4ef"/>' +
      '</svg>',
  );

const dashboard = (
  <div style={{ padding: 16, color: '#8f959e', fontSize: 13 }}>
    A content tab renders whatever the host passes — a dashboard, a graph, a
    summary. The component does not know what it is.
  </div>
);

export default function FilePreviewBasic() {
  const [activeHint, setActiveHint] = useState('');
  const [content, setContent] = useState(markdown);

  const tabs: [FilePreviewTab, ...FilePreviewTab[]] = [
    {
      kind: 'file',
      key: 'file',
      title: 'SKILL.md',
      source: {
        content,
        fileName: 'SKILL.md',
        path: 'viking://system/compile/llm-wiki/SKILL.md',
      },
      onCopyContent: () => setActiveHint('Content copied'),
      onCopyPath: () => setActiveHint('Path copied'),
      operations: { openParent: async () => {} },
      onOpenParent: (path) =>
        setActiveHint(`Simulated parent navigation: ${path}`),
      editing: {
        save: (value) => {
          setContent(value);
          return Promise.resolve();
        },
        onSave: () => setActiveHint('Content saved'),
        onCancel: () => setActiveHint('Editing cancelled'),
      },
    },
    {
      kind: 'file',
      key: 'diagram',
      title: 'diagram.svg',
      source: {
        url: diagramSvg,
        fileName: 'diagram.svg',
        path: 'viking://system/compile/llm-wiki/diagram.svg',
      },
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
      <div style={{ height: 560 }}>
        <FilePreview tabs={tabs} aria-label="File preview" />
      </div>
      <p style={{ margin: 0, color: '#8f959e', fontSize: 12 }}>
        {activeHint ||
          'Switch tabs, copy the path or the content, or toggle preview and source.'}
      </p>
    </div>
  );
}
