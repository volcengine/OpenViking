import { useCallback, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { useLang } from '@rspress/core/runtime';
import { FileTree } from '@openviking/react/components/file-tree';
import type {
  FileTreeHandle,
  FileTreeRow,
} from '@openviking/react/components/file-tree';
import { DirectoryTour } from '@openviking/react/components/directory-tour';
import '@openviking/react/components/file-tree/style.css';
import '@openviking/react/components/directory-tour/style.css';
import '#directory-tour-basic.css';
import '#directory-tour-team.css';
import { TeamTourContent } from '#directory-tour-team-content';

const FILES = ['requirements', 'design', 'technical'];
const entry = (
  id: string,
  name: string,
  depth = 0,
  file = false,
): FileTreeRow => ({
  kind: 'entry',
  id,
  uri: `viking://${id}`,
  name,
  depth,
  type: file ? 'file' : 'directory',
  expanded: !file,
});

export default function DirectoryTourTeam() {
  const zh = useLang() === 'zh';
  const locale = zh ? 'zh-CN' : 'en-US';
  const tree = useRef<FileTreeHandle>(null);
  const [shown, setShown] = useState(false);
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState(false);
  const [status, setStatus] = useState('');
  const [selectedId, select] = useState<string>();
  const close = useCallback(() => setOpen(false), []);
  const locateTarget = useCallback((_target: string, signal: AbortSignal) => {
    if (signal.aborted) return Promise.resolve(null);
    const rows = ['team', ...FILES.map((name) => `team/${name}`)]
      .map((id) => tree.current?.getRowElement(id))
      .filter((row): row is HTMLElement => Boolean(row));
    return Promise.resolve(rows.length ? rows : null);
  }, []);
  const title = zh ? '团队经验' : 'Team experiences';
  const rows = [entry('resources', 'Resources')];
  if (shown)
    rows.push(
      entry('team', title),
      ...FILES.map((name, index) =>
        entry(
          `team/${name}`,
          zh
            ? ['需求评审规范', '设计评审规范', '技术评审规范'][index]!
            : `${name}.md`,
          1,
          true,
        ),
      ),
    );
  const done = () => {
    close();
    setSeen(true);
  };
  return (
    <div className="ov-directory-tour-demo">
      <div className="ov-directory-tour-demo__controls">
        <label>
          <input
            type="checkbox"
            checked={shown}
            onChange={(event) => {
              const next = event.target.checked;
              flushSync(() => setShown(next));
              setOpen(next && !seen);
            }}
          />
          <span>
            {zh ? '模拟团队经验目录出现' : 'Simulate team directory appearing'}
          </span>
        </label>
        {shown && (
          <button type="button" onClick={() => setOpen(true)}>
            {zh ? '查看团队经验介绍' : 'View team introduction'}
          </button>
        )}
        <output aria-live="polite">
          {seen
            ? zh
              ? '当前示例已读，再次出现不会自动提示'
              : 'Read in this demo; reappearance will not reopen the guide'
            : ''}
        </output>
      </div>
      <div className="ov-directory-tour-demo__layout">
        <FileTree
          locale={locale}
          ref={tree}
          rows={rows}
          selectedId={selectedId}
          operations={{
            select: (row) => {
              select(row.id);
              return Promise.resolve();
            },
          }}
        />
        <p>
          {status ||
            (zh
              ? '勾选后高亮整个目录分组；不会改变文件选中状态。'
              : 'Check to highlight the entire group without changing selection.')}
        </p>
      </div>
      <DirectoryTour
        locale={locale}
        open={open}
        onClose={close}
        steps={[
          {
            id: 'team',
            target: 'team',
            title,
            description: '',
            cardWidth: 270,
          },
        ]}
        locateTarget={locateTarget}
        onComplete={done}
        renderContent={({ complete }) => (
          <TeamTourContent
            locale={locale}
            onDone={complete}
            onAction={(action) =>
              setStatus(
                zh
                  ? action === 'video'
                    ? '已触发视频播放回调（示例未配置视频地址）。'
                    : '已触发编辑规则回调（示例）。'
                  : action === 'video'
                    ? 'Video callback triggered; no video URL is configured in this demo.'
                    : 'Edit rules callback triggered (demo).',
              )
            }
          />
        )}
      />
    </div>
  );
}
