import { useState } from 'react';
import { Tui } from '@openviking/react/components/tui';
import type { TuiRecord } from '@openviking/react/components/tui';
import '@openviking/react/components/tui/style.css';

const INITIAL_RECORDS: readonly TuiRecord[] = [
  {
    id: 'find',
    command: '/find "release checklist"',
    status: 'success',
    createdAt: '10:24',
    content: {
      variant: 'search-list',
      summary: 'Search scope: viking://resources/acme · 2 hits',
      items: [
        {
          id: 'release',
          title: 'viking://resources/acme/release.md',
          uri: 'viking://resources/acme/release.md',
          levelLabel: 'L2',
          typeLabel: 'file',
          scoreLabel: '0.92',
          scoreDetails: (
            <span>Semantic 0.92 · Keyword 0.71 · Recency 0.88 (demo data)</span>
          ),
          preview: 'Release checklist: freeze, verify, publish, announce.',
        },
        {
          id: 'overview',
          title: 'viking://resources/acme',
          uri: 'viking://resources/acme',
          levelLabel: 'L1',
          typeLabel: 'dir',
          scoreLabel: '0.74',
          preview: 'Project overview covering release and onboarding.',
        },
      ],
    },
  },
  {
    id: 'stat',
    command: '/stat viking://resources/acme/release.md',
    status: 'success',
    createdAt: '10:25',
    content: {
      variant: 'status',
      title: 'release.md',
      rows: [
        { label: 'URI', value: 'viking://resources/acme/release.md' },
        { label: 'Type', value: 'file' },
        { label: 'Size', value: '2.4 KB' },
        { label: 'Tags', value: 'team=release', variant: 'long-text' },
      ],
    },
  },
  {
    id: 'read',
    command: '/read viking://resources/acme/release.md',
    status: 'success',
    createdAt: '10:26',
    content: {
      variant: 'resource-preview',
      title: 'release.md',
      meta: ['Markdown', '2.4 KB'],
      uri: 'viking://resources/acme/release.md',
      content:
        '# Release checklist\n\n1. Freeze the branch.\n2. Verify the smoke suite.\n3. Publish and announce.\n',
    },
  },
  {
    id: 'ls',
    command: '/ls viking://resources/acme',
    status: 'success',
    createdAt: '10:26',
    content: {
      variant: 'resource-tree',
      title: 'viking://resources/acme',
      items: [
        {
          id: 'release',
          title: 'release.md',
          uri: 'viking://resources/acme/release.md',
          detailRows: [
            { label: 'Name', value: 'release.md' },
            { label: 'Type', value: 'file' },
            { label: 'Size', value: '2.4 KB' },
          ],
        },
        {
          id: 'onboarding',
          title: 'onboarding/',
          uri: 'viking://resources/acme/onboarding',
          detailRows: [
            { label: 'Name', value: 'onboarding/' },
            { label: 'Type', value: 'directory' },
            { label: 'Size', value: '—' },
          ],
        },
      ],
    },
  },
  {
    id: 'mkdir',
    command: '/mkdir viking://resources/acme/archive',
    status: 'success',
    createdAt: '10:27',
    content: {
      variant: 'write-result',
      title: 'Created',
      lines: ['Directory: viking://resources/acme/archive', 'Mode: 0755'],
    },
  },
  {
    id: 'write',
    command: '/write viking://resources/acme/notes.md --content "Hello"',
    status: 'error',
    createdAt: '10:27',
    content: {
      variant: 'text',
      text: 'Permission denied: viking://resources/acme/notes.md is read-only for this account.',
    },
  },
];

const HELP_RECORD: TuiRecord = {
  id: 'help',
  command: '/help',
  status: 'success',
  content: {
    variant: 'command-help',
    sections: [
      {
        key: 'core',
        label: 'Core workflow',
        items: [
          {
            key: 'add-resource',
            name: '/add-resource',
            description: 'Add resources into OpenViking',
          },
          {
            key: 'add-memory',
            name: '/add-memory',
            description: 'Add memory in one shot',
          },
        ],
      },
      {
        key: 'search',
        label: 'Search & context',
        items: [
          {
            key: 'find',
            name: '/find',
            description: 'Run semantic retrieval',
          },
          {
            key: 'read',
            name: '/read',
            description: 'Read file content',
          },
        ],
      },
    ],
  },
};

/** 记录与输入都由本示例维护，演示宿主如何接入 Tui。 */
export default function TuiBasic() {
  const [records, setRecords] = useState(INITIAL_RECORDS);
  const [value, setValue] = useState('');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 520 }}>
      <Tui
        records={records}
        value={value}
        onValueChange={setValue}
        onSubmit={(command) => {
          const pendingId = `pending-${records.length}`;
          setRecords((current) => [
            ...current,
            { id: pendingId, command, status: 'pending' },
          ]);
          setValue('');
          window.setTimeout(() => {
            setRecords((current) =>
              current.map((record) =>
                record.id === pendingId
                  ? {
                      ...record,
                      status: 'success',
                      content: {
                        variant: 'text',
                        text: `Ran ${command} (demo result).`,
                      },
                    }
                  : record,
              ),
            );
          }, 900);
        }}
        onOpenUri={(uri) => window.alert(`Open ${uri} (demo)`)}
        onCopy={(text) => console.log('copied', text)}
      />
      <button
        type="button"
        style={{ alignSelf: 'flex-start', marginTop: 12 }}
        onClick={() => setRecords((current) => [...current, HELP_RECORD])}
      >
        Show command help
      </button>
    </div>
  );
}
