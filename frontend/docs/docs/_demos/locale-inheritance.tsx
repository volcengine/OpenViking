import { Select, SelectOption } from '#demo-controls';
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
  const [block, setBlock] = useState<Override>('inherit');
  const [component, setComponent] = useState<Override>('inherit');
  const options = (
    <>
      <SelectOption value="zh-CN">简体中文</SelectOption>
      <SelectOption value="en-US">English</SelectOption>
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
            <Select
              id={`${id}-global`}
              name="global-locale"
              style={controlStyle}
              value={[global]}
              onChange={([value]) => {
                if (value !== undefined) setGlobal(value);
              }}
            >
              {options}
            </Select>
          </label>
          <label htmlFor={`${id}-block`} style={fieldStyle}>
            {zh ? '区块语言 ' : 'Block language '}
            <Select
              id={`${id}-block`}
              name="block-locale"
              style={controlStyle}
              value={[block]}
              onChange={([value]) => {
                if (value !== undefined) setBlock(value);
              }}
            >
              <SelectOption value="inherit">
                {zh ? '继承上层' : 'Inherit'}
              </SelectOption>
              {options}
            </Select>
          </label>
          <label htmlFor={`${id}-component`} style={fieldStyle}>
            {zh ? '组件语言 ' : 'Component language '}
            <Select
              id={`${id}-component`}
              name="component-locale"
              style={controlStyle}
              value={[component]}
              onChange={([value]) => {
                if (value !== undefined) setComponent(value);
              }}
            >
              <SelectOption value="inherit">
                {zh ? '继承上层' : 'Inherit'}
              </SelectOption>
              {options}
            </Select>
          </label>
        </div>
      </fieldset>
      <OpenVikingProvider locale={global} fetch={transport}>
        <div style={{ height: 460 }}>
          <Console
            locale={block === 'inherit' ? undefined : block}
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
