import { useLang } from '@rspress/core/runtime';
import { FileBrowserEntries } from '@openviking/react/components/file-browser-entries';
import '@openviking/react/components/file-browser-entries/style.css';

export default function FileBrowserEntriesBasic() {
  const componentLocale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  return (
    <div style={{ width: '100%', maxWidth: 352, display: 'grid', gap: 12 }}>
      <FileBrowserEntries
        locale={componentLocale}
        agentConnect={{ apiKey: 'demo-api-key', locale: componentLocale }}
        onImportResources={() => {}}
      />
      <FileBrowserEntries
        locale={componentLocale}
        onImportResources={() => {}}
      />
      <FileBrowserEntries locale={componentLocale} agentConnect={{ apiKey: 'demo-api-key', locale: componentLocale }} />
    </div>
  );
}
