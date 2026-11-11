import { useId, useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { OpenVikingProvider } from '@openviking/react/client';
import type { OpenVikingProviderProps } from '@openviking/react/client';
import { Console } from '@openviking/react/modules/console';
import { FileTree } from '@openviking/react/components/file-tree';
import '@openviking/react/modules/console/style.css';
import '@openviking/react/components/file-tree/style.css';
import { consoleTransport } from '#console-transport';

type Locale = NonNullable<OpenVikingProviderProps['locale']>;
type Override = Locale | 'inherit';
const transport = consoleTransport();
const fieldStyle = {
  display: 'flex',
  flexDirection: 'column',
  gap: '.25rem',
} as const;
const controlStyle = {
  font: 'inherit',
  fontSize: 'max(1rem, 1em)',
  minHeight: '3rem',
};

export default function LocaleInheritance() {
  const zh = useLang() === 'zh';
  const id = useId();
  const [global, setGlobal] = useState<Locale>(zh ? 'zh-CN' : 'en-US');
  const [module, setModule] = useState<Override>('inherit');
  const [component, setComponent] = useState<Override>('inherit');
  const options = (
    <>
      <option value="zh-CN">简体中文</option>
      <option value="en-US">English</option>
    </>
  );
  return (
    <div>
      <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
        <legend>{zh ? '语言配置' : 'Language configuration'}</legend>
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 12,
            marginBottom: 16,
          }}
        >
          <label htmlFor={`${id}-global`} style={fieldStyle}>
            {zh ? '全局语言 ' : 'Global language '}
            <select
              id={`${id}-global`}
              name="global-locale"
              style={controlStyle}
              value={global}
              onChange={(e) => setGlobal(e.target.value as Locale)}
            >
              {options}
            </select>
          </label>
          <label htmlFor={`${id}-module`} style={fieldStyle}>
            {zh ? '模块语言 ' : 'Module language '}
            <select
              id={`${id}-module`}
              name="module-locale"
              style={controlStyle}
              value={module}
              onChange={(e) => setModule(e.target.value as Override)}
            >
              <option value="inherit">{zh ? '继承上层' : 'Inherit'}</option>
              {options}
            </select>
          </label>
          <label htmlFor={`${id}-component`} style={fieldStyle}>
            {zh ? '组件语言 ' : 'Component language '}
            <select
              id={`${id}-component`}
              name="component-locale"
              style={controlStyle}
              value={component}
              onChange={(e) => setComponent(e.target.value as Override)}
            >
              <option value="inherit">{zh ? '继承上层' : 'Inherit'}</option>
              {options}
            </select>
          </label>
        </div>
      </fieldset>
      <OpenVikingProvider locale={global} fetch={transport}>
        <div style={{ height: 460 }}>
          <Console
            locale={module === 'inherit' ? undefined : module}
            defaultMode="command"
            slots={{
              commandHelp: (
                <FileTree
                  rows={[]}
                  locale={component === 'inherit' ? undefined : component}
                  style={{ height: 72 }}
                />
              ),
            }}
          />
        </div>
      </OpenVikingProvider>
    </div>
  );
}
