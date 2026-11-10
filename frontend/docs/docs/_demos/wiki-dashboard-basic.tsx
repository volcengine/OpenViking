import { useLang } from '@rspress/core/runtime';
import { useState } from 'react';
import {
  WikiDashboard,
  type WikiDashboardProps,
} from '@openviking/react/components/wiki-dashboard';
import '@openviking/react/components/wiki-dashboard/style.css';
import { wikiViewData, DEMO_WIKI, DEMO_WIKI_FILE } from '#dashboard-demo-data';

export default function WikiDashboardBasic() {
  const zh = useLang() === 'zh';
  const locale = zh ? 'zh-CN' : 'en-US';
  const text = zh
    ? {
        description: '固定模拟数据。选择文件可查看冲突；无冲突时仍保留看板。',
        directory: 'Wiki 目录',
        file: 'Wiki 文件',
        empty: '无冲突示例',
        show: '显示冲突',
        hidePath: '隐藏文档路径',
        showPath: '显示文档路径',
        readOnly: '示例仅供查看',
        opened: '模拟打开文档',
      }
    : {
        description:
          'Fixed mock data. Select a file to view conflicts; the board remains visible with zero conflicts.',
        directory: 'Wiki directory',
        file: 'Wiki file',
        empty: 'No-conflict example',
        show: 'Show conflicts',
        hidePath: 'Hide document path',
        showPath: 'Show document path',
        readOnly: 'Read-only example',
        opened: 'Simulated document navigation',
      };
  const [uri, setUri] = useState(DEMO_WIKI);
  const [empty, setEmpty] = useState(false);
  const [showPath, setShowPath] = useState(true);
  const [openedPath, setOpenedPath] = useState('');
  const dashboard: WikiDashboardProps =
    uri === DEMO_WIKI
      ? {
          kind: 'directory',
          data: {
            files: empty ? [] : [DEMO_WIKI_FILE],
            conflictFileCount: empty ? 0 : 1,
          },
          onOpenDashboard: setUri,
          onOpenUri: setUri,
        }
      : {
          kind: 'file',
          data: empty
            ? { conflicts: [] }
            : {
                ...wikiViewData,
                path: showPath ? wikiViewData.path : undefined,
              },
          editDisabledReason: text.readOnly,
          discussDisabledReason: text.readOnly,
          onOpenUri: () => setOpenedPath(DEMO_WIKI_FILE),
        };
  return (
    <div lang={locale}>
      <p>{text.description}</p>
      <p className="ov-demo-controls">
        <button
          type="button"
          onClick={() => {
            setUri(DEMO_WIKI);
            setEmpty(false);
          }}
        >
          {text.directory}
        </button>{' '}
        <button
          type="button"
          onClick={() => {
            setUri(DEMO_WIKI_FILE);
            setEmpty(false);
          }}
        >
          {text.file}
        </button>{' '}
        <button type="button" onClick={() => setEmpty(!empty)}>
          {empty ? text.show : text.empty}
        </button>
        {uri !== DEMO_WIKI ? (
          <>
            {' '}
            <button type="button" onClick={() => setShowPath(!showPath)}>
              {showPath ? text.hidePath : text.showPath}
            </button>
          </>
        ) : null}
      </p>
      <WikiDashboard key={uri} locale={locale} {...dashboard} />
      {openedPath ? (
        <p role="status">
          {text.opened}: {openedPath}
        </p>
      ) : null}
    </div>
  );
}
