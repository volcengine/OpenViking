import { FileBrowserEntries } from '@openviking/react/components/file-browser-entries';
import '@openviking/react/components/file-browser-entries/style.css';

export default function FileBrowserEntriesBasic() {
  return (
    <div lang="zh-CN" style={{ width: 352, display: 'grid', gap: 12 }}>
      <FileBrowserEntries
        onConnectAgent={() => {}}
        onImportResources={() => {}}
        labels={{ connectAgent: '连接 Agent', importResources: '导入资源' }}
      />
      <FileBrowserEntries
        onImportResources={() => {}}
        labels={{ importResources: '导入资源' }}
      />
      <FileBrowserEntries
        onConnectAgent={() => {}}
        labels={{ connectAgent: '连接 Agent' }}
      />
    </div>
  );
}
