import { useLang } from '@rspress/core/runtime';
import { useState } from 'react';
import { FileBrowserEntries } from '@openviking/react/components/file-browser-entries';
import '@openviking/react/components/file-browser-entries/style.css';

export default function FileBrowserEntriesCustom() {
  const componentLocale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const [message, setMessage] = useState('导入按钮由宿主包装');
  return (
    <div
      lang="zh-CN"
      style={{ width: 352, maxWidth: '100%', display: 'grid', gap: 8 }}
    >
      <FileBrowserEntries
        locale={componentLocale}
        agentConnect={{ apiKey: 'demo-api-key', locale: componentLocale }}
        onImportResources={() => setMessage('宿主已收到导入事件')}
        renderImportResources={(trigger) => (
          <span data-host-import-trigger>{trigger}</span>
        )}
      />
      <span role="status">{message}</span>
    </div>
  );
}
