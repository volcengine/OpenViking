import { useLang } from '@rspress/core/runtime';
import { useState, type CSSProperties } from 'react';

const checkboxStyle: CSSProperties = {
  display: 'inline-block',
  appearance: 'auto',
  width: 16,
  height: 16,
};
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import '@openviking/react/modules/file-browser/style.css';
import { ACL_URI, createAclDemo } from '#file-browser-acl-data';

export default function FileBrowserAclDemo() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const zh = locale === 'zh-CN';
  const [demo] = useState(createAclDemo);
  const [revision, setRevision] = useState(0);
  return (
    <div lang={locale}>
      <p>
        {zh
          ? '离线模拟数据 · 不修改真实授权。'
          : 'Offline simulation · no real grants are changed.'}
      </p>
      <div
        style={{ display: 'flex', flexWrap: 'wrap', gap: 16, marginBottom: 16 }}
      >
        <label>
          <input
            type="checkbox"
            style={checkboxStyle}
            onChange={(event) => demo.setFailSave(event.target.checked)}
          />{' '}
          {zh ? '模拟保存失败' : 'Simulate save failure'}
        </label>
        <label>
          <input
            type="checkbox"
            style={checkboxStyle}
            onChange={(event) => demo.setFailDirectory(event.target.checked)}
          />{' '}
          {zh ? '模拟成员加载失败' : 'Simulate directory failure'}
        </label>
        <label>
          {zh ? '当前权限 ' : 'Current permission '}
          <select
            defaultValue="manage"
            onChange={(event) => {
              demo.setLevel(event.target.value as 'read' | 'write' | 'manage');
              setRevision((value) => value + 1);
            }}
          >
            <option value="manage">{zh ? '可管理' : 'Can manage'}</option>
            <option value="write">{zh ? '可编辑' : 'Can edit'}</option>
            <option value="read">{zh ? '可阅读' : 'Can read'}</option>
          </select>
        </label>
      </div>
      <div style={{ height: 580, minWidth: 620 }}>
        <OpenVikingProvider fetch={demo.fetch} accountId="demo" userId="alice">
          <FileBrowser
            key={revision}
            acl
            locale={locale}
            rootUri="viking://resources"
            defaultUri={ACL_URI}
          />
        </OpenVikingProvider>
      </div>
    </div>
  );
}
