import { useLang } from '@rspress/core/runtime';
import { useState } from 'react';
import { FileBrowserActionBar } from '@openviking/react/components/file-browser-action-bar';
import '@openviking/react/components/file-browser-action-bar/style.css';

export default function FileBrowserActionBarCustom() {
  const componentLocale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const text =
    componentLocale === 'zh-CN'
      ? { wrapped: '导入按钮由宿主包装', received: '宿主已收到导入事件' }
      : {
          wrapped: 'The import button is wrapped by the host',
          received: 'The host received the import event',
        };
  const [message, setMessage] = useState(text.wrapped);
  return (
    <div
      lang={componentLocale}
      style={{ width: 352, maxWidth: '100%', display: 'grid', gap: 8 }}
    >
      <FileBrowserActionBar
        locale={componentLocale}
        agentConnect={{ apiKey: 'demo-api-key', locale: componentLocale }}
        onImportResources={() => setMessage(text.received)}
        renderImportResources={(trigger) => (
          <span data-host-import-trigger>{trigger}</span>
        )}
      />
      <span role="status">{message}</span>
    </div>
  );
}
