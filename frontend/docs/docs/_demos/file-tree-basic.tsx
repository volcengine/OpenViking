import { useState } from 'react';
import { FileTree } from '@openviking/react/components/file-tree';
import type {
  FileTreeEntryRow,
  FileTreeRow,
} from '@openviking/react/components/file-tree';
import '@openviking/react/components/file-tree/style.css';

/** Directories and the rows revealed when they are expanded. */
const childrenOf: Record<string, FileTreeRow[]> = {
  resources: [
    {
      kind: 'entry',
      id: 'resources/guide.md',
      uri: 'viking://resources/guide.md',
      name: 'guide.md',
      type: 'file',
      depth: 1,
      levelLabel: 'L1',
    },
    {
      kind: 'entry',
      id: 'resources/spec.pdf',
      uri: 'viking://resources/spec.pdf',
      name: 'spec.pdf',
      type: 'file',
      depth: 1,
    },
    {
      kind: 'load-more',
      id: 'load-more:resources',
      parentId: 'resources',
      depth: 1,
    },
  ],
  memories: [
    {
      kind: 'entry',
      id: 'memories/profile.md',
      uri: 'viking://memories/profile.md',
      name: 'profile.md',
      type: 'file',
      depth: 1,
    },
  ],
  skills: [{ kind: 'empty', id: 'empty:skills', parentId: 'skills', depth: 1 }],
};

const topLevel: FileTreeRow[] = [
  {
    kind: 'entry',
    id: 'resources',
    uri: 'viking://resources',
    name: 'resources',
    type: 'directory',
    depth: 0,
    hasChildren: true,
    description: 'Imported documents',
  },
  {
    kind: 'entry',
    id: 'memories',
    uri: 'viking://memories',
    name: 'memories',
    type: 'directory',
    depth: 0,
    hasChildren: true,
  },
  {
    kind: 'entry',
    id: 'skills',
    uri: 'viking://skills',
    name: 'skills',
    type: 'directory',
    depth: 0,
  },
];

/** Flattens the open directories into the ordered row list the tree expects. */
function buildRows(expanded: ReadonlySet<string>): FileTreeRow[] {
  const rows: FileTreeRow[] = [];
  for (const row of topLevel) {
    if (row.kind !== 'entry') {
      rows.push(row);
      continue;
    }
    const isOpen = expanded.has(row.id);
    rows.push({ ...row, expanded: isOpen });
    if (isOpen) rows.push(...(childrenOf[row.id] ?? []));
  }
  return rows;
}

export default function FileTreeBasic() {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(
    new Set(['resources']),
  );
  const [selectedId, setSelectedId] = useState<string | null>(
    'resources/guide.md',
  );

  const handleToggle = (row: FileTreeEntryRow) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(row.id)) next.delete(row.id);
      else next.add(row.id);
      return next;
    });
  };

  return (
    <div style={{ height: 280 }}>
      <FileTree
        rows={buildRows(expanded)}
        selectedId={selectedId}
        slots={{
          toolbar: (
            <div style={{ color: '#8f959e', fontSize: 12 }}>
              Toolbar is a slot — search, import and refresh live here.
            </div>
          ),
        }}
        renderDashboard={(row) =>
          row.id === 'resources' ? <span title="Has a dashboard">◧</span> : null
        }
        operations={{
          select: (row) => {
            setSelectedId(row.id);
            return Promise.resolve();
          },
          toggleExpand: (row) => {
            handleToggle(row);
            return Promise.resolve();
          },
        }}
      />
    </div>
  );
}
