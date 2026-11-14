import { useMemo, useState } from 'react';
import { Select, SelectOption } from '#demo-controls';
import { useLang } from '@rspress/core/runtime';
import { OpenVikingProvider } from '@openviking/react/client';
import { Console } from '@openviking/react/modules/console';
import type { ConsoleMode } from '@openviking/react/modules/console';
import '@openviking/react/modules/console/style.css';
import { botDemoService } from '#bot-demo-service';
import { consoleTransport } from '#console-transport';

const transport = consoleTransport();
const choices = {
  both: ['conversation', 'command'],
  conversation: ['conversation'],
  command: ['command'],
} as const satisfies Record<string, readonly ConsoleMode[]>;

export default function ConsoleModes() {
  const zh = useLang() === 'zh';
  const [choice, setChoice] = useState<keyof typeof choices>('command');
  const service = useMemo(() => botDemoService(), []);
  return (
    <>
      <label>
        {zh ? '启用功能：' : 'Enabled modes: '}
        <Select<keyof typeof choices>
          locale={zh ? 'zh-CN' : 'en-US'}
          value={[choice]}
          onChange={([value]) => {
            if (value !== undefined) setChoice(value);
          }}
        >
          <SelectOption value="both">{zh ? '两者' : 'Both'}</SelectOption>
          <SelectOption value="conversation">VikingBot</SelectOption>
          <SelectOption value="command">TUI</SelectOption>
        </Select>
      </label>
      <OpenVikingProvider fetch={transport}>
        <div style={{ height: 640 }}>
          <Console
            locale={zh ? 'zh-CN' : 'en-US'}
            modes={choices[choice]}
            chatOptions={{ service }}
          />
        </div>
      </OpenVikingProvider>
    </>
  );
}
