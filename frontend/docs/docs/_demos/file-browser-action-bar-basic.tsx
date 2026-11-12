import { useLang } from '@rspress/core/runtime';
import { FileBrowserActionBar } from '@openviking/react/components/file-browser-action-bar';
import '@openviking/react/components/file-browser-action-bar/style.css';

export default function FileBrowserActionBarBasic() {
  const componentLocale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  return (
    <div style={{ width: '100%', maxWidth: 352, display: 'grid', gap: 12 }}>
      <FileBrowserActionBar
        locale={componentLocale}
        agentConnect={{ apiKey: 'demo-api-key', locale: componentLocale }}
        onImportResources={() => {}}
      />
      <FileBrowserActionBar
        locale={componentLocale}
        onImportResources={() => {}}
      />
      <FileBrowserActionBar
        locale={componentLocale}
        agentConnect={{ apiKey: 'demo-api-key', locale: componentLocale }}
      />
    </div>
  );
}
