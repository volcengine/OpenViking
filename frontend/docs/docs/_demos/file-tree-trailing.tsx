import { Button, Select, SelectOption } from '#demo-controls';
import { useLang } from '@rspress/core/runtime';
import { useState } from 'react';
import { FileTree } from '@openviking/react/components/file-tree';
import type { FileTreeProps } from '@openviking/react/components/file-tree';
import '@openviking/react/components/file-tree/style.css';

const rows = [
  { name: 'notes.md', depth: 0 },
  {
    name: 'release-notes-with-a-long-file-name-for-horizontal-scrolling.md',
    depth: 2,
  },
  { name: 'summary.md', depth: 1 },
].map(({ name, depth }) => ({
  kind: 'entry' as const,
  type: 'file' as const,
  id: name,
  uri: `viking://resources/${name}`,
  name,
  depth,
  levelLabel: 'L0',
}));

export default function FileTreeTrailing() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const text =
    locale === 'zh-CN'
      ? {
          position: '行尾位置',
          viewport: '可视区域右侧',
          row: '完整行末尾',
          open: '打开',
          opened: '已打开',
        }
      : {
          position: 'Trailing position',
          viewport: 'Visible right edge',
          row: 'Full row end',
          open: 'Open',
          opened: 'Opened',
        };
  const [position, setPosition] =
    useState<NonNullable<FileTreeProps['rowTrailingPosition']>>('viewport-end');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [opened, setOpened] = useState('');
  return (
    <div>
      <label>
        {text.position}{' '}
        <Select
          value={[position]}
          onChange={([next]) => {
            if (next === 'row-end' || next === 'viewport-end')
              setPosition(next);
          }}
        >
          <SelectOption value="viewport-end">{text.viewport}</SelectOption>
          <SelectOption value="row-end">{text.row}</SelectOption>
        </Select>
      </label>
      <div style={{ height: 160, maxWidth: 360, marginTop: 12 }}>
        <FileTree
          locale={locale}
          rows={rows}
          selectedId={selectedId}
          rowTrailingPosition={position}
          search={false}
          refresh={false}
          operations={{
            select: (row) => {
              setSelectedId(row.id);
              return Promise.resolve();
            },
          }}
          renderRowTrailing={(row) => (
            <Button
              size="small"
              onClick={(event) => {
                event.stopPropagation();
                setOpened(row.name);
              }}
            >
              {text.open}
            </Button>
          )}
        />
      </div>
      <p role="status">{opened ? `${text.opened}: ${opened}` : ''}</p>
    </div>
  );
}
