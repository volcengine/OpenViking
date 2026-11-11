import { Button } from '#demo-controls';
import { useLang } from '@rspress/core/runtime';
import { useState } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import {
  DEMO_WIKI_FILE,
  MEMORIES,
  offlineFetch,
} from '#file-browser-basic-data';
import '@openviking/react/modules/file-browser/style.css';

export default function FileBrowserBasic() {
  const componentLocale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const text =
    componentLocale === 'zh-CN'
      ? {
          canonical: 'Wiki：规范 URI',
          alias: 'Wiki：~ 别名',
          import: '宿主已收到导入事件',
          hint: '打开搜索弹窗，输入 profile 体验离线搜索',
        }
      : {
          canonical: 'Wiki: canonical URI',
          alias: 'Wiki: ~ alias',
          import: 'Host received import',
          hint: 'Open search and submit profile to try offline search',
        };
  const [uri, setUri] = useState<string | null>(null);
  const [imported, setImported] = useState(false);
  return (
    <div
      lang={componentLocale}
      style={{
        height: 720,
        minWidth: 720,
        display: 'grid',
        gridTemplateRows: 'minmax(0, 1fr) auto',
        gap: 8,
      }}
    >
      <OpenVikingProvider fetch={offlineFetch}>
        <FileBrowser
          locale={componentLocale}
          uri={uri ?? ''}
          onUriChange={setUri}
          defaultExpandedUri={MEMORIES}
          agentConnect={{ apiKey: 'demo-api-key', locale: componentLocale }}
          onImportResources={() => setImported(true)}
          renderImportResources={(trigger) => (
            <span data-file-browser-import-trigger>{trigger}</span>
          )}
        />
      </OpenVikingProvider>
      <div className="ov-demo-controls">
        <Button
          size="small"
          type="default"
          htmlType="button"
          onClick={() => setUri(DEMO_WIKI_FILE)}
        >
          {text.canonical}
        </Button>{' '}
        <Button
          size="small"
          type="default"
          htmlType="button"
          onClick={() => setUri('viking://~/resources/handbook/release.md')}
        >
          {text.alias}
        </Button>
        <code>{uri}</code>
        <span role="status">{imported ? text.import : text.hint}</span>
      </div>
    </div>
  );
}
