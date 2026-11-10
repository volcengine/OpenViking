import { useState } from 'react';
import {
  WikiDashboard,
  type WikiDashboardProps,
} from '@openviking/react/components/wiki-dashboard';
import '@openviking/react/components/wiki-dashboard/style.css';
import { wikiViewData, DEMO_WIKI, DEMO_WIKI_FILE } from '#dashboard-demo-data';

export default function WikiDashboardBasic() {
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
          editDisabledReason: '示例仅供查看',
          discussDisabledReason: '示例仅供查看',
          onOpenUri: () => setOpenedPath(DEMO_WIKI_FILE),
        };
  return (
    <div lang="zh-CN">
      <p>固定模拟数据。选择文件可查看冲突；无冲突时仍保留看板。</p>
      <p className="ov-demo-controls">
        <button
          type="button"
          onClick={() => {
            setUri(DEMO_WIKI);
            setEmpty(false);
          }}
        >
          Wiki 目录
        </button>{' '}
        <button
          type="button"
          onClick={() => {
            setUri(DEMO_WIKI_FILE);
            setEmpty(false);
          }}
        >
          Wiki 文件
        </button>{' '}
        <button type="button" onClick={() => setEmpty(!empty)}>
          {empty ? '显示冲突' : '无冲突示例'}
        </button>
        {uri !== DEMO_WIKI ? (
          <>
            {' '}
            <button type="button" onClick={() => setShowPath(!showPath)}>
              {showPath ? '隐藏文档路径' : '显示文档路径'}
            </button>
          </>
        ) : null}
      </p>
      <WikiDashboard key={uri} locale="zh-CN" {...dashboard} />
      {openedPath ? <p role="status">模拟打开文档：{openedPath}</p> : null}
    </div>
  );
}
