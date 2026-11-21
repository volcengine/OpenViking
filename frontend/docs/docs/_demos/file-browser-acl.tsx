import { Checkbox, Select, SelectOption } from '#demo-controls';

import { useLang } from '@rspress/core/runtime';
import { useState } from 'react';

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
        <Checkbox onChange={(next) => demo.setFailSave(next)}>
          {' '}
          {zh ? '模拟保存失败' : 'Simulate save failure'}
        </Checkbox>
        <Checkbox onChange={(next) => demo.setFailDirectory(next)}>
          {' '}
          {zh ? '模拟成员加载失败' : 'Simulate directory failure'}
        </Checkbox>
        <label
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 8,
            maxWidth: '100%',
          }}
        >
          {zh ? '当前权限 ' : 'Current permission '}
          <Select<'read' | 'write' | 'manage'>
            defaultValue={['manage']}
            onChange={([value]) => {
              if (value === undefined) return;
              demo.setLevel(value);
              setRevision((value) => value + 1);
            }}
          >
            <SelectOption value="manage">
              {zh ? '可管理' : 'Can manage'}
            </SelectOption>
            <SelectOption value="write">
              {zh ? '可编辑' : 'Can edit'}
            </SelectOption>
            <SelectOption value="read">
              {zh ? '可阅读' : 'Can read'}
            </SelectOption>
          </Select>
        </label>
      </div>
      <div style={{ height: 580 }}>
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
